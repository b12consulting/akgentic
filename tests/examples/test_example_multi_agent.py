"""Tests for Multi-Agent Coordination example (Story 5-5).

Verifies the example demonstrates multi-agent patterns:
- Custom message types for multi-stage workflow
- Specialized agents (ResearchAgent, WriterAgent, CoordinatorAgent)
- Orchestrator telemetry tracking of complete message flow
- UserProxy integration for human-in-the-loop review
- SimpleLogger subscriber implementing EventSubscriber pattern
"""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from akgentic.core import (
    ActorSystem,
    Akgent,
    BaseConfig,
    BaseState,
    Orchestrator,
)
from akgentic.core.messages import Message


class TestMessageTypesDefinition:
    """Tests for message type definitions."""

    def test_task_request_message_definition(self) -> None:
        """TaskRequest message has topic and requester_id fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "TaskRequest"), "TaskRequest not defined"
        assert issubclass(module.TaskRequest, Message), "TaskRequest must extend Message"

        msg = module.TaskRequest(topic="test topic", requester_id="test-requester")
        assert msg.topic == "test topic"
        assert msg.requester_id == "test-requester"

    def test_research_result_message_definition(self) -> None:
        """ResearchResult message has topic, key_points, and summary fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "ResearchResult"), "ResearchResult not defined"
        assert issubclass(module.ResearchResult, Message), "ResearchResult must extend Message"

        msg = module.ResearchResult(
            topic="test topic",
            key_points=["point1", "point2"],
            summary="test summary",
        )
        assert msg.topic == "test topic"
        assert msg.key_points == ["point1", "point2"]
        assert msg.summary == "test summary"

    def test_draft_content_message_definition(self) -> None:
        """DraftContent message has topic, content, and word_count fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "DraftContent"), "DraftContent not defined"
        assert issubclass(module.DraftContent, Message), "DraftContent must extend Message"

        msg = module.DraftContent(topic="test", content="test content", word_count=2)
        assert msg.topic == "test"
        assert msg.content == "test content"
        assert msg.word_count == 2

    def test_review_request_message_definition(self) -> None:
        """ReviewRequest message has draft and reviewer_notes fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "ReviewRequest"), "ReviewRequest not defined"
        assert issubclass(module.ReviewRequest, Message), "ReviewRequest must extend Message"

        draft = module.DraftContent(topic="test", content="content", word_count=1)
        msg = module.ReviewRequest(draft=draft, reviewer_notes="Please review")
        assert msg.draft.topic == "test"
        assert msg.reviewer_notes == "Please review"

    def test_approval_response_message_definition(self) -> None:
        """ApprovalResponse message has approved, feedback, and draft_id fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "ApprovalResponse"), "ApprovalResponse not defined"
        assert issubclass(module.ApprovalResponse, Message), "ApprovalResponse must extend Message"

        msg = module.ApprovalResponse(
            approved=True,
            feedback="Looks good",
            draft_id="draft-123",
        )
        assert msg.approved is True
        assert msg.feedback == "Looks good"
        assert msg.draft_id == "draft-123"


class TestStateTypesDefinition:
    """Tests for state type definitions."""

    def test_coordinator_state_definition(self) -> None:
        """CoordinatorState extends BaseState with required fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "CoordinatorState"), "CoordinatorState not defined"
        assert issubclass(module.CoordinatorState, BaseState), (
            "CoordinatorState must extend BaseState"
        )

        state = module.CoordinatorState()
        assert hasattr(state, "current_task"), "Missing current_task field"
        assert hasattr(state, "task_status"), "Missing task_status field"
        assert hasattr(state, "workflow_stage"), "Missing workflow_stage field"

    def test_specialist_state_definition(self) -> None:
        """SpecialistState extends BaseState with required fields."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "SpecialistState"), "SpecialistState not defined"
        assert issubclass(module.SpecialistState, BaseState), (
            "SpecialistState must extend BaseState"
        )

        state = module.SpecialistState()
        assert hasattr(state, "items_processed"), "Missing items_processed field"
        assert hasattr(state, "current_input"), "Missing current_input field"
        assert hasattr(state, "last_output"), "Missing last_output field"


class TestAgentDefinitions:
    """Tests for agent class definitions."""

    def test_research_agent_definition(self) -> None:
        """ResearchAgent is properly defined agent."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "ResearchAgent"), "ResearchAgent not defined"
        assert issubclass(module.ResearchAgent, Akgent), "ResearchAgent must extend Akgent"

    def test_writer_agent_definition(self) -> None:
        """WriterAgent is properly defined agent."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "WriterAgent"), "WriterAgent not defined"
        assert issubclass(module.WriterAgent, Akgent), "WriterAgent must extend Akgent"

    def test_coordinator_agent_definition(self) -> None:
        """CoordinatorAgent is properly defined agent."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module, "CoordinatorAgent"), "CoordinatorAgent not defined"
        assert issubclass(module.CoordinatorAgent, Akgent), "CoordinatorAgent must extend Akgent"


