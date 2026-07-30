"""Worker team operation routes.

create, message, send_to, send_from_to, human-input, get, stop, delete, resume.
"""

from __future__ import annotations

import logging
import uuid
from typing import NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from akgentic.core.messages.message import Message
from akgentic.core.messages.orchestrator import SentMessage
from akgentic.infra.adapters.community.local_team_handle import LocalTeamHandle
from akgentic.infra.server.models import (
    EmitMessageRequest,
    HumanInputRequest,
    SendMessageRequest,
    TeamResponse,
)
from akgentic.infra.server.routes._message_payload import decode_message, resolve_send_payload
from akgentic.infra.worker.deps import WorkerServices
from akgentic.team.models import Process, TeamCard, TeamRuntime
from akgentic.team.ports import EventStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


class WorkerCreateTeamRequest(BaseModel):
    """Request body for POST /teams on the worker.

    The worker receives the already-resolved TeamCard plus the caller identity
    (user_id, user_email) from the server — catalog resolution happens
    server-side. ``team_id`` lets the server pin the identifier it already
    handed out; the team manager generates one when it is absent.
    """

    team_card: TeamCard = Field(description="Pre-resolved TeamCard for team creation")
    user_id: str = Field(description="Authenticated user identifier (from server)")
    user_email: str = Field(default="", description="Authenticated user email (from server)")
    team_id: uuid.UUID | None = Field(
        default=None, description="Caller-supplied team identifier; auto-generated when absent"
    )


def get_services(request: Request) -> WorkerServices:
    """FastAPI dependency: extract WorkerServices from app.state."""
    return cast(WorkerServices, request.app.state.services)


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


@router.get("/{team_id}", response_model=TeamResponse)
def get_team(
    team_id: uuid.UUID,
    services: WorkerServices = Depends(get_services),
) -> TeamResponse:
    """Get team metadata by ID."""
    logger.info("GET /teams/%s", team_id)
    process = services.worker_handle.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return _process_to_response(process)


@router.post("", status_code=201, response_model=TeamResponse)
def create_team(
    body: WorkerCreateTeamRequest,
    services: WorkerServices = Depends(get_services),
) -> TeamResponse:
    """Create a new team from a pre-resolved TeamCard.

    The server resolves catalog_entry_id to a TeamCard and forwards it
    to the worker. The worker calls team_manager.create_team() directly.
    """
    logger.info("POST /teams — user_id=%s", body.user_id)
    runtime: TeamRuntime = services.team_manager.create_team(
        team_card=body.team_card,
        user_id=body.user_id,
        user_email=body.user_email,
        team_id=body.team_id,
    )
    # The message / notification / human-input routes resolve via runtime_cache.get,
    # so a team is only reachable once its handle is stored here.
    services.runtime_cache.store(runtime.id, LocalTeamHandle(runtime))
    process = services.worker_handle.get_team(runtime.id)
    if process is None:
        # Drop the handle stored above: this create failed, and nothing else
        # evicts a team that never reached the event store.
        services.runtime_cache.remove(runtime.id)
        msg = f"Team {runtime.id} was created but not found in event store"
        raise RuntimeError(msg)
    return _process_to_response(process)


