"""Real-anyio leak tests for ``ReactAgent.close()`` step 5 (Epic 11, ADR-009).

Story 11-5: ``close()`` evicts anyio's per-loop ``_run_vars`` entry **after**
``loop.close()`` (FR9), so the closed loop is actually freed by the GC instead
of being pinned forever by the ``_root_task`` ``RunVar`` that
``anyio.to_thread.run_sync`` stores in ``anyio.lowlevel._run_vars[loop]``.

Unlike Story 11-4 (which stubbed ``run()`` and never drove the anyio path),
these tests drive a **real** anyio path (``to_thread.run_sync`` / a task group)
on the owned loop so the anchor actually forms, then assert via a ``weakref``
after ``gc.collect()`` that the loop is freed once evicted — with a negative
control (no eviction ⇒ survives) and a positive control (no anyio ⇒ freed
regardless) to isolate the anchor as the cause.

Zero-egress: the anyio drive uses a plain sync no-op via ``to_thread.run_sync``
and a bare task group; no model is contacted and no request is sent.
"""

import asyncio
import gc
import weakref
from typing import Any
from unittest.mock import patch

import anyio
import anyio.lowlevel as ll
import pytest
from akgentic.llm import ModelConfig, ReactAgent, ReactAgentConfig
from akgentic.llm.agent import _evict_anyio_run_vars


@pytest.fixture
def minimal_config() -> ReactAgentConfig:
    """Minimal config; the OpenAI provider builds with no key (no egress here)."""
    return ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))


def _drive_anyio_anchor(loop: asyncio.AbstractEventLoop) -> None:
    """Drive a real anyio path on ``loop`` so ``_run_vars[loop]`` is populated.

    ``to_thread.run_sync`` → ``find_root_task()`` sets the ``_root_task``
    ``RunVar``; a bare task group also touches the run-var machinery. Both run
    on ``loop`` via ``run_until_complete``; the coroutine is run to completion
    and dropped so it pins nothing.
    """

    async def _drive() -> None:
        await anyio.to_thread.run_sync(lambda: None)
        async with anyio.create_task_group():
            pass

    loop.run_until_complete(_drive())


# ---------------------------------------------------------------------------
# AC #4 — the real anyio drive populates _run_vars[loop] (the anchor forms)
# ---------------------------------------------------------------------------


def test_real_anyio_drive_populates_run_vars() -> None:
    """A ``to_thread``/task-group drive sets a non-empty ``_run_vars[loop]`` (AC #4)."""
    loop = asyncio.new_event_loop()
    try:
        _drive_anyio_anchor(loop)
        # Guard for a future anyio that changes this internal; on the locked
        # anyio 4.14.0 the anchor MUST form, so this skip must NOT fire here.
        if loop not in ll._run_vars:
            pytest.skip("anyio _run_vars anchor not populated on this version")
        assert ll._run_vars[loop]  # non-empty: the _root_task RunVar was set
    finally:
        loop.close()
        _evict_anyio_run_vars(loop)


# ---------------------------------------------------------------------------
# AC #5 — after close() + gc.collect(), a weakref to the loop is dead
# ---------------------------------------------------------------------------


def test_loop_freed_after_close_and_eviction(minimal_config: ReactAgentConfig) -> None:
    """The owned loop is collected after ``close()`` (which evicts) + ``gc.collect()`` (AC #5)."""
    agent = ReactAgent(config=minimal_config)
    # Drive the real anchor on the agent's own loop, exactly as production hits it.
    _drive_anyio_anchor(agent._loop)
    if agent._loop not in ll._run_vars:
        agent.close()
        pytest.skip("anyio _run_vars anchor not populated on this version")

    wr = weakref.ref(agent._loop)
    agent.close()  # closes the loop AND evicts _run_vars[loop] (step 5)

    # Drop every strong reference to the loop. __init__ called set_event_loop,
    # so the asyncio policy also pins this loop as the thread-current one; clear
    # it (as constructing the next agent would) or it survives gc on its own.
    del agent
    asyncio.set_event_loop(None)
    gc.collect()  # cyclic loop↔tasks↔agent graph: refcounting alone won't free it

    assert wr() is None


# ---------------------------------------------------------------------------
# AC #6 — negative control: WITHOUT the eviction the loop SURVIVES gc.collect()
# ---------------------------------------------------------------------------


