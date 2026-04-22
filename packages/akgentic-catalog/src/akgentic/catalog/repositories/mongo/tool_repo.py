"""MongoDB-backed repository for tool catalog entries."""

from __future__ import annotations

import builtins
import logging
import re
from typing import TYPE_CHECKING, Any

from pymongo.errors import DuplicateKeyError

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.tool import ToolEntry
from akgentic.catalog.repositories.base import ToolCatalogRepository
from akgentic.catalog.repositories.mongo._helpers import from_document, to_document

if TYPE_CHECKING:
    import pymongo.collection

    from akgentic.catalog.models.queries import ToolQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class MongoToolCatalogRepository(ToolCatalogRepository):
    """MongoDB-backed tool catalog repository.

    Args:
        collection: A pymongo Collection for tool entries.
    """

    def __init__(self, collection: pymongo.collection.Collection) -> None:  # type: ignore[type-arg]
        """Initialize with a pymongo Collection and ensure unique index on _id.

        Args:
            collection: The MongoDB collection for tool entries.
        """
        self._collection = collection
        # _id is inherently unique in MongoDB — no explicit index needed.
        logger.info("MongoToolCatalogRepository initialized")

    def create(self, tool_entry: ToolEntry) -> str:
        """Persist a new tool entry.

        Args:
            tool_entry: The tool entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        doc = to_document(tool_entry)
        try:
            self._collection.insert_one(doc)
        except DuplicateKeyError:
            raise CatalogValidationError([f"Entry with id '{tool_entry.id}' already exists"])
        logger.debug("Created tool entry with id=%s", tool_entry.id)
        return tool_entry.id

    def get(self, id: str) -> ToolEntry | None:
        """Retrieve a tool entry by id.

        Args:
            id: The tool entry id.

        Returns:
            The tool entry, or None if not found.
        """
        doc = self._collection.find_one({"_id": id})
        if doc is None:
            logger.debug("Tool entry not found: id=%s", id)
            return None
        return from_document(doc, ToolEntry)

    def list(self) -> _list[ToolEntry]:
        """Return all tool entries."""
        entries = [from_document(doc, ToolEntry) for doc in self._collection.find()]
        logger.debug("Listed %d tool entries", len(entries))
        return entries

    def search(self, query: ToolQuery) -> _list[ToolEntry]:
        """Filter tools by AND-ing all non-None query fields.

        Uses server-side exact match for ``id`` and ``tool_class``. Uses
        ``$regex`` with case-insensitive option for substring matching on
        ``name`` and ``description`` (nested under ``tool`` key in document).

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching tool entries.
        """
        mongo_filter: dict[str, Any] = {}
        if query.id is not None:
            mongo_filter["_id"] = query.id
        if query.tool_class is not None:
            mongo_filter["tool_class"] = query.tool_class
        if query.name is not None:
            mongo_filter["tool.name"] = {
                "$regex": re.escape(query.name),
                "$options": "i",
            }
        if query.description is not None:
            mongo_filter["tool.description"] = {
                "$regex": re.escape(query.description),
                "$options": "i",
            }

        results = [from_document(doc, ToolEntry) for doc in self._collection.find(mongo_filter)]
        logger.debug("Search returned %d tool entries", len(results))
        return results

    def update(self, id: str, tool_entry: ToolEntry) -> None:
        """Update an existing tool entry.

        Args:
            id: The id of the entry to update.
            tool_entry: The new entry data.

        Raises:
            CatalogValidationError: If tool_entry.id does not match id.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if tool_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{tool_entry.id}'"]
            )
        doc = to_document(tool_entry)
        result = self._collection.replace_one({"_id": id}, doc)
        if result.matched_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated tool entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a tool entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        result = self._collection.delete_one({"_id": id})
        if result.deleted_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted tool entry with id=%s", id)
