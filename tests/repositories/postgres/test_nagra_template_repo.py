"""Behavioural tests for :class:`NagraTemplateCatalogRepository`.

Mirrors the Mongo suite at ``tests/repositories/mongo/test_mongo_template_repo.py``
so that the Nagra backend honours the same CRUD and search contract. Every
test requests the per-test truncation fixture ``postgres_clean_tables`` from
the sibling ``conftest.py`` — which also carries the ``pytest.importorskip``
that makes the whole module skip cleanly when the Postgres extras are absent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("nagra")
pytest.importorskip("testcontainers.postgres")

from akgentic.catalog.models.errors import (  # noqa: E402
    CatalogValidationError,
    EntryNotFoundError,
)
from akgentic.catalog.models.queries import TemplateQuery  # noqa: E402
from akgentic.catalog.repositories.postgres.template_repo import (  # noqa: E402
    NagraTemplateCatalogRepository,
)
from tests.conftest import make_template  # noqa: E402


@pytest.fixture
def repo(postgres_clean_tables: str) -> NagraTemplateCatalogRepository:
    """Fresh repo backed by the per-test-truncated Postgres container."""
    return NagraTemplateCatalogRepository(postgres_clean_tables)


class TestCreateAndGet:
    """create + get round-trip and duplicate-id handling."""

    def test_create_and_get_round_trip(self, repo: NagraTemplateCatalogRepository) -> None:
        entry = make_template(id="tpl-1", template="Hello {name}, you are {role}.")
        created_id = repo.create(entry)
        assert created_id == "tpl-1"

        retrieved = repo.get("tpl-1")
        assert retrieved is not None
        assert retrieved.id == "tpl-1"
        assert retrieved.template == "Hello {name}, you are {role}."
        # placeholders is a computed field derived from the template body —
        # the round trip through JSONB must preserve it.
        assert set(retrieved.placeholders) == {"name", "role"}

    def test_create_duplicate_raises_validation_error(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        entry = make_template(id="dup-1")
        repo.create(entry)

        with pytest.raises(CatalogValidationError, match="already exists"):
            repo.create(entry)

    def test_get_nonexistent_returns_none(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        assert repo.get("nonexistent") is None


class TestList:
    """list() enumerates all rows."""

    def test_list_empty(self, repo: NagraTemplateCatalogRepository) -> None:
        assert repo.list() == []

    def test_list_returns_all_entries(self, repo: NagraTemplateCatalogRepository) -> None:
        repo.create(make_template(id="tpl-a", template="Template {a}"))
        repo.create(make_template(id="tpl-b", template="Template {b}"))
        repo.create(make_template(id="tpl-c", template="Template {c}"))

        results = repo.list()
        assert len(results) == 3
        assert {e.id for e in results} == {"tpl-a", "tpl-b", "tpl-c"}


class TestSearch:
    """search() predicate builders behave like the Mongo backend."""

    def test_search_by_id_hit_and_miss(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        repo.create(make_template(id="tpl-1", template="A {x}"))
        repo.create(make_template(id="tpl-2", template="B {y}"))

        hit = repo.search(TemplateQuery(id="tpl-1"))
        assert len(hit) == 1
        assert hit[0].id == "tpl-1"

        miss = repo.search(TemplateQuery(id="tpl-missing"))
        assert miss == []

    def test_search_by_placeholder_containment(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        repo.create(make_template(id="tpl-1", template="Hello {name}, welcome to {place}."))
        repo.create(make_template(id="tpl-2", template="Dear {title} {name},"))
        repo.create(make_template(id="tpl-3", template="System {role} prompt"))

        results = repo.search(TemplateQuery(placeholder="name"))
        assert {e.id for e in results} == {"tpl-1", "tpl-2"}

        assert repo.search(TemplateQuery(placeholder="nope")) == []

    def test_search_multiple_anded_fields(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        repo.create(make_template(id="tpl-1", template="Hello {name}, {role}."))
        repo.create(make_template(id="tpl-2", template="Hey {name}!"))

        both_match = repo.search(TemplateQuery(id="tpl-1", placeholder="name"))
        assert len(both_match) == 1
        assert both_match[0].id == "tpl-1"

        id_ok_placeholder_missing = repo.search(
            TemplateQuery(id="tpl-2", placeholder="role")
        )
        assert id_ok_placeholder_missing == []

    def test_search_no_filters_returns_all(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        repo.create(make_template(id="tpl-1"))
        repo.create(make_template(id="tpl-2"))

        results = repo.search(TemplateQuery())
        assert len(results) == 2


class TestUpdate:
    """update() replaces rows and enforces id contract."""

    def test_update_existing_entry(self, repo: NagraTemplateCatalogRepository) -> None:
        repo.create(make_template(id="tpl-1", template="Old {x}"))

        updated = make_template(id="tpl-1", template="New {y} template")
        repo.update("tpl-1", updated)

        retrieved = repo.get("tpl-1")
        assert retrieved is not None
        assert retrieved.template == "New {y} template"
        assert "y" in retrieved.placeholders

    def test_update_nonexistent_raises_not_found(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.update("ghost", make_template(id="ghost"))

    def test_update_id_mismatch_raises_validation_error(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        repo.create(make_template(id="tpl-1"))
        mismatched = make_template(id="tpl-2")
        with pytest.raises(CatalogValidationError, match="id mismatch"):
            repo.update("tpl-1", mismatched)


class TestDelete:
    """delete() removes rows and surfaces EntryNotFoundError."""

    def test_delete_existing_entry(self, repo: NagraTemplateCatalogRepository) -> None:
        repo.create(make_template(id="tpl-1"))
        assert repo.get("tpl-1") is not None

        repo.delete("tpl-1")
        assert repo.get("tpl-1") is None

    def test_delete_nonexistent_raises_not_found(
        self, repo: NagraTemplateCatalogRepository
    ) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            repo.delete("ghost")
