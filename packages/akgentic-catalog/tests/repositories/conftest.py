"""Shared fixtures for the ``tests/repositories/`` suite.

Re-declares the ``entries_collection`` fixture (mongomock-backed Mongo
collection) used by the three-backend contract parity tests. The v2 test
suite also declares this fixture under ``tests/v2/conftest.py`` — keeping a
parallel copy here avoids pytest's "fixture defined above a test's scope"
constraint when the test folder is a sibling of the existing v2 folder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import pymongo.collection


@pytest.fixture
def entries_collection() -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide a fresh mongomock-backed ``catalog_entries`` collection per test."""
    import mongomock

    client = mongomock.MongoClient()
    return client["test_catalog"]["catalog_entries"]
