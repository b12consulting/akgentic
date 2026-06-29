"""Team CRUD and action endpoints — create, list, get, delete, message, stop, restore, events."""

from __future__ import annotations

import logging
import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.models import (
    AgentStateListResponse,
    AgentStateResponse,
    CreateTeamRequest,
    EventListResponse,
    EventResponse,
    HumanInputRequest,
    SendMessageRequest,
    TeamListResponse,
    TeamResponse,
)
from akgentic.infra.server.routes._team_access import get_team_service, require_team_access
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.state_keys import CONNECTION_MANAGER
from akgentic.team.models import Process

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


def _process_to_response(process: Process) -> TeamResponse:
    """Convert a Process model to a TeamResponse."""
    team_name = process.team_card.name or process.catalog_namespace or str(process.team_id)
    return TeamResponse(
        team_id=process.team_id,
        name=team_name,
        status=process.status.value,
        user_id=process.user_id,
        created_at=process.created_at,
        updated_at=process.updated_at,
    )


@router.post("", status_code=201, response_model=TeamResponse)
def create_team(
    body: CreateTeamRequest,
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Create a new team from a catalog namespace."""
    logger.info("POST /teams — catalog_namespace=%s", body.catalog_namespace)
    try:
        process = service.create_team(
            catalog_namespace=body.catalog_namespace,
            user_id=user.user_id,
            user_email=user.email,
        )
    except EntryNotFoundError:
        logger.warning(
            "Team creation failed: catalog namespace %s not found",
            body.catalog_namespace,
        )
        raise HTTPException(status_code=404, detail="Catalog namespace not found") from None
    return _process_to_response(process)


@router.get("", response_model=TeamListResponse)
def list_teams(
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
    page: int = 1,
    size: int = 250,
) -> TeamListResponse:
    """List one numbered page of teams for the current user, plus the total count."""
    logger.debug("GET /teams — page=%s size=%s", page, size)
    page_slice, total = service.list_teams(user_id=user.user_id, page=page, size=size)
    return TeamListResponse(
        teams=[_process_to_response(p) for p in page_slice],
        total_count=total,
    )


@router.get(
    "/{team_id}",
    response_model=TeamResponse,
    dependencies=[Depends(require_team_access)],
)
def get_team(
    team_id: uuid.UUID,
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Get a single team by ID."""
    logger.debug("GET /teams/%s", team_id)
    process = service.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return _process_to_response(process)


@router.delete(
    "/{team_id}",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def delete_team(
    team_id: uuid.UUID,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Stop and delete a team."""
    logger.info("DELETE /teams/%s", team_id)
    try:
        service.delete_team(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None


# --- Action Endpoints ---


@router.post(
    "/{team_id}/message",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def send_message(
    team_id: uuid.UUID,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message to a running team."""
    logger.info("POST /teams/%s/message", team_id)
    try:
        service.send_message(team_id, body.content)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/message/{agent_name}",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def send_message_to_agent(
    team_id: uuid.UUID,
    agent_name: str,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message to a specific agent in a running team."""
    logger.info("POST /teams/%s/message/%s", team_id, agent_name)
    try:
        service.send_message_to(team_id, agent_name, body.content)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/message/from/{sender_name}/to/{recipient_name}",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def send_message_from_to(
    team_id: uuid.UUID,
    sender_name: str,
    recipient_name: str,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message from a specific agent to another agent in a running team."""
    logger.info("POST /teams/%s/message/from/%s/to/%s", team_id, sender_name, recipient_name)
    try:
        service.send_message_from_to(team_id, sender_name, recipient_name, body.content)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/human-input",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def human_input(
    team_id: uuid.UUID,
    body: HumanInputRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Provide human input in response to an agent request."""
    logger.info("POST /teams/%s/human-input", team_id)
    try:
        service.process_human_input(team_id, body.content, body.message_id)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/stop",
    status_code=204,
    dependencies=[Depends(require_team_access)],
)
def stop_team(
    team_id: uuid.UUID,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Stop a running team without deleting persisted data."""
    logger.info("POST /teams/%s/stop", team_id)
    try:
        service.stop_team(team_id)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/restore",
    status_code=200,
    response_model=TeamResponse,
    dependencies=[Depends(require_team_access)],
)
def restore_team(
    team_id: uuid.UUID,
    request: Request,
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Restore a stopped team and notify waiting WebSocket connections."""
    logger.info("POST /teams/%s/restore", team_id)
    try:
        process = service.restore_team(team_id)
    except ValueError as exc:
        _raise_action_error(exc)

    conn_mgr = CONNECTION_MANAGER.get(request)
    if conn_mgr is not None:
        from akgentic.infra.server.routes.ws import notify_restore

        notify_restore(conn_mgr, service, team_id)

    return _process_to_response(process)


@router.get(
    "/{team_id}/events",
    response_model=EventListResponse,
    dependencies=[Depends(require_team_access)],
)
def get_events(
    team_id: uuid.UUID,
    service: TeamService = Depends(get_team_service),
) -> EventListResponse:
    """Get all persisted events for a team."""
    logger.debug("GET /teams/%s/events", team_id)
    try:
        events = service.get_events(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    return EventListResponse(
        events=[
            EventResponse(
                team_id=ev.team_id,
                sequence=ev.sequence,
                event=ev.event.model_dump(mode="json"),
                timestamp=ev.timestamp,
            )
            for ev in events
        ]
    )


@router.get(
    "/{team_id}/agent-states",
    response_model=AgentStateListResponse,
    dependencies=[Depends(require_team_access)],
)
def get_agent_states(
    team_id: uuid.UUID,
    service: TeamService = Depends(get_team_service),
) -> AgentStateListResponse:
    """Get the latest persisted state snapshot for each agent of a team."""
    logger.debug("GET /teams/%s/agent-states", team_id)
    try:
        snapshots = service.get_agent_states(team_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    return AgentStateListResponse(
        states=[
            AgentStateResponse(
                agent_id=s.agent_id,
                name=s.name,
                state=s.state.model_dump(mode="json"),
                updated_at=s.updated_at,
            )
            for s in snapshots
        ]
    )


def _raise_action_error(exc: ValueError) -> NoReturn:
    """Map ValueError messages to appropriate HTTP status codes.

    Raises:
        HTTPException: 404 for not-found/deleted errors, 409 for state conflicts.

    Note:
        String matching on exception messages is fragile. Department/enterprise
        tiers should replace this with structured error codes (e.g. typed
        exception subclasses with an ``http_status`` attribute).
    """
    detail = str(exc)
    if "not found" in detail or "deleted" in detail:
        logger.debug("Action error (not found): %s", detail)
        raise HTTPException(status_code=404, detail=detail) from None
    logger.warning("Action error: %s", detail)
    raise HTTPException(status_code=409, detail=detail) from None
