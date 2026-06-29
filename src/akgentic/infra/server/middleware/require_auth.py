"""Shared pre-routing authentication middleware building block (ADR-034 Decision 2a).

One ASGI ``RequireAuthMiddleware`` that, per non-``OPTIONS``/non-allowlisted
``http``/``websocket`` scope, awaits ``services.auth.resolve_request_user``
**exactly once** and stashes the resolved ``RequestUser`` on
``request.state.request_user`` — the same stash the gate, caller-identity
scope, and mutation-log audit read. A raising resolver rejects pre-routing
(WS close ``1008``, else ``JSONResponse``).

The load-bearing invariant — resolve-once + stash key + 401-on-raise
pre-routing — is **not** overridable. Only the edges are tier-pluggable:
``exact_allowlist`` / ``prefix_allowlist``, the ``requires_principal``
predicate, and the ``on_reject`` response shape. Tiers compose this block into
their own stack with their own allowlists (ADR-034 §Layered authorization).
"""

from __future__ import annotations

from collections.abc import Callable

from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

# Default 1008 = WebSocket "policy violation" close code (RFC 6455).
_WS_POLICY_VIOLATION = 1008

RequiresPrincipal = Callable[[HTTPConnection], bool]
OnReject = Callable[[HTTPConnection, HTTPException], Response]


class RequireAuthMiddleware:
    """Resolve the principal once per request and stash it pre-routing.

    Args:
        app: The wrapped ASGI application.
        exact_allowlist: Paths that bypass the guard by exact match.
        prefix_allowlist: Path prefixes that bypass the guard.
        requires_principal: Predicate deciding whether a connection must carry
            a principal. Defaults to "guard unless allowlisted" — a tier may
            supply a richer predicate to exempt paths authenticated by a
            different mechanism (e.g. HMAC-verified webhooks) without treating
            them as anonymous.
        on_reject: Builds the rejection response from the raised
            ``HTTPException``. Defaults to the WS-``1008`` / ``JSONResponse``
            shape; a tier may shape its own 401 (JSON vs redirect, headers).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        exact_allowlist: frozenset[str] = frozenset({"/readiness"}),
        prefix_allowlist: tuple[str, ...] = ("/auth/",),
        requires_principal: RequiresPrincipal | None = None,
        on_reject: OnReject | None = None,
    ) -> None:
        self.app = app
        self._exact_allowlist = exact_allowlist
        self._prefix_allowlist = prefix_allowlist
        self._requires_principal = requires_principal or self._default_requires_principal
        self._on_reject = on_reject or self._default_on_reject

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Resolve + stash the principal once, or reject pre-routing on 401."""
        if scope["type"] not in ("http", "websocket") or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)  # WS-safe; HTTP Request is a subclass
        if not self._requires_principal(connection):
            await self.app(scope, receive, send)
            return
        auth = connection.app.state.services.auth
        try:
            user = await auth.resolve_request_user(connection)
        except HTTPException as exc:
            await self._reject(scope, receive, send, exc)
            return
        scope.setdefault("state", {})["request_user"] = user
        await self.app(scope, receive, send)

    def _default_requires_principal(self, connection: HTTPConnection) -> bool:
        """Guard every request whose path is not allowlisted."""
        path = connection.scope.get("path", "")
        if path in self._exact_allowlist:
            return False
        return not path.startswith(self._prefix_allowlist)

    @staticmethod
    def _default_on_reject(connection: HTTPConnection, exc: HTTPException) -> Response:
        """Default rejection: a JSON 401 body (HTTP); WS uses the close path."""
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        exc: HTTPException,
    ) -> None:
        """Send the rejection: WS close ``1008``, else the ``on_reject`` response."""
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
            return
        response = self._on_reject(HTTPConnection(scope), exc)
        await response(scope, receive, send)
