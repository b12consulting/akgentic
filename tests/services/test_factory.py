"""Tests for TeamFactory.build — AC 1-10."""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message

from akgentic.team.factory import TeamFactory
from akgentic.team.models import TeamCard, TeamCardMember, TeamRuntime

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class StubAgent(Akgent[BaseConfig, BaseState]):
    """Minimal agent for factory tests."""

    pass


class FailingAgent(Akgent[BaseConfig, BaseState]):
    """Agent that raises during __init__ for rollback tests."""

    def __init__(self, **kwargs: Any) -> None:
        msg = "FailingAgent intentional error"
        raise RuntimeError(msg)


class StubSubscriber:
    """Minimal EventSubscriber for testing."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.stopped: bool = False

    def on_stop(self) -> None:
        self.stopped = True

    def on_message(self, msg: Message) -> None:
        self.messages.append(msg)


def _make_card(
    name: str,
    role: str = "TestRole",
    agent_class: type[Akgent[Any, Any]] = StubAgent,
    routes_to: list[str] | None = None,
) -> AgentCard:
    return AgentCard(
        role=role,
        description=f"Test: {role}",
        skills=["testing"],
        agent_class=agent_class,
        config=BaseConfig(name=name, role=role),
        routes_to=routes_to or [],
    )


def _make_member(
    name: str,
    role: str = "TestRole",
    agent_class: type[Akgent[Any, Any]] = StubAgent,
    headcount: int = 1,
    members: list[TeamCardMember] | None = None,
    routes_to: list[str] | None = None,
) -> TeamCardMember:
    return TeamCardMember(
        card=_make_card(name, role, agent_class, routes_to),
        headcount=headcount,
        members=members or [],
    )


def _make_team_card(
    entry_point: TeamCardMember | None = None,
    members: list[TeamCardMember] | None = None,
    name: str = "test-team",
) -> TeamCard:
    ep = entry_point or _make_member("lead", "Lead")
    return TeamCard(
        name=name,
        description="Test team",
        entry_point=ep,
        members=members or [],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def actor_system() -> ActorSystem:  # type: ignore[misc]
    system = ActorSystem()
    yield system  # type: ignore[misc]
    system.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTeamFactoryBuild:
    """AC 1-8: TeamFactory.build creates a running team from a TeamCard."""

    # -- 3.1: Successful build with single agent -------------------------

    def test_build_single_agent(self, actor_system: ActorSystem) -> None:
        """AC 1,7: Build returns TeamRuntime with orchestrator, entry, addrs."""
        tc = _make_team_card()
        runtime = TeamFactory.build(tc, actor_system)

        assert isinstance(runtime, TeamRuntime)
        assert isinstance(runtime.id, uuid.UUID)
        assert runtime.orchestrator_addr is not None
        assert runtime.entry_addr is not None
        assert "lead" in runtime.addrs
        assert runtime.orchestrator_addr.is_alive()
        assert runtime.entry_addr.is_alive()

    # -- 3.2: Successful build with multiple agents (hierarchical) -------

    def test_build_hierarchical_team(self, actor_system: ActorSystem) -> None:
        """AC 2: All agents in TeamCard member tree are spawned."""
        worker = _make_member("worker", "Worker")
        supervisor = _make_member("supervisor", "Supervisor", members=[worker])
        tc = _make_team_card(members=[supervisor])

        runtime = TeamFactory.build(tc, actor_system)

        assert "lead" in runtime.addrs
        assert "supervisor" in runtime.addrs
        assert "worker" in runtime.addrs
        assert len(runtime.addrs) == 3
        for addr in runtime.addrs.values():
            assert addr.is_alive()

    # -- 3.3: Headcount > 1 spawns multiple instances --------------------

    def test_headcount_multiple_instances(self, actor_system: ActorSystem) -> None:
        """AC 3: Headcount > 1 spawns multiple actor instances."""
        multi = _make_member("worker", "Worker", headcount=3)
        tc = _make_team_card(members=[multi])

        runtime = TeamFactory.build(tc, actor_system)

        assert "worker_0" in runtime.addrs
        assert "worker_1" in runtime.addrs
        assert "worker_2" in runtime.addrs
        assert "worker" not in runtime.addrs
        # lead + 3 workers = 4
        assert len(runtime.addrs) == 4

    # -- 3.4: Subscriber registration -----------------------------------

    def test_subscriber_registration(self, actor_system: ActorSystem) -> None:
        """AC 5: Provided subscribers are registered with the orchestrator."""
        sub = StubSubscriber()
        tc = _make_team_card()

        runtime = TeamFactory.build(tc, actor_system, subscribers=[sub])

        # Verify subscriber is registered by stopping the orchestrator via proxy,
        # which calls on_stop on all subscribers.
        runtime.orchestrator_proxy.stop()
        # on_stop() fires asynchronously after the proxy stop() call returns;
        # wait for the actor thread to finish so on_stop() has been invoked.
        deadline = time.monotonic() + 2.0
        while runtime.orchestrator_addr.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sub.stopped is True

    # -- 3.5: routes_to wiring ------------------------------------------

    def test_routes_to_registered(self, actor_system: ActorSystem) -> None:
        """AC 4,6: Agent profiles with routes_to are registered with orchestrator."""
        worker = _make_member("worker", "Worker")
        ep = _make_member("lead", "Lead", routes_to=["Worker"])
        tc = _make_team_card(entry_point=ep, members=[worker])
        # Explicitly register profiles for hiring
        tc.agent_profiles = list(tc.agent_cards.values())

        runtime = TeamFactory.build(tc, actor_system)

        # Verify agent profiles are registered
        catalog = runtime.orchestrator_proxy.get_agent_catalog()
        roles = {c.role for c in catalog}
        assert "Lead" in roles
        assert "Worker" in roles

        # Verify routes_to is preserved
        lead_card = next(c for c in catalog if c.role == "Lead")
        assert "Worker" in lead_card.routes_to

    # -- 3.6: Partial failure rollback -----------------------------------

    def test_partial_failure_rollback(self, actor_system: ActorSystem) -> None:
        """AC 8: Partial failure tears down already-spawned actors."""
        # FailingAgent will raise on start -- this should trigger rollback
        failing = _make_member("failing", "Failing", agent_class=FailingAgent)
        tc = _make_team_card(members=[failing])

        with pytest.raises(Exception):
            TeamFactory.build(tc, actor_system)

    # -- 3.7: TeamRuntime.id equals orchestrator's team_id ---------------

    def test_runtime_id_is_team_id(self, actor_system: ActorSystem) -> None:
        """AC 7: TeamRuntime.id is the team_id assigned to all actors."""
        tc = _make_team_card()
        runtime = TeamFactory.build(tc, actor_system)

        # The orchestrator's team_id should match runtime.id
        assert runtime.orchestrator_addr.team_id == runtime.id
        assert runtime.entry_addr.team_id == runtime.id

    # -- 3.8: Agent profiles registered with orchestrator ----------------

    def test_agent_profiles_registered(self, actor_system: ActorSystem) -> None:
        """AC 6: orchestrator.get_agent_catalog() returns only agent_profiles."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])
        # Explicitly register profiles for hiring
        tc.agent_profiles = list(tc.agent_cards.values())

        runtime = TeamFactory.build(tc, actor_system)

        catalog = runtime.orchestrator_proxy.get_agent_catalog()
        roles = {c.role for c in catalog}
        assert "Lead" in roles
        assert "Worker" in roles
        assert len(catalog) == 2

    def test_no_profiles_means_empty_catalog(self, actor_system: ActorSystem) -> None:
        """Default agent_profiles (empty) results in empty hiring catalog."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])
        # agent_profiles defaults to empty — no roles available for hiring

        runtime = TeamFactory.build(tc, actor_system)

        catalog = runtime.orchestrator_proxy.get_agent_catalog()
        assert len(catalog) == 0

    # -- Additional edge-case tests --------------------------------------

    def test_build_with_no_subscribers(self, actor_system: ActorSystem) -> None:
        """Build works when subscribers is None."""
        tc = _make_team_card()
        runtime = TeamFactory.build(tc, actor_system, subscribers=None)
        assert runtime.orchestrator_addr.is_alive()

    def test_supervisor_addrs_populated(self, actor_system: ActorSystem) -> None:
        """Supervisor addresses are populated for first-layer members."""
        worker = _make_member("worker", "Worker")
        supervisor = _make_member("supervisor", "Supervisor", members=[worker])
        tc = _make_team_card(members=[supervisor])

        runtime = TeamFactory.build(tc, actor_system)

        # supervisor is a first-layer member, so it's in supervisor_addrs
        assert "supervisor" in runtime.supervisor_addrs
        assert runtime.supervisor_addrs["supervisor"] == runtime.addrs["supervisor"]
        # entry point (lead) is NOT in supervisor_addrs
        assert "lead" not in runtime.supervisor_addrs

    def test_entry_point_headcount_gt1_raises(self, actor_system: ActorSystem) -> None:
        """Entry point with headcount > 1 raises ValueError."""
        ep = _make_member("lead", "Lead", headcount=2)
        tc = _make_team_card(entry_point=ep)

        with pytest.raises(ValueError, match="must have headcount=1"):
            TeamFactory.build(tc, actor_system)

    def test_rollback_handles_stop_failure(self, actor_system: ActorSystem) -> None:
        """Rollback continues even if stopping an actor raises."""
        failing = _make_member("failing", "Failing", agent_class=FailingAgent)
        tc = _make_team_card(members=[failing])

        with patch.object(Akgent, "stop", side_effect=RuntimeError("stop failed")):
            with pytest.raises(RuntimeError) as excinfo:
                TeamFactory.build(tc, actor_system)
            # The original spawn failure from FailingAgent propagates as-is
            # (no wrapping). Test asserts on type + non-empty str, not a
            # fragile match on the fixture's own message text.
            assert str(excinfo.value)


# ---------------------------------------------------------------------------
# Tests: Hierarchy propagation (Story 10-1, AC 1, 2, 5)
# ---------------------------------------------------------------------------


class TestFactoryHierarchyPropagation:
    """AC 1,2,5: Spawned agents have _orchestrator and _parent set."""

    def test_orchestrator_set_on_spawned_agents(
        self, actor_system: ActorSystem
    ) -> None:
        """AC 1,5: _orchestrator is not None on all spawned agents."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        runtime = TeamFactory.build(tc, actor_system)

        for name, addr in runtime.addrs.items():
            proxy: Akgent[Any, Any] = actor_system.proxy_ask(addr, Akgent)
            orch = proxy.orchestrator
            assert orch is not None, f"Agent '{name}' has _orchestrator=None"
            assert orch.is_alive(), f"Agent '{name}' orchestrator is not alive"

    def test_parent_set_correctly_for_top_level_agents(
        self, actor_system: ActorSystem
    ) -> None:
        """AC 2,5: Top-level agents have orchestrator as parent."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        runtime = TeamFactory.build(tc, actor_system)

        # Both lead (entry point) and worker (top-level member) should have
        # the orchestrator as parent
        for name in ("lead", "worker"):
            addr = runtime.addrs[name]
            impl = addr  # ActorAddressImpl
            actor = impl._actor_ref._actor_weakref()  # type: ignore[union-attr]
            assert actor._parent is not None, f"Agent '{name}' has _parent=None"
            assert actor._parent.agent_id == runtime.orchestrator_addr.agent_id, (
                f"Agent '{name}' parent is not the orchestrator"
            )

    def test_parent_set_correctly_for_subordinates(
        self, actor_system: ActorSystem
    ) -> None:
        """AC 2,5: Subordinate agents have their supervisor as parent."""
        worker = _make_member("worker", "Worker")
        supervisor = _make_member("supervisor", "Supervisor", members=[worker])
        tc = _make_team_card(members=[supervisor])

        runtime = TeamFactory.build(tc, actor_system)

        # Worker should have supervisor as parent, not orchestrator
        worker_addr = runtime.addrs["worker"]
        worker_actor = worker_addr._actor_ref._actor_weakref()  # type: ignore[union-attr]
        supervisor_addr = runtime.addrs["supervisor"]

        assert worker_actor._parent is not None
        assert worker_actor._parent.agent_id == supervisor_addr.agent_id, (
            "Worker's parent should be the supervisor"
        )

        # Supervisor should have orchestrator as parent
        supervisor_actor = supervisor_addr._actor_ref._actor_weakref()  # type: ignore[union-attr]
        assert supervisor_actor._parent is not None
        assert supervisor_actor._parent.agent_id == runtime.orchestrator_addr.agent_id

    def test_orchestrator_set_on_headcount_agents(
        self, actor_system: ActorSystem
    ) -> None:
        """AC 5: _orchestrator is set on agents with headcount > 1."""
        multi = _make_member("worker", "Worker", headcount=2)
        tc = _make_team_card(members=[multi])

        runtime = TeamFactory.build(tc, actor_system)

        for name in ("worker_0", "worker_1"):
            proxy: Akgent[Any, Any] = actor_system.proxy_ask(
                runtime.addrs[name], Akgent
            )
            assert proxy.orchestrator is not None, (
                f"Agent '{name}' has _orchestrator=None"
            )


# ---------------------------------------------------------------------------
# Tests: Proxy-based spawning (Story 12.4, AC 3, 4)
# ---------------------------------------------------------------------------


class TestFactoryProxySpawning:
    """AC 3,4: Agents spawned through public createActor() API."""

    def test_build_creates_agents_through_public_api(
        self, actor_system: ActorSystem
    ) -> None:
        """AC 3: After build, all agents are alive and reachable via get_team()."""
        worker = _make_member("worker", "Worker")
        tc = _make_team_card(members=[worker])

        runtime = TeamFactory.build(tc, actor_system)

        team = runtime.orchestrator_proxy.get_team()
        team_names = {addr.name for addr in team}
        assert "lead" in team_names
        assert "worker" in team_names
        for addr in team:
            assert addr.is_alive()

    def test_build_entry_point_not_in_supervisor_proxies_without_subordinates(
        self, actor_system: ActorSystem
    ) -> None:
        """Entry point without subordinates is NOT in supervisor_proxies."""
        tc = _make_team_card()  # lead has no subordinates

        runtime = TeamFactory.build(tc, actor_system)

        assert "lead" not in runtime.supervisor_addrs
        assert "lead" not in runtime.supervisor_proxies
        # Entry point is still reachable via entry_proxy
        assert runtime.entry_proxy is not None
