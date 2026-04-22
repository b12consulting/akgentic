"""Tests for MongoCatalogConfig (AC-2).

Verifies connection string validation, default collection names, and
database/collection accessor methods.
"""

from __future__ import annotations

import mongomock
import pytest

from akgentic.catalog.repositories.mongo._config import MongoCatalogConfig


class TestMongoCatalogConfigValidation:
    """AC-2: MongoCatalogConfig validates connection string and collection names."""

    def test_valid_mongodb_connection_string(self) -> None:
        """Standard mongodb:// URI is accepted."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="catalog",
        )
        assert config.connection_string == "mongodb://localhost:27017"
        assert config.database == "catalog"

    def test_valid_mongodb_srv_connection_string(self) -> None:
        """SRV mongodb+srv:// URI is accepted."""
        config = MongoCatalogConfig(
            connection_string="mongodb+srv://user:pass@cluster.example.com",
            database="catalog",
        )
        assert config.connection_string.startswith("mongodb+srv://")

    def test_invalid_connection_string_raises(self) -> None:
        """Non-MongoDB URI scheme is rejected."""
        with pytest.raises(ValueError, match="must start with 'mongodb://'"):
            MongoCatalogConfig(
                connection_string="postgres://localhost:5432",
                database="catalog",
            )

    def test_empty_connection_string_raises(self) -> None:
        """Empty string does not pass validation."""
        with pytest.raises(ValueError, match="must start with 'mongodb://'"):
            MongoCatalogConfig(connection_string="", database="catalog")

    def test_default_collection_names(self) -> None:
        """Default collection names match architecture spec."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="catalog",
        )
        assert config.template_entries_collection == "template_entries"
        assert config.tool_entries_collection == "tool_entries"
        assert config.agent_entries_collection == "agent_entries"
        assert config.team_entries_collection == "team_entries"

    def test_custom_collection_names(self) -> None:
        """Collection names can be overridden."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="catalog",
            template_entries_collection="my_templates",
            tool_entries_collection="my_tools",
        )
        assert config.template_entries_collection == "my_templates"
        assert config.tool_entries_collection == "my_tools"


class TestMongoCatalogConfigAccessors:
    """AC-2: MongoCatalogConfig provides database and collection accessors."""

    def test_get_database_returns_named_database(self) -> None:
        """get_database() returns the correct database by name."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="test_db",
        )
        client = mongomock.MongoClient()
        db = config.get_database(client)
        assert db.name == "test_db"

    def test_get_collection_returns_named_collection(self) -> None:
        """get_collection() returns the correct collection from the database."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="test_db",
        )
        client = mongomock.MongoClient()
        coll = config.get_collection(client, config.template_entries_collection)
        assert coll.name == "template_entries"

    def test_create_client_returns_mongo_client(self) -> None:
        """create_client() returns a functional MongoClient instance."""
        config = MongoCatalogConfig(
            connection_string="mongodb://localhost:27017",
            database="test_db",
        )
        # mongomock patches MongoClient so this works in tests
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("pymongo.MongoClient", mongomock.MongoClient)
            client = config.create_client()
            assert isinstance(client, mongomock.MongoClient)
