"""FastAPI authentication dependency for ``/admin/*`` routes.

Per ADR-023: administrative HTTP surfaces (``/admin/catalog/*`` today, any future
``/admin/*`` family) resolve their principal from the single ``get_request_user``
seam — the same source ``scope_catalog_caller_identity`` and the ADR-028
owner-or-admin gates already trust. There is ONE identity source across all tiers.

The gate never 401s on its own. Community's default ``get_request_user`` returns
``RequestUser(user_id="anonymous")`` and passes. The only 401 paths are a *raising*
``get_request_user`` override (dept/enterprise bad credentials) and the pre-routing
``RequireAuthMiddleware`` — both outside this module.
"""

from __future__ import annotations

import logging

from fastapi import Depends, Request

from akgentic.infra.server.auth import RequestUser, get_request_user

logger = logging.getLogger(__name__)

__all__ = ["require_authenticated_principal"]


def require_authenticated_principal(
    request: Request,
    user: RequestUser = Depends(get_request_user),
) -> str:
    """Resolve the principal ID from the ``get_request_user`` seam.

    Reads the request user through the ADR-023 seam (honouring each tier's
    ``app.dependency_overrides``) and returns its ``user_id``. The resolved
    ``RequestUser`` is stashed on ``request.state`` so the DI-less
    ``AdminCatalogMutationLogMiddleware`` can read the same principal. The gate
    adds no failure branch of its own — a raising override surfaces its own 401.

    Args:
        request: The incoming FastAPI/Starlette request.
        user: The authenticated principal, resolved via ``get_request_user``.

    Returns:
        The authenticated principal ID (``user.user_id``).
    """
    request.state.request_user = user
    return user.user_id
