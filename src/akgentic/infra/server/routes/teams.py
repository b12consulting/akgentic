"""Team CRUD and action endpoints — create, list, get, delete, message, stop, restore, events."""

from __future__ import annotations

import logging
import uuid
from typing import NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from akgentic.catalog.models.errors import EntryNotFoundError
from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.models import (
    CreateTeamRequest,
    EmitMessageRequest,
    EventListResponse,
    EventResponse,
    HumanInputRequest,
    SendMessageRequest,
    TeamListResponse,
    TeamResponse,
)
from akgentic.infra.server.routes._message_payload import decode_message, resolve_send_payload
from akgentic.infra.server.services.team_service import TeamService
from akgentic.team import EventNotFoundError
from akgentic.team.models import Process

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


def get_team_service(request: Request) -> TeamService:
    """FastAPI dependency: extract TeamService from app.state."""
    return cast(TeamService, request.app.state.team_service)


def _process_to_response(process: Process) -> TeamResponse:
    """Convert a Process model to a TeamResponse."""
    return TeamResponse(
        team_id=process.team_id,
        name=process.team_card.name,
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
    """Create a new team from a catalog entry."""
    logger.info("POST /teams — catalog_entry=%s", body.catalog_entry_id)
    try:
        process = service.create_team(
            catalog_entry_id=body.catalog_entry_id,
            user_id=user.user_id,
            user_email=user.email,
        )
    except EntryNotFoundError:
        logger.warning("Team creation failed: catalog entry %s not found", body.catalog_entry_id)
        raise HTTPException(status_code=404, detail="Catalog entry not found") from None
    return _process_to_response(process)


@router.get("", response_model=TeamListResponse)
def list_teams(
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
) -> TeamListResponse:
    """List all teams for the current user."""
    logger.debug("GET /teams")
    processes = service.list_teams(user_id=user.user_id)
    return TeamListResponse(teams=[_process_to_response(p) for p in processes])


@router.get("/{team_id}", response_model=TeamResponse)
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


@router.delete("/{team_id}", status_code=204)
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


@router.post("/{team_id}/message", status_code=204)
def send_message(
    team_id: uuid.UUID,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message to a running team.

    Accepts either a plain ``content`` string or a pre-formed typed ``Message``
    (the ``message`` wire envelope, deserialized to the concrete type for agent
    processing); a bad envelope is a 400 (ADR-22). Team-state errors map through
    ``_raise_action_error``.
    """
    logger.info("POST /teams/%s/message", team_id)
    try:
        service.send_message(team_id, resolve_send_payload(body))
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/message/{agent_name}", status_code=204)
def send_message_to_agent(
    team_id: uuid.UUID,
    agent_name: str,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message (plain ``content`` or typed ``message`` envelope) to a specific agent."""
    logger.info("POST /teams/%s/message/%s", team_id, agent_name)
    try:
        service.send_message_to(team_id, agent_name, resolve_send_payload(body))
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/message/from/{sender_name}/to/{recipient_name}", status_code=204)
def send_message_from_to(
    team_id: uuid.UUID,
    sender_name: str,
    recipient_name: str,
    body: SendMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Send a message (plain ``content`` or typed ``message``) from one agent to another."""
    logger.info("POST /teams/%s/message/from/%s/to/%s", team_id, sender_name, recipient_name)
    try:
        service.send_message_from_to(
            team_id, sender_name, recipient_name, resolve_send_payload(body)
        )
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/notification", status_code=204)
def emit_notification(
    team_id: uuid.UUID,
    body: EmitMessageRequest,
    service: TeamService = Depends(get_team_service),
) -> None:
    """Inject a pre-formed notification Message into a running team.

    Deserializes the ``__model__``-tagged payload back into the concrete
    ``Message`` and publishes it to the team's subscribers with no agent
    processing (ADR-22). A decode failure or a non-``Message`` payload is a
    client error (400); team-state errors map through ``_raise_action_error``.
    """
    logger.info("POST /teams/%s/notification", team_id)
    message = decode_message(body.message)
    try:
        service.emit_message(team_id, message)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/human-input", status_code=204)
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


@router.post("/{team_id}/stop", status_code=204)
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


@router.post("/{team_id}/restore", status_code=200, response_model=TeamResponse)
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

    conn_mgr = getattr(request.app.state, "connection_manager", None)
    if conn_mgr is not None:
        from akgentic.infra.server.routes.ws import notify_restore

        notify_restore(conn_mgr, service, team_id)

    return _process_to_response(process)


@router.get("/{team_id}/events", response_model=EventListResponse)
def get_events(
    team_id: uuid.UUID,
    after_event_id: uuid.UUID | None = None,
    service: TeamService = Depends(get_team_service),
) -> EventListResponse:
    """Get persisted events for a team, or only those after ``after_event_id``."""
    logger.debug("GET /teams/%s/events after_event_id=%s", team_id, after_event_id)
    try:
        events = service.get_events(team_id, after_event_id=after_event_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Team not found") from None
    except EventNotFoundError:
        # A stale cursor is a bad cursor, not a missing team — 404 here would tell
        # the client the team is gone and tear down a live team's view.
        raise HTTPException(
            status_code=400, detail=f"Unknown after_event_id cursor: {after_event_id}"
        ) from None
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
