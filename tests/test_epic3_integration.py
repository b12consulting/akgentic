"""Integration tests for Epic 3: Orchestrator inactivity timer.

Tests the full end-to-end behaviour of the Orchestrator's inactivity timer,
verifying that:
  - ReceivedMessage pauses the timer (task_started)
  - ProcessedMessage restarts the timer (task_completed)
  - Subscribers receive ``on_stop_request`` when the timer fires and the
    orchestrator itself stays alive (shutdown is delegated to the subscriber)
  - Manual stop cancels the timer cleanly
  - WarningError in handler: timer resets properly, no ErrorMessage sent
  - Other exception in handler: timer resets properly, ErrorMessage sent
"""

import os
import time
import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pykka
import pytest

from akgentic.core.actor_address_impl import ActorAddressImpl
from akgentic.core.agent import Akgent, WarningError
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import ErrorMessage, ProcessedMessage, ReceivedMessage
from akgentic.core.orchestrator import Orchestrator


@pytest.fixture(autouse=True)
def cleanup_actors() -> Generator[None, None, None]:
    """Ensure all actors are stopped after each test."""
    yield
    pykka.ActorRegistry.stop_all()


def _make_external_sender_address(name: str = "test-agent"):
    """Create a real actor address to use as an external sender."""

    class _SenderOrch(Orchestrator):
        pass

    sender_ref = _SenderOrch.start(config=BaseConfig(name=name, role="Agent"))
    addr = sender_ref.proxy().myAddress.get()
    return sender_ref, addr


