"""Integration test fixtures: real agent classes, team card fixtures, and helpers.

All agents are functional Akgent subclasses with real receiveMsg_UserMessage handlers.
No stubs, no mocks -- these exercise the full actor lifecycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pykka
import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import UserMessage

from akgentic.team.models import TeamCard, TeamCardMember

# ---------------------------------------------------------------------------
# Custom config for routing agents
# ---------------------------------------------------------------------------


class RoutingConfig(BaseConfig):
    """Config that carries routes_to targets for the RoutingAgent."""

    routes_to: list[str] = []


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------


class RecordingState(BaseState):
    """State that records received messages."""

    messages: list[str] = []
    counter: int = 0


# ---------------------------------------------------------------------------
# Real agent classes
# ---------------------------------------------------------------------------


class RecordingAgent(Akgent[BaseConfig, RecordingState]):
    """Agent that records every UserMessage it receives into state."""

    def on_start(self) -> None:
        """Initialize with RecordingState."""
        self.state = RecordingState()

    def receiveMsg_UserMessage(  # noqa: N802
        self, msg: UserMessage, sender: ActorAddress
    ) -> None:
        """Append message content to state and increment counter."""
        self.state.messages = [*self.state.messages, msg.content]
        self.state.counter += 1
        self.notify_state_change(self.state)


class RoutingAgent(Akgent[RoutingConfig, BaseState]):
    """Agent that forwards UserMessages to routes_to targets via the orchestrator."""

    def receiveMsg_UserMessage(  # noqa: N802
        self, msg: UserMessage, sender: ActorAddress
    ) -> None:
        """Look up each route target and forward the message."""
        for target_name in self.config.routes_to:
            addr = self.orchestrator_proxy_ask.get_team_member(target_name)
            if addr is not None:
                self.send(addr, msg)


class StatefulAgent(Akgent[BaseConfig, RecordingState]):
    """Agent that increments a counter on each UserMessage for lifecycle tests."""

    def on_start(self) -> None:
        """Initialize with RecordingState."""
        self.state = RecordingState()

    def receiveMsg_UserMessage(  # noqa: N802
        self, msg: UserMessage, sender: ActorAddress
    ) -> None:
        """Increment counter and notify state change."""
        self.state.counter += 1
        self.notify_state_change(self.state)


# ---------------------------------------------------------------------------
# Helper: make_agent_card for integration tests
# ---------------------------------------------------------------------------


def make_integration_agent_card(
    name: str,
    role: str,
    agent_class: type[Akgent[Any, Any]],
    routes_to: list[str] | None = None,
    config: BaseConfig | None = None,
) -> AgentCard:
    """Create an AgentCard with a real agent class for integration tests."""
    if config is None:
        if routes_to:
            config = RoutingConfig(name=name, role=role, routes_to=routes_to)
        else:
            config = BaseConfig(name=name, role=role)
    return AgentCard(
        role=role,
        description=f"Integration test agent: {role}",
        skills=["testing"],
        agent_class=agent_class,
        config=config,
        routes_to=routes_to or [],
    )


# ---------------------------------------------------------------------------
# Team card fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def routing_team_card() -> TeamCard:
    """TeamCard: entry(RoutingAgent, routes_to=[@worker]) with one member worker(RecordingAgent).

    The @ prefix mirrors V1 naming convention for routes_to targets.
    """
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name="router",
            role="Router",
            agent_class=RoutingAgent,
            routes_to=["worker"],
        ),
    )
    worker = TeamCardMember(
        card=make_integration_agent_card(
            name="worker",
            role="Worker",
            agent_class=RecordingAgent,
        ),
    )
    return TeamCard(
        name="routing-team",
        description="A routing team for integration tests",
        entry_point=entry,
        members=[worker],
        message_types=[UserMessage],
    )


@pytest.fixture()
def hierarchical_team_card() -> TeamCard:
    """TeamCard: entry(RecordingAgent) with supervisor(RoutingAgent) containing workers.

    Structure: entry → [supervisor(routes_to=[@worker_a]) → [worker_a, worker_b]]
    """
    worker_a = TeamCardMember(
        card=make_integration_agent_card(
            name="worker_a",
            role="WorkerA",
            agent_class=RecordingAgent,
        ),
    )
    worker_b = TeamCardMember(
        card=make_integration_agent_card(
            name="worker_b",
            role="WorkerB",
            agent_class=RecordingAgent,
        ),
    )
    supervisor = TeamCardMember(
        card=make_integration_agent_card(
            name="supervisor",
            role="Supervisor",
            agent_class=RoutingAgent,
            routes_to=["worker_a"],
        ),
        members=[worker_a, worker_b],
    )
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name="lead",
            role="Lead",
            agent_class=RecordingAgent,
        ),
    )
    return TeamCard(
        name="hierarchical-team",
        description="A hierarchical team for integration tests",
        entry_point=entry,
        members=[supervisor],
        message_types=[UserMessage],
    )


# ---------------------------------------------------------------------------
# Actor system fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def actor_system() -> ActorSystem:
    """Create an ActorSystem, yield it, tear down all actors after test."""
    system = ActorSystem()
    yield system  # type: ignore[misc]
    pykka.ActorRegistry.stop_all()


# ---------------------------------------------------------------------------
# Polling helper
# ---------------------------------------------------------------------------


def get_actor_from_addr(addr: ActorAddress) -> Akgent[Any, Any]:
    """Extract the underlying Akgent instance from an ActorAddress.

    Warning: reaches into Pykka internals (``_actor_ref._actor_weakref()``).
    This is acceptable in integration tests but couples to Pykka's internal
    layout.  If Pykka upgrades break this, update the access path here.
    """
    impl = addr
    if isinstance(impl, ActorAddressImpl):
        return impl._actor_ref._actor_weakref()  # type: ignore[return-value]
    msg = f"Cannot extract actor from address type: {type(addr)}"
    raise TypeError(msg)


def wait_for_agent_state(
    addr: ActorAddress,
    predicate: Callable[[BaseState], bool],
    timeout: float = 2.0,
) -> bool:
    """Poll agent state until predicate returns True or timeout.

    Args:
        addr: ActorAddress of the agent to poll.
        predicate: Function that takes agent state and returns True when done.
        timeout: Maximum seconds to wait.

    Returns:
        True if predicate was satisfied, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        actor = get_actor_from_addr(addr)
        if predicate(actor.state):
            return True
        time.sleep(0.05)
    return False