class TestUserProxyIntegration:
    """Tests for UserProxy human-in-the-loop integration."""

    def test_simulated_user_proxy_definition(self) -> None:
        """SimulatedUserProxy is defined and extends UserProxy."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        from akgentic.core import UserProxy

        assert hasattr(module, "SimulatedUserProxy"), "SimulatedUserProxy not defined"
        assert issubclass(module.SimulatedUserProxy, UserProxy), (
            "SimulatedUserProxy must extend UserProxy"
        )

    def test_user_proxy_handles_review_request(self) -> None:
        """SimulatedUserProxy handles ReviewRequest messages."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        assert hasattr(module.SimulatedUserProxy, "receiveMsg_ReviewRequest"), (
            "Must have receiveMsg_ReviewRequest handler"
        )


class TestSimpleLoggerSubscriber:
    """Tests for SimpleLogger implementing EventSubscriber."""

    def test_simple_logger_definition(self) -> None:
        """SimpleLogger is defined and implements EventSubscriber."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)


        assert hasattr(module, "SimpleLogger"), "SimpleLogger not defined"
        # Check that it has required methods
        logger = module.SimpleLogger()
        assert hasattr(logger, "on_message"), "Must have on_message method"
        assert hasattr(logger, "on_stop"), "Must have on_stop method"


class TestMessageRoutingChain:
    """Tests for complete message routing through workflow."""

    def test_task_request_routes_to_research_agent(self, capsys) -> None:
        """TaskRequest is routed from CoordinatorAgent to ResearchAgent."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            research_addr = orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            writer_addr = orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            user_proxy_addr = orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            coordinator_addr = orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            # Setup routing
            coordinator_proxy = system.proxy_tell(coordinator_addr, module.CoordinatorAgent)
            coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

            time.sleep(0.3)

            # Send task request
            system.tell(
                coordinator_addr,
                module.TaskRequest(topic="test topic", requester_id="test"),
            )

            time.sleep(0.5)

            # Check output
            captured = capsys.readouterr()
            assert "[CoordinatorAgent] Routing task" in captured.out, "Should route task"
            assert "[ResearchAgent] Researching" in captured.out, "Should receive and process task"

        finally:
            system.shutdown(timeout=5)

    def test_research_result_routes_to_writer_agent(self, capsys) -> None:
        """ResearchResult from ResearchAgent is routed to WriterAgent."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            research_addr = orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            writer_addr = orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            user_proxy_addr = orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            coordinator_addr = orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            coordinator_proxy = system.proxy_tell(coordinator_addr, module.CoordinatorAgent)
            coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

            time.sleep(0.3)

            # Send task - will flow through research to writer
            system.tell(
                coordinator_addr,
                module.TaskRequest(topic="test topic", requester_id="test"),
            )

            time.sleep(0.8)

            captured = capsys.readouterr()
            assert "[WriterAgent] Drafting" in captured.out, "Writer should receive research result"

        finally:
            system.shutdown(timeout=5)

    def test_draft_content_routes_to_user_proxy(self, capsys) -> None:
        """DraftContent from WriterAgent is routed to UserProxy for review."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            research_addr = orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            writer_addr = orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            user_proxy_addr = orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            coordinator_addr = orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            coordinator_proxy = system.proxy_tell(coordinator_addr, module.CoordinatorAgent)
            coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

            time.sleep(0.3)

            system.tell(
                coordinator_addr,
                module.TaskRequest(topic="test topic", requester_id="test"),
            )

            time.sleep(1.0)

            captured = capsys.readouterr()
            assert "[UserProxy] Awaiting human input" in captured.out, (
                "UserProxy should receive draft"
            )

        finally:
            system.shutdown(timeout=5)


