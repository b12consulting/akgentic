"""Cross-tool gc-reclamation regression tests (Epic 22 / ADR-030, Story 22.3).

A tool's command registry — built via the public ``ToolFactory`` API — must never
pin a weak-referenced owning agent. After the only strong reference to the observer
is dropped and ``gc.collect()`` runs, the agent is reclaimed even while the registry
and its ``_CommandEntry`` closures are still held. This proves FR6: no tool, its
closures, or the agent's command registry keeps a stopped ``BaseAgent`` alive.

Also covers ``PlanningTool._update_planning_factory`` deref-at-call behaviour:
the alive path performs the existing ``planning_proxy.update_planning(...)`` call;
the gone path raises ``RetriableError`` (not ``AttributeError`` / ``ToolObserverGone``).
"""

from __future__ import annotations

import gc
import uuid
import weakref
from unittest.mock import Mock

import pytest
from akgentic.core import ActorAddressProxy
from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import ToolFactory
from akgentic.tool.errors import RetriableError
from akgentic.tool.event import TeamManagementToolObserver
from akgentic.tool.planning.planning import PlanningTool, UpdatePlanning
from akgentic.tool.team import TeamTool


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
    """Weak-referenceable observer with a DETACHED orchestrator proxy.

    ``Mock`` (unlike a bare ``object()``) is weak-referenceable. The orchestrator
    proxy is a free-standing ``Mock`` (NOT a child of ``observer``): a child mock's
    parent chain would back-edge to the observer and, pinned by a tool's strong
    ``_orchestrator_proxy`` / ``_planning_proxy``, defeat gc reclamation.
    ``proxy_ask`` returns that detached orchestrator for every call, so both
    ``PlanningTool`` and ``TeamTool`` wire against it without pinning the observer.
    """
    observer = Mock(spec=TeamManagementToolObserver)
    observer.orchestrator = _address("@Orchestrator", "Orchestrator")
    observer.myAddress = _address("@Manager", "Manager")

    orchestrator = Mock(spec=Orchestrator)
    orchestrator.get_team.return_value = [_address("@Manager", "Manager")]
    observer.proxy_ask = Mock(return_value=orchestrator)
    return observer


def _build_registry(observer: Mock) -> object:
    """Wire TeamTool + PlanningTool through the public ToolFactory and build the registry.

    ``PlanningTool(vector_store=False)`` runs in degraded mode so it declares no
    ``VectorStoreTool`` dependency — the command wiring under test (and the weak
    observer edge) is independent of the VectorStoreActor lookup.
    """
    factory = ToolFactory([TeamTool(), PlanningTool(vector_store=False)], observer=observer)
    return factory.get_command_registry()


# ── Cross-tool command-registry gc reclamation (AC6) ─────────────────────────


def test_command_registry_does_not_pin_stopped_agent() -> None:
    # AC6: a command registry built from TeamTool + PlanningTool must NOT keep the
    # weak-referenced agent alive once the only strong ref is dropped + gc runs.
    observer = _make_observer()
    registry = _build_registry(observer)

    agent_weakref = weakref.ref(observer)
    del observer
    gc.collect()

    assert agent_weakref() is None  # registry + closures do not pin the agent
    assert registry is not None  # registry is still held, yet the agent was reclaimed


# ── PlanningTool._update_planning_factory deref-at-call (AC3, AC7) ────────────


def test_update_planning_in_life_calls_proxy() -> None:
    # AC7 alive path: while the agent is alive, update_planning performs the same
    # planning_proxy.update_planning(update, observer.myAddress) call as before.
    observer = _make_observer()
    tool = PlanningTool(vector_store=False)
    tool.observer(observer)

    # Replace the wired PlanActor proxy with a dedicated mock to assert the call.
    planning_proxy = Mock()
    planning_proxy.update_planning.return_value = "updated"
    tool._planning_proxy = planning_proxy
    update_planning = tool._update_planning_factory(UpdatePlanning())

    update = Mock()
    result = update_planning(update)

    assert result == "updated"
    planning_proxy.update_planning.assert_called_once_with(update, observer.myAddress)


def test_update_planning_raises_after_stop() -> None:
    # AC3 gone path: once the agent is collected, update_planning raises
    # RetriableError (not AttributeError / ToolObserverGone).
    observer = _make_observer()
    tool = PlanningTool(vector_store=False)
    tool.observer(observer)
    update_planning = tool._update_planning_factory(UpdatePlanning())

    del observer
    gc.collect()

    assert tool._observer_or_none() is None
    with pytest.raises(RetriableError, match="shutting down"):
        update_planning(Mock())
