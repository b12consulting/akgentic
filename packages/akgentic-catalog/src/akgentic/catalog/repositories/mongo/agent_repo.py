"""MongoDB-backed repository for agent catalog entries."""

from __future__ import annotations

import builtins
import logging
import re
from typing import TYPE_CHECKING, Any

from pymongo.errors import DuplicateKeyError

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.repositories.base import AgentCatalogRepository
from akgentic.catalog.repositories.mongo._helpers import from_document, to_document

if TYPE_CHECKING:
    import pymongo.collection

    from akgentic.catalog.models.queries import AgentQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class MongoAgentCatalogRepository(AgentCatalogRepository):
    """MongoDB-backed agent catalog repository.

    Args:
        collection: A pymongo Collection for agent entries.
    """

    def __init__(self, collection: pymongo.collection.Collection) -> None:  # type: ignore[type-arg]
        """Initialize with a pymongo Collection and ensure indexes.

        Creates indexes on ``card.config.role`` (secondary) and ``card.skills``
        (multikey for ``$in`` queries). ``_id`` is inherently unique.

        Per ADR-007, ``AgentCard.role`` is a non-serialized ``@property``
        derived from ``config.role`` — the stored BSON has no ``card.role``
        key, so the secondary index targets ``card.config.role`` where the
        registry key actually lives.

        Args:
            collection: The MongoDB collection for agent entries.
        """
        self._collection = collection
        # _id is inherently unique in MongoDB — no explicit index needed.
        self._collection.create_index("card.config.role")
        self._collection.create_index("card.skills")
        logger.info(
            "MongoAgentCatalogRepository initialized with indexes on "
            "_id, card.config.role, card.skills"
        )

    def create(self, agent_entry: AgentEntry) -> str:
        """Persist a new agent entry.

        Args:
            agent_entry: The agent entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        doc = to_document(agent_entry)
        try:
            self._collection.insert_one(doc)
        except DuplicateKeyError as e:
            raise CatalogValidationError(
                [f"Entry with id '{agent_entry.id}' already exists"]
            ) from e
        logger.debug("Created agent entry with id=%s", agent_entry.id)
        return agent_entry.id

    def get(self, id: str) -> AgentEntry | None:
        """Retrieve an agent entry by id.

        Args:
            id: The agent entry id.

        Returns:
            The agent entry, or None if not found.
        """
        doc = self._collection.find_one({"_id": id})
        if doc is None:
            logger.debug("Agent entry not found: id=%s", id)
            return None
        return from_document(doc, AgentEntry)

    def list(self) -> _list[AgentEntry]:
        """Return all agent entries."""
        entries = [from_document(doc, AgentEntry) for doc in self._collection.find()]
        logger.debug("Listed %d agent entries", len(entries))
        return entries

    def search(self, query: AgentQuery) -> _list[AgentEntry]:
        """Filter agents by AND-ing all non-None query fields.

        Uses server-side exact match for ``id`` and ``card.config.role``.
        Uses ``$in`` for ``card.skills`` (ANY overlap semantics). Uses
        ``$regex`` with case-insensitive option for substring matching on
        ``card.description``.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching agent entries.
        """
        mongo_filter: dict[str, Any] = {}
        if query.id is not None:
            mongo_filter["_id"] = query.id
        if query.role is not None:
            mongo_filter["card.config.role"] = query.role
        if query.skills is not None:
            mongo_filter["card.skills"] = {"$in": query.skills}
        if query.description is not None:
            mongo_filter["card.description"] = {
                "$regex": re.escape(query.description),
                "$options": "i",
            }

        results = [from_document(doc, AgentEntry) for doc in self._collection.find(mongo_filter)]
        logger.debug("Search returned %d agent entries", len(results))
        return results

    def update(self, id: str, agent_entry: AgentEntry) -> None:
        """Update an existing agent entry.

        Args:
            id: The id of the entry to update.
            agent_entry: The new entry data.

        Raises:
            CatalogValidationError: If agent_entry.id does not match id.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if agent_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{agent_entry.id}'"]
            )
        doc = to_document(agent_entry)
        result = self._collection.replace_one({"_id": id}, doc)
        if result.matched_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated agent entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete an agent entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        result = self._collection.delete_one({"_id": id})
        if result.deleted_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted agent entry with id=%s", id)
