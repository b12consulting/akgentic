"""Integration tests for process_human_input: rehydration and recipient-based routing.

Tests use real Akgent subclasses, real orchestrators, and the InMemoryEventStore
to verify that messages with ActorAddressProxy sender/recipient are rehydrated
and routed to the correct UserProxy agent.
"""

from __future__ import annotations

import uuid

import pykka
import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressImpl, ActorAddressProxy
from akgentic.core.actor_system_impl import ActorSystem
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import ResultMessage, UserMessage
from akgentic.core.user_proxy import UserProxy
from akgentic.core.utils.deserializer import ActorAddressDict

from akgentic.team.manager import TeamManager
from akgentic.team.models import TeamCard, TeamCardMember
from tests.integration.conftest import (
    make_integration_agent_card,
    wait_for_agent_state,
)
from tests.services.conftest import InMemoryEventStore

# ---------------------------------------------------------------------------
# Agent classes for this test
# ---------------------------------------------------------------------------


class RecordingUserProxy(UserProxy):
    """UserProxy that records process_human_input calls for test verification."""

    def on_start(self) -> None:
        """Initialize recording state."""
        self.state = RecordingProxyState()

    def process_human_input(self, content: str, message: UserMessage) -> None:
        """Record the call and send reply to sender (exercises live address invariant)."""
        self.state.received_contents = [*self.state.received_contents, content]
        self.state.call_count += 1
        # This call will crash if message.sender is ActorAddressProxy
        super().process_human_input(content, message)


class RecordingProxyState(BaseState):
    """State for RecordingUserProxy."""

    received_contents: list[str] = []
    call_count: int = 0


class ManagerAgent(Akgent[BaseConfig, BaseState]):
    """Simple agent that records received messages for verification."""

    def on_start(self) -> None:
        """Initialize recording state."""
        self.state = _ManagerState()

    def receiveMsg_UserMessage(  # noqa: N802
        self, msg: UserMessage, sender: ActorAddress
    ) -> None:
        """Record message and sender for test assertions."""
        self.state.messages = [*self.state.messages, msg.content]
        self.state.counter += 1
        self.notify_state_change(self.state)

    def receiveMsg_ResultMessage(  # noqa: N802
        self, msg: ResultMessage, sender: ActorAddress
    ) -> None:
        """Record ResultMessage replies from UserProxy agents."""
        self.state.messages = [*self.state.messages, msg.content]
        self.state.counter += 1
        self.notify_state_change(self.state)


class _ManagerState(BaseState):
    """State for ManagerAgent."""

    messages: list[str] = []
    counter: int = 0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def actor_system() -> ActorSystem:
    """Create an ActorSystem, yield it, tear down all actors after test."""
    system = ActorSystem()
    yield system  # type: ignore[misc]
    pykka.ActorRegistry.stop_all()


def _make_multi_human_team_card() -> TeamCard:
    """Team with @Human (entry, RecordingUserProxy), @Manager, @Support (RecordingUserProxy)."""
    entry = TeamCardMember(
        card=make_integration_agent_card(
            name="@Human",
            role="Human",
            agent_class=RecordingUserProxy,
        ),
    )
    manager = TeamCardMember(
        card=make_integration_agent_card(
            name="@Manager",
            role="Manager",
            agent_class=ManagerAgent,
        ),
    )
    support = TeamCardMember(
        card=make_integration_agent_card(
            name="@Support",
            role="Support",
            agent_class=RecordingUserProxy,
        ),
    )
    return TeamCard(
        name="multi-human-team",
        description="Team with @Human, @Manager, @Support for process_human_input tests",
        entry_point=entry,
        members=[manager, support],
        message_types=[UserMessage],
    )


def _addr_to_proxy(addr: ActorAddress) -> ActorAddressProxy:
    """Convert a live ActorAddressImpl to an ActorAddressProxy (simulating deserialization)."""
    impl: ActorAddressImpl = addr  # type: ignore[assignment]
    addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": "akgentic.core.agent.Akgent",
        "agent_id": str(impl.agent_id),
        "name": impl.name,
        "role": impl.role,
        "team_id": str(impl.team_id) if impl.team_id else str(uuid.uuid4()),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    return ActorAddressProxy(addr_dict)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProcessHumanInputIntegration:
    """AC8: End-to-end multi-human integration test."""

    def test_rehydrate_and_route_to_support(
        self,
        actor_system: ActorSystem,
    ) -> None:
        """Full flow: create team, build message with proxy addrs, call process_human_input.

        Verifies:
        - @Support (not @Human) receives the call
        - @Support successfully sends a reply back to @Manager (live address invariant)
        - @Manager receives the reply
        """
        event_store = InMemoryEventStore()
        manager = TeamManager(actor_system, event_store)
        team_card = _make_multi_human_team_card()

        runtime = manager.create_team(team_card)

        # Get live addresses for @Manager and @Support
        manager_addr = runtime._orchestrator_proxy.get_team_member("@Manager")
        support_addr = runtime._orchestrator_proxy.get_team_member("@Support")
        assert manager_addr is not None
        assert support_addr is not None

        # Simulate what YamlEventStore.load_events() produces: proxy addresses
        proxy_sender = _addr_to_proxy(manager_addr)
        proxy_recipient = _addr_to_proxy(support_addr)

        # Build a message with proxy addresses (as would come from event store reload)
        message = UserMessage(content="@Support describe your role")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        # This is the method under test
        runtime.process_human_input("I coordinate user onboarding", message)

        # Wait for @Support to process the call
        reached = wait_for_agent_state(
            support_addr,
            lambda state: getattr(state, "call_count", 0) >= 1,
            timeout=3.0,
        )
        assert reached, "@Support did not receive process_human_input call"

        # Wait for @Manager to receive the reply from @Support
        reached = wait_for_agent_state(
            manager_addr,
            lambda state: getattr(state, "counter", 0) >= 1,
            timeout=3.0,
        )
        assert reached, "@Manager did not receive reply from @Support (live address invariant)"
