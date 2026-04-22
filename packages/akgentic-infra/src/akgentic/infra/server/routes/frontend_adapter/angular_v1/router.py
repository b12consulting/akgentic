"""V1 REST route translations for the Angular V1 frontend adapter.

Every route delegates to TeamService — zero business logic in the adapter.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from akgentic.core.messages.orchestrator import (
    StateChangedMessage,
)
from akgentic.infra.server.routes.frontend_adapter.angular_v1._helpers import (
    classify_message_type,
    extract_message_content,
    get_sender_name,
)
from akgentic.infra.server.routes.frontend_adapter.angular_v1.models import (
    V1ActorAddress,
    V1ConfigEntry,
    V1ConfigPutBody,
    V1DescriptionBody,
    V1FeedbackEntry,
    V1MessageEntry,
    V1ProcessContext,
    V1StateUpdateBody,
    V1StatusResponse,
)
from akgentic.infra.server.services.team_service import TeamService
from akgentic.team.models import PersistedEvent, Process, TeamStatus

process_router = APIRouter(prefix="/process", tags=["v1-compat"])
processes_router = APIRouter(prefix="/processes", tags=["v1-compat"])
human_input_router = APIRouter(prefix="/process_human_input", tags=["v1-compat"])
messages_router = APIRouter(prefix="/messages", tags=["v1-compat"])
llm_context_router = APIRouter(prefix="/llm_context", tags=["v1-compat"])
states_router = APIRouter(prefix="/states", tags=["v1-compat"])
config_router = APIRouter(prefix="/config", tags=["v1-compat"])
team_configs_router = APIRouter(prefix="/team-configs", tags=["v1-compat"])
feedback_router = APIRouter(tags=["v1-compat"])
relaunch_router = APIRouter(prefix="/relaunch", tags=["v1-compat"])
state_update_router = APIRouter(prefix="/state", tags=["v1-compat"])
auth_router = APIRouter(prefix="/auth", tags=["v1-compat"])


class V1MessageBody(BaseModel):
    """Request body for PATCH /process/{id}."""

    content: str = Field(description="Message content to send")


class V1HumanInputBody(BaseModel):
    """Request body for POST /process_human_input/{id}/human/{proxy}."""

    content: str = Field(description="Human input content")
    message: dict[str, object] = Field(description="Full BaseMessage object from frontend")


def get_team_service(request: Request) -> TeamService:
    """FastAPI dependency: extract TeamService from app.state."""
    return cast(TeamService, request.app.state.team_service)


def _to_v1_process_context(process: Process) -> V1ProcessContext:
    """Convert a V2 Process to a V1ProcessContext."""
    entry_point = process.team_card.entry_point
    return V1ProcessContext(
        id=str(process.team_id),
        type=process.team_card.name,
        status=process.status.value,
        created_at=process.created_at.isoformat(),
        updated_at=process.updated_at.isoformat(),
        params={"workspace": "false", "knowledge_graph": "false"},
        orchestrator=V1ActorAddress(
            name=entry_point.card.config.name,
            role=entry_point.card.role,
        ),
        running=process.status == TeamStatus.RUNNING,
        config_name=process.team_card.name,
        user_id=process.user_id,
        user_email=process.user_email,
    )


def _raise_action_error(exc: ValueError) -> NoReturn:
    """Map ValueError messages to appropriate HTTP status codes."""
    detail = str(exc)
    if "not found" in detail or "deleted" in detail:
        raise HTTPException(status_code=404, detail=detail) from None
    raise HTTPException(status_code=409, detail=detail) from None


def _parse_uuid(id_str: str) -> uuid.UUID:
    """Parse a string to UUID, raising 422 on failure."""
    try:
        return uuid.UUID(id_str)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid UUID: {id_str}") from None


def _events_to_v1_messages(events: list[PersistedEvent]) -> list[V1MessageEntry]:
    """Filter and transform persisted events to V1 message entries."""
    result: list[V1MessageEntry] = []
    for ev in events:
        content = extract_message_content(ev.event)
        if content is None:
            continue
        result.append(
            V1MessageEntry(
                id=str(ev.event.id),
                sender=get_sender_name(ev.event),
                content=content,
                timestamp=ev.timestamp.isoformat(),
                type=classify_message_type(ev.event),
            )
        )
    return result


# --- Process routes ---


@process_router.post("/{type}", response_model=V1ProcessContext)
def create_process(
    type: str,
    service: TeamService = Depends(get_team_service),
) -> V1ProcessContext:
    """POST /process/{type} -> create team via V2 service."""
    from akgentic.catalog.models.errors import EntryNotFoundError

    try:
        process = service.create_team(catalog_entry_id=type, user_id="anonymous")
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Catalog entry not found") from None
    return _to_v1_process_context(process)


@process_router.get("/", response_model=list[V1ProcessContext])
def list_processes(
    service: TeamService = Depends(get_team_service),
) -> list[V1ProcessContext]:
    """GET /process/ -> list teams as flat list."""
    processes = service.list_teams(user_id="anonymous")
    return [_to_v1_process_context(p) for p in processes]


@processes_router.get("/", response_model=list[V1ProcessContext])
def list_processes_alias(
    service: TeamService = Depends(get_team_service),
) -> list[V1ProcessContext]:
    """GET /processes -> alias for GET /process/, returns flat list."""
    return list_processes(service)


@process_router.get("/{id}", response_model=V1ProcessContext)
def get_process(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> V1ProcessContext:
    """GET /process/{id} -> get team with V1 response shape."""
    team_id = _parse_uuid(id)
    process = service.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return _to_v1_process_context(process)


@process_router.patch("/{id}", status_code=200, response_model=V1StatusResponse)
def send_process_message(
    id: str,
    body: V1MessageBody,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """PATCH /process/{id} -> send message via V2 service."""
    team_id = _parse_uuid(id)
    try:
        service.send_message(team_id, body.content)
    except ValueError as exc:
        _raise_action_error(exc)
    return V1StatusResponse(status="ok")


@process_router.delete("/{id}", status_code=200, response_model=V1StatusResponse)
def delete_process(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """DELETE /process/{id} -> delete team via V2 service."""
    team_id = _parse_uuid(id)
    try:
        service.delete_team(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    return V1StatusResponse(status="ok")


@process_router.delete("/{id}/archive", status_code=200, response_model=V1StatusResponse)
def archive_process(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """DELETE /process/{id}/archive -> stop team via V2 service."""
    team_id = _parse_uuid(id)
    try:
        service.stop_team(team_id)
    except ValueError as exc:
        _raise_action_error(exc)
    return V1StatusResponse(status="ok")


@process_router.post("/{id}/restore", response_model=V1ProcessContext)
def restore_process(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> V1ProcessContext:
    """POST /process/{id}/restore -> restore team via V2 service."""
    team_id = _parse_uuid(id)
    try:
        process = service.restore_team(team_id)
    except ValueError as exc:
        _raise_action_error(exc)
    return _to_v1_process_context(process)


# --- Human input route ---


@human_input_router.post("/{id}/human/{proxy}", status_code=200, response_model=V1StatusResponse)
def process_human_input(
    id: str,
    proxy: str,
    body: V1HumanInputBody,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """POST /process_human_input/{id}/human/{proxy} -> route human input.

    V1 had `proxy` in path but V2 finds HumanProxy automatically — ignore proxy param.
    """
    team_id = _parse_uuid(id)
    if "id" not in body.message:
        raise HTTPException(status_code=422, detail="message must contain 'id' field")
    message_id = str(body.message["id"])
    try:
        service.process_human_input(team_id, body.content, message_id)
    except ValueError as exc:
        _raise_action_error(exc)
    return V1StatusResponse(status="ok")


# --- Messages route ---


@messages_router.get("/{id}", response_model=list[V1MessageEntry])
def get_messages(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> list[V1MessageEntry]:
    """GET /messages/{id} -> event-sourced messages in V1 format."""
    team_id = _parse_uuid(id)
    try:
        events = service.get_events(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    return _events_to_v1_messages(events)


# --- LLM context route ---


@llm_context_router.get("/{id}", response_model=dict[str, object])
def get_llm_context(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> dict[str, object]:
    """GET /llm_context/{id} -> LLM context grouped by agent ID."""
    team_id = _parse_uuid(id)
    try:
        events = service.get_events(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    grouped: dict[str, list[dict[str, str]]] = {}
    for ev in events:
        content = extract_message_content(ev.event)
        if content is None:
            continue
        agent_id = get_sender_name(ev.event)
        grouped.setdefault(agent_id, [])
        grouped[agent_id].append(
            {
                "role": classify_message_type(ev.event),
                "content": content,
                "timestamp": ev.timestamp.isoformat(),
            }
        )
    return {agent_id: {"context": entries} for agent_id, entries in grouped.items()}


# --- States route ---


@states_router.get("/{id}", response_model=dict[str, object])
def get_states(
    id: str,
    service: TeamService = Depends(get_team_service),
) -> dict[str, object]:
    """GET /states/{id} -> agent states grouped by agent ID, latest per agent."""
    team_id = _parse_uuid(id)
    try:
        events = service.get_events(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    latest: dict[str, object] = {}
    for ev in events:
        if isinstance(ev.event, StateChangedMessage):
            agent_name = get_sender_name(ev.event)
            latest[agent_name] = {
                "schema": {},
                "state": ev.event.state.model_dump(mode="json"),
            }
    return latest


# --- Description route (extends process_router) ---


@process_router.patch(
    "/{id}/description",
    status_code=200,
    response_model=V1StatusResponse,
)
def update_description(
    id: str,
    body: V1DescriptionBody,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """PATCH /process/{id}/description -> no-op, V2 descriptions are immutable."""
    team_id = _parse_uuid(id)
    process = service.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return V1StatusResponse(status="ok")


# --- Relaunch route (extends process_router) ---


@relaunch_router.post(
    "/{id}/message/{msg_id}",
    status_code=200,
    response_model=V1StatusResponse,
)
def relaunch_message(
    id: str,
    msg_id: str,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """POST /relaunch/{id}/message/{msgId} -> re-send original message to team."""
    team_id = _parse_uuid(id)
    try:
        events = service.get_events(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    original_content: str | None = None
    for ev in events:
        if str(ev.event.id) == msg_id:
            original_content = extract_message_content(ev.event)
            break
    if original_content is None:
        raise HTTPException(status_code=404, detail="Message not found")
    try:
        service.send_message(team_id, original_content)
    except ValueError as exc:
        _raise_action_error(exc)
    return V1StatusResponse(status="ok")


# --- State update route ---


@state_update_router.patch(
    "/{id}/of/{agent}",
    status_code=200,
    response_model=V1StatusResponse,
)
def update_agent_state(
    id: str,
    agent: str,
    body: V1StateUpdateBody,
    service: TeamService = Depends(get_team_service),
) -> V1StatusResponse:
    """PATCH /state/{id}/of/{agent} -> send content to specific agent."""
    team_id = _parse_uuid(id)
    try:
        service.send_message_to(team_id, agent, body.content)
    except ValueError as exc:
        _raise_action_error(exc)
    return V1StatusResponse(status="ok")


# --- Config routes ---


def _get_catalog_for_type(request: Request, config_type: str) -> Any:
    """Look up the catalog service for a given config type."""
    services = request.app.state.services
    catalogs: dict[str, Any] = {
        "team": services.team_catalog,
        "agent": services.agent_catalog,
        "tool": services.tool_catalog,
        "template": services.template_catalog,
    }
    catalog = catalogs.get(config_type)
    if catalog is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown config type: {config_type}",
        )
    return catalog


@config_router.get("/{config_type}", response_model=list[V1ConfigEntry])
def get_config(config_type: str, request: Request) -> list[V1ConfigEntry]:
    """GET /config/{type} -> list catalog entries by type."""
    catalog = _get_catalog_for_type(request, config_type)
    entries = catalog.list()
    return [
        V1ConfigEntry(
            id=entry.id,
            type=config_type,
            data=entry.model_dump(mode="json"),
        )
        for entry in entries
    ]


@config_router.put("/{config_type}", status_code=200, response_model=V1StatusResponse)
def put_config(
    config_type: str,
    body: V1ConfigPutBody,
    request: Request,
) -> V1StatusResponse:
    """PUT /config/{config_type} -> create or update a catalog entry."""
    catalog = _get_catalog_for_type(request, config_type)
    existing = catalog.get(body.id)
    if existing is not None:
        catalog.update(body.id, existing.__class__.model_validate(body.config))
    else:
        existing_entries = catalog.list()
        entry_cls = type(existing_entries[0]) if existing_entries else None
        if entry_cls is None:
            raise HTTPException(status_code=400, detail="Cannot determine entry type")
        catalog.create(entry_cls.model_validate(body.config))
    return V1StatusResponse(status="ok")


@config_router.delete(
    "/{config_type}/{config_id}",
    status_code=200,
    response_model=V1StatusResponse,
)
def delete_config(
    config_type: str,
    config_id: str,
    request: Request,
) -> V1StatusResponse:
    """DELETE /config/{config_type}/{config_id} -> delete a catalog entry."""
    catalog = _get_catalog_for_type(request, config_type)
    existing = catalog.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Config entry not found")
    catalog.delete(config_id)
    return V1StatusResponse(status="ok")


# --- Team configs route ---


@team_configs_router.get("/", response_model=dict[str, object])
def get_team_configs(request: Request) -> dict[str, object]:
    """GET /team-configs -> dict keyed by team name for V1 frontend."""
    catalog = request.app.state.services.team_catalog
    entries = catalog.list()
    return {
        entry.id: {
            "module": entry.id,
            "setup": json.dumps(entry.model_dump(mode="json")),
        }
        for entry in entries
    }


# --- Feedback routes (stubs — no V2 feedback system) ---


@feedback_router.get("/get-feedback", response_model=list[V1FeedbackEntry])
def get_feedback() -> list[V1FeedbackEntry]:
    """GET /get-feedback -> stub returning empty list."""
    return []


@feedback_router.post("/set-feedback", status_code=200, response_model=V1StatusResponse)
def set_feedback(body: V1FeedbackEntry) -> V1StatusResponse:
    """POST /set-feedback -> stub returning ok."""
    return V1StatusResponse(status="ok")


# --- Auth stub routes (Community tier NoAuth) ---


@auth_router.get("/me")
def auth_me() -> dict[str, str]:
    """GET /auth/me -> anonymous user stub for Community tier."""
    return {"user_id": "anonymous", "email": "", "name": "Anonymous"}


@auth_router.get("/ws-ticket")
def auth_ws_ticket() -> dict[str, str]:
    """GET /auth/ws-ticket -> noauth ticket stub for Community tier."""
    return {"ticket": "noauth"}


@auth_router.get("/logout")
def auth_logout() -> dict[str, str]:
    """GET /auth/logout -> no-op logout stub for Community tier."""
    return {"auth_type": "none"}
