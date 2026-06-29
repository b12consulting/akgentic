"""ASGI middleware emitting one structured INFO log per ``/admin/catalog/*`` mutation.

Per ADR-023 §D5 / ADR-022 §D7: every ``POST`` / ``PUT`` / ``DELETE`` targeting a
route under ``/admin/catalog/*`` emits one INFO-level structured log line after
the handler returns. ``GET`` requests are silent. The log fires regardless of
response status — a 409 is logged alongside a 201.

Fields emitted on every mutation:

* ``principal_id`` — read from the ``RequestUser`` the ``/admin/*`` auth gate
  stashes on ``request.state`` (``_auth_dep.require_authenticated_principal``).
  This middleware has no FastAPI DI context, so it cannot ``Depends`` the seam
  itself; it reads the gate's stash, which is populated before the handler runs.
* ``kind`` — the ``{kind}`` segment of the URL path (``team``, ``agent``, ...).
* ``namespace`` — the ``namespace`` query string value when present; empty
  string when absent (list / create-without-namespace paths).
* ``entry_id`` — the ``{id}`` segment after ``{kind}``; empty string when
  absent (POST on the collection endpoint).
* ``operation`` — derived from HTTP method: ``POST → create``, ``PUT →
  update``, ``DELETE → delete``.
* ``status_code`` — the HTTP status the handler produced.

The v2 unified router does not emit this log itself, so infra owns the
concern. Implementing as a pure ASGI middleware avoids the FastAPI
dependency ordering caveats and cleanly separates the concern from the
authentication gate.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["AdminCatalogMutationLogMiddleware"]

logger = logging.getLogger(__name__)

_ADMIN_CATALOG_PREFIX = "/admin/catalog/"
_MUTATION_METHODS: dict[str, str] = {
    "POST": "create",
    "PUT": "update",
    "DELETE": "delete",
}


class AdminCatalogMutationLogMiddleware(BaseHTTPMiddleware):
    """Emit one INFO log line per admin-catalog mutation response.

    Filters on the URL path prefix (``/admin/catalog/``) and the HTTP method
    set (``POST`` / ``PUT`` / ``DELETE``) internally, so the middleware is
    safe to register globally on the FastAPI app.

    The middleware accepts ``**kwargs`` so FastAPI's ``add_middleware``
    forwarding behaviour (which passes any extra keyword arguments through)
    is compatible.
    """

    def __init__(self, app: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Dispatch the request and emit the mutation log after handler returns."""
        response = await call_next(request)
        if not self._is_logged(request):
            return response
        self._emit_log(request, response.status_code)
        return response

    @staticmethod
    def _is_logged(request: Request) -> bool:
        """True when the request is an admin-catalog mutation we log."""
        if request.method not in _MUTATION_METHODS:
            return False
        return request.url.path.startswith(_ADMIN_CATALOG_PREFIX)

    def _emit_log(self, request: Request, status_code: int) -> None:
        """Build the structured log record and emit it at INFO level."""
        principal_id = self._resolve_principal_id(request)
        kind, entry_id = _parse_kind_and_id(request.url.path)
        namespace = request.query_params.get("namespace", "")
        operation = _MUTATION_METHODS[request.method]
        logger.info(
            "admin catalog mutation",
            extra={
                "principal_id": principal_id,
                "kind": kind,
                "namespace": namespace,
                "entry_id": entry_id,
                "operation": operation,
                "status_code": status_code,
            },
        )

    @staticmethod
    def _resolve_principal_id(request: Request) -> str:
        """Return the principal id from the gate's ``request.state`` stash.

        The ``/admin/*`` auth gate (``require_authenticated_principal``) runs
        before the handler and stashes the resolved ``RequestUser`` on
        ``request.state.request_user``. By the time this post-response hook
        fires, the stash is present. A missing stash (defensive guard for
        non-standard app shapes in tests) degrades to an empty string rather
        than crashing the middleware chain.
        """
        user = getattr(request.state, "request_user", None)
        return user.user_id if user is not None else ""


def _parse_kind_and_id(path: str) -> tuple[str, str]:
    """Extract ``(kind, entry_id)`` from an ``/admin/catalog/...`` path.

    Returns ``("", "")`` for paths that do not match the expected shape.
    ``entry_id`` is the empty string when the path targets the collection
    endpoint (e.g. ``POST /admin/catalog/team``).
    """
    remainder = path[len(_ADMIN_CATALOG_PREFIX):]
    segments = [s for s in remainder.split("/") if s]
    if not segments:
        return "", ""
    kind = segments[0]
    entry_id = segments[1] if len(segments) > 1 else ""
    return kind, entry_id
