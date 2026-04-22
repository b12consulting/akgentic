"""Tests for agent state management with observer pattern."""

from __future__ import annotations

from pydantic import BaseModel

from akgentic.core.agent_state import BaseState


class Notification(BaseModel):
    """Notification payload - plain BaseModel, not BaseState."""

    count: int = 0
    dummy_field: str | None = None


class WorkerState(BaseState):
    """Custom state model that extends BaseState with observable fields."""

    dummy_field: str = "default"


class MockObserver:
    """Mock observer that creates Notifications when WorkerState changes.

    When state_changed() is called, it extracts data from the state,
    creates a Notification, and stores it for testing verification.
    """

    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def notify_state_change(self, state: BaseState) -> None:
        """Called when observed state changes.

        Extracts data from state and creates a notification record.
        """
        count = len(self.notifications) + 1
        dummy_field = getattr(state, "dummy_field", None)
        notification = Notification(count=count, dummy_field=dummy_field)
        self.notifications.append(notification)


class TestAkgentStateObserver:
    """Test the observer protocol definition."""

    def test_observer_protocol_defined(self) -> None:
        """Verify observer has notify_state_change method."""
        observer = MockObserver()
        assert hasattr(observer, "notify_state_change")

    def test_observer_receives_state_changes(self) -> None:
        """Verify observer receives and records state changes."""
        observer = MockObserver()
        state = WorkerState(dummy_field="test")

        # Manually trigger state change
        observer.notify_state_change(state)

        # Observer should have recorded the notification
        assert len(observer.notifications) == 1
        assert observer.notifications[0].count == 1
        assert observer.notifications[0].dummy_field == "test"


class TestBaseState:
    """Test BaseState observer pattern mechanics."""

    def test_base_state_instantiation(self) -> None:
        """Test BaseState can be instantiated with no observer."""
        state = BaseState()
        assert state._observer is None

    def test_observer_attachment(self) -> None:
        """Test observer can be attached and returns self for chaining."""
        state = BaseState()
        observer = MockObserver()

        result = state.observer(observer)

        assert state._observer is observer
        assert result is state

    def test_observer_triggers_notification_on_attach(self) -> None:
        """Test attaching observer triggers immediate notification."""
        state = BaseState()
        observer = MockObserver()

        state.observer(observer)

        # Should have one notification from attachment
        assert len(observer.notifications) == 1

    def test_notify_state_change(self) -> None:
        """Test notify_state_change triggers observer callback."""
        state = BaseState()
        observer = MockObserver()
        state.observer(observer)
        observer.notifications.clear()

        state.notify_state_change()

        assert len(observer.notifications) == 1

    def test_notify_without_observer(self) -> None:
        """Test notify_state_change doesn't raise when no observer attached."""
        state = BaseState()

        # Should not raise
        state.notify_state_change()

    def test_serializable_copy_excludes_observer(self) -> None:
        """Test serializable_copy creates copy without observer reference."""
        state = BaseState()
        observer = MockObserver()
        state.observer(observer)

        copy = state.serializable_copy()

        assert copy._observer is None
        assert copy is not state

    def test_detach_observer(self) -> None:
        """Test observer can be detached by setting to None."""
        state = BaseState()
        observer = MockObserver()
        state.observer(observer)

        ## Notification when attaching observer
        assert len(observer.notifications) == 1

        observer.notifications.clear()

        # Detach observer
        state.observer(None)

        # Observer was set to None BEFORE notify_state_change() call,
        # so no notification occurs
        assert len(observer.notifications) == 0

        # Explicit notify should also not trigger
        state.notify_state_change()
        assert len(observer.notifications) == 0

    def test_observer_replacement(self) -> None:
        """Test replacing one observer with another."""
        state = BaseState()
        observer1 = MockObserver()
        observer2 = MockObserver()

        # Attach first observer
        state.observer(observer1)
        assert len(observer1.notifications) == 1

        # Replace with second observer
        state.observer(observer2)
        assert len(observer2.notifications) == 1

        # Only observer2 should receive new notifications
        observer1.notifications.clear()
        observer2.notifications.clear()

        state.notify_state_change()

        assert len(observer1.notifications) == 0
        assert len(observer2.notifications) == 1


