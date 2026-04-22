"""MongoDB catalog configuration model.

Provides validated configuration for MongoDB connection parameters and
collection naming. The config is serializable and does not hold a MongoClient
instance — callers use create_client() to obtain one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    import pymongo
    import pymongo.collection
    import pymongo.database

logger = logging.getLogger(__name__)


class MongoCatalogConfig(BaseModel):
    """Validated configuration for MongoDB catalog backend.

    Holds connection parameters and collection naming. Does not manage a live
    MongoClient — use create_client() and get_database()/get_collection()
    to obtain database objects.
    """

    connection_string: str = Field(description="MongoDB connection URI")
    database: str = Field(description="Name of the MongoDB database")

    template_entries_collection: str = Field(
        default="template_entries",
        description="Collection name for template catalog entries",
    )
    tool_entries_collection: str = Field(
        default="tool_entries",
        description="Collection name for tool catalog entries",
    )
    agent_entries_collection: str = Field(
        default="agent_entries",
        description="Collection name for agent catalog entries",
    )
    team_entries_collection: str = Field(
        default="team_entries",
        description="Collection name for team catalog entries",
    )

    @field_validator("connection_string")
    @classmethod
    def _validate_connection_string(cls, v: str) -> str:
        """Ensure connection string uses a valid MongoDB URI scheme."""
        if not v.startswith(("mongodb://", "mongodb+srv://")):
            msg = f"connection_string must start with 'mongodb://' or 'mongodb+srv://'; got: {v!r}"
            raise ValueError(msg)
        return v

    def create_client(self) -> pymongo.MongoClient:  # type: ignore[type-arg]
        """Create a new MongoClient from the configured connection string.

        Returns:
            A pymongo.MongoClient instance connected to the configured URI.
        """
        import pymongo

        logger.info("Creating MongoClient for database %s", self.database)
        return pymongo.MongoClient(self.connection_string)

    def get_database(self, client: pymongo.MongoClient) -> pymongo.database.Database:  # type: ignore[type-arg]
        """Obtain the configured database from a MongoClient.

        Args:
            client: An active MongoClient instance.

        Returns:
            The pymongo Database object for the configured database name.
        """
        return client[self.database]

    def get_collection(
        self,
        client: pymongo.MongoClient,  # type: ignore[type-arg]
        collection_name: str,
    ) -> pymongo.collection.Collection:  # type: ignore[type-arg]
        """Obtain a named collection from the configured database.

        Args:
            client: An active MongoClient instance.
            collection_name: One of the configured collection name fields.

        Returns:
            The pymongo Collection object.
        """
        return self.get_database(client)[collection_name]


__all__ = ["MongoCatalogConfig"]
