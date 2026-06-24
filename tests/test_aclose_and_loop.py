"""Behavior tests for ``ReactAgent`` loop ownership, ``aclose()`` and ``close()``.

Covers Story 11-1 (Epic 11, ADR-009): ``ReactAgent`` creates and owns its own
asyncio loop in ``__init__``, ``run_sync`` always runs on that loop (no
``asyncio.run()`` fallback) and raises once the agent is closed, ``aclose()``
stays async and resource-only (httpx, double-close guarded), and the new
synchronous idempotent ``close()`` drives ``aclose()`` then drains and closes
the loop. The deprecated ``event_loop=`` argument is accepted and ignored.

Also retains Story 10-1 (Epic 10, ADR-008) coverage of the instance-held
``httpx.AsyncClient`` handle and ``aclose()`` releasing that pool.

All tests are zero-egress: ``aclose()`` / ``close()`` are exercised on the real
client the agent builds (no request is ever sent), and the ``run_sync``
loop-selection tests patch ``ReactAgent.run`` with a stub coroutine so no model
is contacted.
"""

import asyncio
import warnings
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from akgentic.llm import ModelConfig, ReactAgent, ReactAgentConfig


@pytest.fixture
def minimal_config() -> ReactAgentConfig:
    """Minimal config; the OpenAI provider builds with no key (no egress here)."""
    return ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))


# ---------------------------------------------------------------------------
# AC #4 — instance handle wires the pool
# ---------------------------------------------------------------------------


def test_http_client_held_on_instance(minimal_config: ReactAgentConfig) -> None:
    """The agent exposes the ``httpx.AsyncClient`` it built as ``_http_client``."""
    agent = ReactAgent(config=minimal_config)
    assert isinstance(agent._http_client, httpx.AsyncClient)
    assert not agent._http_client.is_closed


def test_model_uses_the_held_client(minimal_config: ReactAgentConfig) -> None:
    """The client passed to ``create_model`` is the same one ``aclose()`` closes."""
    import akgentic.llm.agent as agent_mod

    captured: dict[str, Any] = {}
    real_create_model = agent_mod.create_model

    def spy_create_model(model_cfg: Any, http_client: httpx.AsyncClient) -> Any:
        captured["http_client"] = http_client
        return real_create_model(model_cfg, http_client)

    with patch.object(agent_mod, "create_model", side_effect=spy_create_model):
        agent = ReactAgent(config=minimal_config)

    # The client handed to create_model() is the very instance reachable for aclose().
    assert captured["http_client"] is agent._http_client


# ---------------------------------------------------------------------------
# AC #1 — aclose() releases the client and is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_client(minimal_config: ReactAgentConfig) -> None:
    """After ``aclose()`` the underlying client is closed."""
    agent = ReactAgent(config=minimal_config)
    assert not agent._http_client.is_closed

    await agent.aclose()

    assert agent._http_client.is_closed


@pytest.mark.asyncio
async def test_aclose_is_idempotent(minimal_config: ReactAgentConfig) -> None:
    """A second ``aclose()`` is harmless (does not raise)."""
    agent = ReactAgent(config=minimal_config)

    await agent.aclose()
    # Second close must not raise — teardown may run more than once.
    await agent.aclose()

    assert agent._http_client.is_closed


@pytest.mark.asyncio
async def test_aclose_targets_only_owned_client(minimal_config: ReactAgentConfig) -> None:
    """``aclose()`` closes only the client the agent created (no internals reach)."""
    agent = ReactAgent(config=minimal_config)
    owned = agent._http_client

    await agent.aclose()

    # The closed client is exactly the agent's own handle.
    assert agent._http_client is owned
    assert owned.is_closed


# ---------------------------------------------------------------------------
# AC #1 — __init__ creates and owns an open, non-running, current loop
# ---------------------------------------------------------------------------


def test_init_owns_open_nonrunning_loop(minimal_config: ReactAgentConfig) -> None:
    """``__init__`` exposes its own loop, open and not running after construction."""
    agent = ReactAgent(config=minimal_config)
    try:
        assert isinstance(agent._loop, asyncio.AbstractEventLoop)
        assert not agent._loop.is_closed()
        assert not agent._loop.is_running()
        # The agent's loop is current on the constructing thread.
        assert asyncio.get_event_loop() is agent._loop
    finally:
        agent.close()


def test_init_does_not_mark_closed(minimal_config: ReactAgentConfig) -> None:
    """A freshly constructed agent is not closed."""
    agent = ReactAgent(config=minimal_config)
    try:
        assert agent._closed is False
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# AC #2 — run_sync always runs on the agent's own loop (no asyncio.run fallback)
# ---------------------------------------------------------------------------


