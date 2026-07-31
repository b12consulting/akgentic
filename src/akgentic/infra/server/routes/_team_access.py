"""Resource-ownership authorization gate for the per-``team_id`` routes.

Implements ADR-034 §Layered authorization and ADR-035 Decision 8: a single
infra-owned FastAPI dependency, ``require_team_access``, attached per-route to
the team routes that resolve a specific ``team_id``. It is **authorization, not
authentication** — a ``Depends`` at the route consuming the principal the auth
seam already resolved, mirroring ADR-028's ``require_namespace_owner_or_admin``.

Infra keeps the team lookup and the load-bearing 404-over-403
no-existence-leak machinery non-overridable; only the allow/deny *rule* is
pluggable. The rule is a
:class:`~akgentic.infra.protocols.authz.TeamAccessPolicy` carried on the
services bundle (``TierServices.team_access_policy``); the base default is
:class:`~akgentic.infra.adapters.shared.owner_or_admin_policy.OwnerOrAdminPolicy`::

    allow iff process.user_id == caller.user_id OR "admin" in caller.roles

A missing team and a denied caller both raise **404** — 404-over-403, so the
gate never leaks the existence of a team the caller may not see. The missing
team is rejected *before* the policy is consulted. The team-access seam is the
existing infra ``TeamService.get_team`` (resolved from ``app.state``). The gate
is ``async`` so a tier policy may consult an external membership / RBAC store.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Depends, HTTPException, Request

from akgentic.infra.protocols.authz import TeamAccessContext, TeamAccessPolicy
from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.state_keys import SERVICES, TEAM_SERVICE

logger = logging.getLogger(__name__)

__all__ = [
    "get_team_access_policy",
    "get_team_service",
    "require_team_access",
    "require_workspace_access",
]


def get_team_service(request: Request) -> TeamService:
    """Team-access seam: the wired ``TeamService`` from ``app.state``."""
    return TEAM_SERVICE.require(request)


def get_team_access_policy(request: Request) -> TeamAccessPolicy:
    """Per-team authorization seam: the wired ``TeamAccessPolicy`` from ``app.state``."""
    return SERVICES.require(request).team_access_policy


async def require_team_access(
    team_id: uuid.UUID,
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
    policy: TeamAccessPolicy = Depends(get_team_access_policy),
) -> RequestUser:
    """Authorize a per-team route: the caller must satisfy the wired policy.

    Args:
        team_id: The target team, bound from the route path.
        user: The authenticated principal (always populated by the seam).
        service: The team-access seam resolving the ``Process`` by ``team_id``.
        policy: The wired per-team authorization rule (owner-or-admin default).

    Returns:
        The authenticated ``RequestUser`` on success.

    Raises:
        HTTPException: 404 when the team is missing (raised *before* the policy
            is consulted) OR the wired policy denies the caller (404-over-403,
            no existence leak).
    """
    process = service.get_team(team_id)
    if process is None:
        raise HTTPException(status_code=404, detail="Team not found")
    ctx = TeamAccessContext(team_id=team_id, owner_user_id=process.user_id)
    if not await policy.is_allowed(ctx=ctx, user=user):
        logger.info(
            "team-access gate denied",
            extra={"team_id": str(team_id), "user_id": user.user_id, "owner": process.user_id},
        )
        raise HTTPException(status_code=404, detail="Team not found")
    return user


async def require_workspace_access(
    workspace_id: str | None = None,
    user: RequestUser = Depends(get_request_user),
    service: TeamService = Depends(get_team_service),
    policy: TeamAccessPolicy = Depends(get_team_access_policy),
) -> RequestUser:
    """Authorize the optional ``?workspace_id=`` query param (ADR-034 question #5).

    A ``workspace_id`` that resolves to an existing **team** the caller may not
    access (per the wired policy) raises **404** — a caller cannot read a
    foreign team's workspace by passing that team's id. A ``workspace_id`` that
    is not a team id (does not parse as a UUID, or parses but names no team — a
    shared / agent-configured segment, ADR-029) and an omitted param both pass
    through **without consulting the policy**; the route's
    ``_validate_workspace_id`` 400 segment guard and the team-id fallback are
    unchanged.

    Args:
        workspace_id: The target workspace, bound from the route query by name.
        user: The authenticated principal (always populated by the seam).
        service: The team-access seam resolving the ``Process`` by team id.
        policy: The wired per-team authorization rule (owner-or-admin default).

    Returns:
        The authenticated ``RequestUser`` on success.

    Raises:
        HTTPException: 404 when ``workspace_id`` names an existing team the
            wired policy denies the caller (404-over-403, no existence leak).
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
    ctx = TeamAccessContext(team_id=team_id, owner_user_id=process.user_id)
    if not await policy.is_allowed(ctx=ctx, user=user):
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
