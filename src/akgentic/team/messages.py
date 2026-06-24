"""Message types specific to team lifecycle management.

Defines WelcomeMessage, the team's static startup greeting. It is a distinct
Message subclass so the greeting is identifiable in the persisted event log
and renderable specially by clients, without content heuristics.
"""

from __future__ import annotations

from typing import Literal

from akgentic.core.messages.message import Message


class WelcomeMessage(Message):
    """The team's static startup greeting, announced on the event stream.

    Carried as the payload of a SentMessage that TeamFactory hands to the
    orchestrator at build time. A distinct type -- never produced by an LLM --
    so the greeting is identifiable in the event log and renderable specially
    by clients.

    Attributes:
        content: The greeting text declared on the TeamCard.
        display_type: Display category for UI rendering; defaults to "other"
            -- the greeting is a synthetic system announcement, not an AI turn.
    """

    content: str
    display_type: Literal["human", "ai", "other"] = "other"