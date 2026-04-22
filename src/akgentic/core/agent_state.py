"""Agent state management with observer pattern for reactive updates.

This module provides the base state class that agents use to manage their
internal state with automatic change notification to the orchestrator.
"""

from __future__ import annotations

from typing import Protocol, Self

from pydantic import PrivateAttr

from akgentic.core.utils.serializer import SerializableBaseModel


class AkgentStateObserver(Protocol):
    """Protocol for objects that observe agent state changes.

    Implement this protocol to receive notifications when an agent's
    state is modified. The Agent class implements this protocol to
    forward state changes to the Orchestrator.

    Example:
        >>> class MyObserver:
        ...     def notify_state_change(self, state: BaseState) -> None:
        ...         print(f"State changed: {state}")
    """

    def notify_state_change(self, state: BaseState) -> None:
        """Called when the observed state changes.

        Args:
            state: The updated state object.
        """
        ...


class BaseState(SerializableBaseModel):
    """Base class for agent state with observer pattern.

    Agents maintain state that can change during message processing.
    BaseState provides:
    - Observer pattern for reactive workflows (state changes notify orchestrator)
    - Pydantic model for validation and serialization
    - serializable_copy() for safe persistence without observer references

    The observer is stored as a private attribute and excluded from
    serialization to prevent circular references.

    Attributes:
        _observer: Private observer reference (not serialized).

    Example:
        >>> class WorkerState(BaseState):
        ...     tasks_completed: int = 0
        ...     current_task: str | None = None
        ...
        >>> state = WorkerState()
        >>> state.observer(my_agent)  # Agent becomes observer
        >>> state.tasks_completed = 5
        >>> state.notify_state_change()  # Triggers observer callback
    """

    _observer: AkgentStateObserver | None = PrivateAttr(default=None)

    def observer(self, observer: AkgentStateObserver | None) -> Self:
        """Attach an observer and trigger initial notification.

        This method is typically called during agent initialization
        to connect the state to the agent's state_changed handler.

        Args:
            observer: Object implementing AkgentStateObserver protocol,
                or None to detach current observer.

        Returns:
            Self, enabling method chaining.

        Example:
            >>> state = WorkerState()
            >>> state.observer(self)  # Agent observes its own state
        """
        self._observer = observer
        self.notify_state_change()
        return self

    def notify_state_change(self) -> None:
        """Notify the observer of a state change.

        Called automatically when observer is attached, and should be
        called explicitly after modifying state fields.

        If no observer is attached, this method does nothing.
        """
        if self._observer is not None:
            self._observer.notify_state_change(self)

    def serializable_copy(self) -> BaseState:
        """Create a copy of the state without the observer for serialization.

        The observer reference is excluded to prevent circular references
        and ensure clean serialization for persistence or network transport.

        Returns:
            A new instance of the same state class without observer.

        Example:
            >>> state = WorkerState(tasks_completed=5)
            >>> state.observer(my_agent)
            >>> clean_state = state.serializable_copy()
            >>> clean_state._observer is None  # True
        """
        return self.__class__.model_validate(self.model_dump(), context=self._observer)
