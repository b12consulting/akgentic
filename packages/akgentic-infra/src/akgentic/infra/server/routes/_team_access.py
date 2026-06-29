"""Resource-ownership authorization gate for the per-``team_id`` routes.

Implements ADR-034 §Layered authorization: a single infra-owned FastAPI
dependency, ``require_team_access``, attached per-route to the team routes that
resolve a specific ``team_id``. It is **authorization, not authentication** — a
``Depends`` at the route consuming the principal the auth seam already
resolved, mirroring ADR-028's ``require_namespace_owner_or_admin``.

The rule, keyed on the team owner ``Process.user_id``::

    allow iff process.user_id == caller.user_id OR "admin" in caller.roles

A missing team and a non-owner non-admin both raise **404** — 404-over-403, so
the gate never leaks the existence of a team the caller may not see. The
``admin`` role bypasses ownership. The team-access seam is the existing infra
``TeamService.get_team`` (resolved from ``app.state``); the RBAC role
*vocabulary* stays tier-side — this gate only checks ``"admin"`` membership.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, HTTPException, Request

from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.state_keys import TEAM_SERVICE
from akgentic.team.models import Process

logger = logging.getLogger(__name__)

__all__ = [
    "get_team_service",
    "owner_or_admin",
    "require_team_access",
    "require_workspace_access",
]

_ADMIN_ROLE = "admin"


def get_team_service(request: Request) -> TeamService:
    """Team-access seam: the wired ``TeamService`` from ``app.state``."""
    return TEAM_SERVICE.require(request)


def _owner_or_admin(process: Process, user: RequestUser) -> bool:
    """The single owner-or-admin rule: caller owns the team or holds ``admin``."""
    return process.user_id == user.user_id or _ADMIN_ROLE in user.roles


def owner_or_admin(process: Process, user: RequestUser) -> bool:
    """Public seam for the owner-or-admin rule, reused by the WS handler.

    The WS route has no ``Depends`` 404 path, so it applies the rule in-handler
    rather than importing the module-private ``_owner_or_admin`` — the rule stays
    defined exactly once.
    """
    return _owner_or_admin(process, user)


def require_team_access(
    team_id: uuid.UUID,
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
) -> RequestUser:
    """Authorize a per-team route: caller must own ``team_id`` or be admin.

    Args:
        team_id: The target team, bound from the route path.
        user: The authenticated principal (always populated by the seam).
        service: The team-access seam resolving the ``Process`` by ``team_id``.

    Returns:
        The authenticated ``RequestUser`` on success.

    Raises:
        HTTPException: 404 when the team is missing OR the caller is neither
            the owner nor an admin (404-over-403, no existence leak).
    """
    process = service.get_team(team_id)
    if process is None or not _owner_or_admin(process, user):
        owner = None if process is None else process.user_id
        logger.info(
            "team-access gate denied",
            extra={"team_id": str(team_id), "user_id": user.user_id, "owner": owner},
        )
        raise HTTPException(status_code=404, detail="Team not found")
    return user


def require_workspace_access(
    workspace_id: str | None = None,
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
) -> RequestUser:
    """Authorize the optional ``?workspace_id=`` query param (ADR-034 question #5).

    A ``workspace_id`` that resolves to an existing **team** the caller neither
    owns nor (as ``admin``) may access raises **404** — a caller cannot read a
    foreign team's workspace by passing that team's id. A ``workspace_id`` that
    is not a team id (does not parse as a UUID, or parses but names no team — a
    shared / agent-configured segment, ADR-029) and an omitted param both pass
    through; the route's ``_validate_workspace_id`` 400 segment guard and the
    team-id fallback are unchanged.

    Args:
        workspace_id: The target workspace, bound from the route query by name.
        user: The authenticated principal (always populated by the seam).
        service: The team-access seam resolving the ``Process`` by team id.

    Returns:
        The authenticated ``RequestUser`` on success.

    Raises:
        HTTPException: 404 when ``workspace_id`` names an existing team the
            caller may not access (404-over-403, no existence leak).
    """
    if workspace_id is None:
        return user
    try:
        team_id = uuid.UUID(workspace_id)
    except ValueError:
        return user  # not a team id → shared/agent segment, pass through
    process = service.get_team(team_id)
    if process is None:
        return user  # a UUID that names no team → shared/agent segment
    if not _owner_or_admin(process, user):
        logger.info(
            "workspace-access gate denied",
            extra={
                "workspace_id": workspace_id,
                "user_id": user.user_id,
                "owner": process.user_id,
            },
        )
        raise HTTPException(status_code=404, detail="Team not found")
    return user
