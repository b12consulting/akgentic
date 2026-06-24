"""Weak-observer regression tests for ``TeamTool`` (Epic 22 / ADR-030).

Story 22.2: every observer-capturing closure factory captures the tool's weak
accessor (``self._observer_or_none``) and derefs at call time, so a ``TeamTool``,
its tool/command/prompt closures, and the agent's command registry can never pin
a stopped owning agent. Once the agent is collected, hire/fire callables raise
``RetriableError`` and the roster prompt returns the benign fallback.
"""

from __future__ import annotations

import gc
import uuid
import weakref
from unittest.mock import Mock

import pytest
from akgentic.core import ActorAddressProxy
from akgentic.core.orchestrator import Orchestrator

from akgentic.tool.errors import RetriableError
from akgentic.tool.event import TeamManagementToolObserver
from akgentic.tool.team import (
    FireTeamMember,
    GetTeamRoster,
    HireTeamMember,
    TeamTool,
)


def _address(name: str, role: str = "Agent") -> ActorAddressProxy:
    """Create a mock ActorAddress for testing."""
    return ActorAddressProxy(
        {
            "__actor_address__": True,
            "__actor_type__": "test.Agent",
            "agent_id": str(uuid.uuid4()),
            "name": name,
            "role": role,
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": True,
        }
    )


def _make_observer() -> Mock:
    """Weak-referenceable observer stand-in with a live orchestrator proxy.

    ``Mock`` (unlike a bare ``object()``) is weak-referenceable, matching the
    idiom the existing ``TeamTool`` tests rely on.
    """
    observer = Mock(spec=TeamManagementToolObserver)
    observer.orchestrator = _address("@Orchestrator", "Orchestrator")
    observer.myAddress = _address("@Manager", "Manager")

    # Detached orchestrator mock: NOT a child of ``observer`` (a child mock's
    # parent chain would strongly pin the observer through
    # ``tool._orchestrator_proxy`` and defeat the gc reclamation assertions).
    orchestrator = Mock(spec=Orchestrator)
    orchestrator.get_team.return_value = [_address("@Manager", "Manager")]
    observer.proxy_ask = Mock(return_value=orchestrator)
    return observer


# ── In-life parity (AC4) ─────────────────────────────────────────────────────


def test_in_life_roster_unchanged() -> None:
    # While the agent is alive, the roster prompt produces its usual output.
    observer = _make_observer()
    tool = TeamTool()
    tool.observer(observer)

    roster_prompt = tool.get_system_prompts()[0]
    result = roster_prompt()
    assert "Here is the team member list by name (and role):" in result
    assert "@Manager (role: Manager)" in result
    assert "[you]" in result


def test_in_life_hire_unchanged() -> None:
    # While the agent is alive, hire still calls createActor + on_hire.
    observer = _make_observer()
    orchestrator = observer.proxy_ask.return_value

    agent_card = Mock()
    agent_card.role = "Developer"
    agent_card.get_agent_class.return_value = Mock
    agent_card.get_config_copy.return_value = Mock()
    orchestrator.get_team.return_value = []
    orchestrator.get_agent_catalog.return_value = [agent_card]
    observer.createActor.return_value = _address("@Developer123", "Developer")

    tool = TeamTool()
    tool.observer(observer)
    hire_members = tool.get_tools()[0]

    result = hire_members(["Developer"])
    assert "Members hired:" in result
    observer.createActor.assert_called_once()
    observer.on_hire.assert_called_once()


# ── Post-stop reclamation + clean failure (AC5) ──────────────────────────────


def test_closures_do_not_pin_stopped_agent() -> None:
    # AC5 (a)+(b): after dropping the only strong ref and gc, the observer is
    # collected even though the built closures are still held.
    observer = _make_observer()
    tool = TeamTool()
    tool.observer(observer)

    ref = weakref.ref(observer)
    # Build (and hold) tool/command/prompt closures — they must NOT pin the agent.
    held = [
        *tool.get_tools(),
        *tool.get_system_prompts(),
        *tool.get_commands().values(),
    ]

    del observer
    gc.collect()

    assert tool._observer_or_none() is None  # tool does not keep the observer alive
    assert ref() is None  # closures do not pin the agent
    assert held  # closures are still referenced here, yet the agent was reclaimed


def test_hire_callables_raise_after_stop() -> None:
    # AC5 (c): hire tool + command raise RetriableError (not AttributeError /
    # ToolObserverGone) once the agent is gone.
    observer = _make_observer()
    tool = TeamTool()
    tool.observer(observer)

    hire_members = tool.get_tools()[0]
    hire_member = tool.get_commands()[HireTeamMember]

    del observer
    gc.collect()

    with pytest.raises(RetriableError, match="cannot hire"):
        hire_members(["Developer"])
    with pytest.raises(RetriableError, match="cannot hire"):
        hire_member("Developer")


def test_fire_callables_raise_after_stop() -> None:
    # AC5 (c): fire tool + command raise RetriableError once the agent is gone.
    observer = _make_observer()
    tool = TeamTool()
    tool.observer(observer)

    fire_members = tool.get_tools()[1]
    fire_member = tool.get_commands()[FireTeamMember]

    del observer
    gc.collect()

    with pytest.raises(RetriableError, match="cannot fire"):
        fire_members(["@Developer123"])
    with pytest.raises(RetriableError, match="cannot fire"):
        fire_member("@Developer123")


def test_roster_prompt_returns_fallback_after_stop() -> None:
    # AC5 (d): roster prompt returns the benign fallback (a string, no raise)
    # once the agent is gone — observer.myAddress.name must never run on None.
    observer = _make_observer()
    tool = TeamTool()
    tool.observer(observer)

    roster_prompt = tool.get_commands()[GetTeamRoster]

    del observer
    gc.collect()

    result = roster_prompt()
    assert isinstance(result, str)
    assert result == ""