def test_run_sync_runs_on_owned_loop(minimal_config: ReactAgentConfig) -> None:
    """``run_sync`` runs the coroutine on ``self._loop`` and leaves it usable."""
    used_loop: list[asyncio.AbstractEventLoop] = []

    async def stub_run(*_: Any, **__: Any) -> str:
        used_loop.append(asyncio.get_running_loop())
        return "ran-on-owned-loop"

    agent = ReactAgent(config=minimal_config)
    try:
        with patch.object(ReactAgent, "run", new=stub_run):
            first = agent.run_sync("q")
            second = agent.run_sync("q")

        assert first == "ran-on-owned-loop"
        assert second == "ran-on-owned-loop"
        # Both calls executed on the agent's own loop...
        assert used_loop == [agent._loop, agent._loop]
        # ...and the loop is still usable across repeated calls (AC #2).
        assert not agent._loop.is_closed()
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# AC #3 — run_sync after close() raises
# ---------------------------------------------------------------------------


def test_run_sync_after_close_raises(minimal_config: ReactAgentConfig) -> None:
    """Calling ``run_sync`` on a closed agent raises (not swallowed)."""
    agent = ReactAgent(config=minimal_config)
    agent.close()

    with pytest.raises(RuntimeError):
        agent.run_sync("q")


# ---------------------------------------------------------------------------
# AC #5 / #6 — close() closes client + loop, ordering, idempotent, no warnings
# ---------------------------------------------------------------------------


def test_close_closes_client_and_loop(minimal_config: ReactAgentConfig) -> None:
    """``close()`` closes both the httpx client and the owned loop (AC #5, #6)."""
    agent = ReactAgent(config=minimal_config)
    assert not agent._http_client.is_closed
    assert not agent._loop.is_closed()

    agent.close()

    assert agent._http_client.is_closed
    assert agent._loop.is_closed()
    assert agent._closed is True


def test_close_is_idempotent(minimal_config: ReactAgentConfig) -> None:
    """A second ``close()`` is a harmless no-op (does not raise)."""
    agent = ReactAgent(config=minimal_config)
    agent.close()
    # Second close must not raise and must not re-run teardown.
    agent.close()

    assert agent._http_client.is_closed
    assert agent._loop.is_closed()


def test_close_ordering_no_pending_task_warning(minimal_config: ReactAgentConfig) -> None:
    """``close()`` tears down cleanly: client closed and no leaked-pending warning."""
    agent = ReactAgent(config=minimal_config)
    client = agent._http_client

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent.close()

    # Async resource teardown completed on a still-open loop before loop.close():
    # the client is closed AND the loop is closed.
    assert client.is_closed
    assert agent._loop.is_closed()
    # No "Task was destroyed but it is pending" / pending-task warning surfaced.
    messages = [str(w.message) for w in caught]
    assert not any("pending" in m.lower() for m in messages), messages


def test_close_logs_teardown_failure_and_still_closes_loop(
    minimal_config: ReactAgentConfig,
) -> None:
    """A failing ``aclose()`` is logged (not raised) and the loop still closes (AC #5)."""
    import akgentic.llm.agent as agent_mod

    async def boom(_self: ReactAgent) -> None:
        raise RuntimeError("teardown boom")

    agent = ReactAgent(config=minimal_config)
    with (
        patch.object(ReactAgent, "aclose", new=boom),
        patch.object(agent_mod, "logger") as log,
    ):
        # close() must NOT propagate the teardown failure.
        agent.close()

    # Failure was logged with exc_info, never raised, and the loop is still closed.
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs.get("exc_info") is True
    assert agent._loop.is_closed()


def test_close_cancels_pending_tasks(minimal_config: ReactAgentConfig) -> None:
    """``close()`` cancels stragglers left on the loop before closing it (AC #6)."""
    agent = ReactAgent(config=minimal_config)

    async def forever() -> None:
        await asyncio.sleep(3600)

    # Schedule a long-lived task on the agent's loop without running the loop,
    # so it is still pending when close() runs the cancel step.
    pending = agent._loop.create_task(forever())

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent.close()

    assert pending.cancelled()
    assert agent._loop.is_closed()
    # The straggler was drained, not destroyed-while-pending.
    messages = [str(w.message) for w in caught]
    assert not any("pending" in m.lower() for m in messages), messages


# ---------------------------------------------------------------------------
# AC #7 — event_loop= is accepted and ignored (deprecated compat shim)
# ---------------------------------------------------------------------------


def test_event_loop_arg_is_accepted_and_ignored(minimal_config: ReactAgentConfig) -> None:
    """Passing ``event_loop=`` does not raise and is not adopted as ``self._loop``."""
    passed_loop = asyncio.new_event_loop()
    used_loop: list[asyncio.AbstractEventLoop] = []

    async def stub_run(*_: Any, **__: Any) -> str:
        used_loop.append(asyncio.get_running_loop())
        return "ran-on-owned-loop"

    try:
        # Construction does not raise despite the deprecated argument.
        agent = ReactAgent(config=minimal_config, event_loop=passed_loop)
        try:
            # The passed loop is NOT adopted as the agent's own loop.
            assert agent._loop is not passed_loop
            # run_sync still runs on the agent's own loop, never the passed one.
            with patch.object(ReactAgent, "run", new=stub_run):
                agent.run_sync("q")
            assert used_loop == [agent._loop]
            # The passed loop was never used (still open, untouched by the agent).
            assert not passed_loop.is_closed()
        finally:
            agent.close()
    finally:
        passed_loop.close()
