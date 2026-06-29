"""Tier-agnostic authentication contract (ADR-034 Decision 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection  # WS-safe; HTTP Request is a subclass
    from starlette.routing import BaseRoute

    from akgentic.infra.server.auth import RequestUser


@runtime_checkable
class AuthStrategy(Protocol):
    """Tier-agnostic authentication contract.

    A tier supplies one async resolver turning an inbound connection into the
    neutral infra ``RequestUser``, raising ``HTTPException(401)`` on bad/absent
    credentials. Identity then flows through the ``get_request_user`` seam; the
    shared ``RequireAuthMiddleware`` is the pre-routing 401 path. There is no
    synchronous entry point (removed in Story 40.1).
    """

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        """Resolve the authenticated principal, or raise ``HTTPException(401)``."""
        ...

    def get_auth_routes(self) -> list[BaseRoute]:
        """The tier's ``/auth/*`` routes; community returns ``[]``."""
        ...