@router.post("/{team_id}/message", status_code=204)
def send_message(
    team_id: uuid.UUID,
    body: SendMessageRequest,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Send a message to a running team on this worker."""
    logger.info("POST /teams/%s/message", team_id)
    handle = services.runtime_cache.get(team_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Team not found in worker cache")
    try:
        handle.send(resolve_send_payload(body))
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/message/{agent_name}", status_code=204)
def send_message_to_agent(
    team_id: uuid.UUID,
    agent_name: str,
    body: SendMessageRequest,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Send a message to a specific agent within a running team."""
    logger.info("POST /teams/%s/message/%s", team_id, agent_name)
    handle = services.runtime_cache.get(team_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Team not found in worker cache")
    try:
        handle.send_to(agent_name, resolve_send_payload(body))
    except ValueError as exc:
        _raise_action_error(exc)


@router.post(
    "/{team_id}/message/from/{sender_name}/to/{recipient_name}",
    status_code=204,
)
def send_message_from_to(
    team_id: uuid.UUID,
    sender_name: str,
    recipient_name: str,
    body: SendMessageRequest,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Send a message from a specific agent to another agent."""
    logger.info(
        "POST /teams/%s/message/from/%s/to/%s", team_id, sender_name, recipient_name
    )
    handle = services.runtime_cache.get(team_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Team not found in worker cache")
    try:
        handle.send_from_to(sender_name, recipient_name, resolve_send_payload(body))
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/notification", status_code=204)
def emit_notification(
    team_id: uuid.UUID,
    body: EmitMessageRequest,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Publish a pre-formed notification Message to a running team on this worker.

    Mirrors the server-side ``/notification`` route (ADR-22) on the worker tier so
    the department/enterprise deployments stop duplicating it. The ``__model__``-
    tagged ``message`` envelope is decoded into the concrete typed ``Message`` (400
    on a bad envelope) and handed to ``handle.emitMessage`` — durable store + live
    stream, no agent processing. The wire envelope is the DICT form the merged
    ``/message*`` routes use (``EmitMessageRequest.message`` is a serialized
    ``model_dump(mode="json")`` dict; ``decode_message`` takes that dict).
    """
    logger.info("POST /teams/%s/notification", team_id)
    message = decode_message(body.message)
    handle = services.runtime_cache.get(team_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Team not found in worker cache")
    try:
        handle.emitMessage(message)
    except ValueError as exc:
        _raise_action_error(exc)


def _find_message(
    event_store: EventStore, team_id: uuid.UUID, message_id: str
) -> Message:
    """Find a message by ID in persisted events.

    Raises:
        ValueError: If message not found.
    """
    events = event_store.load_events(team_id)
    for ev in events:
        if str(ev.event.id) == message_id:
            return ev.event
    msg = f"Message {message_id} not found"
    raise ValueError(msg)


@router.post("/{team_id}/human-input", status_code=204)
def human_input(
    team_id: uuid.UUID,
    body: HumanInputRequest,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Process human input for a running team."""
    logger.info("POST /teams/%s/human-input", team_id)
    handle = services.runtime_cache.get(team_id)
    if handle is None:
        raise HTTPException(status_code=404, detail="Team not found in worker cache")
    try:
        event = _find_message(services.event_store, team_id, body.message_id)
        if not isinstance(event, SentMessage):
            msg = f"Message {body.message_id} is a {type(event).__name__}, expected SentMessage"
            raise ValueError(msg)
        inner = event.message
        handle.process_human_input(body.content, inner)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/stop", status_code=204)
def stop_team(
    team_id: uuid.UUID,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Stop a running team without deleting persisted data."""
    logger.info("POST /teams/%s/stop", team_id)
    try:
        services.worker_handle.stop_team(team_id)
        # Evict only once the stop succeeded, so the cache never diverges from
        # team state. A rejected stop leaves the handle cached; the eviction
        # subscriber reclaims it when the orchestrator does tear down.
        services.runtime_cache.remove(team_id)
    except ValueError as exc:
        _raise_action_error(exc)


@router.delete("/{team_id}", status_code=204)
def delete_team(
    team_id: uuid.UUID,
    services: WorkerServices = Depends(get_services),
) -> None:
    """Delete a team and its resources."""
    logger.info("DELETE /teams/%s", team_id)
    try:
        services.worker_handle.delete_team(team_id)
        # Only evict once the delete actually succeeded: a rejected delete (team
        # still RUNNING) leaves the team live, and its handle must stay reachable.
        services.runtime_cache.remove(team_id)
    except ValueError as exc:
        _raise_action_error(exc)


@router.post("/{team_id}/resume", status_code=200, response_model=TeamResponse)
def resume_team(
    team_id: uuid.UUID,
    services: WorkerServices = Depends(get_services),
) -> TeamResponse:
    """Resume a stopped team and return its metadata."""
    logger.info("POST /teams/%s/resume", team_id)
    try:
        handle = services.worker_handle.resume_team(team_id)
    except ValueError as exc:
        _raise_action_error(exc)
    services.runtime_cache.store(team_id, handle)
    process = services.worker_handle.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found after resume")
    return _process_to_response(process)


def _raise_action_error(exc: ValueError) -> NoReturn:
    """Map ValueError messages to appropriate HTTP status codes.

    Raises:
        HTTPException: 404 for not-found/deleted errors, 409 for state conflicts.
    """
    detail = str(exc)
    if "not found" in detail or "deleted" in detail:
        raise HTTPException(status_code=404, detail=detail) from None
    raise HTTPException(status_code=409, detail=detail) from None
