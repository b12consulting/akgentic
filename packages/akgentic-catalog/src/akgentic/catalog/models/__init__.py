"""Public API surface for catalog v2 data models.

Re-exports the unified ``Entry`` model and its supporting types
(``EntryKind``, ``AllowlistedPath``, ``NonEmptyStr``), the v2 query / clone
models (``EntryQuery``, ``CloneRequest``), and the v2-alive error types
(``CatalogValidationError``, ``EntryNotFoundError``).
"""

from __future__ import annotations

from akgentic.catalog.models.entry import AllowlistedPath, Entry, EntryKind, NonEmptyStr
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.namespace_meta import NamespaceMeta
from akgentic.catalog.models.queries import CloneRequest, EntryQuery

__all__ = [
    "AllowlistedPath",
    "CatalogValidationError",
    "CloneRequest",
    "Entry",
    "EntryKind",
    "EntryNotFoundError",
    "EntryQuery",
    "NamespaceMeta",
    "NonEmptyStr",
]
