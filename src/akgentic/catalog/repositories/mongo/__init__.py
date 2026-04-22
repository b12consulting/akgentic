"""Public API surface for MongoDB-backed catalog repositories.

Gates all MongoDB imports behind a pymongo availability check. When pymongo
is not installed, importing this package raises ImportError with installation
instructions. When pymongo IS available, re-exports MongoCatalogConfig and
shared document helpers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import pymongo  # noqa: F401 — side-effect import to verify availability
except ImportError as exc:
    logger.warning("pymongo is not installed; MongoDB backend is unavailable")
    raise ImportError(
        "pymongo is required for the MongoDB backend. "
        "Install with: pip install akgentic-catalog[mongo]"
    ) from exc

# Only reached if pymongo is available
from akgentic.catalog.repositories.mongo._config import MongoCatalogConfig  # noqa: E402, I001
from akgentic.catalog.repositories.mongo._helpers import from_document  # noqa: E402
from akgentic.catalog.repositories.mongo._helpers import to_document  # noqa: E402
from akgentic.catalog.repositories.mongo.agent_repo import MongoAgentCatalogRepository  # noqa: E402
from akgentic.catalog.repositories.mongo.team_repo import MongoTeamCatalogRepository  # noqa: E402
from akgentic.catalog.repositories.mongo.template_repo import MongoTemplateCatalogRepository  # noqa: E402
from akgentic.catalog.repositories.mongo.tool_repo import MongoToolCatalogRepository  # noqa: E402

__all__ = [
    "MongoAgentCatalogRepository",
    "MongoCatalogConfig",
    "MongoTeamCatalogRepository",
    "MongoTemplateCatalogRepository",
    "MongoToolCatalogRepository",
    "from_document",
    "to_document",
]
