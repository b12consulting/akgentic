"""FastAPI authentication dependency for ``/admin/*`` routes.

Per ADR-023 §D3: administrative HTTP surfaces (``/admin/catalog/*`` today,
any future ``/admin/*`` family) are gated by the wired ``AuthStrategy`` as a
router-level FastAPI dependency. The dependency reads ``app.state.services.auth``
live, so community's ``NoAuth`` and enterprise's ``SsoRbacAuth`` work without
any code change — each tier wires its own strategy at app-build time.

The helper is kept intentionally tiny: all auth policy lives in the strategy
implementations (``NoAuth``, ``SsoRbacAuth``, etc.). This module is only the
FastAPI glue.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

__all__ = ["require_authenticated_principal"]


def require_authenticated_principal(request: Request) -> str:
    """Resolve the principal ID from the wired ``AuthStrategy`` or raise 401.

    Reads the tier's wired strategy from ``request.app.state.services.auth``
    and delegates the actual authentication check to it. A ``None`` return
    from the strategy is treated as an authentication failure and surfaced as
    ``HTTPException(status_code=401, detail="authentication required")``.

    Args:
        request: The incoming FastAPI/Starlette request.

    Returns:
        The authenticated principal ID (string) as returned by the strategy.

    Raises:
        HTTPException: 401 when the wired strategy returns ``None``.
    """
    services = request.app.state.services
    principal: str | None = services.auth.authenticate(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal
