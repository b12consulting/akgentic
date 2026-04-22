"""MongoDB-backed repository for template catalog entries."""

from __future__ import annotations

import builtins
import logging
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.repositories.base import TemplateCatalogRepository
from akgentic.catalog.repositories.mongo._helpers import from_document, to_document

if TYPE_CHECKING:
    import pymongo.collection

    from akgentic.catalog.models.queries import TemplateQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class MongoTemplateCatalogRepository(TemplateCatalogRepository):
    """MongoDB-backed template catalog repository.

    Args:
        collection: A pymongo Collection for template entries.
    """

    def __init__(self, collection: pymongo.collection.Collection) -> None:  # type: ignore[type-arg]
        """Initialize with a pymongo Collection and ensure unique index on _id.

        Args:
            collection: The MongoDB collection for template entries.
        """
        self._collection = collection
        # _id is inherently unique in MongoDB — no explicit index needed.
        logger.info("MongoTemplateCatalogRepository initialized")

    def create(self, template_entry: TemplateEntry) -> str:
        """Persist a new template entry.

        Args:
            template_entry: The template entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        doc = to_document(template_entry)
        try:
            self._collection.insert_one(doc)
        except DuplicateKeyError:
            raise CatalogValidationError([f"Entry with id '{template_entry.id}' already exists"])
        logger.debug("Created template entry with id=%s", template_entry.id)
        return template_entry.id

    def get(self, id: str) -> TemplateEntry | None:
        """Retrieve a template entry by id.

        Args:
            id: The template entry id.

        Returns:
            The template entry, or None if not found.
        """
        doc = self._collection.find_one({"_id": id})
        if doc is None:
            logger.debug("Template entry not found: id=%s", id)
            return None
        return from_document(doc, TemplateEntry)

    def list(self) -> _list[TemplateEntry]:
        """Return all template entries."""
        entries = [from_document(doc, TemplateEntry) for doc in self._collection.find()]
        logger.debug("Listed %d template entries", len(entries))
        return entries

    def search(self, query: TemplateQuery) -> _list[TemplateEntry]:
        """Filter templates by AND-ing all non-None query fields.

        For ``id``, uses server-side exact match. For ``placeholder``, uses
        client-side filtering on the computed ``placeholders`` field after
        hydration via ``from_document()``.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching template entries.
        """
        mongo_filter: dict[str, str] = {}
        if query.id is not None:
            mongo_filter["_id"] = query.id

        candidates = [
            from_document(doc, TemplateEntry) for doc in self._collection.find(mongo_filter)
        ]

        if query.placeholder is not None:
            candidates = [entry for entry in candidates if query.placeholder in entry.placeholders]

        logger.debug("Search returned %d template entries", len(candidates))
        return candidates

    def update(self, id: str, template_entry: TemplateEntry) -> None:
        """Update an existing template entry.

        Args:
            id: The id of the entry to update.
            template_entry: The new entry data.

        Raises:
            CatalogValidationError: If template_entry.id does not match id.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if template_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{template_entry.id}'"]
            )
        doc = to_document(template_entry)
        result = self._collection.replace_one({"_id": id}, doc)
        if result.matched_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated template entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a template entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        result = self._collection.delete_one({"_id": id})
        if result.deleted_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted template entry with id=%s", id)
