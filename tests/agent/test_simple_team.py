"""Tests for simple_team.py example.

Tests the simple team example components and setup.
"""

from multiprocessing import process
import os
import re
import time

from akgentic.core.messages.orchestrator import ProcessedMessage, ReceivedMessage
import pytest
from akgentic.core import ActorSystem, AgentCard, BaseConfig, Orchestrator
from akgentic.core.messages import Message
from akgentic.llm import ModelConfig, PromptTemplate

from akgentic.agent import BaseAgent, HumanProxy
from akgentic.agent.config import AgentConfig
from akgentic.agent.messages import AgentMessage

# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def actor_system():
    """Create an ActorSystem for tests."""
    system = ActorSystem()
    yield system
    # Cleanup: shutdown all actors to prevent hanging
    try:
        system.shutdown(timeout=5)
    except Exception:
        pass  # Best effort cleanup


@pytest.fixture
def orchestrator_address(actor_system: ActorSystem):
    """Create an Orchestrator actor."""
    addr = actor_system.createActor(
        Orchestrator, config=BaseConfig(name="@Orchestrator", role="Orchestrator")
    )
    yield addr
    # Orchestrator cleanup happens via actor_system.shutdown()


def _wait_for_quiescence(orchestrator: Orchestrator, timeout: float = 60) -> None:
    """Wait until no new messages appear, so all agents finish processing."""
    prev_count = -1
    stable_ticks = 0
    elapsed = 0.0
    while elapsed < timeout:
        msg_count = len(orchestrator.get_messages())
        if msg_count == prev_count:
            stable_ticks += 1
            if stable_ticks >= 3:  # stable for 1.5s
                return
        else:
            stable_ticks = 0
        prev_count = msg_count
        time.sleep(0.5)
        elapsed += 0.5


# =============================================================================
# AGENTCARD CREATION TESTS
# =============================================================================


