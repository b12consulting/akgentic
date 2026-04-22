"""
Multi-Agent Coordination - Complex Workflow with Orchestrator and Human-in-the-Loop
====================================================================================

This example demonstrates the most complex coordination pattern:
- Multiple specialized agents (ResearchAgent, WriterAgent, CoordinatorAgent)
- Custom message types for multi-stage workflow
- Orchestrator telemetry tracking complete message flow
- UserProxy for human-in-the-loop review and approval
- SimpleLogger subscriber implementing EventSubscriber pattern

Run with: python examples/05_multi_agent.py
Or with:  uv run python examples/05_multi_agent.py
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from akgentic.core import (
    ActorAddress,
    ActorSystem,
    Akgent,
    BaseConfig,
    BaseState,
    EventSubscriber,
    Orchestrator,
    UserProxy,
)
from akgentic.core.messages import Message

if TYPE_CHECKING:
    pass


# =============================================================================
# STEP 1: Define message types for multi-stage workflow
# =============================================================================
# Messages flow through the workflow:
# TaskRequest -> ResearchResult -> DraftContent -> ReviewRequest -> ApprovalResponse


class TaskRequest(Message):
    """Request for multi-agent coordination workflow.

    Attributes:
        topic: The topic to research and write about.
        requester_id: ID of the agent making the request.
    """

    topic: str
    requester_id: str = ""


class ResearchResult(Message):
    """Result of research phase from ResearchAgent.

    Attributes:
        topic: The researched topic.
        key_points: List of key findings from research.
        summary: Summary of research findings.
    """

    topic: str
    key_points: list[str]
    summary: str


class DraftContent(Message):
    """Draft content from WriterAgent based on research.

    Attributes:
        topic: The topic of the content.
        content: The drafted content text.
        word_count: Number of words in the draft.
    """

    topic: str
    content: str
    word_count: int


class ReviewRequest(Message):
    """Request for human review of draft content.

    Attributes:
        draft: The DraftContent message being reviewed.
        reviewer_notes: Optional notes for the reviewer.
    """

    draft: DraftContent
    reviewer_notes: str = ""


class ApprovalResponse(Message):
    """Response with human approval/rejection of draft.

    Attributes:
        approved: Whether the draft was approved.
        feedback: Human feedback on the draft.
        draft_id: ID of the reviewed draft.
    """

    approved: bool
    feedback: str
    draft_id: str = ""


# Rebuild model to resolve forward reference
ReviewRequest.model_rebuild()


# =============================================================================
# STEP 2: Define custom state types
# =============================================================================


class CoordinatorState(BaseState):
    """State for CoordinatorAgent tracking task flow.

    Attributes:
        current_task: The task currently being processed.
        task_status: Current status (pending, researching, drafting, reviewing, complete).
        workflow_stage: Which stage of workflow we're in.
    """

    current_task: str = ""
    task_status: str = "pending"
    workflow_stage: str = "init"


class SpecialistState(BaseState):
    """State for specialist agents (ResearchAgent, WriterAgent).

    Attributes:
        items_processed: Count of items processed.
        current_input: Current input being processed.
        last_output: Last output produced.
    """

    items_processed: int = 0
    current_input: str = ""
    last_output: str = ""


# =============================================================================
# STEP 3: Define ResearchAgent - receives tasks and returns research
# =============================================================================


class ResearchAgent(Akgent[BaseConfig, SpecialistState]):
    """Agent that researches topics and returns key findings.

    This agent demonstrates:
    - Receiving TaskRequest messages
    - Processing research and generating key points
    - Sending ResearchResult back to coordinator
    - State tracking of processed items
    """

    def on_start(self) -> None:
        """Initialize research agent state."""
        self.state = SpecialistState()
        self.state.observer(self)

    def receiveMsg_TaskRequest(self, message: TaskRequest, sender: ActorAddress) -> None:
        """Handle task request by conducting research.

        Args:
            message: TaskRequest containing topic to research.
            sender: CoordinatorAgent sending the request.
        """
        topic = message.topic
        print(f'[ResearchAgent] Researching: "{topic}"')

        # Simulate research - in real scenario would call APIs, search databases, etc.
        key_points = [
            "Actor model enables concurrent message-passing computations",
            "Agents encapsulate state and behavior in isolated entities",
            "Orchestrator provides telemetry and coordination without tight coupling",
        ]

        # Create research result
        result = ResearchResult(
            topic=topic,
            key_points=key_points,
            summary=f"Research on {topic}: {len(key_points)} key points discovered",
        )

        print(f"[ResearchAgent] Research complete: {len(key_points)} key points found")

        # Update state
        self.state.items_processed += 1
        self.state.current_input = topic
        self.state.last_output = result.summary
        self.state.notify_state_change()

        # Send result back to coordinator
        self.send(sender, result)


# =============================================================================
# STEP 4: Define WriterAgent - takes research and drafts content
# =============================================================================


class WriterAgent(Akgent[BaseConfig, SpecialistState]):
    """Agent that drafts content from research findings.

    This agent demonstrates:
    - Receiving ResearchResult messages
    - Creating content from research
    - Sending DraftContent to coordinator
    - State tracking of drafting work
    """

    def on_start(self) -> None:
        """Initialize writer agent state."""
        self.state = SpecialistState()
        self.state.observer(self)

    def receiveMsg_ResearchResult(self, message: ResearchResult, sender: ActorAddress) -> None:
        """Handle research result by drafting content.

        Args:
            message: ResearchResult containing research findings.
            sender: CoordinatorAgent sending the research.
        """
        topic = message.topic
        key_points = message.key_points

        print("[WriterAgent] Drafting content from research...")

        # Create draft content from research points
        content_lines = [
            f"Summary: {topic}",
            "",
            "Key Points:",
        ]
        for i, point in enumerate(key_points, 1):
            content_lines.append(f"{i}. {point}")

        content = "\n".join(content_lines)
        word_count = len(content.split())

        # Create draft
        draft = DraftContent(topic=topic, content=content, word_count=word_count)

        print(f"[WriterAgent] Draft complete: {word_count} words")

        # Update state
        self.state.items_processed += 1
        self.state.current_input = topic
        self.state.last_output = content[:50] + "..."
        self.state.notify_state_change()

        # Send draft back to coordinator
        self.send(sender, draft)


# =============================================================================
# STEP 5: Define CoordinatorAgent - routes messages between agents
# =============================================================================


class CoordinatorAgent(Akgent[BaseConfig, CoordinatorState]):
    """Agent that coordinates multi-agent workflow.

    This agent demonstrates:
    - Routing TaskRequest to ResearchAgent
    - Receiving ResearchResult and sending to WriterAgent
    - Receiving DraftContent and sending to UserProxy for review
    - Handling ApprovalResponse and completing workflow
    - State tracking of workflow progression
    """

    def on_start(self) -> None:
        """Initialize coordinator state."""
        self.state = CoordinatorState()
        self.state.observer(self)
        self.research_agent: ActorAddress | None = None
        self.writer_agent: ActorAddress | None = None
        self.user_proxy: ActorAddress | None = None

    def set_agents(
        self,
        research_agent: ActorAddress,
        writer_agent: ActorAddress,
        user_proxy: ActorAddress,
    ) -> None:
        """Set references to other agents for routing.

        Args:
            research_agent: Address of ResearchAgent.
            writer_agent: Address of WriterAgent.
            user_proxy: Address of UserProxy for human review.
        """
        self.research_agent = research_agent
        self.writer_agent = writer_agent
        self.user_proxy = user_proxy

    def receiveMsg_TaskRequest(self, message: TaskRequest, sender: ActorAddress) -> None:
        """Handle task request by routing to ResearchAgent.

        Args:
            message: TaskRequest from external source.
            sender: Sender of the request.
        """
        topic = message.topic
        print(f'[CoordinatorAgent] Routing task: "{topic}"')

        # Update state
        self.state.current_task = topic
        self.state.task_status = "researching"
        self.state.workflow_stage = "research"
        self.state.notify_state_change()

        # Route to research agent
        if self.research_agent is not None:
            self.send(
                self.research_agent,
                TaskRequest(topic=topic, requester_id="coordinator"),
            )

    def receiveMsg_ResearchResult(self, message: ResearchResult, sender: ActorAddress) -> None:
        """Handle research result by routing to WriterAgent.

        Args:
            message: ResearchResult from ResearchAgent.
            sender: ResearchAgent address.
        """
        print("[CoordinatorAgent] Received research, routing to WriterAgent")

        # Update state
        self.state.task_status = "drafting"
        self.state.workflow_stage = "writing"
        self.state.notify_state_change()

        # Route to writer agent
        if self.writer_agent is not None:
            self.send(self.writer_agent, message)

    def receiveMsg_DraftContent(self, message: DraftContent, sender: ActorAddress) -> None:
        """Handle draft content by sending to UserProxy for review.

        Args:
            message: DraftContent from WriterAgent.
            sender: WriterAgent address.
        """
        print("[CoordinatorAgent] Sending draft to UserProxy for human review")

        # Update state
        self.state.task_status = "reviewing"
        self.state.workflow_stage = "review"
        self.state.notify_state_change()

        # Create review request
        review = ReviewRequest(
            draft=message,
            reviewer_notes="Please review the draft for accuracy and clarity.",
        )

        # Send to user proxy
        if self.user_proxy is not None:
            self.send(self.user_proxy, review)

    def receiveMsg_ApprovalResponse(self, message: ApprovalResponse, sender: ActorAddress) -> None:
        """Handle approval response from UserProxy.

        Args:
            message: ApprovalResponse with human decision.
            sender: UserProxy address.
        """
        if message.approved:
            print("[CoordinatorAgent] Task complete. Workflow finished.")
        else:
            print(f"[CoordinatorAgent] Draft rejected. Feedback: {message.feedback}")

        # Update state to complete
        self.state.task_status = "complete"
        self.state.workflow_stage = "done"
        self.state.notify_state_change()


# =============================================================================
# STEP 6: Define SimpleLogger - demonstrates EventSubscriber
# =============================================================================


class SimpleLogger(EventSubscriber):
    """Simple event logger implementing EventSubscriber.

    This demonstrates the subscriber pattern for extensibility.
    In Phase 3, this pattern enables Redis publishers, WebSocket streams,
    or database persistence.
    """

    def __init__(self) -> None:
        """Initialize the logger."""
        self.message_count = 0

    def on_message(self, msg: Message) -> None:
        """Called when an orchestrator message is received.

        Args:
            msg: The Message being logged.
        """
        self.message_count += 1

    def set_restoring(self, restoring: bool) -> None:
        """No-op — this simple logger doesn't need to handle restoring."""
        pass

    def on_stop_request(self) -> None:
        """No-op — stop handling is bridged by TimerStopSubscriber in akgentic-team."""
        pass

    def on_stop(self) -> None:
        """Called when orchestrator stops."""
        pass


