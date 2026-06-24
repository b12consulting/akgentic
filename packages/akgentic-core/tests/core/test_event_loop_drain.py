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
import weakref

import pytest

from akgentic.core.agent import Akgent, _evict_anyio_run_vars

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


class TestCancelPendingTasks:
    """Stragglers are cancelled and awaited before the loop closes."""

    def test_pending_tasks_are_cancelled(self) -> None:
        loop = asyncio.new_event_loop()
        try:

            async def _forever() -> None:
                await asyncio.sleep(3600)

            loop.create_task(_forever())
            assert any(not t.done() for t in asyncio.all_tasks(loop))

            Akgent._cancel_pending_tasks(loop)

            assert all(t.done() for t in asyncio.all_tasks(loop))
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

        Covers the double-stop path and subclasses that never created a loop
        (the ``loop.is_closed()`` guard skips the close work).
        """
        loop = asyncio.new_event_loop()
        loop.close()

        stub = _DrainStub()
        # First call sees a closed loop → guard skips close, only evicts run-vars.
        Akgent._drain_event_loop(stub, loop)  # type: ignore[arg-type]
        # Second call must also be a no-op: no double-close, no raise.
        Akgent._drain_event_loop(stub, loop)  # type: ignore[arg-type]

        assert loop.is_closed()
