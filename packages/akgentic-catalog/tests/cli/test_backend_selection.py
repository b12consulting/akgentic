"""Tests for CLI backend selection (YAML and MongoDB)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import mongomock
import pytest
from typer.testing import CliRunner

from akgentic.catalog.cli.main import GlobalState, app
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.repositories.mongo._helpers import to_document

from .conftest import strip_ansi

if TYPE_CHECKING:
    import pymongo.collection

runner = CliRunner()

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB = "test_catalog"


# --- Fixtures ---


@pytest.fixture
def mongo_client() -> mongomock.MongoClient:  # type: ignore[type-arg]
    """Provide a fresh mongomock MongoClient per test."""
    return mongomock.MongoClient()


@pytest.fixture
def _patch_mongo_client(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: mongomock.MongoClient,  # type: ignore[type-arg]
) -> None:
    """Patch MongoCatalogConfig.create_client to return the mongomock client."""
    monkeypatch.setattr(
        "akgentic.catalog.repositories.mongo._config.MongoCatalogConfig.create_client",
        lambda self: mongo_client,
    )


@pytest.fixture
def template_collection(
    mongo_client: mongomock.MongoClient,  # type: ignore[type-arg]
) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the template_entries collection for seeding data."""
    return mongo_client[MONGO_DB]["template_entries"]


@pytest.fixture
def tool_collection(
    mongo_client: mongomock.MongoClient,  # type: ignore[type-arg]
) -> pymongo.collection.Collection:  # type: ignore[type-arg]
    """Provide the tool_entries collection for seeding data."""
    return mongo_client[MONGO_DB]["tool_entries"]


def _seed_template(
    collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    template_id: str,
    template: str,
) -> None:
    """Insert a template entry document into the MongoDB collection."""
    entry = TemplateEntry(id=template_id, template=template)
    collection.insert_one(to_document(entry))


TOOL_CLASS = "akgentic.tool.search.SearchTool"


def _seed_tool(
    collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    tool_id: str,
    name: str = "search",
    description: str = "Search tool",
    tool_class: str = TOOL_CLASS,
) -> None:
    """Insert a tool entry document into the MongoDB collection.

    Uses raw dict in to_document format (``_id`` instead of ``id``) because
    ToolEntry's validator resolves ``tool_class`` imports, preventing use of
    arbitrary class paths needed for search-filter tests.
    """
    doc = {
        "_id": tool_id,
        "tool_class": tool_class,
        "tool": {"name": name, "description": description},
    }
    collection.insert_one(doc)


# --- Task 1 tests: GlobalState backend fields ---


class TestGlobalStateBackendFields:
    """Verify GlobalState includes backend, mongo_uri, and mongo_db fields."""

    def test_default_backend_is_yaml(self) -> None:
        state = GlobalState()
        assert state.backend == "yaml"

    def test_default_mongo_uri_is_none(self) -> None:
        state = GlobalState()
        assert state.mongo_uri is None

    def test_default_mongo_db_is_none(self) -> None:
        state = GlobalState()
        assert state.mongo_db is None

    def test_backend_can_be_set(self) -> None:
        state = GlobalState(backend="mongodb", mongo_uri="mongodb://localhost", mongo_db="mydb")
        assert state.backend == "mongodb"
        assert state.mongo_uri == "mongodb://localhost"
        assert state.mongo_db == "mydb"


# --- AC-4: YAML default (backward compatible) ---


class TestYamlBackendDefault:
    """AC-4: YAML is default when no --backend specified."""

    def test_no_backend_flag_uses_yaml(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["--catalog-dir", str(tmp_path), "template", "list"])
        assert result.exit_code == 0

    def test_explicit_yaml_backend(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["--backend", "yaml", "--catalog-dir", str(tmp_path), "template", "list"]
        )
        assert result.exit_code == 0


class TestBackendHelpOutput:
    """Verify --help shows backend options."""

    def test_help_lists_backend_option(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--backend" in strip_ansi(result.output)

    def test_help_lists_mongo_uri_option(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--mongo-uri" in strip_ansi(result.output)

    def test_help_lists_mongo_db_option(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--mongo-db" in strip_ansi(result.output)


# --- AC-2: Missing MongoDB options error ---


class TestMongoMissingOptions:
    """AC-2: Clear error when MongoDB options missing."""

    def test_missing_mongo_uri(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-db",
                "test_db",
                "template",
                "list",
            ],
        )
        assert result.exit_code == 1
        assert "--mongo-uri" in result.output or "MONGO_URI" in result.output

    def test_missing_mongo_db(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 1
        assert "--mongo-db" in result.output or "MONGO_DB" in result.output

    def test_missing_both_mongo_options(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "template",
                "list",
            ],
        )
        assert result.exit_code == 1
        assert "--mongo-uri" in result.output or "MONGO_URI" in result.output
        assert "--mongo-db" in result.output or "MONGO_DB" in result.output

    def test_invalid_mongo_uri_scheme(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                "http://localhost:27017",
                "--mongo-db",
                "test_db",
                "template",
                "list",
            ],
        )
        assert result.exit_code == 1
        assert "mongodb://" in result.output or "mongodb+srv://" in result.output

    def test_invalid_backend_value_rejected(self) -> None:
        result = runner.invoke(
            app,
            ["--backend", "redis", "template", "list"],
        )
        assert result.exit_code == 1
        assert "Invalid backend" in result.output
        assert "redis" in result.output

    def test_no_python_traceback_on_error(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "template",
                "list",
            ],
        )
        assert result.exit_code == 1
        assert "Traceback" not in result.output