# =============================================================================
# STEP 7: Custom UserProxy for simulated human approval
# =============================================================================


class SimulatedUserProxy(UserProxy):
    """UserProxy that simulates human approval for demo purposes.

    This demonstrates extending UserProxy for custom workflows.
    In real applications, override receiveMsg_UserMessage to integrate
    with UI systems (WebSocket, REST API, CLI, etc.).
    """

    def receiveMsg_ReviewRequest(self, message: ReviewRequest, sender: ActorAddress) -> None:
        """Handle review request by simulating human approval.

        Args:
            message: ReviewRequest from CoordinatorAgent.
            sender: CoordinatorAgent address.
        """
        print("[UserProxy] Awaiting human input for review...")
        print("[UserProxy] Human approved the draft")

        # Simulate human approval
        approval = ApprovalResponse(
            approved=True,
            feedback="Draft looks good! Ready to publish.",
            draft_id=str(message.draft.id),
        )

        # Send approval back to coordinator
        self.send(sender, approval)


# =============================================================================
# STEP 8: Main execution - orchestrate all agents
# =============================================================================


def main() -> None:
    """Run the multi-agent coordination example."""
    print("[Multi-Agent] Starting multi-agent coordination demo...")

    actor_system = ActorSystem()

    try:
        # Create Orchestrator to track all telemetry
        orchestrator_addr = actor_system.createActor(
            Orchestrator,
            config=BaseConfig(name="orchestrator", role="Orchestrator"),
        )

        # Subscribe a simple logger to demonstrate the subscriber pattern
        orch_proxy = actor_system.proxy_ask(orchestrator_addr, Orchestrator)
        logger = SimpleLogger()
        orch_proxy.subscribe(logger)

        # Create specialized agents via orchestrator — team_id, orchestrator, parent all auto-propagated
        research_addr = orch_proxy.createActor(
            ResearchAgent, config=BaseConfig(name="research", role="ResearchAgent")
        )
        writer_addr = orch_proxy.createActor(
            WriterAgent, config=BaseConfig(name="writer", role="WriterAgent")
        )

        # Create UserProxy for human-in-the-loop
        user_proxy_addr = orch_proxy.createActor(
            SimulatedUserProxy, config=BaseConfig(name="human", role="UserProxy")
        )

        # Create and configure CoordinatorAgent
        coordinator_addr = orch_proxy.createActor(
            CoordinatorAgent, config=BaseConfig(name="coordinator", role="CoordinatorAgent")
        )

        # Set agent references in coordinator (for routing)
        coordinator_proxy = actor_system.proxy_tell(coordinator_addr, CoordinatorAgent)
        coordinator_proxy.set_agents(research_addr, writer_addr, user_proxy_addr)

        # Wait for agent initialization
        time.sleep(0.3)

        print(
            "[Orchestrator] Team assembled: CoordinatorAgent, ResearchAgent, WriterAgent, UserProxy"
        )

        # Send initial task request
        actor_system.tell(
            coordinator_addr,
            TaskRequest(topic="Write a summary of actor model benefits", requester_id="system"),
        )

        # Wait for the workflow to complete
        time.sleep(1.5)

        # Query orchestrator for telemetry
        team = orch_proxy.get_team()
        messages = orch_proxy.get_messages()
        states = orch_proxy.get_states()

        # Print summary
        print("")
        print("=== Orchestrator Summary ===")
        print(f"Total messages: {len(messages)}")
        print(f"Team members: {len(team)} agents")
        print(f"State snapshots: {len(states)} agents tracked")
        print("===========================")

        print("[Multi-Agent] Demo complete. Shutting down.")

    finally:
        actor_system.shutdown(timeout=5)


if __name__ == "__main__":
    main()
