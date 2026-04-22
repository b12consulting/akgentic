"""V1 response models for the Angular V1 frontend adapter.

Self-contained Pydantic models representing V1 API response shapes.
These are independent from V2 models — no shared response models.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class V1ActorAddress(BaseModel):
    """V1 actor address representation."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Actor display name")
    role: str = Field(description="Actor role from AgentCard")
    actor_address: str = Field(
        default="",
        alias="__actor_address__",
        description="String-serialized actor address",
    )
    address: str = Field(default="", description="Actor address string")
    agent_id: str = Field(default="", description="Agent unique ID")
    squad_id: str = Field(default="", description="Team ID string")
    user_message: str = Field(default="", description="User message, populated when applicable")


class V1ProcessParams(BaseModel):
    """V1 process creation parameters."""

    type: str = Field(description="Team type / catalog entry ID")
    agents: list[V1ActorAddress] = Field(
        default_factory=list,
        description="List of actors in the team",
    )


class V1ProcessContext(BaseModel):
    """V1 team representation — maps from V2 Process model."""

    id: str = Field(description="UUID as string")
    type: str = Field(description="Team name / catalog entry")
    status: str = Field(description="Team lifecycle status")
    created_at: str = Field(description="ISO datetime string")
    updated_at: str = Field(description="ISO datetime string")
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Feature flags: workspace, knowledge_graph",
    )
    orchestrator: V1ActorAddress = Field(
        default_factory=lambda: V1ActorAddress(name="", role=""),
        description="Team entry point agent as V1ActorAddress",
    )
    running: bool = Field(default=False, description="Derived from status == running")
    config_name: str = Field(default="", description="Team card name / catalog entry ID")
    user_id: str = Field(default="", description="User who owns this team")
    user_email: str = Field(default="", description="Email of the user who owns this team")


class V1MessageEntry(BaseModel):
    """V1 message history entry — maps from persisted events."""

    id: str = Field(description="Message UUID as string")
    sender: str = Field(description="Sender name")
    content: str = Field(description="Message content")
    timestamp: str = Field(description="ISO datetime string")
    type: str = Field(description="Message type: user, agent, system")


class V1LlmContextEntry(BaseModel):
    """V1 LLM context entry — maps from persisted events."""

    role: str = Field(description="Message role: user, agent, system")
    content: str = Field(description="Message content")
    timestamp: str = Field(description="ISO datetime string")


class V1StateEntry(BaseModel):
    """V1 state change entry — maps from StateChangedMessage events."""

    agent: str = Field(description="Agent name that changed state")
    state: dict[str, object] = Field(description="Serialized agent state")
    timestamp: str = Field(description="ISO datetime string")


class V1ConfigPutBody(BaseModel):
    """Request body for PUT /config/{config_type}."""

    id: str = Field(description="Config entry ID")
    name: str = Field(default="", description="Config entry name")
    config: dict[str, object] = Field(default_factory=dict, description="Config data")
    dry_run: bool = Field(default=False, description="Dry run flag")


class V1StatusResponse(BaseModel):
    """V1 simple status response for action endpoints."""

    status: str = Field(description="Operation status, e.g. 'ok'")


class V1DescriptionBody(BaseModel):
    """Request body for PATCH /process/{id}/description."""

    description: str = Field(description="New description for the process")


class V1StateUpdateBody(BaseModel):
    """Request body for PATCH /state/{id}/of/{agent}."""

    content: str = Field(description="State update content to send to agent")


class V1ConfigEntry(BaseModel):
    """V1 catalog configuration entry for GET/PUT/DELETE /config."""

    id: str = Field(description="Configuration entry ID")
    type: str = Field(description="Configuration type: team, agent, tool, template")
    data: dict[str, object] = Field(
        default_factory=dict,
        description="Configuration data",
    )


class V1FeedbackEntry(BaseModel):
    """V1 feedback entry for GET/POST /feedback."""

    id: str = Field(default="", description="Feedback entry ID")
    content: str = Field(default="", description="Feedback content")
    rating: int = Field(default=0, description="Feedback rating")