# --- AC-1: MongoDB backend selection ---


@pytest.mark.usefixtures("_patch_mongo_client")
class TestMongoBackendSelection:
    """AC-1: MongoDB backend selection works."""

    def test_template_list_empty_from_mongodb(self) -> None:
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0
        assert "No entries found" in result.output

    def test_template_list_from_mongodb(
        self,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_template(template_collection, "greet-v1", "Hello {name}")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0
        assert "greet-v1" in result.output

    def test_template_get_from_mongodb(
        self,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_template(template_collection, "greet-v1", "Hello {name}")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "get",
                "greet-v1",
            ],
        )
        assert result.exit_code == 0
        assert "greet-v1" in result.output

    def test_mongodb_srv_uri_accepted(self) -> None:
        """Verify mongodb+srv:// scheme passes validation."""
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                "mongodb+srv://cluster.example.com",
                "--mongo-db",
                MONGO_DB,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0

    def test_tool_list_from_mongodb(
        self,
        tool_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_tool(tool_collection, "tavily-search", name="Tavily Search")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "tool",
                "list",
            ],
        )
        assert result.exit_code == 0
        assert "tavily-search" in result.output


# --- AC-3: Environment variable fallback ---


@pytest.mark.usefixtures("_patch_mongo_client")
class TestMongoEnvVarFallback:
    """AC-3: Environment variable fallback for MongoDB options."""

    def test_env_var_used_when_no_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MONGO_URI", MONGO_URI)
        monkeypatch.setenv("MONGO_DB", MONGO_DB)
        result = runner.invoke(app, ["--backend", "mongodb", "template", "list"])
        assert result.exit_code == 0

    def test_env_var_mongo_uri_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MONGO_URI", MONGO_URI)
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-db",
                MONGO_DB,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0

    def test_env_var_mongo_db_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MONGO_DB", MONGO_DB)
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0

    def test_cli_flags_override_env_vars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        """CLI flags should take precedence over env vars."""
        monkeypatch.setenv("MONGO_URI", "mongodb://wrong-host:27017")
        monkeypatch.setenv("MONGO_DB", "wrong_db")
        _seed_template(template_collection, "t1", "Hello {name}")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "list",
            ],
        )
        assert result.exit_code == 0
        assert "t1" in result.output


# --- AC-5: MongoDB search ---


@pytest.mark.usefixtures("_patch_mongo_client")
class TestMongoSearch:
    """AC-5: Search commands work with MongoDB backend."""

    def test_template_search_by_placeholder(
        self,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_template(template_collection, "t1", "Hello {name}")
        _seed_template(template_collection, "t2", "You are {role}. Do {task}")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "search",
                "--placeholder",
                "name",
            ],
        )
        assert result.exit_code == 0
        assert "t1" in result.output
        # t2 does not have {name}, so it should not appear
        assert "t2" not in result.output

    def test_tool_search_by_class(
        self,
        tool_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_tool(tool_collection, "tavily-search", name="Tavily", tool_class=TOOL_CLASS)
        _seed_tool(tool_collection, "calc", name="Calculator", tool_class="math.Calculator")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "tool",
                "search",
                "--class",
                TOOL_CLASS,
            ],
        )
        assert result.exit_code == 0
        assert "tavily-search" in result.output

    def test_template_search_no_results(
        self,
        template_collection: pymongo.collection.Collection,  # type: ignore[type-arg]
    ) -> None:
        _seed_template(template_collection, "t1", "Hello {name}")
        result = runner.invoke(
            app,
            [
                "--backend",
                "mongodb",
                "--mongo-uri",
                MONGO_URI,
                "--mongo-db",
                MONGO_DB,
                "template",
                "search",
                "--placeholder",
                "nonexistent",
            ],
        )
        assert result.exit_code == 0
        assert "t1" not in result.output


# --- Subtask 5.4: pymongo import error handling ---


class TestMongoPymongoImportError:
    """Verify clear error when pymongo is not installed."""

    def test_import_guard_message_exists_in_mongo_package(self) -> None:
        """Verify the import guard provides clear installation instructions."""
        import akgentic.catalog.repositories.mongo as mongo_pkg

        source_file = mongo_pkg.__file__
        assert source_file is not None
        with open(source_file) as f:
            source = f.read()
        assert "pip install akgentic-catalog[mongo]" in source