class TestOrchestratorTracking:
    """Tests for Orchestrator telemetry tracking."""

    def test_orchestrator_tracks_multiple_messages(self) -> None:
        """Orchestrator tracks 12+ messages in multi-agent workflow."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            research_addr = orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            writer_addr = orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            user_proxy_addr = orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            coordinator_addr = orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            coordinator_proxy = system.proxy_tell(coordinator_addr, module.CoordinatorAgent)
            coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

            time.sleep(0.3)

            system.tell(
                coordinator_addr,
                module.TaskRequest(topic="test", requester_id="test"),
            )

            time.sleep(1.0)

            # Query orchestrator
            messages = orch_proxy.get_messages()

            # Should have many messages: StartMessages for each agent, SentMessages, etc.
            assert len(messages) >= 12, f"Expected 12+ messages, got {len(messages)}"

        finally:
            system.shutdown(timeout=5)

    def test_orchestrator_tracks_team_composition(self) -> None:
        """Orchestrator.get_team() returns all 4 agents."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            # This ensures StartMessages are delivered to orchestrator via auto-propagated orchestrator ref
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            time.sleep(0.3)

            # Query orchestrator for team
            team = orch_proxy.get_team()

            # Should have 4 agents (research, writer, coordinator, user_proxy)
            assert len(team) == 4, f"Expected 4 team members, got {len(team)}"

        finally:
            system.shutdown(timeout=5)

    def test_orchestrator_tracks_agent_states(self) -> None:
        """Orchestrator.get_states() tracks agent state snapshots."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"
        spec = importlib.util.spec_from_file_location("multi_agent", example_path)
        assert spec is not None, f"Example file not found: {example_path}"
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None, "No loader for example module"
        spec.loader.exec_module(module)

        system = ActorSystem()
        try:
            orchestrator_addr = system.createActor(
                Orchestrator,
                config=BaseConfig(name="orchestrator", role="Orchestrator"),
            )

            # Create agents via orchestrator proxy — correct pattern
            orch_proxy = system.proxy_ask(orchestrator_addr, Orchestrator)
            research_addr = orch_proxy.createActor(
                module.ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
            )
            writer_addr = orch_proxy.createActor(
                module.WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
            )
            user_proxy_addr = orch_proxy.createActor(
                module.SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
            )
            coordinator_addr = orch_proxy.createActor(
                module.CoordinatorAgent,
                config=BaseConfig(name="coordinator", role="CoordinatorAgent"),
            )

            coordinator_proxy = system.proxy_tell(coordinator_addr, module.CoordinatorAgent)
            coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

            time.sleep(0.3)

            system.tell(
                coordinator_addr,
                module.TaskRequest(topic="test", requester_id="test"),
            )

            time.sleep(1.0)

            # Query orchestrator for states
            states = orch_proxy.get_states()

            # Should track at least 3 agents (research, writer, coordinator)
            assert len(states) >= 3, f"Expected 3+ tracked states, got {len(states)}"

        finally:
            system.shutdown(timeout=5)


class TestEndToEndExecution:
    """Tests for complete example execution."""

    def test_example_runs_without_exceptions(self) -> None:
        """Example runs end-to-end without errors."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0, f"Example failed with stderr: {result.stderr}"

    def test_example_demonstrates_complete_workflow(self) -> None:
        """Example output shows complete workflow progression."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0
        output = result.stdout

        # Check for workflow stages
        assert "[CoordinatorAgent] Routing task" in output, "Should show task routing"
        assert "[ResearchAgent] Researching" in output, "Should show research"
        assert "[ResearchAgent] Research complete" in output, "Should show research completion"
        assert "[WriterAgent] Drafting" in output, "Should show drafting"
        assert "[WriterAgent] Draft complete" in output, "Should show draft completion"
        assert "[UserProxy] Awaiting human input" in output, "Should show user proxy awaiting"
        assert "[UserProxy] Human approved" in output, "Should show human approval"
        assert "[CoordinatorAgent] Task complete" in output, "Should show task completion"

    def test_example_shows_orchestrator_summary(self) -> None:
        """Example output shows Orchestrator telemetry summary."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0
        output = result.stdout

        # Check for summary section
        assert "=== Orchestrator Summary ===" in output, "Should show summary header"
        assert "Total messages:" in output, "Should show message count"
        assert "Team members:" in output, "Should show team member count"
        assert "State snapshots:" in output, "Should show state snapshot count"
        assert "===========================" in output, "Should show summary footer"

    def test_example_shows_team_assembly(self) -> None:
        """Example output shows team assembly message."""
        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"

        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(example_path.parent.parent),
        )

        assert result.returncode == 0
        output = result.stdout

        # Check for team assembly
        assert "[Orchestrator] Team assembled" in output, "Should show team assembly"
        assert "CoordinatorAgent" in output, "Should mention CoordinatorAgent"
        assert "ResearchAgent" in output, "Should mention ResearchAgent"
        assert "WriterAgent" in output, "Should mention WriterAgent"
        assert "UserProxy" in output, "Should mention UserProxy"

    def test_example_completes_within_timeout(self) -> None:
        """Example completes execution within 15 seconds."""
        import time

        example_path = Path(__file__).parent.parent.parent / "examples" / "05_multi_agent.py"

        start = time.time()
        result = subprocess.run(
            [sys.executable, str(example_path)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(example_path.parent.parent),
        )
        elapsed = time.time() - start

        assert result.returncode == 0, f"Example failed: {result.stderr}"
        assert elapsed < 15, f"Example took {elapsed:.2f}s, should be <15s"
