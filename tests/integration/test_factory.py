"""Integration tests for TeamFactory: verify built teams are functional.

Tests use real Akgent subclasses, real orchestrators, and real message flow.
"""

from __future__ import annotations

from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.orchestrator import Orchestrator

from akgentic.team.factory import TeamFactory
from akgentic.team.models import TeamCard, TeamRuntime
from tests.integration.conftest import (
    get_actor_from_addr,
    wait_for_agent_state,
)


def _build_and_track(
    team_card: TeamCard,
    actor_system: ActorSystem,
) -> TeamRuntime:
    """Build a team and return the runtime."""
    return TeamFactory.build(team_card, actor_system)


class TestFactoryIntegration:
    """TeamFactory integration tests -- verify built teams are functional."""

    def test_built_team_agents_are_discoverable(
        self,
        routing_team_card: TeamCard,
        actor_system: ActorSystem,
    ) -> None:
        """Every agent in the TeamCard is findable via orchestrator.get_team_member()."""
        runtime = _build_and_track(routing_team_card, actor_system)

        orchestrator_proxy: Orchestrator = actor_system.proxy_ask(
            runtime.orchestrator_addr, Orchestrator
        )

        for name in routing_team_card.agent_cards:
            addr = orchestrator_proxy.get_team_member(name)
            assert addr is not None, f"Agent '{name}' not found via orchestrator"
            assert addr.is_alive(), f"Agent '{name}' is not alive"

    def test_built_team_message_reaches_target(
        self,
        routing_team_card: TeamCard,
        actor_system: ActorSystem,
    ) -> None:
        """A message sent via runtime.send() reaches the worker agent."""
        runtime = _build_and_track(routing_team_card, actor_system)

        runtime.send("hello")

        worker_addr = runtime.addrs["worker"]
        reached = wait_for_agent_state(
            worker_addr,
            lambda state: "hello" in getattr(state, "messages", []),
            timeout=3.0,
        )
        assert reached, "Message 'hello' did not reach worker agent"

    def test_built_team_routes_to_resolves_to_existing_agents(
        self,
        routing_team_card: TeamCard,
        actor_system: ActorSystem,
    ) -> None:
        """routes_to sends to existing members, not hiring new ones."""
        runtime = _build_and_track(routing_team_card, actor_system)

        orchestrator_proxy: Orchestrator = actor_system.proxy_ask(
            runtime.orchestrator_addr, Orchestrator
        )
        initial_team_count = len(orchestrator_proxy.get_team())

        runtime.send("test-routing")

        worker_addr = runtime.addrs["worker"]
        reached = wait_for_agent_state(
            worker_addr,
            lambda state: "test-routing" in getattr(state, "messages", []),
            timeout=3.0,
        )
        assert reached, "Message did not reach worker"

        final_team_count = len(orchestrator_proxy.get_team())
        assert final_team_count == initial_team_count, (
            f"Team size changed from {initial_team_count} to {final_team_count} "
            f"-- duplicate hiring detected"
        )

    def test_built_team_parent_child_hierarchy(
        self,
        hierarchical_team_card: TeamCard,
        actor_system: ActorSystem,
    ) -> None:
        """Spawned agents have correct parent-child relationships."""
        runtime = _build_and_track(hierarchical_team_card, actor_system)

        supervisor_addr = runtime.addrs["supervisor"]
        worker_a_addr = runtime.addrs["worker_a"]
        worker_b_addr = runtime.addrs["worker_b"]

        worker_a_actor = get_actor_from_addr(worker_a_addr)
        worker_b_actor = get_actor_from_addr(worker_b_addr)
        supervisor_actor = get_actor_from_addr(supervisor_addr)

        assert worker_a_actor._parent == supervisor_addr, (
            "worker_a._parent does not point to supervisor"
        )
        assert worker_b_actor._parent == supervisor_addr, (
            "worker_b._parent does not point to supervisor"
        )
        assert worker_a_addr in supervisor_actor._children, (
            "supervisor._children does not contain worker_a"
        )
        assert worker_b_addr in supervisor_actor._children, (
            "supervisor._children does not contain worker_b"
        )

    def test_built_team_orchestrator_propagated(
        self,
        hierarchical_team_card: TeamCard,
        actor_system: ActorSystem,
    ) -> None:
        """Every agent has _orchestrator set to the orchestrator's address."""
        runtime = _build_and_track(hierarchical_team_card, actor_system)

        for name, addr in runtime.addrs.items():
            actor = get_actor_from_addr(addr)
            assert actor._orchestrator is not None, (
                f"Agent '{name}' has _orchestrator=None"
            )
            assert actor._orchestrator == runtime.orchestrator_addr, (
                f"Agent '{name}' _orchestrator does not match team orchestrator"
            )
