"""Shared test fixtures for MongoDB repository tests.

Provides mongomock-backed client, database, and per-collection fixtures
that mirror the production MongoCatalogConfig collection naming.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import mongomock
import pytest

if TYPE_CHECKING:
    import pymongo.collection
    import pymongo.database


@pytest.fixture
def mongo_client() -> mongomock.MongoClient:  # type: ignore[type-arg]
    """Provide a mongomock in-memory MongoClient."""
    return mongomock.MongoClient()


@pytest.fixture
def mongo_db(mongo_client: mongomock.MongoClient) -> pymongo.database.Database:  # type: ignore[type-arg]
    """Provide the test catalog database."""
    return mongo_client["test_catalog"]


@pytest.fixture
def template_collection(mongo_db: pymongo.database.Database) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the template_entries collection."""
    return mongo_db["template_entries"]


@pytest.fixture
def tool_collection(mongo_db: pymongo.database.Database) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the tool_entries collection."""
    return mongo_db["tool_entries"]


@pytest.fixture
def agent_collection(mongo_db: pymongo.database.Database) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the agent_entries collection."""
    return mongo_db["agent_entries"]


@pytest.fixture
def team_collection(mongo_db: pymongo.database.Database) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the team_entries collection."""
    return mongo_db["team_entries"]
