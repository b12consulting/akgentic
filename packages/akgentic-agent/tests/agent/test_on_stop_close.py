"""Behavior tests for ``BaseAgent.on_stop()`` delegating teardown to ``close()``.

Covers Story 11-1 (Epic 11): ``on_stop()`` calls the ReactAgent's synchronous,
idempotent ``close()`` exactly once, always runs ``super().on_stop()`` afterwards
(so the core StopMessage telemetry fires), swallows + logs a raising ``close()``,
and no longer reads ``self._event_loop`` to drive teardown.

Test-boundary note (per story): the ordering invariant — ``close()`` runs strictly
before the base teardown — is a white-box property of ``on_stop()`` that cannot be
observed deterministically from outside a Pykka ``ActorSystem`` stop. These tests
call ``on_stop()`` directly on a bare ``BaseAgent`` (the established
``object.__new__`` pattern used by the other unit tests in this package). ``close()``
is synchronous, so no real ``asyncio`` loop is wired; ``_react_agent`` is a stub
with a ``MagicMock`` ``close`` and no real network egress occurs.
"""

from typing import Any
from unittest.mock import MagicMock, patch

from akgentic.agent.agent import BaseAgent
from akgentic.core import Akgent

# =============================================================================
# HELPERS
# =============================================================================


def _make_agent(close: Any) -> BaseAgent:
    """Bare ``BaseAgent`` (no Pykka actor system) wired for ``on_stop()`` tests.

    Sets only the attributes ``on_stop()`` (and the real ``super().on_stop()``)
    read: ``_react_agent`` (with the given synchronous ``close``), ``config.name``
    (for log formatting) and ``_orchestrator = None`` so the base teardown's
    orchestrator notify is a no-op. No ``_event_loop`` is wired — teardown no
    longer depends on the actor loop (AC #4/#6).
    """
    agent: BaseAgent = object.__new__(BaseAgent)

    react_agent = MagicMock()
    react_agent.close = close
    agent._react_agent = react_agent  # type: ignore[attr-defined]

    config = MagicMock()
    config.name = "@TestAgent"
    agent.config = config  # type: ignore[assignment]

    agent._orchestrator = None  # type: ignore[assignment]
    return agent


# =============================================================================
# AC #1 — FR1: close() is called exactly once on stop
# =============================================================================


def test_on_stop_calls_close_exactly_once() -> None:
    """FR1: stopping the agent invokes ``react_agent.close()`` exactly once."""
    close = MagicMock(name="close")
    agent = _make_agent(close)

    with patch.object(Akgent, "on_stop"):
        agent.on_stop()

    close.assert_called_once_with()


# =============================================================================
# AC #2 — FR1: super().on_stop() always runs, AFTER close()
# =============================================================================


def test_on_stop_calls_super_after_close() -> None:
    """FR1 ordering: ``close()`` runs strictly BEFORE ``super().on_stop()``."""
    order: list[str] = []

    close = MagicMock(name="close", side_effect=lambda: order.append("close"))
    agent = _make_agent(close)

    def fake_super_on_stop(_self: Akgent[Any, Any]) -> None:
        order.append("super")

    with patch.object(Akgent, "on_stop", fake_super_on_stop):
        agent.on_stop()

    assert order == ["close", "super"]


# =============================================================================
# AC #3 — FR1: a raising close() is swallowed + logged; super() still runs
# =============================================================================


def test_on_stop_swallows_close_exception_and_calls_super() -> None:
    """FR1: when ``close()`` raises, ``on_stop()`` does not propagate the error
    and still completes ``super().on_stop()``."""
    close = MagicMock(name="close", side_effect=RuntimeError("pool release failed"))
    agent = _make_agent(close)

    with patch.object(Akgent, "on_stop") as super_on_stop:
        # Must not raise despite close() blowing up.
        agent.on_stop()

    super_on_stop.assert_called_once()


def test_on_stop_logs_when_close_raises(caplog: Any) -> None:
    """FR1: the swallowed ``close()`` failure is logged (not silent)."""
    close = MagicMock(name="close", side_effect=RuntimeError("pool release failed"))
    agent = _make_agent(close)

    with patch.object(Akgent, "on_stop"):
        with caplog.at_level("WARNING", logger="akgentic.agent.agent"):
            agent.on_stop()

    assert any("close" in record.getMessage() for record in caplog.records), (
        "expected a log record mentioning the failed close()"
    )


# =============================================================================
# AC #4/#6 — FR1/FR3: teardown does not depend on self._event_loop
# =============================================================================


def test_on_stop_calls_close_without_event_loop() -> None:
    """FR1/FR3: ``close()`` runs even when no ``_event_loop`` attribute is wired.

    The old None/closed-loop skip is gone — teardown delegates to ``close()``
    unconditionally and never reads ``self._event_loop``.
    """
    close = MagicMock(name="close")
    agent = _make_agent(close)  # no _event_loop set on the bare agent

    assert not hasattr(agent, "_event_loop")

    with patch.object(Akgent, "on_stop") as super_on_stop:
        agent.on_stop()

    close.assert_called_once_with()
    super_on_stop.assert_called_once()
