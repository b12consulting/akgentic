"""Tests for MongoTemplateCatalogRepository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import TemplateQuery
from akgentic.catalog.repositories.mongo.template_repo import MongoTemplateCatalogRepository
from tests.conftest import make_template

if TYPE_CHECKING:
    import pymongo.collection


@pytest.fixture
def repo(template_collection: pymongo.collection.Collection) -> MongoTemplateCatalogRepository:  # type: ignore[type-arg]
    """Provide a MongoTemplateCatalogRepository backed by mongomock."""
    return MongoTemplateCatalogRepository(template_collection)


class TestCreateAndGet:
    """Test create + get round-trip."""

    def test_create_and_get_round_trip(self, repo: MongoTemplateCatalogRepository) -> None:
        """Create an entry and retrieve it by id."""
        entry = make_template(id="tpl-1", template="Hello {name}, you are {role}.")
        created_id = repo.create(entry)
        assert created_id == "tpl-1"

        retrieved = repo.get("tpl-1")
        assert retrieved is not None
        assert retrieved.id == "tpl-1"
        assert retrieved.template == "Hello {name}, you are {role}."
        assert set(retrieved.placeholders) == {"name", "role"}

    def test_create_duplicate_raises_validation_error(
        self, repo: MongoTemplateCatalogRepository
    ) -> None:
        """Creating an entry with an existing id raises CatalogValidationError."""
        entry = make_template(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(self, repo: MongoTemplateCatalogRepository) -> None:
        """Getting a nonexistent id returns None."""
        assert repo.get("nonexistent") is None


class TestList:
    """Test list returns all entries."""

    def test_list_empty(self, repo: MongoTemplateCatalogRepository) -> None:
        """Empty collection returns empty list."""
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: MongoTemplateCatalogRepository) -> None:
        """List returns all created entries."""
        repo.create(make_template(id="tpl-a", template="Template {a}"))
        repo.create(make_template(id="tpl-b", template="Template {b}"))
        repo.create(make_template(id="tpl-c", template="Template {c}"))

        results = repo.list()
        assert len(results) == 3
        ids = {e.id for e in results}
        assert ids == {"tpl-a", "tpl-b", "tpl-c"}


class TestSearch:
    """Test search with various query combinations."""

    def test_search_by_id_exact_match(self, repo: MongoTemplateCatalogRepository) -> None:
        """Search by id returns exact match only."""
        repo.create(make_template(id="tpl-1", template="A {x}"))
        repo.create(make_template(id="tpl-2", template="B {y}"))

        results = repo.search(TemplateQuery(id="tpl-1"))
        assert len(results) == 1
        assert results[0].id == "tpl-1"

    def test_search_by_placeholder_containment(self, repo: MongoTemplateCatalogRepository) -> None:
        """Search by placeholder returns entries containing that placeholder."""
        repo.create(make_template(id="tpl-1", template="Hello {name}, welcome to {place}."))
        repo.create(make_template(id="tpl-2", template="Dear {title} {name},"))
        repo.create(make_template(id="tpl-3", template="System {role} prompt"))

        results = repo.search(TemplateQuery(placeholder="name"))
        assert len(results) == 2
        ids = {e.id for e in results}
        assert ids == {"tpl-1", "tpl-2"}

    def test_search_with_multiple_anded_fields(self, repo: MongoTemplateCatalogRepository) -> None:
        """Search with both id and placeholder AND-ed together."""
        repo.create(make_template(id="tpl-1", template="Hello {name}, {role}."))
        repo.create(make_template(id="tpl-2", template="Hey {name}!"))

        # Both id and placeholder must match
        results = repo.search(TemplateQuery(id="tpl-1", placeholder="name"))
        assert len(results) == 1
        assert results[0].id == "tpl-1"

        # id matches but placeholder doesn't
        results = repo.search(TemplateQuery(id="tpl-2", placeholder="role"))
        assert len(results) == 0

    def test_search_no_filters_returns_all(self, repo: MongoTemplateCatalogRepository) -> None:
        """Search with no filters returns all entries."""
        repo.create(make_template(id="tpl-1"))
        repo.create(make_template(id="tpl-2"))

        results = repo.search(TemplateQuery())
        assert len(results) == 2


class TestUpdate:
    """Test update operations."""

    def test_update_existing_entry(self, repo: MongoTemplateCatalogRepository) -> None:
        """Update replaces the entry data."""
        repo.create(make_template(id="tpl-1", template="Old {x}"))

        updated_entry = make_template(id="tpl-1", template="New {y} template")
        repo.update("tpl-1", updated_entry)

        retrieved = repo.get("tpl-1")
        assert retrieved is not None
        assert retrieved.template == "New {y} template"
        assert "y" in retrieved.placeholders

    def test_update_nonexistent_raises_not_found(
        self, repo: MongoTemplateCatalogRepository
    ) -> None:
        """Updating a nonexistent entry raises EntryNotFoundError."""
        entry = make_template(id="ghost")
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", entry)

    def test_update_id_mismatch_raises_validation_error(
        self, repo: MongoTemplateCatalogRepository
    ) -> None:
        """Updating with mismatched entry id raises CatalogValidationError."""
        repo.create(make_template(id="tpl-1"))
        mismatched = make_template(id="tpl-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("tpl-1", mismatched)


class TestDelete:
    """Test delete operations."""

    def test_delete_existing_entry(self, repo: MongoTemplateCatalogRepository) -> None:
        """Delete removes the entry from the collection."""
        repo.create(make_template(id="tpl-1"))
        assert repo.get("tpl-1") is not None

        repo.delete("tpl-1")
        assert repo.get("tpl-1") is None

    def test_delete_nonexistent_raises_not_found(
        self, repo: MongoTemplateCatalogRepository
    ) -> None:
        """Deleting a nonexistent entry raises EntryNotFoundError."""
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