def test_negative_control_no_eviction_loop_survives() -> None:
    """Same drive, but skip the eviction ⇒ the closed loop is NOT collected (AC #6).

    This is the test-of-the-test: it proves AC #5's pass is caused by the
    eviction, not by something else freeing the loop. Cleans up in ``finally``
    so the pinned loop does not leak into the rest of the suite.
    """
    loop = asyncio.new_event_loop()
    _drive_anyio_anchor(loop)
    if loop not in ll._run_vars:
        loop.close()
        _evict_anyio_run_vars(loop)
        pytest.skip("anyio _run_vars anchor not populated on this version")

    wr = weakref.ref(loop)
    try:
        loop.close()  # close WITHOUT calling _evict_anyio_run_vars
        del loop
        gc.collect()
        # The _root_task anchor in _run_vars still pins the closed loop.
        assert wr() is not None
    finally:
        # Now evict and collect so the loop does not leak into the suite.
        survivor = wr()
        if survivor is not None:
            _evict_anyio_run_vars(survivor)
            del survivor
        gc.collect()
    # With the eviction applied, the loop is now collectable.
    assert wr() is None


# ---------------------------------------------------------------------------
# AC #7 — positive control: a loop that never touches anyio is freed regardless
# ---------------------------------------------------------------------------


def test_positive_control_no_anyio_loop_freed() -> None:
    """A loop that never ran any anyio path is collected regardless of eviction (AC #7)."""
    loop = asyncio.new_event_loop()
    wr = weakref.ref(loop)

    loop.close()  # never drove to_thread / a task group / run()
    del loop
    gc.collect()

    # No anchor was ever set, so the loop is collectable with no eviction.
    assert wr() is None


# ---------------------------------------------------------------------------
# AC #2 — the helper is best-effort and non-raising under every failure mode
# ---------------------------------------------------------------------------


def test_helper_no_raise_on_loop_anyio_never_saw() -> None:
    """Popping a loop anyio never saw is a no-op and does not raise (AC #2)."""
    loop = asyncio.new_event_loop()
    try:
        # Missing-key pop: harmless no-op, no exception.
        _evict_anyio_run_vars(loop)
        # Idempotent: calling it twice on the same loop also does not raise.
        _evict_anyio_run_vars(loop)
    finally:
        loop.close()


def test_helper_no_raise_when_run_vars_broken() -> None:
    """A broken/absent ``_run_vars`` is swallowed by the ``except Exception`` (AC #2)."""
    loop = asyncio.new_event_loop()
    try:
        # Simulate a broken anyio internal: _run_vars rebound to None, so the
        # helper's `.pop(...)` raises AttributeError — which its try/except must
        # swallow (no exception escapes).
        with patch("anyio.lowlevel._run_vars", new=None):
            _evict_anyio_run_vars(loop)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# AC #3 — close() calls the eviction in finally, AFTER loop.close()
# ---------------------------------------------------------------------------


def test_close_calls_eviction_after_loop_closed(minimal_config: ReactAgentConfig) -> None:
    """``close()`` invokes ``_evict_anyio_run_vars(loop)`` once, after the loop closes (AC #3)."""
    import akgentic.llm.agent as agent_mod

    agent = ReactAgent(config=minimal_config)
    loop = agent._loop
    seen: list[tuple[Any, bool]] = []

    def spy(target_loop: asyncio.AbstractEventLoop) -> None:
        # Record which loop was passed and whether it was already closed.
        seen.append((target_loop, target_loop.is_closed()))

    with patch.object(agent_mod, "_evict_anyio_run_vars", side_effect=spy):
        agent.close()

    # Called exactly once, with the agent's own loop, after loop.close() ran.
    assert len(seen) == 1
    assert seen[0][0] is loop
    assert seen[0][1] is True


# ---------------------------------------------------------------------------
# AC #8 — close() stays idempotent and non-raising with the eviction added
# ---------------------------------------------------------------------------


def test_close_idempotent_and_non_raising_with_eviction(
    minimal_config: ReactAgentConfig,
) -> None:
    """A full ``close()`` does not raise; a second ``close()`` is a no-op (AC #8)."""
    agent = ReactAgent(config=minimal_config)

    agent.close()  # must not raise (drives teardown + eviction)
    assert agent._loop.is_closed()
    assert agent._closed is True

    # Second close short-circuits at the guard: no double-eviction, no raise.
    agent.close()
    assert agent._loop.is_closed()


def test_close_non_raising_when_anyio_absent(minimal_config: ReactAgentConfig) -> None:
    """``close()`` still closes the loop and does not raise when anyio is broken (AC #8).

    With the real (guarded) helper, a broken ``_run_vars`` is swallowed: the
    teardown completes, the loop closes, and ``close()`` never raises — the
    Story 11-1 idempotency/non-raising guarantee survives the step-5 addition.
    """
    agent = ReactAgent(config=minimal_config)
    with patch("anyio.lowlevel._run_vars", new=None):
        agent.close()  # helper's pop would raise on a None _run_vars -> swallowed
    assert agent._loop.is_closed()
    assert agent._closed is True
