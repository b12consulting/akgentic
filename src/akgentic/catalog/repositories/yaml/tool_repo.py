"""YAML-backed repository for tool catalog entries."""

from __future__ import annotations

import builtins
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import ToolCatalogRepository
from akgentic.catalog.repositories.yaml._base import YamlRepositoryBase

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import ToolQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class YamlToolCatalogRepository(ToolCatalogRepository, YamlRepositoryBase[ToolEntry]):
    """YAML directory-backed tool catalog repository."""

    _entry_type = ToolEntry

    def __init__(self, catalog_dir: Path) -> None:
        """Initialize with the directory containing tool YAML files.

        Args:
            catalog_dir: Path to the directory of tool catalog YAML files.
        """
        YamlRepositoryBase.__init__(self, catalog_dir)

    def create(self, tool_entry: ToolEntry) -> str:
        """Persist a new tool entry."""
        return YamlRepositoryBase.create(self, tool_entry)

    def get(self, id: str) -> ToolEntry | None:
        """Retrieve a tool entry by id."""
        return YamlRepositoryBase.get(self, id)

    def list(self) -> _list[ToolEntry]:
        """Return all tool entries."""
        return YamlRepositoryBase.list(self)

    def update(self, id: str, tool_entry: ToolEntry) -> None:
        """Update an existing tool entry."""
        YamlRepositoryBase.update(self, id, tool_entry)

    def delete(self, id: str) -> None:
        """Delete a tool entry by id."""
        YamlRepositoryBase.delete(self, id)

    def search(self, query: ToolQuery) -> _list[ToolEntry]:
        """Filter tools by AND-ing all non-None query fields.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching tool entries.
        """
        results: _list[ToolEntry] = []
        for entry in self._ensure_loaded():
            if query.id is not None and entry.id != query.id:
                continue
            if query.tool_class is not None and entry.tool_class != query.tool_class:
                continue
            if query.name is not None and query.name.lower() not in entry.tool.name.lower():
                continue
            if (
                query.description is not None
                and query.description.lower() not in entry.tool.description.lower()
            ):
                continue
            results.append(entry)
        return results