class TestCustomState:
    """Test custom states extending BaseState."""

    def test_worker_state_instantiation(self) -> None:
        """Test WorkerState can be instantiated with default values."""
        state = WorkerState()
        assert state.dummy_field == "default"
        assert state._observer is None

    def test_worker_state_with_custom_value(self) -> None:
        """Test WorkerState can be instantiated with custom values."""
        state = WorkerState(dummy_field="custom")
        assert state.dummy_field == "custom"

    def test_worker_state_observer_pattern(self) -> None:
        """Test WorkerState notifies observer when state changes."""
        state = WorkerState(dummy_field="initial")
        observer = MockObserver()

        # Attach observer
        state.observer(observer)

        # Should have one notification from attachment
        assert len(observer.notifications) == 1
        assert observer.notifications[0].dummy_field == "initial"

        # Change state and notify
        observer.notifications.clear()
        state.dummy_field = "updated"
        state.notify_state_change()

        # Should have one notification from the update
        assert len(observer.notifications) == 1
        assert observer.notifications[0].dummy_field == "updated"

    def test_multiple_state_changes(self) -> None:
        """Test observer receives multiple notifications for multiple changes."""
        state = WorkerState(dummy_field="v1")
        observer = MockObserver()
        state.observer(observer)
        observer.notifications.clear()

        # Multiple state changes
        state.dummy_field = "v2"
        state.notify_state_change()

        state.dummy_field = "v3"
        state.notify_state_change()

        state.dummy_field = "v4"
        state.notify_state_change()

        # Should have three notifications
        assert len(observer.notifications) == 3
        assert observer.notifications[0].count == 1
        assert observer.notifications[0].dummy_field == "v2"
        assert observer.notifications[1].count == 2
        assert observer.notifications[1].dummy_field == "v3"
        assert observer.notifications[2].count == 3
        assert observer.notifications[2].dummy_field == "v4"

    def test_worker_state_serializable_copy(self) -> None:
        """Test serializable_copy preserves WorkerState data without observer."""
        state = WorkerState(dummy_field="test_value")
        observer = MockObserver()
        state.observer(observer)

        copy = state.serializable_copy()

        assert isinstance(copy, WorkerState)
        assert copy.dummy_field == "test_value"
        assert copy._observer is None

    def test_worker_state_serialization(self) -> None:
        """Test WorkerState can be serialized via model_dump."""
        state = WorkerState(dummy_field="serialize_me")
        data = state.model_dump()

        assert data["dummy_field"] == "serialize_me"
        assert "__model__" in data

    def test_custom_state_with_multiple_fields(self) -> None:
        """Test custom state with multiple observable fields."""

        class TaskState(BaseState):
            task_name: str = "default_task"
            task_count: int = 0
            is_active: bool = False

        state = TaskState(task_name="test", task_count=5, is_active=True)
        assert state.task_name == "test"
        assert state.task_count == 5
        assert state.is_active is True

        # Test observer pattern with custom state
        observer = MockObserver()
        state.observer(observer)

        assert len(observer.notifications) == 1

    def test_notification_captures_state_snapshot(self) -> None:
        """Test notifications capture state at time of change, not live references."""
        state = WorkerState(dummy_field="snapshot1")
        observer = MockObserver()
        state.observer(observer)
        observer.notifications.clear()

        # Create first notification
        state.dummy_field = "snapshot2"
        state.notify_state_change()

        # Create second notification
        state.dummy_field = "snapshot3"
        state.notify_state_change()

        # First notification should still have snapshot2, not snapshot3
        assert observer.notifications[0].dummy_field == "snapshot2"
        assert observer.notifications[1].dummy_field == "snapshot3"

        # Changing state after notifications shouldn't affect them
        state.dummy_field = "snapshot4"
        assert observer.notifications[0].dummy_field == "snapshot2"
        assert observer.notifications[1].dummy_field == "snapshot3"
