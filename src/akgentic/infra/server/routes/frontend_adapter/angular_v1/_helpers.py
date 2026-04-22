"""Shared helper functions for the Angular V1 frontend adapter.

Used by both REST route translations (router.py) and WebSocket event
wrapping (ws.py) to avoid duplicating message classification logic.
"""

from __future__ import annotations

from akgentic.core.messages.message import Message, ResultMessage, UserMessage
from akgentic.core.messages.orchestrator import SentMessage


def extract_message_content(event: Message) -> str | None:
    """Extract displayable content from a message event.

    Args:
        event: The message to extract content from.

    Returns:
        The displayable content string, or None if no content is available.
    """
    if hasattr(event, "content"):
        return str(event.content)
    if isinstance(event, SentMessage) and hasattr(event.message, "content"):
        return str(event.message.content)
    return None


def classify_message_type(event: Message) -> str:
    """Classify a message event as user/agent/system.

    Args:
        event: The message to classify.

    Returns:
        One of "user", "agent", or "system".
    """
    if isinstance(event, UserMessage):
        return "user"
    if isinstance(event, (ResultMessage, SentMessage)):
        return "agent"
    return "system"


def get_sender_name(event: Message) -> str:
    """Extract sender name from a message event.

    Args:
        event: The message to extract the sender from.

    Returns:
        The sender name string, or "system" if no sender.
    """
    if event.sender is not None:
        return str(event.sender.name) if hasattr(event.sender, "name") else str(event.sender)
    return "system"
