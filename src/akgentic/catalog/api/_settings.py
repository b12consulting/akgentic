"""Settings for the catalog API router.

This module owns the single tunable that decides whether the generic
``/catalog/{kind}`` CRUD family is registered on the API router. Issue
b12consulting/akgentic-catalog#136 (Story 16.7) introduces the split so that
community-tier deployments do not advertise low-level entry CRUD routes that
a basic frontend cannot drive correctly, while keeping the routes available
for power-user CLIs, admin UIs, and enterprise tiers that opt in via the
``AKGENTIC_CATALOG_EXPOSE_GENERIC_KIND_CRUD`` environment variable.

The setting deliberately lives inside ``akgentic-catalog`` rather than
``akgentic-infra`` so the catalog package stays self-contained: nothing in
the router depends on the infra server's ``ServerSettings``, and the flag
can be toggled by any deployment that mounts the catalog router directly.
See CLAUDE.md Golden Rule #4 (never modify code outside the active
submodule) for the rationale.
"""

from __future__ import annotations

import os
from typing import ClassVar

from pydantic import BaseModel, Field

__all__ = ["CatalogRouterSettings"]


_ENV_VAR = "AKGENTIC_CATALOG_EXPOSE_GENERIC_KIND_CRUD"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})


class CatalogRouterSettings(BaseModel):
    """Configuration for the ``/catalog`` FastAPI router.

    Attributes:
        expose_generic_kind_crud: When ``True``, the eight generic
            ``/catalog/{kind}`` CRUD routes are registered on the router.
            When ``False`` (default) those routes are **not registered** —
            requests return HTTP 404 and the paths are absent from
            ``/openapi.json`` and Swagger UI. The namespace-scoped routes
            (``/catalog/namespaces``, ``/catalog/namespace/*``,
            ``/catalog/team/{namespace}/resolve``, ``/catalog/schema``,
            ``/catalog/model_types``, ``/catalog/clone``) are unaffected.

    The environment variable ``AKGENTIC_CATALOG_EXPOSE_GENERIC_KIND_CRUD``
    overrides the default via :meth:`from_env`. Truthy values: ``1``,
    ``true``, ``yes``, ``on`` (case-insensitive). Falsy values: ``0``,
    ``false``, ``no``, ``off``, empty string. Any other value raises
    :class:`ValueError`.
    """

    env_var: ClassVar[str] = _ENV_VAR

    expose_generic_kind_crud: bool = Field(
        default=False,
        description=(
            "If True, register the eight generic /catalog/{kind} CRUD routes. "
            "Defaults to False for community-tier deployments."
        ),
    )

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> CatalogRouterSettings:
        """Build settings from ``os.environ`` (or an explicit mapping).

        Args:
            environ: Mapping of environment variables. Defaults to
                ``os.environ`` when omitted. Tests pass an explicit dict to
                avoid leaking into the ambient process environment.

        Returns:
            A populated :class:`CatalogRouterSettings` instance.

        Raises:
            ValueError: If ``AKGENTIC_CATALOG_EXPOSE_GENERIC_KIND_CRUD`` is
                set to a value that is neither truthy nor falsy.
        """
        env = environ if environ is not None else os.environ
        raw = env.get(_ENV_VAR)
        if raw is None:
            return cls()
        normalised = raw.strip().lower()
        if normalised in _TRUTHY:
            return cls(expose_generic_kind_crud=True)
        if normalised in _FALSY:
            return cls(expose_generic_kind_crud=False)
        msg = (
            f"{_ENV_VAR}={raw!r} is not a recognised boolean "
            f"(expected one of {sorted(_TRUTHY | _FALSY)})"
        )
        raise ValueError(msg)
