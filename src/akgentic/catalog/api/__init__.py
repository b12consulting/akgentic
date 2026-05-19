"""FastAPI REST API for the Akgentic catalog.

Exposes :func:`create_app` which wires the unified ``/catalog`` router
against a YAML- or MongoDB-backed :class:`EntryRepository`.

Requires the ``api`` extra: ``pip install akgentic-catalog[api]``.
"""

from __future__ import annotations

try:
    import fastapi as _fastapi  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "FastAPI is required. Install with: pip install akgentic-catalog[api]"
    ) from exc

from akgentic.catalog.api._errors import ErrorResponse, add_exception_handlers
from akgentic.catalog.api.app import create_app

__all__ = [
    "ErrorResponse",
    "add_exception_handlers",
    "create_app",
]
