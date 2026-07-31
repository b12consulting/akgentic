"""Request-user identity seam for tier-agnostic server routes (ADR-023, ADR-034)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection


class RequestUser(BaseModel):
    """The authenticated principal for a server request, tier-agnostic.

    Deployment tiers adapt their own auth identity into this shape.
    Community supplies the anonymous default. Routes never see a
    tier-specific identity type.

    ``scopes`` is the additive, default-empty inbound-authz axis: it carries
    the principal's granted scopes for scope-based authorization, alongside the
    coarser ``roles`` axis (ADR-035).
    """

    user_id: str
    email: str = ""
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


def get_request_user(conn: HTTPConnection) -> RequestUser:
    """Resolve the authenticated principal for an HTTP request or WS connection.

    Reads the ``RequestUser`` the shared ``RequireAuthMiddleware`` stashes on
    ``conn.state.request_user`` (ADR-034 Decision 2b — auth runs once per
    request). ``HTTPConnection`` is the common base of ``Request`` and
    ``WebSocket``, so the WS route resolves identity through this same seam.
    When no middleware has populated the stash (the community tier, which mounts
    none), falls back to the anonymous default — never ``None``, never raises.
    """
    user = getattr(conn.state, "request_user", None)
    if isinstance(user, RequestUser):
        return user
    return RequestUser(user_id="anonymous")