class TestSimpleTeam:
    """Test the simple team example components."""

    def test_agent_card_creation(self) -> None:
        """Test creating AgentCards for team roles."""
        # Create cards for each role
        manager_card = AgentCard(
            role="Manager",
            description="Helpful manager coordinating team work",
            skills=["coordination", "delegation"],
            agent_class="akgentic.agent.BaseAgent",
            config=AgentConfig(
                name="@Manager",
                role="Manager",
                prompt=PromptTemplate(
                    template="You are a helpful manager. Coordinate the team effectively."
                ),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
            ),
            routes_to=["Assistant", "Expert"],
        )

        assistant_card = AgentCard(
            role="Assistant",
            description="Helpful assistant providing support",
            skills=["research", "writing"],
            agent_class="akgentic.agent.BaseAgent",
            config=AgentConfig(
                name="@Assistant",
                role="Assistant",
                prompt=PromptTemplate(
                    template="You are a helpful assistant. Provide clear and accurate information."
                ),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
            ),
        )

        # Verify card properties
        assert manager_card.role == "Manager"
        assert manager_card.agent_class == "akgentic.agent.BaseAgent"
        assert manager_card.has_skill("coordination")
        assert manager_card.can_route_to("Assistant")
        assert manager_card.can_route_to("Expert")
        assert not manager_card.can_route_to("Other")

        assert assistant_card.role == "Assistant"
        assert assistant_card.has_skill("research")
        assert assistant_card.has_skill("writing")
        # Empty routes_to means can route to anyone
        assert assistant_card.can_route_to("Manager")

    # =============================================================================
    # MANUAL TEAM SETUP TESTS
    # =============================================================================

    def test_manual_team_setup(self, actor_system: ActorSystem, orchestrator_address) -> None:
        """Test manual team setup without TeamFactory."""
        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)

        # Define and register cards
        manager_card = AgentCard(
            role="Manager",
            description="Test manager",
            skills=["coordination"],
            agent_class="akgentic.agent.BaseAgent",
            config=AgentConfig(
                name="@Manager",
                role="Manager",
                prompt=PromptTemplate(template="You are a test manager."),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
            ),
            routes_to=["Assistant"],
        )

        assistant_card = AgentCard(
            role="Assistant",
            description="Test assistant",
            skills=["research"],
            agent_class="akgentic.agent.BaseAgent",
            config=AgentConfig(
                name="@Assistant",
                role="Assistant",
                prompt=PromptTemplate(template="You are a test assistant."),
                model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
            ),
        )

        orchestrator.register_agent_profile(manager_card)
        orchestrator.register_agent_profile(assistant_card)

        # Get registered cards back
        cards = orchestrator.get_agent_catalog()
        assert len(cards) == 2
        assert any(c.role == "Manager" for c in cards)
        assert any(c.role == "Assistant" for c in cards)

        # Create manager agent manually
        manager_config = AgentConfig(
            name="@Manager",
            role="Manager",
            prompt=PromptTemplate(template="You are a test manager."),
            model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
        )
        manager_addr = orchestrator.createActor(BaseAgent, config=manager_config)

        # Create HumanProxy
        human_config = BaseConfig(name="@Human", role="Human")
        human_addr = orchestrator.createActor(HumanProxy, config=human_config)

        # Register manager with HumanProxy
        actor_system.tell(human_addr, ("RegisterManager", manager_addr))

        # Wait for initialization
        time.sleep(0.2)

        # Verify team roster
        team = orchestrator.get_team()
        assert len(team) >= 2  # At least Manager and Human
        team_names = [member.name for member in team]
        assert "@Manager" in team_names
        assert "@Human" in team_names

    # =============================================================================
    # EVENT SUBSCRIBER TESTS
    # =============================================================================

    def test_orchestrator_event_subscriber(
        self, actor_system: ActorSystem, orchestrator_address
    ) -> None:
        """Test EventSubscriber captures agent lifecycle events."""
        from akgentic.core import EventSubscriber

        events_captured: list[str] = []

        class TestSubscriber(EventSubscriber):
            def on_stop(self) -> None:
                pass

            def on_message(self, message: Message) -> None:
                events_captured.append(type(message).__name__)

        subscriber = TestSubscriber()

        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)
        orchestrator.subscribe(subscriber)

        # Creating an agent triggers a StartMessage to the orchestrator
        config = AgentConfig(
            name="@TestAgent",
            role="TestAgent",
            prompt=PromptTemplate(template="You are a test agent."),
            model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
        )
        orchestrator.createActor(BaseAgent, config=config)

        time.sleep(0.5)

        # Subscriber should have captured lifecycle events (StartMessage at minimum)
        assert len(events_captured) > 0
        assert "StartMessage" in events_captured

    # =============================================================================
    # MESSAGE ROUTING TESTS
    # =============================================================================

    def test_message_routing_by_name(self, actor_system: ActorSystem, orchestrator_address) -> None:
        """Test @ mention routing to specific agents."""
        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)

        # Create two agents
        agent1_config = AgentConfig(
            name="@Agent1",
            role="Agent1",
            prompt=PromptTemplate(template="You are agent 1."),
            model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
        )
        orchestrator.createActor(BaseAgent, config=agent1_config)

        agent2_config = AgentConfig(
            name="@Agent2",
            role="Agent2",
            prompt=PromptTemplate(template="You are agent 2."),
            model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
        )
        orchestrator.createActor(BaseAgent, config=agent2_config)

        time.sleep(0.2)

        # Test get_team_member routing
        found_agent1 = orchestrator.get_team_member("@Agent1")
        found_agent2 = orchestrator.get_team_member("@Agent2")

        assert found_agent1 is not None
        assert found_agent2 is not None
        assert found_agent1.name == "@Agent1"
        assert found_agent2.name == "@Agent2"

        # Test non-existent agent
        not_found = orchestrator.get_team_member("@NonExistent")
        assert not_found is None

    # =============================================================================
    # HUMANPROXY TESTS
    # =============================================================================

    def test_human_proxy_creation(self, actor_system: ActorSystem, orchestrator_address) -> None:
        """Test HumanProxy creation and team membership."""
        orchestrator = actor_system.proxy_ask(orchestrator_address, Orchestrator)

        # Create manager
        manager_config = AgentConfig(
            name="@Manager",
            role="Manager",
            prompt=PromptTemplate(template="You are a manager."),
            model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.3),
        )
        orchestrator.createActor(BaseAgent, config=manager_config)

        # Create HumanProxy
        human_config = BaseConfig(name="@Human", role="Human")
        human_addr = orchestrator.createActor(HumanProxy, config=human_config)

        time.sleep(0.2)

        # Verify both exist in team
        team = orchestrator.get_team()
        team_names = [member.name for member in team]
        assert "@Manager" in team_names
        assert "@Human" in team_names

        # Verify HumanProxy is accessible via proxy (don't send AgentMessage —
        # it would trigger an LLM call that outlives the test fixture)
        human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)
        assert human_proxy is not None

    # =============================================================================
    # IMPORT TESTS
    # =============================================================================

    def test_example_imports(self) -> None:
        """Test that all imports in the example are available."""
        # Test core imports
        # Test llm imports
        # Test messages imports
        from akgentic.core import (
            ActorSystem,
            AgentCard,
            Orchestrator,
        )
        from akgentic.llm import ModelConfig, PromptTemplate

        # Test team imports
        from akgentic.agent import (
            BaseAgent,
            HumanProxy,
        )
        from akgentic.agent.config import AgentConfig

        # All imports should succeed without errors
        assert ActorSystem is not None
        assert Orchestrator is not None
        assert AgentCard is not None
        assert BaseAgent is not None
        assert HumanProxy is not None
        assert ModelConfig is not None
        assert PromptTemplate is not None
        assert AgentConfig is not None


