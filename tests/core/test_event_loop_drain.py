"""Regression tests for Akgent event-loop draining on stop.

Covers the anyio per-loop run-var leak: a finished run Task held in anyio's
module-global ``_run_vars`` (keyed weakly by the loop) strong-references the loop,
defeating weak collection so the closed loop never gets reclaimed. The fix evicts
the loop's entry on stop.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import logging
import sys
import weakref

import pytest
from akgentic.core import agent as agent_module
from akgentic.core.agent import Akgent, _evict_anyio_run_vars
from akgentic.core.messages.orchestrator import StopMessage

# anyio is an optional transitive dep (pydantic-ai/httpx); the eviction feature is
# best-effort precisely because anyio may be absent. Skip only the anyio-shape tests
# when it is not installed — the drain/cancel tests below need no anyio.
_anyio_available = importlib.util.find_spec("anyio") is not None


class _LoopHolder:
    """Stand-in for a finished anyio Task: a value that strong-refs the loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop  # mirrors Task._loop — the self-reference that defeats the weak key


@pytest.mark.skipif(not _anyio_available, reason="anyio not installed in this environment")
class TestEvictAnyioRunVars:
    """Evicting anyio's per-loop entry releases the otherwise-pinned loop."""

    def test_self_referential_entry_pins_loop_until_evicted(self) -> None:
        from anyio.lowlevel import _run_vars

        loop = asyncio.new_event_loop()
        loop.close()
        # Reproduce anyio's leak shape: _run_vars[loop] value strong-refs the loop.
        _run_vars[loop] = {"holder": _LoopHolder(loop)}
        ref = weakref.ref(loop)

        del loop
        gc.collect()
        survivor = ref()
        # CONTROL: the self-referential anyio entry keeps the closed loop alive.
        assert survivor is not None

        # FIX: evicting the per-loop entry breaks the self-ref → loop reclaimed.
        _evict_anyio_run_vars(survivor)
        del survivor
        gc.collect()
        assert ref() is None, "loop must be reclaimed after anyio run-vars eviction"

    def test_evict_is_noop_when_loop_absent(self) -> None:
        from anyio.lowlevel import _run_vars

        loop = asyncio.new_event_loop()
        loop.close()
        _evict_anyio_run_vars(loop)  # not in _run_vars — must not raise
        assert loop not in _run_vars


class TestEvictDegradesWithoutAnyio:
    """Eviction degrades to a silent no-op when anyio's private API is unreachable."""

    def test_evict_is_noop_when_anyio_import_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A None entry in sys.modules makes the import raise, standing in for anyio
        # being absent or having renamed/relocated _run_vars.
        monkeypatch.setitem(sys.modules, "anyio.lowlevel", None)

        loop = asyncio.new_event_loop()
        loop.close()
        _evict_anyio_run_vars(loop)  # must swallow the ImportError, not raise


class TestCancelPendingTasks:
    """Stragglers are cancelled and awaited before the loop closes."""

    def test_pending_tasks_are_cancelled(self) -> None:
        loop = asyncio.new_event_loop()
        try:

            async def _forever() -> None:
                await asyncio.sleep(3600)

            task = loop.create_task(_forever())
            assert not task.done()

            Akgent._cancel_pending_tasks(loop)

            # The straggler itself was cancelled and awaited, not merely dropped.
            assert task.done()
            assert task.cancelled()
            assert not asyncio.all_tasks(loop)
        finally:
            loop.close()


class _DrainStub:
    """Minimal stand-in exposing the ``config.name`` the drain logs on failure."""

    class _Config:
        name = "drain-stub"

    config = _Config()


class TestDrainEventLoop:
    """``_drain_event_loop`` is best-effort and safe to call repeatedly."""

    def test_drain_is_idempotent_on_closed_loop(self) -> None:
        """Draining an already-closed loop is a no-op and never raises.

        Covers the double-stop path: the ``loop.is_closed()`` guard skips the
        close work so teardown can run twice without a double-close.
        """
        loop = asyncio.new_event_loop()
        loop.close()

        stub = _DrainStub()
        # First call sees a closed loop → guard skips close, only evicts run-vars.
        Akgent._drain_event_loop(stub, loop)  # type: ignore[arg-type]
        # Second call must also be a no-op: no double-close, no raise.
        Akgent._drain_event_loop(stub, loop)  # type: ignore[arg-type]

        assert loop.is_closed()


class _FailingLoop:
    """Loop stand-in whose teardown raises, exercising the drain's failure path."""

    def is_closed(self) -> bool:
        return False

    def shutdown_asyncgens(self) -> None:
        raise RuntimeError("teardown boom")


class _OnStopProbe:
    """Borrows Akgent's teardown methods so on_stop can run without a live actor."""

    on_stop = Akgent.on_stop
    _drain_event_loop = Akgent._drain_event_loop
    _cancel_pending_tasks = staticmethod(Akgent._cancel_pending_tasks)
    config = _DrainStub._Config()

    def __init__(self, loop: object) -> None:
        self._event_loop = loop
        self.notified: list[object] = []

    def _notify_orchestrator(self, message: object) -> None:
        self.notified.append(message)


class TestDrainFailureIsBestEffort:
    """A failing teardown is logged and swallowed — it never blocks stop telemetry."""

    def test_failure_is_logged_and_run_vars_still_evicted(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        evicted: list[object] = []
        monkeypatch.setattr(agent_module, "_evict_anyio_run_vars", evicted.append)

        loop = _FailingLoop()
        with caplog.at_level(logging.WARNING):
            Akgent._drain_event_loop(_DrainStub(), loop)  # type: ignore[arg-type]

        assert "event-loop drain failed" in caplog.text
        # The eviction lives in a finally, so it runs even when the close path raised.
        assert evicted == [loop]

    def test_stop_message_is_sent_even_when_drain_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        probe = _OnStopProbe(_FailingLoop())

        with caplog.at_level(logging.WARNING):
            probe.on_stop()

        assert "event-loop drain failed" in caplog.text
        assert [type(m) for m in probe.notified] == [StopMessage]
