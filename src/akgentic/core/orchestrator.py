"""Orchestrator agent for workflow coordination and telemetry tracking.

This module provides an Orchestrator agent that manages workflow coordination
and telemetry without external dependencies. It tracks agent lifecycle events,
message flows, and state changes using in-memory storage.
"""

import logging
import os
import threading
import uuid
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

# Default grace period (seconds) for a non-blocking orchestrator stop before the
# backstop timer forces teardown (ADR-012 §2/§4). It is the single stop timeout:
# callers wait on the returned event with NO timeout, and the backstop guarantees
# the event is set within ~STOP_TIMEOUT seconds.
STOP_TIMEOUT = 30.0


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

    def set_restoring(self, team_id: uuid.UUID, restoring: bool) -> None:  # noqa: FBT001
        """Toggle restore-replay guard.

        Called by ``TeamManager.resume_team()`` before and after replaying
        persisted events. Each subscriber decides independently whether to
        skip processing during restore. Default implementation is a no-op.

        Args:
            team_id: ``team_id`` of the orchestrator triggering the notification,
                enabling per-team routing on shared subscriber instances.
            restoring: ``True`` when replay starts, ``False`` when it ends.
        """
        ...

    def on_stop_request(self, team_id: uuid.UUID) -> None:
        """Called when an orchestrator makes a stop request (e.g. inactivity timer fires).

        Default implementation is a no-op — subscribers (e.g. ``TimerStopSubscriber``
        in ``akgentic-team``) override this to decide how to actually stop the
        team. The orchestrator itself does NOT stop on this signal; shutdown is
        the subscriber's responsibility.

        Args:
            team_id: ``team_id`` of the orchestrator triggering the notification,
                enabling per-team routing on shared subscriber instances.
        """
        ...

    def on_stop(self, team_id: uuid.UUID) -> None:
        """Called when an orchestrator stops.

        Args:
            team_id: ``team_id`` of the orchestrator triggering the notification,
                enabling per-team routing on shared subscriber instances.
        """
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

    Load-bearing assumption — Pykka mailbox drain on self-stop:
        When an ``Orchestrator`` (or any ``Akgent``) calls ``self.stop()`` from
        inside one of its own ``receiveMsg_*`` handlers — the canonical shape
        every ``orchestrator_proxy.stop()`` graceful-stop call drives via
        ``Akgent.receiveMsg_StopRecursively`` → ``self.stop()`` — Pykka
        guarantees that the actor's mailbox is fully drained before ``on_stop``
        fires. Every queued message is processed to completion first;
        ``on_stop`` runs exactly once, afterwards.

        The dispatch path this invariant protects is
        ``self._notify_subscribers_lifecycle("on_stop", self.team_id)``, called
        from ``Orchestrator.on_stop`` BEFORE ``super().on_stop()``. Downstream
        subscribers (``RedisStreamSubscriber.on_stop`` and the rest of the
        master cross-package ADR-024 chain — see also package-local ADR-011)
        rely on the invariant: without it, an ``on_message`` → ``XADD`` could
        land *after* ``on_stop`` → ``DEL`` and resurface the very race ADR-024
        eliminates.

        Verification test:
        ``packages/akgentic-core/tests/core/test_pykka_mailbox_drain_on_self_stop.py``
        — ``test_pykka_drains_mailbox_before_on_stop_on_self_stop``. If a
        future Pykka upgrade regresses this guarantee, that test fails and
        surfaces the regression before the master ADR-024 chain breaks in
        production.
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

        # Non-blocking-stop completion signal + backstop timer (ADR-012 §2/§4).
        # Created lazily when stop() is first called; the backstop forces
        # finalization if a child wedges and the roster never empties.
        self._stop_event: threading.Event | None = None
        self._stop_backstop: threading.Timer | None = None

        # Tool actors (name prefixed "#") deferred to phase 2 of the stop
        # sequence (ADR-012 §2a): populated in phase 1 (reverse creation order)
        # and CLEARED once told, which is the fire-once guard preventing a re-tell
        # on every subsequent StopMessage. Tool actors stop only after every
        # non-tool agent has fully stopped, so a consumer still inside a handler
        # can never call a tool whose actor was already torn down.
        self._pending_tool_stops: list[ActorAddress] = []

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
        self._cancel_stop_backstop()
        self._notify_subscribers_lifecycle("on_stop", self.team_id)
        super().on_stop()
        self.subscribers.clear()
        # Release any caller blocked in stop().wait(). Event.set() is idempotent,
        # so the graceful path and the forced (backstop) path can both call it
        # with no guard flag (ADR-012 §4).
        if self._stop_event is not None:
            self._stop_event.set()
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
        self._notify_subscribers_lifecycle("on_stop_request", team_id)

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

    def _notify_subscribers_message(self, event_method: str, message: Message) -> None:
        """Dispatch an ``on_message`` notification (the only message-bearing hook).

        Snapshots actor addresses on the caller thread, then fans out to every
        subscriber. Per-subscriber exceptions are caught and logged so a single
        faulty subscriber cannot block the rest of the dispatch chain.

        Args:
            event_method: Name of the message-bearing subscriber method to call
                (today: ``"on_message"``).
            message: Message to forward to every subscriber after snapshotting.
        """
        snapshot = self.snapshot_for_subscribers(message)
        for subscriber in self.subscribers:
            try:
                getattr(subscriber, event_method)(snapshot)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Subscriber {subscriber.__class__.__name__} failed {event_method}: {e}"
                )

    def _notify_subscribers_lifecycle(
        self,
        event_method: str,
        team_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        """Dispatch a lifecycle notification (``set_restoring``, ``on_stop_request``, ``on_stop``).

        Every lifecycle hook carries ``team_id`` as its first positional
        argument so shared subscribers (one instance attached to N teams on a
        worker) can route cleanup per-team. Additional keyword arguments are
        forwarded to the subscriber method (e.g. ``restoring=True`` for
        ``set_restoring``). Per-subscriber exceptions are caught and logged so
        a single faulty subscriber cannot block the rest of the dispatch chain.

        Args:
            event_method: Name of the lifecycle subscriber method to call.
            team_id: ``team_id`` of the orchestrator triggering the notification,
                passed as the first positional argument to the subscriber method.
            **kwargs: Extra kwargs forwarded to the lifecycle method
                (e.g. ``restoring=True`` for ``set_restoring``).
        """
        for subscriber in self.subscribers:
            try:
                getattr(subscriber, event_method)(team_id, **kwargs)
            except Exception as e:  # noqa: BLE001
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
        self._notify_subscribers_message("on_message", message)

    def receiveMsg_StopMessage(self, message: StopMessage, sender: ActorAddress) -> None:
        """Handle agent stop events (ADR-012 §3).

        Always records the ``StopMessage`` and invalidates the team-roster cache
        so ``get_team()`` reflects each child stopping. During an in-progress
        non-blocking teardown (``_stopping``) this doubles as the completion
        signal: once the roster empties, the orchestrator finalizes via
        ``_finalize_stop()`` (event is set in ``on_stop``). Subscriber dispatch is
        suppressed while ``_stopping`` to preserve the XADD-before-DEL ordering
        and resume-history semantics (teardown ``StopMessage``s must stay out of
        the stream).

        Args:
            message: StopMessage from agent
            sender: ActorAddress of sending agent
        """
        self.messages.append(message)
        self._current_team_members = None  # Invalidate roster cache

        if self._stopping:
            # PHASE 2 gate (ADR-012 §2a): once the non-tool roster has emptied,
            # tell the deferred tool actors to stop (reverse creation order).
            self._maybe_stop_pending_tools()
            if not self.get_team():  # whole tree down (GC-safe identity — ADR-012 §5)
                self._finalize_stop()
            return  # suppress subscriber dispatch during teardown

        self._notify_subscribers_message("on_message", message)

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
        self._notify_subscribers_message("on_message", message)

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
        self._notify_subscribers_message("on_message", message)

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
        self._notify_subscribers_message("on_message", message)

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
        self._notify_subscribers_message("on_message", message)

    def receiveMsg_StateChangedMessage(
        self, message: StateChangedMessage, sender: ActorAddress
    ) -> None:
        """Handle agent state change events.

        Args:
            message: StateChangedMessage containing updated state
            sender: ActorAddress of sending agent
        """
        self.state_dict[str(sender.agent_id)] = message.state
        self._notify_subscribers_message("on_message", message)

    def receiveMsg_EventMessage(self, message: EventMessage, sender: ActorAddress) -> None:
        """Handle agent event message.

        Args:
            message: EventMessage containing the event type and payload
            sender: ActorAddress of sending agent
        """
        self.messages.append(message)
        self._notify_subscribers_message("on_message", message)

    def restore_message(self, message: Message) -> None:
        """Replay a single persisted event during team restoration.

        Appends the message to ``self.messages`` so that ``get_team()`` and
        other history-based queries work correctly after restore, then
        dispatches to all subscribers via ``_notify_subscribers_message``.

        Args:
            message: The persisted message to replay.
        """
        self.messages.append(message)
        self._current_team_members = None  # Invalidate cache
        self._notify_subscribers_message("on_message", message)

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

        # Compute from message history using comprehensions.
        # Exclude the orchestrator ITSELF by identity (agent_id), not by role
        # string: the orchestrator is never its own team member, and a
        # role-string filter both misses a self that carries a non-orchestrator
        # role and wrongly drops legitimate sub-orchestrator members.
        started_agentid_addr_dict = {
            str(msg.sender.agent_id): msg.sender
            for msg in self.messages
            if isinstance(msg, StartMessage)
            and msg.sender is not None
            and msg.sender.agent_id != self.agent_id
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

    def stop(self, grace_timeout: float = STOP_TIMEOUT) -> threading.Event:  # type: ignore[override]
        """Initiate a non-blocking, recursive team teardown (ADR-012 §2).

        Tells every child to stop, then returns IMMEDIATELY without waiting — the
        orchestrator's actor thread stays free to answer reentrant asks
        (``get_team()``) while the team drains bottom-up. This is precisely what
        makes stop deadlock-proof: the orchestrator never blocks on a child, so a
        child that re-enters the orchestrator mid-message can always be served.

        The returned :class:`threading.Event` is the completion signal. It is
        **guaranteed** to be set within ~``grace_timeout`` seconds, one of two
        ways:

        * **gracefully** — the moment the last agent has stopped (the common
          case, typically milliseconds); or
        * **forcibly** — if a child wedges and the team never reaches quiescence,
          the internal backstop timer fires at ``grace_timeout`` and tears the
          orchestrator down anyway (logs a WARNING; the wedged child is reaped
          later by ``ActorRegistry.stop_all()``).

        To block until the team is fully stopped, wait on the event with **no
        timeout** — it cannot hang, because the backstop bounds it::

            orchestrator.stop(grace_timeout).wait()    # returns within ~grace

        Calling ``stop()`` and ignoring the event is a valid fire-and-forget
        teardown. Idempotent: calling ``stop()`` again while already stopping
        returns the same event and does not re-tell the children.

        Args:
            grace_timeout: Seconds to allow for graceful quiescence before the
                backstop forces teardown. This is the ONLY stop timeout. Callers
                do NOT pass a separate wait timeout: a wait shorter than this
                could only ever yield a spurious failure, and a longer one is
                redundant (the event is already guaranteed to set by
                ``grace_timeout``).

        Returns:
            A ``threading.Event`` set once the orchestrator is fully stopped —
            mailbox drained, subscribers' ``on_stop`` fired, actor deregistered.
        """
        if self._stopping:
            # Idempotent — same event, no re-tell of children.
            assert self._stop_event is not None
            return self._stop_event

        self._timer.cancel()  # no inactivity auto-stop mid-teardown
        self._stopping = True
        self._stop_event = threading.Event()
        self._stop_non_tool_children()  # PHASE 1: tell non-tool children; defer tools (§2a)
        # Kick phase 2 now in case there are NO non-tool agents to wait for (a
        # tools-only team): no StopMessage would ever arrive to drive the gate,
        # so tell the tool actors straight away. A no-op when non-tool agents
        # exist (the roster still has them) — the gate then fires on their stops.
        self._maybe_stop_pending_tools()
        self._arm_stop_backstop(grace_timeout)  # guarantees the event sets (§4)
        if not self.get_team():  # zero-agent team: nothing will report
            self._finalize_stop()
        return self._stop_event

    @staticmethod
    def _is_tool_actor(addr: ActorAddress) -> bool:
        """A tool actor is identified by the ``#`` name-prefix convention.

        Tool actors (``#VectorStore``, ``#PlanningTool``, ``#KnowledgeGraphTool``,
        ``#SandboxActor``, …) back the tools other agents call mid-handler; they
        are stopped in phase 2, after every non-tool agent (ADR-012 §2a).
        """
        return addr.name.startswith("#")

    def _live_children_reversed(self) -> list[ActorAddress]:
        """Live children in REVERSE creation order.

        ``_children`` preserves creation order (append-only in ``createActor``).
        Tool actors are created in dependency order by ``ToolFactory``'s
        topological sort (a prerequisite like ``#VectorStore`` is created before
        its consumers ``#PlanningTool``/``#KnowledgeGraphTool``). Reversing that
        order therefore stops consumers before their dependency — no runtime
        ``depends_on`` lookup needed. INVARIANT: creation order encodes stop
        order; do not reorder ``_children`` or drop the topological sort.
        """
        return [child for child in reversed(self._children) if child.is_alive()]

    def _stop_non_tool_children(self) -> None:
        """PHASE 1 of the stop sequence (ADR-012 §2a).

        Tells every NON-tool child to stop (reverse creation order) and stashes
        the tool actors in ``_pending_tool_stops`` (also reverse-ordered) for
        phase 2. Tool actors stay alive here, so a consumer agent finishing its
        in-flight handler can still invoke its tool.
        """
        reversed_live = self._live_children_reversed()
        self._pending_tool_stops = [c for c in reversed_live if self._is_tool_actor(c)]
        for child in reversed_live:
            if not self._is_tool_actor(child):
                self.proxy_tell(child, Akgent).stop()  # non-blocking tell

    def _maybe_stop_pending_tools(self) -> None:
        """PHASE 2 of the stop sequence (ADR-012 §2a).

        Once no non-tool agent remains in the roster (every non-tool subtree has
        emitted its ``StopMessage`` — the "fully stopped" signal), tell the
        deferred tool actors to stop, in the reverse creation order captured in
        phase 1 (consumers before their dependency, e.g. ``#PlanningTool`` before
        ``#VectorStore``). Fires exactly once — guarded by ``_pending_tool_stops``.

        The roster gate reads ``get_team()`` (whole-tree, telemetry-derived) so a
        consumer's grandchildren must also be down before tools stop. Never blocks.
        """
        if not self._pending_tool_stops:
            return
        if any(not self._is_tool_actor(member) for member in self.get_team()):
            return  # non-tool agents still alive — wait for the next StopMessage
        for child in self._pending_tool_stops:  # already reverse-ordered (phase 1)
            if child.is_alive():
                self.proxy_tell(child, Akgent).stop()
        self._pending_tool_stops = []

    def _arm_stop_backstop(self, grace_timeout: float) -> None:
        """Arm the backstop timer that forces teardown after ``grace_timeout``."""
        self._stop_backstop = threading.Timer(grace_timeout, self._force_stop)
        self._stop_backstop.daemon = True
        self._stop_backstop.start()

    def _cancel_stop_backstop(self) -> None:
        """Cancel the backstop timer if armed (idempotent)."""
        if self._stop_backstop is not None:
            self._stop_backstop.cancel()
            self._stop_backstop = None

    def _finalize_stop(self) -> None:
        """Stop the orchestrator actor itself WITHOUT re-running stop_children.

        ``super().stop()`` == ``Akgent.stop()`` == blocking ``stop_children``, which
        hangs on a child that is alive-but-mid-``on_stop``. The native ref stop
        enqueues ``_ActorStop``; the mailbox drains and ``on_stop`` runs (setting
        the event). Cancels the backstop so it cannot fire spuriously after a
        graceful finalize.
        """
        self._cancel_stop_backstop()
        self.actor_ref.stop(block=False)

    def _force_stop(self) -> None:
        """Force finalization from the backstop Timer thread (ADR-012 §4).

        Runs on the Timer thread, NOT the actor thread. Uses the native
        ``actor_ref.stop(block=False)`` — NOT ``super().stop()`` — so it does not
        re-enter ``stop_children`` and block on the very child that wedged. The
        native stop enqueues ``_ActorStop``; the orchestrator's own actor thread
        then drains its mailbox and runs ``on_stop`` (which sets the event).
        """
        if self.actor_ref.is_alive():
            logger.warning(
                "Orchestrator stop timed out (team=%s); forcing teardown", self.team_id
            )
            # A non-tool agent wedged before phase 2 fired, so the tool actors may
            # still be un-told. Flush their stop tells (reverse order) for a last
            # graceful chance before forcing teardown; any that wedge are reaped by
            # ActorRegistry.stop_all() (ADR-012 §2a/§4). Non-blocking — the backstop
            # must not block on a wedged child either.
            for child in self._pending_tool_stops:
                if child.is_alive():
                    self.proxy_tell(child, Akgent).stop()
            self._pending_tool_stops = []
            self.actor_ref.stop(block=False)

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
