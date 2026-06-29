"""NoAuth — community-tier authentication strategy (ADR-034 Decision 3)."""

from __future__ import annotations

from starlette.requests import HTTPConnection
from starlette.routing import BaseRoute

from akgentic.infra.server.auth import RequestUser


class NoAuth:
    """Community-tier strategy: the trivial anonymous resolver.

    Community identity is the anonymous principal; ``resolve_request_user``
    never raises and the tier mounts no ``RequireAuth`` middleware. ``NoAuth``
    now satisfies the real ``AuthStrategy`` contract (ADR-034) — behaviour is
    byte-identical to the prior marker.
    """

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        """Resolve the community anonymous principal (never raises)."""
        return RequestUser(user_id="anonymous")

    def get_auth_routes(self) -> list[BaseRoute]:
        """Community exposes no ``/auth/*`` routes."""
        return []
