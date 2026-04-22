"""MongoDB-backed repository for team catalog entries."""

from __future__ import annotations

import builtins
import logging
import re
from typing import TYPE_CHECKING, Any

from pymongo.errors import DuplicateKeyError

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.team import TeamEntry, agent_in_members
from akgentic.catalog.repositories.base import TeamCatalogRepository
from akgentic.catalog.repositories.mongo._helpers import from_document, to_document

if TYPE_CHECKING:
    import pymongo.collection

    from akgentic.catalog.models.queries import TeamQuery

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class MongoTeamCatalogRepository(TeamCatalogRepository):
    """MongoDB-backed team catalog repository.

    Args:
        collection: A pymongo Collection for team entries.
    """

    def __init__(self, collection: pymongo.collection.Collection) -> None:  # type: ignore[type-arg]
        """Initialize with a pymongo Collection and ensure unique index on _id.

        Args:
            collection: The MongoDB collection for team entries.
        """
        self._collection = collection
        # _id is inherently unique in MongoDB — no explicit index needed.
        logger.info("MongoTeamCatalogRepository initialized")

    def create(self, team_entry: TeamEntry) -> str:
        """Persist a new team entry.

        Args:
            team_entry: The team entry to create.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        doc = to_document(team_entry)
        try:
            self._collection.insert_one(doc)
        except DuplicateKeyError as e:
            raise CatalogValidationError([f"Entry with id '{team_entry.id}' already exists"]) from e
        logger.debug("Created team entry with id=%s", team_entry.id)
        return team_entry.id

    def get(self, id: str) -> TeamEntry | None:
        """Retrieve a team entry by id.

        Args:
            id: The team entry id.

        Returns:
            The team entry, or None if not found.
        """
        doc = self._collection.find_one({"_id": id})
        if doc is None:
            logger.debug("Team entry not found: id=%s", id)
            return None
        return from_document(doc, TeamEntry)

    def list(self) -> _list[TeamEntry]:
        """Return all team entries."""
        entries = [from_document(doc, TeamEntry) for doc in self._collection.find()]
        logger.debug("Listed %d team entries", len(entries))
        return entries

    def search(self, query: TeamQuery) -> _list[TeamEntry]:
        """Filter teams by AND-ing all non-None query fields.

        Applies server-side filters for ``id``, ``name``, and ``description``
        first, then applies client-side recursive ``agent_id`` filtering on
        hydrated results.

        Args:
            query: Query with optional filter fields.

        Returns:
            Matching team entries.
        """
        mongo_filter: dict[str, Any] = {}
        if query.id is not None:
            mongo_filter["_id"] = query.id
        if query.name is not None:
            mongo_filter["name"] = {
                "$regex": re.escape(query.name),
                "$options": "i",
            }
        if query.description is not None:
            mongo_filter["description"] = {
                "$regex": re.escape(query.description),
                "$options": "i",
            }

        results = [from_document(doc, TeamEntry) for doc in self._collection.find(mongo_filter)]

        # Client-side recursive filter for agent_id in member tree
        if query.agent_id is not None:
            results = [t for t in results if agent_in_members(query.agent_id, t.members)]

        logger.debug("Search returned %d team entries", len(results))
        return results

    def update(self, id: str, team_entry: TeamEntry) -> None:
        """Update an existing team entry.

        Args:
            id: The id of the entry to update.
            team_entry: The new entry data.

        Raises:
            CatalogValidationError: If team_entry.id does not match id.
            EntryNotFoundError: If no entry with the given id exists.
        """
        if team_entry.id != id:
            raise CatalogValidationError(
                [f"Entry id mismatch: expected '{id}', got '{team_entry.id}'"]
            )
        doc = to_document(team_entry)
        result = self._collection.replace_one({"_id": id}, doc)
        if result.matched_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Updated team entry with id=%s", id)

    def delete(self, id: str) -> None:
        """Delete a team entry by id.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        result = self._collection.delete_one({"_id": id})
        if result.deleted_count == 0:
            raise EntryNotFoundError(f"Entry with id '{id}' not found")
        logger.debug("Deleted team entry with id=%s", id)
