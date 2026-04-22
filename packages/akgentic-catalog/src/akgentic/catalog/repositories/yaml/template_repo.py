"""YAML-backed repository for template catalog entries."""

from __future__ import annotations

import builtins
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.repositories.base import TemplateCatalogRepository
from akgentic.catalog.repositories.yaml._base import YamlRepositoryBase

if TYPE_CHECKING:
    from akgentic.catalog.models.queries import TemplateQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class YamlTemplateCatalogRepository(TemplateCatalogRepository, YamlRepositoryBase[TemplateEntry]):
    """YAML directory-backed template catalog repository."""

    _entry_type = TemplateEntry

    def __init__(self, catalog_dir: Path) -> None:
        """Initialize with the directory containing template YAML files.

        Args:
            catalog_dir: Path to the directory of template catalog YAML files.
        """
        YamlRepositoryBase.__init__(self, catalog_dir)

    def create(self, template_entry: TemplateEntry) -> str:
        """Persist a new template entry."""
        return YamlRepositoryBase.create(self, template_entry)

    def get(self, id: str) -> TemplateEntry | None:
        """Retrieve a template entry by id."""
        return YamlRepositoryBase.get(self, id)

    def list(self) -> _list[TemplateEntry]:
        """Return all template entries."""
        return YamlRepositoryBase.list(self)

    def update(self, id: str, template_entry: TemplateEntry) -> None:
        """Update an existing template entry."""
        YamlRepositoryBase.update(self, id, template_entry)

    def delete(self, id: str) -> None:
        """Delete a template entry by id."""
        YamlRepositoryBase.delete(self, id)

    def search(self, query: TemplateQuery) -> _list[TemplateEntry]:
        """Filter templates by AND-ing all non-None query fields.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching template entries.
        """
        results: _list[TemplateEntry] = []
        for entry in self._ensure_loaded():
            if query.id is not None and entry.id != query.id:
                continue
            if query.placeholder is not None and query.placeholder not in entry.placeholders:
                continue
            results.append(entry)
        return results
