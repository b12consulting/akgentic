"""Pydantic request/response models for the REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CreateTeamRequest(BaseModel):
    """Request body for POST /teams."""

    catalog_entry_id: str = Field(description="Catalog entry ID to resolve into a TeamCard")
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Pass-through configuration parameters",
    )


class TeamResponse(BaseModel):
    """Serialized team metadata returned by team endpoints."""

    team_id: uuid.UUID = Field(description="Unique team identifier")
    name: str = Field(description="Human-readable team name")
    status: str = Field(description="Current team lifecycle status")
    user_id: str = Field(description="Owner user identifier")
    created_at: datetime = Field(description="Team creation timestamp")
    updated_at: datetime = Field(description="Last status change timestamp")


class TeamListResponse(BaseModel):
    """Response body for GET /teams."""

    teams: list[TeamResponse] = Field(description="List of team metadata entries")


class SendMessageRequest(BaseModel):
    """Request body for the POST /teams/{team_id}/message[...] routes.

    Carries EXACTLY ONE of two mutually exclusive payloads (enforced below):

    - ``content``: a plain ``str`` — the framework wraps it in the team's
      default message type (unchanged legacy behaviour).
    - ``message``: a pre-formed *processing* ``Message`` as a wire envelope — a
      serialized ``Message.model_dump(mode="json")`` dict carrying the
      ``__model__`` tag. The route deserializes it into the concrete typed
      ``Message`` and hands it to ``TeamService.send_message*`` for agent
      processing. It is deserialized immediately and never becomes application
      state, so this is NOT the ``dict[str, Any]`` anti-pattern (Golden Rule #1)
      — the same documented polymorphic-envelope exception ``EmitMessageRequest``
      carries for the publish-only ``/notification`` route.
    """

    content: str | None = Field(
        default=None, description="Plain-string message content (framework wraps it)"
    )
    message: dict[str, Any] | None = Field(
        default=None,
        description="Serialized Message dict (model_dump(mode='json')) carrying the __model__ tag",
    )

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> SendMessageRequest:
        """Require exactly one of ``content`` / ``message`` (neither and both are errors)."""
        if (self.content is None) == (self.message is None):
            raise ValueError("exactly one of 'content' or 'message' must be set")
        return self


class EmitMessageRequest(BaseModel):
    """Request body for POST /teams/{team_id}/notification.

    ``message`` is a polymorphic-Message wire envelope — a serialized
    ``Message.model_dump(mode="json")`` dict carrying the ``__model__`` tag.
    The route deserializes it immediately into a concrete typed ``Message``
    via ``deserialize_object``; it never becomes application state, so this
    is NOT the ``dict[str, Any]`` anti-pattern (Golden Rule #1). A typed
    ``message: Message`` field was rejected: ``SerializableBaseModel`` only
    strips ``__model__`` for the declared class and would not dispatch a base
    annotation to the concrete subclass — ``deserialize_object`` is the
    reconstruction path.
    """

    message: dict[str, Any] = Field(
        description="Serialized Message dict (model_dump(mode='json')) carrying the __model__ tag"
    )


class HumanInputRequest(BaseModel):
    """Request body for POST /teams/{team_id}/human-input."""

    content: str = Field(description="Human response content")
    message_id: str = Field(description="ID of the original message being answered")


class EventResponse(BaseModel):
    """Serialized form of a PersistedEvent."""

    team_id: uuid.UUID = Field(description="Team instance this event belongs to")
    sequence: int = Field(description="Monotonically increasing event sequence number")
    event: dict[str, object] = Field(description="Serialized event payload")
    timestamp: datetime = Field(description="Timestamp when the event was persisted")


class EventListResponse(BaseModel):
    """Response body for GET /teams/{team_id}/events."""

    events: list[EventResponse] = Field(description="List of persisted events")


# --- Workspace response models ---


class WorkspaceFileEntry(BaseModel):
    """Single entry in a workspace file tree listing."""

    name: str = Field(description="File or directory name")
    is_dir: bool = Field(description="True if entry is a directory")
    size: int = Field(description="File size in bytes (0 for directories)")


class WorkspaceTreeResponse(BaseModel):
    """Response body for GET /workspace/{team_id}/tree."""

    team_id: str = Field(description="Team identifier")
    path: str = Field(description="Listed directory path")
    entries: list[WorkspaceFileEntry] = Field(description="Directory entries")


class WorkspaceFileUploadResponse(BaseModel):
    """Response body for POST /workspace/{team_id}/file."""

    path: str = Field(description="Destination file path")
    size: int = Field(description="File size in bytes")
