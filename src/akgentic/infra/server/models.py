"""Pydantic request/response models for the REST API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateTeamRequest(BaseModel):
    """Request body for POST /teams."""

    catalog_namespace: str = Field(
        description="Catalog namespace (v2) whose team entry resolves into a TeamCard"
    )
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
    """Response body for GET /teams (one numbered page + full owned count)."""

    teams: list[TeamResponse] = Field(description="Current page of team metadata entries")
    total_count: int = Field(description="Total teams for the user across all pages")


class SendMessageRequest(BaseModel):
    """Request body for POST /teams/{team_id}/message."""

    content: str = Field(description="Message content to send to the team")


class HumanInputRequest(BaseModel):
    """Request body for POST /teams/{team_id}/human-input."""

    content: str = Field(description="Human response content")
    message_id: str = Field(
        description=(
            "Inner Message.id (SentMessage.message.id) of the original message "
            "being answered — the id a reply's parent_id references, not the "
            "outer SentMessage envelope id."
        )
    )


class EventResponse(BaseModel):
    """Serialized form of a PersistedEvent."""

    team_id: uuid.UUID = Field(description="Team instance this event belongs to")
    sequence: int = Field(description="Monotonically increasing event sequence number")
    event: dict[str, object] = Field(description="Serialized event payload")
    timestamp: datetime = Field(description="Timestamp when the event was persisted")


class EventListResponse(BaseModel):
    """Response body for GET /teams/{team_id}/events."""

    events: list[EventResponse] = Field(description="List of persisted events")


class AgentStateResponse(BaseModel):
    """Serialized form of a persisted AgentStateSnapshot.

    A faithful passthrough of the snapshot store: ``agent_id`` and ``name``
    are returned exactly as stored (no resolution / re-keying), and ``state``
    is the snapshot's serialized ``BaseState`` — same shape convention as
    ``EventResponse.event``.
    """

    agent_id: str = Field(
        description="Snapshot's stored agent identifier, returned as-is (no resolution)"
    )
    name: str | None = Field(
        description="Agent display name as stored; None for pre-Epic-23 snapshots"
    )
    state: dict[str, object] = Field(description="Serialized agent state payload")
    updated_at: datetime = Field(description="Timestamp when the snapshot was taken")


class AgentStateListResponse(BaseModel):
    """Response body for GET /teams/{team_id}/agent-states."""

    states: list[AgentStateResponse] = Field(description="List of persisted agent state snapshots")


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
