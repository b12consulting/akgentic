"""Query models for catalog repository search operations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentQuery",
    "TeamQuery",
    "TemplateQuery",
    "ToolQuery",
]


class TemplateQuery(BaseModel):
    """Query model for filtering template entries."""

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, description="Filter by exact template id")
    placeholder: str | None = Field(
        default=None, description="Filter by placeholder name present in template"
    )


class ToolQuery(BaseModel):
    """Query model for filtering tool entries."""

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, description="Filter by exact tool id")
    tool_class: str | None = Field(default=None, description="Filter by exact tool class path")
    name: str | None = Field(default=None, description="Filter by tool name substring")
    description: str | None = Field(
        default=None, description="Filter by tool description substring"
    )


class AgentQuery(BaseModel):
    """Query model for filtering agent entries."""

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, description="Filter by exact agent id")
    role: str | None = Field(default=None, description="Filter by exact agent role")
    skills: list[str] | None = Field(
        default=None, description="Filter by overlap with agent skill set"
    )
    description: str | None = Field(
        default=None, description="Filter by agent description substring"
    )


class TeamQuery(BaseModel):
    """Query model for filtering team entries."""

    model_config = ConfigDict(frozen=True)

    id: str | None = Field(default=None, description="Filter by exact team id")
    name: str | None = Field(default=None, description="Filter by team name substring")
    description: str | None = Field(
        default=None, description="Filter by team description substring"
    )
    agent_id: str | None = Field(
        default=None, description="Filter by agent id present in team members"
    )
