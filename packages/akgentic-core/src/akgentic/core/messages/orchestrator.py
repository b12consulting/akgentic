"""Orchestrator telemetry messages for actor lifecycle events.

Provides message types for tracking actor communication, state changes,
and error conditions. Used by the orchestrator for system observability.

Source: akgentic-framework/libs/akgentic/akgentic/core/messages/orchestrator.py
"""

from __future__ import annotations

import uuid
from typing import Any

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.core.messages.message import Message


class SentMessage(Message):
    """Telemetry message indicating a message was sent.

    Records when an actor sends a message to another actor,
    including both the message content and the recipient.

    Attributes:
        message: The message that was sent.
        recipient: Address of the receiving actor.
    """

    message: Message
    recipient: ActorAddress


class ReceivedMessage(Message):
    """Telemetry message indicating a message was received.

    Records when an actor receives a message from another actor,
    storing only the message ID for lightweight telemetry.

    Attributes:
        message_id: UUID of the received message.
    """

    message_id: uuid.UUID


class ProcessedMessage(Message):
    """Telemetry message indicating a message was processed.

    Records when an actor completes processing of a message.

    Attributes:
        message_id: UUID of the processed message.
    """

    message_id: uuid.UUID


class StartMessage(Message):
    """Message to start an actor with configuration.

    Signals that an actor should initialize with the provided
    configuration and optional parent reference.

    Attributes:
        config: Actor configuration for initialization.
        parent: Optional parent actor address.
    """

    config: BaseConfig
    parent: ActorAddress | None = None


class StopMessage(Message):
    """Message to stop an actor.

    Signals that the receiving actor should stop processing
    and clean up resources.
    """

    pass


class ErrorMessage(Message):
    """Telemetry message for actor errors.

    Records exceptions that occur during actor message processing,
    including the error details and the message being processed.

    Attributes:
        exception_type: Fully qualified name of the exception class.
        exception_value: String representation of the exception.
        current_message: The message being processed when error occurred.
    """

    exception_type: str
    exception_value: str
    traceback: str | None = None
    current_message: Message | None = None


class StateChangedMessage(Message):
    """Telemetry message for state changes.

    Records when an actor's state changes.

    Attributes:
        state: The new state after the change.
    """

    state: BaseState


class EventMessage(Message):
    """Telemetry message for actor events.

    Records domain events or custom events emitted by actors during
    their execution, allowing for event-driven monitoring and logging.

    Attributes:
        event: Domain event object emitted by a component.
    """

    event: Any