# =============================================================================
# INTEGRATION TESTS (require OPENAI_API_KEY)
# =============================================================================

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_real_manager_responds_to_human(actor_system: ActorSystem, orchestrator_address) -> None:
    """Integration test: Manager processes a message from HumanProxy and responds.

    Verifies that the Manager produces a SentMessage (AgentMessage) routed
    back to @Human after processing the request.
    """
    from akgentic.core.messages.orchestrator import SentMessage

    orchestrator_proxy = actor_system.proxy_ask(orchestrator_address, Orchestrator)

    manager_config = AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(
            template="You are a helpful manager. Always respond directly to @Human."
        ),
        model_cfg=ModelConfig(provider="openai", model="gpt-5-mini", temperature=0.0),
    )
    manager_card = AgentCard(
        role="Manager",
        description="Helpful manager",
        skills=["coordination"],
        agent_class="akgentic.agent.BaseAgent",
        config=manager_config,
    )
    orchestrator_proxy.register_agent_profile(manager_card)

    # Create Manager and HumanProxy
    manager_addr = orchestrator_proxy.createActor(BaseAgent, config=manager_config)
    human_config = BaseConfig(name="@Human", role="Human")
    human_addr = orchestrator_proxy.createActor(HumanProxy, config=human_config)
    time.sleep(0.3)

    # HumanProxy sends message to Manager
    human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)
    human_proxy.send(manager_addr, AgentMessage(content="Hello, are you available?"))

    # Wait for a SentMessage (Manager → @Human response)
    timeout = 60
    poll_interval = 0.5
    elapsed = 0.0
    sent_agent_messages: list[SentMessage] = []

    while elapsed < timeout:
        messages = orchestrator_proxy.get_messages()
        sent_agent_messages = [
            msg
            for msg in messages
            if isinstance(msg, SentMessage) and isinstance(msg.message, AgentMessage)
        ]
        if len(sent_agent_messages) > 1:
            break
        time.sleep(poll_interval)
        elapsed += poll_interval
    else:
        pytest.fail(f"Manager did not respond after {timeout}s timeout")

    # Verify the response message
    first_sent_message = sent_agent_messages[0].message
    second_sent_message = sent_agent_messages[1].message
    assert isinstance(first_sent_message, AgentMessage)
    assert isinstance(second_sent_message, AgentMessage)
    assert first_sent_message.type == "request"
    assert second_sent_message.type == "response" or "notification"

    _wait_for_quiescence(orchestrator_proxy)

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_real_manager_delegates_to_assistant(
    actor_system: ActorSystem, orchestrator_address
) -> None:
    """Integration test: Manager receives a task and delegates to Assistant.

    Verifies the full delegation flow: Manager processes the request via LLM,
    produces a StructuredOutput with a Request targeting @Assistant, and sends
    an AgentMessage to the Assistant actor.
    """
    from akgentic.core.messages.orchestrator import SentMessage

    orchestrator_proxy = actor_system.proxy_ask(orchestrator_address, Orchestrator)

    # Register AgentCards so routing knows about both roles
    manager_config = AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(
            template=("You are a manager. You NEVER answer questions yourself. "),
        ),
        model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
    )
    assistant_config = AgentConfig(
        name="@Assistant",
        role="Assistant",
        prompt=PromptTemplate(template="You are a helpful assistant. Answer concisely."),
        model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
    )
    human_config = BaseConfig(name="@Human", role="Human")

    # Create both agents so delegation can actually route
    manager_addr = orchestrator_proxy.createActor(BaseAgent, config=manager_config)
    orchestrator_proxy.createActor(BaseAgent, config=assistant_config)
    time.sleep(0.3)

    human_addr = orchestrator_proxy.createActor(HumanProxy, config=human_config)
    human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)

    # Send a task to the manager
    human_proxy.send(manager_addr, AgentMessage(content="Ask to @Assistant what is 2026+4?"))

    # Wait for a SentMessage containing an AgentMessage (Manager → Assistant)
    timeout = 120
    poll_interval = 0.5
    elapsed = 0.0
    sent_agent_messages: list[SentMessage] = []

    while elapsed < timeout:
        messages = orchestrator_proxy.get_messages()
        sent_agent_messages = [
            msg
            for msg in messages
            if isinstance(msg, SentMessage) and isinstance(msg.message, AgentMessage)
        ]
        if len(sent_agent_messages) > 3:
            break
        time.sleep(poll_interval)
        elapsed += poll_interval
    else:
        pytest.fail(f"Manager did not delegate after {timeout}s timeout")

    # Verify the delegation: Manager sent an AgentMessage to another agent
    result_message = next(
        (
            msg
            for msg in sent_agent_messages
            if isinstance(msg.message, AgentMessage) and "2030" in msg.message.content
        ),
        None,
    )
    assert result_message is not None, "Should find a SentMessage with answer 2030"
    _wait_for_quiescence(orchestrator_proxy)

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_real_event_subscriber_captures_full_lifecycle(
    actor_system: ActorSystem, orchestrator_address
) -> None:
    """Integration test: EventSubscriber captures Start → Received → Processed → Sent."""
    from akgentic.core import EventSubscriber
    from akgentic.core.messages.orchestrator import SentMessage

    orchestrator_proxy = actor_system.proxy_ask(orchestrator_address, Orchestrator)

    events_captured: list[str] = []
    sent_messages: list[SentMessage] = []
    received_messages: list[ReceivedMessage] = []
    processed_messages: list[ProcessedMessage] = []

    class TestSubscriber(EventSubscriber):
        def on_stop(self) -> None:
            pass

        def on_message(self, message: Message) -> None:
            events_captured.append(type(message).__name__)
            if isinstance(message, SentMessage) and isinstance(message.message, AgentMessage):
                sent_messages.append(message)
            if isinstance(message, ReceivedMessage):
                received_messages.append(message)
            if isinstance(message, ProcessedMessage):
                processed_messages.append(message)

    subscriber = TestSubscriber()
    orchestrator_proxy.subscribe(subscriber)

    # Create Manager and Human so the Manager can respond to @Human
    manager_config = AgentConfig(
        name="@Manager",
        role="Manager",
        prompt=PromptTemplate(template="You are a manager. Always respond directly to @Human."),
        model_cfg=ModelConfig(provider="openai", model="gpt-5-mini"),
    )
    manager_addr = orchestrator_proxy.createActor(BaseAgent, config=manager_config)

    human_config = BaseConfig(name="@Human", role="Human")
    human_addr = orchestrator_proxy.createActor(HumanProxy, config=human_config)
    human_proxy = actor_system.proxy_tell(human_addr, HumanProxy)
    time.sleep(0.3)

    # Send message to trigger the full lifecycle
    human_proxy.send(manager_addr, AgentMessage(content="Are you ready?"))

    # Wait for a SentMessage (full cycle complete)
    timeout = 15
    poll_interval = 0.5
    elapsed = 0.0

    while elapsed < timeout:
        if sent_messages and received_messages and processed_messages:
            break
        time.sleep(poll_interval)
        elapsed += poll_interval

    # Subscriber should capture the full lifecycle
    assert "StartMessage" in events_captured, "Should capture agent startup"
    assert "ReceivedMessage" in events_captured, "Should capture message receipt"
    assert "ProcessedMessage" in events_captured, "Should capture message processing"
    assert "SentMessage" in events_captured, "Should capture outbound message"

    # Verify the sent message is an AgentMessage
    assert len(sent_messages) > 0, "Should have captured at least one AgentMessage"
    assert isinstance(sent_messages[0].message, AgentMessage)

    _wait_for_quiescence(orchestrator_proxy)