class TestOrchestratorTimerIntegration:
    """End-to-end integration tests for the inactivity timer workflow."""

    def test_received_message_pauses_timer(self) -> None:
        """ReceivedMessage triggers task_started: timer is paused (task_count > 0)."""
        config = BaseConfig(name="orch-integration", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        assert timer.task_count == 0

        sender_ref, sender_addr = _make_external_sender_address("agent-1")

        msg = ReceivedMessage(message_id=uuid.uuid4())
        orch.receiveMsg_ReceivedMessage(msg, sender_addr).get()

        assert timer.task_count == 1
        assert timer._timer is None  # timer paused while task active

        sender_ref.stop()
        orch_ref.stop()

    def test_processed_message_restarts_timer(self) -> None:
        """ProcessedMessage triggers task_completed: timer restarts when idle."""
        config = BaseConfig(name="orch-integration", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()

        sender_ref, sender_addr = _make_external_sender_address("agent-2")

        # Simulate: agent receives message
        recv_msg = ReceivedMessage(message_id=uuid.uuid4())
        orch.receiveMsg_ReceivedMessage(recv_msg, sender_addr).get()
        assert timer.task_count == 1
        assert timer._timer is None

        # Simulate: agent completes processing
        proc_msg = ProcessedMessage(message_id=uuid.uuid4())
        orch.receiveMsg_ProcessedMessage(proc_msg, sender_addr).get()

        assert timer.task_count == 0
        assert timer._timer is not None  # timer restarted after idle

        sender_ref.stop()
        orch_ref.stop()

    def test_timeout_notifies_subscribers_and_orchestrator_stays_alive(self) -> None:
        """After the inactivity timeout fires, subscribers are notified via
        ``on_stop_request`` and the orchestrator itself stays alive.

        The refactor (Story 3.6) delegates shutdown to subscribers — the
        orchestrator no longer sends ``StopRecursively`` to itself. Uses a
        1-second timeout for test speed.
        """

        class _StopSub:
            def __init__(self) -> None:
                self.stop_request_count = 0

            def set_restoring(self, restoring: bool) -> None:  # noqa: FBT001
                pass

            def on_stop_request(self) -> None:
                self.stop_request_count += 1

            def on_stop(self) -> None:
                pass

            def on_message(self, msg: Message) -> None:
                pass

        with patch.dict(os.environ, {"ORCHESTRATOR_TIMEOUT_DELAY": "1"}):
            config = BaseConfig(name="orch-timeout-test", role="Orchestrator")
            orch_ref = Orchestrator.start(config=config)
            orch = orch_ref.proxy()

            sub = _StopSub()
            orch.subscribe(sub).get()

            assert orch_ref.is_alive()

            # Wait for the timer (1s) to fire; poll the subscriber up to 3s.
            deadline = time.monotonic() + 3.0
            while sub.stop_request_count == 0 and time.monotonic() < deadline:
                time.sleep(0.1)

            assert sub.stop_request_count >= 1
            # Orchestrator did NOT self-stop — the subscriber owns shutdown now
            assert orch_ref.is_alive()

            orch_ref.stop()

    def test_timer_resets_on_new_message_after_previous_task_completes(self) -> None:
        """Timer resets correctly through multiple receive/process cycles."""
        config = BaseConfig(name="orch-cycle-test", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        sender_ref, sender_addr = _make_external_sender_address("cycle-agent")

        # Cycle 1
        orch.receiveMsg_ReceivedMessage(ReceivedMessage(message_id=uuid.uuid4()), sender_addr).get()
        assert timer.task_count == 1

        orch.receiveMsg_ProcessedMessage(
            ProcessedMessage(message_id=uuid.uuid4()), sender_addr
        ).get()
        assert timer.task_count == 0

        # Cycle 2 — new message arrives, timer cancels again
        orch.receiveMsg_ReceivedMessage(ReceivedMessage(message_id=uuid.uuid4()), sender_addr).get()
        assert timer.task_count == 1
        assert timer._timer is None  # timer cancelled again

        # Complete cycle 2
        orch.receiveMsg_ProcessedMessage(
            ProcessedMessage(message_id=uuid.uuid4()), sender_addr
        ).get()
        assert timer.task_count == 0
        assert timer._timer is not None  # timer active again

        sender_ref.stop()
        orch_ref.stop()

    def test_manual_stop_cancels_timer_before_timeout(self) -> None:
        """Calling stop() before timer fires properly cancels the timer."""
        config = BaseConfig(name="orch-manual-stop", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        assert timer._timer is not None

        # Stop immediately via the orchestrator's stop() method
        orch.stop().get()
        time.sleep(0.1)

        assert timer._timer is None
        assert not orch_ref.is_alive()


# ---------------------------------------------------------------------------
# Test messages and agents for error-handling integration tests
# ---------------------------------------------------------------------------


class TriggerMessage(Message):
    """Message that triggers a handler which raises an exception."""

    error_type: str = "warning"  # "warning" or "runtime"


class _FailingAgent(Akgent[BaseConfig, BaseState]):
    """Agent whose handler raises WarningError or RuntimeError depending on the message."""

    def receiveMsg_TriggerMessage(self, msg: TriggerMessage, sender: Any) -> None:
        if msg.error_type == "warning":
            raise WarningError("non-critical issue")
        raise RuntimeError("critical failure")


# ---------------------------------------------------------------------------
# Integration tests: error handling and timer behaviour
# ---------------------------------------------------------------------------


class TestTimerBehaviourOnHandlerErrors:
    """End-to-end tests verifying timer correctness when an agent handler raises.

    The Timer relies exclusively on ReceivedMessage (task_started) and
    ProcessedMessage (task_completed).  When _handle_receive catches an
    exception it always emits ProcessedMessage, so the timer must reset
    correctly regardless of error type.

    WarningError → ProcessedMessage emitted, NO ErrorMessage emitted.
    Other exception → ProcessedMessage emitted, ErrorMessage emitted (but
    does NOT affect timer task_count).
    """

    def test_warning_error_resets_timer_and_sends_no_error_message(self) -> None:
        """WarningError: timer resets (count 0), no ErrorMessage stored in orchestrator."""
        config = BaseConfig(name="orch-warning-test", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        orch_address = ActorAddressImpl(orch_ref)

        # Start an agent wired to this orchestrator
        agent_ref = _FailingAgent.start(
            config=BaseConfig(name="failing-agent-warning", role="Agent"),
            orchestrator=orch_address,
        )

        # Give on_start + StartMessage time to propagate
        time.sleep(0.3)

        # Reset timer state to a clean baseline
        timer.task_count = 0
        timer.cancel()
        timer.start()
        messages_before = len(orch.messages.get())

        # Send message via tell() — this is how agents deliver messages in production.
        # The raw Message goes through _handle_receive, where isinstance(msg, Message)
        # is True, ensuring ProcessedMessage is emitted on error.
        trigger = TriggerMessage(error_type="warning")
        agent_ref.tell(trigger)

        # Allow the actor to process and telemetry to propagate
        time.sleep(0.5)

        # Timer should be active again (task_count back to 0 → timer restarted)
        assert timer.task_count == 0
        assert timer._timer is not None

        # Verify NO ErrorMessage was recorded by the orchestrator
        messages_after = orch.messages.get()
        new_messages = messages_after[messages_before:]
        error_messages = [m for m in new_messages if isinstance(m, ErrorMessage)]
        assert len(error_messages) == 0, (
            f"WarningError should not produce ErrorMessage, got {error_messages}"
        )

        agent_ref.stop()
        orch_ref.stop()

    def test_runtime_error_resets_timer_and_sends_error_message(self) -> None:
        """RuntimeError: timer resets (count 0), ErrorMessage IS stored in orchestrator."""
        config = BaseConfig(name="orch-runtime-test", role="Orchestrator")
        orch_ref = Orchestrator.start(config=config)
        orch = orch_ref.proxy()

        timer = orch.get_timer().get()
        orch_address = ActorAddressImpl(orch_ref)

        # Start an agent wired to this orchestrator
        agent_ref = _FailingAgent.start(
            config=BaseConfig(name="failing-agent-runtime", role="Agent"),
            orchestrator=orch_address,
        )

        # Give on_start + StartMessage time to propagate
        time.sleep(0.3)

        # Reset timer state to a clean baseline
        timer.task_count = 0
        timer.cancel()
        timer.start()
        messages_before = len(orch.messages.get())

        # Send message via tell() — real message delivery path
        trigger = TriggerMessage(error_type="runtime")
        agent_ref.tell(trigger)

        # Allow the actor to process and telemetry to propagate
        time.sleep(0.5)

        # Timer should be active again (task_count back to 0 → timer restarted)
        assert timer.task_count == 0
        assert timer._timer is not None

        # Verify an ErrorMessage WAS recorded (but did not affect the timer)
        messages_after = orch.messages.get()
        new_messages = messages_after[messages_before:]
        error_messages = [m for m in new_messages if isinstance(m, ErrorMessage)]
        assert len(error_messages) == 1, (
            f"RuntimeError should produce exactly one ErrorMessage, got {len(error_messages)}"
        )
        assert error_messages[0].exception_type == "RuntimeError"
        assert "critical failure" in error_messages[0].exception_value

        agent_ref.stop()
        orch_ref.stop()
