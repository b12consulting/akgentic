"""Context management with checkpointing and compactification."""

import copy
import hashlib
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
)

from akgentic.llm.event import (
    ContextObserver,
    LlmCheckpointCreatedEvent,
    LlmCheckpointRestoredEvent,
    LlmMessageEvent,
    LlmSystemPromptEvent,
    LlmUsageEvent,
    SystemPromptPartSnapshot,
    ToolCallEvent,
    ToolReturnEvent,
)


def _is_system_message(msg: ModelMessage) -> bool:
    """Check if a message is a system message.

    System messages contain SystemPromptPart and should be preserved
    during sliding window operations.

    Args:
        msg: Message to check

    Returns:
        True if message is a system message
    """
    return isinstance(msg, ModelRequest) and any(
        isinstance(part, SystemPromptPart) for part in msg.parts
    )


class ContextSnapshot(BaseModel):
    """Immutable snapshot of conversation context.

    Used for checkpoint/rewind functionality. Messages are deep-copied
    to ensure immutability.

    Attributes:
        checkpoint_id: Unique checkpoint identifier
        timestamp: When the checkpoint was created
        messages: Deep copy of messages at checkpoint
        metadata: Optional custom metadata
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    checkpoint_id: str = Field(..., description="Unique checkpoint identifier")
    timestamp: datetime = Field(..., description="When checkpoint was created")
    # FIXME: Using Any instead of list[ModelMessage] due to pydantic-ai 1.60.0 bug
    # pydantic-ai's ModelMessage dataclasses contain forward refs with AliasChoices
    # that cause Pydantic schema generation to fail. Should either:
    # 1. Convert ContextSnapshot to @dataclass(frozen=True) to avoid Pydantic validation
    # 2. Wait for pydantic-ai fix and restore proper type: list[ModelMessage]
    messages: Any = Field(..., description="Deep copy of messages (list[ModelMessage])")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Custom metadata")


class ContextManager:
    """Manages LLM conversation context with checkpointing.

    Features:
    - Message history tracking
    - Observer pattern for notifications
    - Checkpoint/rewind support
    - Sliding window with system message preservation

    This implementation replicates V1's base_agent.py context management
    with additional checkpoint functionality.

    Observer Behavior:
    - Observers are notified synchronously
    - Exceptions from observers propagate to caller
    - Use try/except in observer methods if exceptions should not interrupt operations

    Example:
        >>> from akgentic.llm import ContextManager
        >>> from pydantic_ai.messages import ModelRequest, UserPromptPart
        >>>
        >>> manager = ContextManager(max_messages=10)
        >>> manager.add_message(ModelRequest(parts=[UserPromptPart(content="Hello")]))
        >>> snapshot = manager.checkpoint("before-llm-call")
        >>> # ... LLM interaction ...
        >>> manager.rewind("before-llm-call")  # Restore if needed
    """

    def __init__(
        self,
        max_messages: int | None = None,
    ) -> None:
        """Initialize context manager.

        Args:
            max_messages: Maximum messages to keep (None = unlimited).
                System messages are always preserved.

        Raises:
            ValueError: If max_messages is negative.
        """
        if max_messages is not None and max_messages < 0:
            raise ValueError(f"max_messages must be non-negative, got {max_messages}")
        self._max_messages = max_messages
        self._messages: list[ModelMessage] = []
        self._checkpoints: dict[str, ContextSnapshot] = {}
        self._checkpoint_order: list[str] = []
        self._observers: list[ContextObserver] = []
        self._last_system_prompt_hash: str | None = None
        self._pending_operator_actions: list[str] = []

    @property
    def messages(self) -> list[ModelMessage]:
        """Get current message history.

        Returns a shallow copy to prevent external mutation.

        Returns:
            Copy of current messages
        """
        return list(self._messages)

    def _notify(self, event: object) -> None:
        """Notify all observers with a domain event.

        Args:
            event: Domain event to broadcast
        """
        for observer in self._observers:
            observer.notify_event(event)

    def add_message(self, message: ModelMessage) -> None:
        """Add a message to context.

        Appends message, applies sliding window if configured,
        and notifies observers. Emits ``LlmMessageEvent`` first, then
        any tool-related events (``ToolCallEvent``, ``ToolReturnEvent``),
        and finally ``LlmUsageEvent`` for ``ModelResponse`` messages with usage data.

        Args:
            message: Message to add
        """
        self._messages.append(message)
        self._apply_window()
        self._notify(LlmMessageEvent(message=message))
        self._emit_tool_events(message)
        self._emit_usage_event(message)

    def record_operator_action(self, entry: str) -> None:
        """Record a human operator-action entry, respecting pydantic-ai's first-run rule.

        pydantic-ai injects its registered ``@system_prompt`` functions only when
        the ``message_history`` it is handed arrives empty (``if not messages``).
        It never *adds* a missing system prompt to a non-empty history. That
        couples how an operator action may be recorded to whether the run buffer
        has been materialized yet:

        - **After the first run** a system-bearing ``ModelRequest`` already exists
          in ``_messages``, so appending a bare user-role ``ModelRequest`` is safe:
          it emits an ``LlmMessageEvent`` and becomes visible in the next run's
          history without affecting injection.
        - **Before the first run** ``_messages`` is empty. Appending a system-less
          ``ModelRequest`` here would make the next run's ``message_history``
          non-empty and **suppress** system-prompt injection, leaving the agent's
          first turn blind to its backstory/date/roster/mailbox. So the entry is
          buffered instead, and ``ReactAgent.run`` folds it into the next run's
          ``user_prompt`` (keeping ``message_history`` empty).

        Args:
            entry: The fully formatted operator-action text to record.
        """
        if self._messages:
            self.add_message(ModelRequest(parts=[UserPromptPart(content=entry)]))
        else:
            self._pending_operator_actions.append(entry)

    def drain_pending_operator_actions(self) -> list[str]:
        """Return buffered pre-first-run operator actions and clear the buffer.

        Returns the buffered entries in record order, then resets the buffer to
        empty. ``ReactAgent.run`` calls this to fold the entries into the next
        run's ``user_prompt`` so they reach the model without suppressing
        system-prompt injection (see ``record_operator_action``).

        Returns:
            The buffered operator-action entries, in record order. Empty when
            nothing has been buffered.
        """
        pending = self._pending_operator_actions
        self._pending_operator_actions = []
        return pending

    def _emit_tool_events(self, message: ModelMessage) -> None:
        """Emit ToolCallEvent or ToolReturnEvent for tool-related message parts.

        Called after LlmMessageEvent is emitted. LlmMessageEvent ordering is guaranteed.
        Gracefully handles messages with no parts attribute.

        Args:
            message: Message whose parts are inspected for tool activity
        """
        run_id = str(message.run_id)
        for part in getattr(message, "parts", []):
            match part.part_kind:
                case "tool-call":
                    self._notify(
                        ToolCallEvent(
                            run_id=run_id,
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            arguments=(
                                part.args
                                if isinstance(part.args, str)
                                else part.args_as_json_str()
                            ),
                        )
                    )
                case "tool-return":
                    self._notify(
                        ToolReturnEvent(
                            run_id=run_id,
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            success=True,
                        )
                    )
                case "retry-prompt" if part.tool_name is not None:
                    self._notify(
                        ToolReturnEvent(
                            run_id=run_id,
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            success=False,
                        )
                    )

    def _emit_usage_event(self, message: ModelMessage) -> None:
        """Emit LlmUsageEvent for ModelResponse messages with usage data."""
        if getattr(message, "kind", None) != "response":
            return
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        self._notify(
            LlmUsageEvent(
                run_id=str(getattr(message, "run_id", None) or ""),
                model_name=getattr(message, "model_name", None) or "",
                provider_name=getattr(message, "provider_name", None) or "",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                requests=usage.requests,
            )
        )

    def record_system_prompt(self, run_id: str) -> None:
        """Capture the effective system prompt for a run; emit if it changed.

        Scans the first ``ModelRequest`` in context for ``SystemPromptPart``s
        (pydantic-ai re-evaluates dynamic parts in place before each model call),
        hashes the ordered ``(dynamic_ref, content)`` sequence, and emits an
        ``LlmSystemPromptEvent`` only when the hash differs from the last
        recorded one. Implements ADR-004 §2.

        No event is emitted when there is no first ``ModelRequest``, when it has
        no ``SystemPromptPart``s, or when the rendering is unchanged since the
        last recorded hash. See ADR-004 for the dedup rationale.

        Args:
            run_id: The ReactAgent run ID this rendering belongs to.
        """
        snapshots = self._snapshot_system_parts()
        if not snapshots:
            return
        content_hash = self._hash_parts(snapshots)
        if content_hash == self._last_system_prompt_hash:
            return
        self._notify(LlmSystemPromptEvent(run_id, tuple(snapshots), content_hash))
        self._last_system_prompt_hash = content_hash

    def seed_system_prompt_hash(self, content_hash: str | None) -> None:
        """Seed the dedup hash without notifying observers.

        Mirrors the ``restore`` contract: load persisted dedup state so an
        unchanged rendering does not re-emit after restoration, without firing
        observer events. Implements ADR-004 §3.

        Args:
            content_hash: The ``content_hash`` of the latest persisted
                ``LlmSystemPromptEvent``, or ``None`` to reset dedup state.
        """
        self._last_system_prompt_hash = content_hash

    def _snapshot_system_parts(self) -> list[SystemPromptPartSnapshot]:
        """Snapshot the first ModelRequest's system parts, in model order.

        Returns:
            One snapshot per ``SystemPromptPart`` on the first ``ModelRequest``,
            in part order. Empty if there is no first ``ModelRequest`` or it has
            no system parts.
        """
        first_request = next(
            (m for m in self._messages if isinstance(m, ModelRequest)), None
        )
        if first_request is None:
            return []
        return [
            SystemPromptPartSnapshot(dynamic_ref=part.dynamic_ref, content=part.content)
            for part in first_request.parts
            if part.part_kind == "system-prompt"
        ]

    @staticmethod
    def _hash_parts(snapshots: list[SystemPromptPartSnapshot]) -> str:
        """Compute a stable, order-sensitive sha256 hex digest over snapshots.

        Each ``(dynamic_ref, content)`` pair is encoded with a length prefix so
        distinct sequences cannot collide on the same byte string. ``None`` and
        the empty string for ``dynamic_ref`` are disambiguated by a leading
        marker byte.

        Args:
            snapshots: System part snapshots, in model order.

        Returns:
            sha256 hex digest of the encoded sequence.
        """
        hasher = hashlib.sha256()
        for snap in snapshots:
            if snap.dynamic_ref is None:
                hasher.update(b"0")
            else:
                ref_bytes = snap.dynamic_ref.encode("utf-8")
                hasher.update(b"1")
                hasher.update(str(len(ref_bytes)).encode("ascii"))
                hasher.update(b":")
                hasher.update(ref_bytes)
            content_bytes = snap.content.encode("utf-8")
            hasher.update(str(len(content_bytes)).encode("ascii"))
            hasher.update(b":")
            hasher.update(content_bytes)
        return hasher.hexdigest()

    def _apply_window(self) -> None:
        """Apply sliding window to messages.

        Keeps most recent max_messages while preserving all system messages.
        System messages don't count toward the limit.
        """
        if self._max_messages is None or len(self._messages) <= self._max_messages:
            return

        # Separate system and non-system messages
        system_msgs = [m for m in self._messages if _is_system_message(m)]
        non_system = [m for m in self._messages if not _is_system_message(m)]

        # Keep as many non-system messages as possible after system messages
        keep_non_system = max(0, self._max_messages - len(system_msgs))
        self._messages = system_msgs + non_system[-keep_non_system:]

    def checkpoint(
        self,
        checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextSnapshot:
        """Create a checkpoint of current context.

        Creates a deep copy of messages for immutable snapshot.
        Auto-generates UUID if no id provided.

        Args:
            checkpoint_id: Optional checkpoint identifier (UUID generated if None)
            metadata: Optional metadata to store with checkpoint

        Returns:
            Created snapshot
        """
        if checkpoint_id is None:
            checkpoint_id = str(uuid.uuid4())

        snapshot = ContextSnapshot(
            checkpoint_id=checkpoint_id,
            timestamp=datetime.now(),
            messages=copy.deepcopy(self._messages),
            metadata=metadata or {},
        )

        self._checkpoints[checkpoint_id] = snapshot
        self._checkpoint_order.append(checkpoint_id)

        self._notify(LlmCheckpointCreatedEvent(snapshot=snapshot))

        return snapshot

    def rewind(self, checkpoint_id: str) -> None:
        """Restore context to a checkpoint.

        Replaces current messages with copy from checkpoint.
        Snapshot already contains deep copy, so no additional deepcopy needed.

        Args:
            checkpoint_id: Checkpoint to restore

        Raises:
            KeyError: If checkpoint_id not found
        """
        snapshot = self._checkpoints[checkpoint_id]  # Raises KeyError if not found
        self._messages = list(snapshot.messages)

        self._notify(LlmCheckpointRestoredEvent(snapshot=snapshot))

    def get_checkpoint(self, checkpoint_id: str) -> ContextSnapshot | None:
        """Get a checkpoint by id.

        Args:
            checkpoint_id: Checkpoint to retrieve

        Returns:
            Snapshot if found, None otherwise
        """
        return self._checkpoints.get(checkpoint_id)

    def list_checkpoints(self) -> list[str]:
        """List all checkpoint ids in creation order.

        Returns:
            List of checkpoint ids
        """
        return list(self._checkpoint_order)

    def subscribe(self, observer: ContextObserver) -> None:
        """Subscribe an observer to context events.

        Args:
            observer: Observer to add
        """
        self._observers.append(observer)

    def unsubscribe(self, observer: ContextObserver) -> None:
        """Unsubscribe an observer from context events.

        No-op if observer not present.

        Args:
            observer: Observer to remove
        """
        if observer in self._observers:
            self._observers.remove(observer)

    def restore(self, messages: list[ModelMessage]) -> None:
        """Replace message history with the provided list.

        Bulk-restores context from persisted event history. Unlike
        ``add_message``, this method does **not** notify observers (restored
        messages are already persisted — re-emitting events would cause
        duplicates) and does **not** apply the sliding window (restored
        messages are the authoritative history).

        Args:
            messages: Messages to restore as the new history.
                A defensive copy is made so the caller's list is not shared.
        """
        self._messages = list(messages)

    def clear(self) -> None:
        """Clear all messages and checkpoints.

        Resets context to empty state.
        """
        self._messages.clear()
        self._checkpoints.clear()
        self._checkpoint_order.clear()
