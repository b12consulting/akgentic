"""Orchestrator agent for workflow coordination and telemetry tracking.

This module provides an Orchestrator agent that manages workflow coordination
and telemetry without external dependencies. It tracks agent lifecycle events,
message flows, and state changes using in-memory storage.
"""

import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Protocol, override

from pydantic import Field

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import (
    ErrorMessage,
    EventMessage,
    ProcessedMessage,
    ReceivedMessage,
    SentMessage,
    StartMessage,
    StateChangedMessage,
    StopMessage,
)
from akgentic.core.utils.serializer import SerializableBaseModel

logger = logging.getLogger(__name__)

TIMER_DELAY = 3600  # 1 hour default inactivity timeout


class Timer:
    """Helper class for inactivity timeout management.

    Tracks active tasks and triggers a timeout callback after a configurable
    delay when the orchestrator becomes idle (task_count reaches 0).

    The timer automatically cancels itself when tasks are active and restarts
    when the orchestrator becomes idle again.

    Args:
        delay: Seconds of inactivity before timeout_callback is invoked.
        timeout_callback: Zero-argument callable invoked on timeout.

    Example:
        >>> def on_timeout():
        ...     print("Timed out!")
        >>> timer = Timer(delay=60, timeout_callback=on_timeout)
        >>> timer.start()
        >>> timer.task_started()   # pauses countdown
        >>> timer.task_completed() # restarts countdown
        >>> timer.cancel()         # prevents callback from firing
    """

    def __init__(self, delay: int, timeout_callback: Callable[[], None]) -> None:
        self.delay = delay
        self.timeout_callback = timeout_callback
        self.task_count: int = 0
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        """Start or restart the countdown timer.

        Cancels any existing timer before starting a new one.
        """
        self.cancel()
        self._timer = threading.Timer(self.delay, self.timeout_callback)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        """Cancel the current timer, preventing the callback from firing."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def task_started(self) -> None:
        """Increment task count and cancel timer while tasks are active."""
        self.task_count += 1
        if self.task_count > 0:
            self.cancel()

    def task_completed(self) -> None:
        """Decrement task count and restart timer when orchestrator becomes idle."""
        self.task_count -= 1
        if self.task_count <= 0:
            self.task_count = 0  # Prevent negative count
            self.start()


class Event(SerializableBaseModel):
    event: object = Field(..., description="Domain event object")


class EventSubscriber(Protocol):
    """Protocol for subscribing to orchestrator events.

    Implementations can provide custom handling for workflow events such as
    Redis publishing, WebSocket streaming, or database persistence.

    Phase 3 implementations:
        - RedisEventSubscriber: Publishes events to Redis streams
        - WebSocketEventSubscriber: Streams events to WebSocket clients
        - PostgresEventSubscriber: Persists events to PostgreSQL
    """

    def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001
        """Toggle restore-replay guard.

        Called by ``TeamManager.resume_team()`` before and after replaying
        persisted events. Each subscriber decides independently whether to
        skip processing during restore. Default implementation is a no-op.

        Args:
            restoring: ``True`` when replay starts, ``False`` when it ends.
        """
        ...

    def on_stop_request(self) -> None:
        """Called when an orchestrator makes a stop request (e.g. inactivity timer fires).

        Default implementation is a no-op — subscribers (e.g. ``TimerStopSubscriber``
        in ``akgentic-team``) override this to decide how to actually stop the
        team. The orchestrator itself does NOT stop on this signal; shutdown is
        the subscriber's responsibility.
        """
        ...

    def on_stop(self) -> None:
        """Called when an orchestrator stops."""
        ...

    def on_message(self, msg: Message) -> None:
        """Called when an agent life-cycle message is received:
            - StartMessage
            - StopMessage
            - SentMessage
            - ReceivedMessage
            - ProcessedMessage
            - ErrorMessage
            - StateChangedMessage
            - EventMessage

        Args:
            msg: Orchestrator telemetry message
        """
        ...


class Orchestrator(Akgent[BaseConfig, BaseState]):
    """Orchestrator agent for workflow coordination and telemetry tracking.

    The Orchestrator manages workflow coordination and telemetry tracking without
    external dependencies. It maintains in-memory storage of:
        - Message history (all telemetry events)
        - Per-agent state snapshots
        - Per-agent LLM context
        - Per-tool state tracking
        - Team roster (computed from message history)

    The Orchestrator uses a subscriber pattern to enable extensibility. Subscribers
    can implement custom event handling for features like Redis publishing, WebSocket
    streaming, or database persistence.

    Attributes:
        messages: Complete message history (all telemetry events)
        state_dict: Per-agent state snapshots (keyed by agent_id string)
        llm_context_dict: Per-agent LLM context (keyed by agent_id string)
        tool_state_dict: Per-tool state tracking
        subscribers: List of event subscribers for extensibility

    Example:
        >>> system = ActorSystem.start().proxy()
        >>> orchestrator_ref = system.create_agent(
        ...     Orchestrator,
        ...     config=BaseConfig(name="orchestrator", role="Orchestrator")
        ... )
        >>> # Agents automatically send telemetry to orchestrator
        >>> messages = orchestrator_ref.get_messages().get()
    """

    @override
    def on_start(self) -> None:
        """Initialize the Orchestrator with empty in-memory state.

        The inactivity timer delay is configurable via the
        ``ORCHESTRATOR_TIMEOUT_DELAY`` environment variable (seconds).
        Defaults to 3600 seconds (1 hour) when the variable is not set.

        Args:
            config: Base configuration for the agent
            **kwargs: Additional keyword arguments passed to parent Akgent class
        """
        self._orchestrator = self.myAddress

        # Message history
        self.messages: list[Message] = []

        # Per-agent state tracking
        self.state_dict: dict[str, BaseState] = {}

        # Agent profile catalog (keyed by role)
        self.agent_cards: dict[str, AgentCard] = {}

        # Team roster cache
        self._current_team_members: list[ActorAddress] | None = None

        # Shutdown flag
        self._stopping: bool = False

        # Event subscribers for Phase 3 extensibility
        self.subscribers: list[EventSubscriber] = []

        # Inactivity timer — configurable via env var, defaults to TIMER_DELAY
        timer_delay = int(os.environ.get("ORCHESTRATOR_TIMEOUT_DELAY", str(TIMER_DELAY)))
        self._timer = Timer(delay=timer_delay, timeout_callback=self._timeout_handler)
        self._timer.start()

        # Notify orchestrator of its own startup
        start_message = StartMessage(config=self.config)
        start_message.init(self.myAddress, self.team_id)
        self.receiveMsg_StartMessage(start_message, self.myAddress)

    @override
    def on_stop(self) -> None:
        self._notify_subscribers("on_stop")
        super().on_stop()
        logger.info(f">>> [{self.config.name}] Stopped !")

    @override
    def _notify_orchestrator(self, message: Message) -> None:
        """Override to directly append orchestrator's own messages without telemetry cascade."""
        pass

    def _timeout_handler(self) -> None:
        """Handle inactivity timeout by stopping the orchestrator.

        Logs the timeout event with the team ID from config, then sends
        a ``StopRecursively`` message to self to trigger graceful shutdown.
        Any exception during the stop is caught and logged to prevent the
        timer thread from crashing silently.
        """
        team_id = self.team_id
        logger.info(f"Orchestrator timeout after {self._timer.delay}s inactivity (team={team_id})")
        self._notify_subscribers("on_stop_request")

    def get_timer(self) -> Timer:
        """Return the inactivity Timer instance (for testing and introspection).

        Returns:
            The Timer managing inactivity-based shutdown.
        """
        return self._timer

    def subscribe(self, subscriber: EventSubscriber) -> None:
        """Add an event subscriber to receive orchestrator events.

        Args:
            subscriber: Subscriber implementing EventSubscriber protocol
        """
        self.subscribers.append(subscriber)

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """Remove an event subscriber. No-op if not registered (idempotent).

        Args:
            subscriber: Subscriber to remove from notification list
        """
        try:
            self.subscribers.remove(subscriber)
        except ValueError:
            pass

    @staticmethod
    def snapshot_for_subscribers(message: Message) -> Message:
        """Create a subscriber-safe copy with serialized actor addresses.

        Delegates to :func:`akgentic.core.utils.snapshot_addresses` which
        recursively walks Pydantic model fields and replaces every live
        ``ActorAddressImpl`` with an ``ActorAddressProxy`` snapshot.
        """
        from akgentic.core.utils import snapshot_addresses

        return snapshot_addresses(message)  # type: ignore[return-value]

    def _notify_subscribers(self, event_method: str, message: Message | None = None) -> None:
        """Unified subscriber notification with fault tolerance.

        Args:
            event_method: Name of the subscriber method to call
            message: Message to pass to subscriber
        """
        if message is not None:
            message = self.snapshot_for_subscribers(message)
        for subscriber in self.subscribers:
            try:
                method = getattr(subscriber, event_method)
                if message is None:
                    method()
                else:
                    method(message)
            except Exception as e:
                logger.error(
                    f"Subscriber {subscriber.__class__.__name__} failed {event_method}: {e}"
                )

    def receiveMsg_StartMessage(self, message: StartMessage, sender: ActorAddress) -> None:
        """Handle agent start events.

        Args:
            message: StartMessage from agent
            sender: ActorAddress of sending agent
        """
        self.messages.append(message)
        self._current_team_members = None  # Clear cache
        self._notify_subscribers("on_message", message)

    def receiveMsg_StopMessage(self, message: StopMessage, sender: ActorAddress) -> None:
        """Handle agent stop events.

        Skips recording if orchestrator is shutting down (_stopping flag).

        Args:
            message: StopMessage from agent
            sender: ActorAddress of sending agent
        """
        if self._stopping:
            # Don't record StopMessages during orchestrator shutdown
            return

        self.messages.append(message)
        self._current_team_members = None  # Clear cache
        self._notify_subscribers("on_message", message)

    def receiveMsg_SentMessage(self, message: SentMessage, sender: ActorAddress) -> None:
        """Handle message sent events.

        Args:
            message: SentMessage containing sent message details
            sender: ActorAddress of sending agent
        """
        # Skip orchestrator's own telemetry to avoid recursion
        if sender == self.myAddress:
            return
        self.messages.append(message)
        self._notify_subscribers("on_message", message)

    def receiveMsg_ReceivedMessage(self, message: ReceivedMessage, sender: ActorAddress) -> None:
        """Handle message received events.

        Args:
            message: ReceivedMessage containing received message details
            sender: ActorAddress of sending agent
        """
        # Skip orchestrator's own telemetry to avoid recursion
        if sender == self.myAddress:
            return
        self._timer.task_started()
        self.messages.append(message)
        self._notify_subscribers("on_message", message)

    def receiveMsg_ProcessedMessage(self, message: ProcessedMessage, sender: ActorAddress) -> None:
        """Handle message processed events.

        Args:
            message: ProcessedMessage containing processing completion details
            sender: ActorAddress of sending agent
        """
        # Skip orchestrator's own telemetry to avoid recursion
        if sender == self.myAddress:
            return
        self._timer.task_completed()
        self.messages.append(message)
        self._notify_subscribers("on_message", message)

    def receiveMsg_ErrorMessage(self, message: ErrorMessage, sender: ActorAddress) -> None:
        """Handle error events.

        Args:
            message: ErrorMessage containing error details
            sender: ActorAddress of sending agent
        """
        # Skip orchestrator's own telemetry to avoid recursion
        if sender == self.myAddress:
            return
        self.messages.append(message)
        self._notify_subscribers("on_message", message)

    def receiveMsg_StateChangedMessage(
        self, message: StateChangedMessage, sender: ActorAddress
    ) -> None:
        """Handle agent state change events.

        Args:
            message: StateChangedMessage containing updated state
            sender: ActorAddress of sending agent
        """
        self.state_dict[str(sender.agent_id)] = message.state
        self._notify_subscribers("on_message", message)

    def receiveMsg_EventMessage(self, message: EventMessage, sender: ActorAddress) -> None:
        """Handle agent event message.

        Args:
            message: EventMessage containing the event type and payload
            sender: ActorAddress of sending agent
        """
        self.messages.append(message)
        self._notify_subscribers("on_message", message)

    def restore_message(self, message: Message) -> None:
        """Replay a single persisted event during team restoration.

        Appends the message to ``self.messages`` so that ``get_team()`` and
        other history-based queries work correctly after restore, then
        dispatches to all subscribers via ``_notify_subscribers``.

        Args:
            message: The persisted message to replay.
        """
        self.messages.append(message)
        self._current_team_members = None  # Invalidate cache
        self._notify_subscribers("on_message", message)

    def end_restoration(self) -> None:
        """Signal that restoration replay is complete.

        Sets ``_restoring`` to ``False`` so the orchestrator resumes normal
        operation (e.g. recording telemetry, processing live messages).
        """
        self._restoring = False
        logger.info("Orchestrator restoration complete, resuming normal operation")

    def getChildrenOrCreate(  # noqa: N802
        self, actor_class: type[Akgent[Any, Any]], config: BaseConfig
    ) -> ActorAddress:
        """Return an existing live child by name, or create a new one.

        Uses the synchronous ``_children`` list rather than
        ``get_team_member()`` (which depends on the asynchronous
        ``StartMessage`` arrival) to avoid race conditions when
        multiple callers request the same singleton concurrently.

        Args:
            actor_class: Class of the agent to instantiate.
            config: Configuration for the new agent.
        Returns:
            ActorAddress of the existing child or newly created agent.
        """
        for child in self._children:
            if child.is_alive() and child.name == config.name:
                return child
        return self.createActor(actor_class, config=config)

    def get_team(self) -> list[ActorAddress]:
        """Get list of active agents (excludes Orchestrator role).

        Team is computed from message history: agents that sent StartMessage
        but not StopMessage. Result is cached and cleared when team membership changes.

        Returns:
            List of ActorAddress for active team members
        """
        if self._current_team_members is not None:
            return self._current_team_members

        # Compute from message history using comprehensions
        started_agentid_addr_dict = {
            str(msg.sender.agent_id): msg.sender
            for msg in self.messages
            if isinstance(msg, StartMessage)
            and msg.sender is not None
            and msg.sender.role != "Orchestrator"
        }
        stopped_agent_id_set = {
            str(msg.sender.agent_id)
            for msg in self.messages
            if isinstance(msg, StopMessage) and msg.sender is not None
        }

        # Active = started but not stopped
        active = [
            addr
            for aid, addr in started_agentid_addr_dict.items()
            if aid not in stopped_agent_id_set
        ]

        # Cache result
        self._current_team_members = active
        return active

    def get_team_member(self, name: str) -> ActorAddress | None:
        """Get a team member by name.

        Args:
            name: Agent name

        Returns:
            ActorAddress if found, None otherwise
        """
        return next(
            (mbr for mbr in self.get_team() if mbr.name == name),
            None,
        )

    def get_messages(
        self, sender: ActorAddress | None = None, message_type: type | None = None
    ) -> list[Message]:
        """Get messages from message history.

        Args:
            sender: Optional ActorAddress to filter by sender.
            message_type: Optional message type to filter by isinstance check.

        Returns:
            Filtered list of messages matching the given criteria,
            or all messages if no filters are provided.
        """
        if sender and message_type:
            return [
                msg
                for msg in self.messages
                if msg.sender == sender and isinstance(msg, message_type)
            ]
        elif sender:
            return [msg for msg in self.messages if msg.sender == sender]
        elif message_type:
            return [msg for msg in self.messages if isinstance(msg, message_type)]

        return self.messages

    def get_events(
        self,
        agent_id: str | None = None,
        event_class: type | None = None,
    ) -> list[EventMessage]:
        """Query stored EventMessages with optional filters.

        Args:
            agent_id: Filter by sender agent_id (optional).
            event_class: Filter by event payload class via isinstance (optional).

        Returns:
            List of matching EventMessage instances.
        """
        return [
            msg
            for msg in self.messages
            if isinstance(msg, EventMessage)
            and (
                agent_id is None
                or (msg.sender is not None and str(msg.sender.agent_id) == agent_id)
            )
            and (event_class is None or isinstance(msg.event, event_class))
        ]

    def get_states(self) -> dict[str, BaseState]:
        """Get all agent states tracked by orchestrator.

        Returns:
            Dictionary mapping agent_id (as string) to agent state
        """
        return self.state_dict

    def stop(self) -> None:
        """Override stop to cancel timer and set _stopping flag before shutdown.

        Cancels the inactivity timer first to prevent it from firing during
        shutdown, then sets the _stopping flag to suppress StopMessage recording,
        then calls the parent stop() method.
        """
        self._timer.cancel()
        self._stopping = True
        super().stop()

    # =============================================================================
    # Agent Profile Catalog Management
    # =============================================================================

    def register_agent_profile(self, card: AgentCard) -> None:
        """Register an agent profile in the team catalog.

        Args:
            card: AgentCard describing the profile
        """
        self.agent_cards[card.role] = card
        logger.info(f"[Orchestrator] Registered agent profile: {card.role}")

    def register_agent_profiles(self, cards: list[AgentCard]) -> None:
        """Register agent profiles in the team catalog.

        Args:
            card: AgentCard list describing the profile
        """
        for card in cards:
            self.register_agent_profile(card)

    def get_agent_catalog(self) -> list[AgentCard]:
        """Get all available agent profiles in the team catalog.

        Returns:
            List of all registered AgentCards
        """
        return list(self.agent_cards.values())

    def get_agent_profile(self, role: str) -> AgentCard | None:
        """Get a specific agent profile by role.

        Args:
            role: The role to look up (e.g., "ResearchAgent")

        Returns:
            AgentCard if found, None otherwise
        """
        return self.agent_cards.get(role)

    def get_profiles_by_skill(self, skill: str) -> list[AgentCard]:
        """Find all agent profiles that have a specific skill.

        Args:
            skill: Skill to search for (e.g., "web_search")

        Returns:
            List of AgentCards with that skill
        """
        return [card for card in self.agent_cards.values() if card.has_skill(skill)]

    def get_available_roles(self) -> list[str]:
        """Get list of all roles available in the catalog.

        Returns:
            List of role names
        """
        return list(self.agent_cards.keys())

    def get_available_skills(self) -> list[str]:
        """Get unique set of all skills across all profiles.

        Returns:
            Sorted list of unique skills
        """
        skills = set()
        for card in self.agent_cards.values():
            skills.update(card.skills)
        return sorted(skills)
