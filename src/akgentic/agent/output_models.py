"""Structured output models for agent response patterns.

This module defines Pydantic models that enable agents to return structured responses
in three distinct patterns:
"""

from typing import Literal

from pydantic import BaseModel, Field

REPLY_PROTOCOLS: dict[str, str] = {
    "request": "Carry out the task, then respond to {sender}. You may also delegate to others.",
    "response": "Analyse the response, then continue or end the exchange.",
    "instruction": "Carry out the task, then acknowledge to {sender} if requested.",
    "notification": "Informational message. No reply is expected.",
    "acknowledgment": "No further action needed.",
}


class Request(BaseModel):
    """A message directed to a specific team member or role."""

    message_type: Literal[
        "request",
        "response",
        "notification",
        "instruction",
        "acknowledgment",
    ] = Field(
        ...,
        description="Choose based on intent: "
        "'request' = ask recipient to perform a task and reply to you with the result; "
        "'response' = respond to a previous request; "
        "'instruction' = direct recipient to perform a task, you may ask for acknowledgement; "
        "'notification' = send information to the recipient, no reply is expected; "
        "'acknowledgment' = confirm receipt of an instruction, no reply is expected.",
    )
    message: str = Field(..., description="The message content to send")
    recipient: str = Field(
        ...,
        description="The recipient by name (e.g. '@Developer') or by role (e.g. 'Developer')",
    )


class StructuredOutput(BaseModel):
    messages: list[Request] = Field(
        default_factory=list,
        description="Requests to send to team members; empty if no delegation needed",
    )
