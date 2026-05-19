"""Tests for the v2 ``ak-catalog`` CLI graph + schema verbs — Story 17.2.

Every verb (``clone``, ``references``, ``resolve``, ``load-team``, ``schema``,
``model-types``) is exercised through :class:`typer.testing.CliRunner` against
a YAML-backed tmp-dir ``Catalog`` seeded with a team + model + tool + agent
where the agent references the tool via ``{"__ref__": ...}``.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel
from typer.testing import CliRunner

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.cli import main as cli_main
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.repositories.yaml import YamlEntryRepository

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_FIXTURE_MODULE = "akgentic.catalog.tests_fixture_17_2"
_LEAF_TYPE = f"{_FIXTURE_MODULE}.LeafModel"
_AGENT_TYPE = f"{_FIXTURE_MODULE}.AgentModel"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _team_payload() -> dict[str, Any]:
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "role": "entry",
                "description": "entry",
                "skills": [],
                "agent_class": "akgentic.core.agent.Akgent",
                "config": {"name": "entry", "role": "entry"},
            },
            "headcount": 1,
            "members": [],
        },
        "members": [],
        "agent_profiles": [],
    }


@pytest.fixture(autouse=True)
def _register_fixture_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register throwaway Pydantic classes under an ``akgentic.*`` module path.

    ``load_model_type`` gates on the ``akgentic.*`` prefix, so the fixture
    classes MUST live inside a module with that prefix to be loadable by the
    resolver / schema verb.
    """

    class LeafModel(BaseModel):
        provider: str = "openai"
        temperature: float = 0.0

    class AgentModel(BaseModel):
        provider: str = "openai"
        temperature: float = 0.0
        linked: LeafModel | None = None

    module = types.ModuleType(_FIXTURE_MODULE)
    module.LeafModel = LeafModel  # type: ignore[attr-defined]
    module.AgentModel = AgentModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _FIXTURE_MODULE, module)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    """Seed a YAML catalog with team + tool + agent-refs-tool + bare model.

    ``agent-a`` references ``tool-a`` via ``{"__ref__": "tool-a"}`` inside its
    payload's ``linked`` field — exercising ``resolve`` and ``references``.
    """
    root = tmp_path / "catalog"
    root.mkdir()
    catalog = Catalog(YamlEntryRepository(root))
    catalog.create(
        Entry(
            id="team-a",
            kind="team",
            namespace="ns-a",
            user_id="alice",
            model_type=_TEAM_TYPE,
            description="primary team",
            payload=_team_payload(),
        )
    )
    catalog.create(
        Entry(
            id="tool-a",
            kind="tool",
            namespace="ns-a",
            user_id="alice",
            model_type=_LEAF_TYPE,
            description="the tool",
            payload={"provider": "openai", "temperature": 0.0},
        )
    )
    catalog.create(
        Entry(
            id="model-a",
            kind="model",
            namespace="ns-a",
            user_id="alice",
            model_type=_LEAF_TYPE,
            description="the model",
            payload={"provider": "openai", "temperature": 0.1},
        )
    )
    catalog.create(
        Entry(
            id="agent-a",
            kind="agent",
            namespace="ns-a",
            user_id="alice",
            model_type=_AGENT_TYPE,
            description="agent referencing tool",
            payload={"provider": "openai", "temperature": 0.0, "linked": {"__ref__": "tool-a"}},
        )
    )
    return root


def _base_args(catalog_root: Path) -> list[str]:
    return ["--backend", "yaml", "--root", str(catalog_root)]


# --------------------------------------------------------------------------- #
# `clone` verb
# --------------------------------------------------------------------------- #


class TestCloneVerb:
    """AC3-AC6 + AC27-AC28 — clone delegation, error handling, round-trip."""

    def test_clone_cross_namespace_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "team-a",
                "--dst-namespace",
                "ns-b",
                "--dst-user-id",
                "bob",
            ],
        )
        assert result.exit_code == 0, result.stderr
        assert "team-a" in result.stdout
        assert "ns-b" in result.stdout

    def test_clone_cross_namespace_json_roundtrip(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        # AC27 round-trip: cross-namespace clone preserves id and stamps the
        # destination owner; no lineage breadcrumb is persisted (ADR-010).
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "team-a",
                "--dst-namespace",
                "ns-c",
                "--dst-user-id",
                "user@example.com",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["kind"] == "team"
        assert payload["namespace"] == "ns-c"
        assert payload["user_id"] == "user@example.com"
        assert "parent_namespace" not in payload
        assert "parent_id" not in payload

    def test_clone_round_trip_dedup(self, runner: CliRunner, tmp_path: Path) -> None:
        """AC27 deduplication: a shared tool is cloned once, referenced by two parents."""
        root = tmp_path / "catalog"
        root.mkdir()
        catalog = Catalog(YamlEntryRepository(root))
        catalog.create(
            Entry(
                id="team-s",
                kind="team",
                namespace="ns-src",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            )
        )
        catalog.create(
            Entry(
                id="tool-shared",
                kind="tool",
                namespace="ns-src",
                user_id="alice",
                model_type=_LEAF_TYPE,
                payload={"provider": "openai", "temperature": 0.0},
            )
        )
        catalog.create(
            Entry(
                id="agent-1",
                kind="agent",
                namespace="ns-src",
                user_id="alice",
                model_type=_AGENT_TYPE,
                payload={"linked": {"__ref__": "tool-shared"}},
            )
        )
        catalog.create(
            Entry(
                id="agent-2",
                kind="agent",
                namespace="ns-src",
                user_id="alice",
                model_type=_AGENT_TYPE,
                payload={"linked": {"__ref__": "tool-shared"}},
            )
        )
        # Clone agent-1 into a fresh namespace — pulls tool-shared transitively.
        result = runner.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(root)]
            + [
                "clone",
                "--src-namespace",
                "ns-src",
                "--src-id",
                "agent-1",
                "--dst-namespace",
                "ns-dst",
                "--dst-user-id",
                "bob",
            ],
        )
        assert result.exit_code == 0, result.stderr
        cloned = Catalog(YamlEntryRepository(root)).list_by_namespace("ns-dst")
        # Exactly two entries — the agent and the single cloned tool.
        ids = sorted(e.id for e in cloned)
        assert ids == ["agent-1", "tool-shared"]

    def test_clone_empty_string_user_id_rejected(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        """Story 18.1 / AC8 — the CLI no longer accepts the empty-string sentinel.

        The empty string fails Pydantic's ``NonEmptyStr`` validation on
        ``CloneRequest.dst_user_id``; the CLI exits with code 2 and prints a
        clear validation error. To clone into a community-tier deployment
        the caller passes ``"anonymous"`` (or omits the flag).
        """
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "agent-a",
                "--dst-namespace",
                "ns-ent",
                "--dst-user-id",
                "",
            ],
        )
        assert result.exit_code == 2
        assert "validation error" in result.stderr

    def test_clone_anonymous_user_id_is_community_tier(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        """Story 18.1 / AC8 — passing ``"anonymous"`` clones into the community-tier default."""
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "agent-a",
                "--dst-namespace",
                "ns-ent",
                "--dst-user-id",
                "anonymous",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["user_id"] == "anonymous"

    def test_clone_src_not_found(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "does-not-exist",
                "--dst-namespace",
                "ns-b",
                "--dst-user-id",
                "bob",
            ],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_clone_missing_src_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "clone",
                "--src-id",
                "team-a",
                "--dst-namespace",
                "ns-b",
                "--dst-user-id",
                "bob",
            ],
        )
        assert result.exit_code == 2

    def test_clone_omitting_dst_user_id_defaults_to_anonymous(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        """Story 18.1 / AC8 — omitting ``--dst-user-id`` clones into the
        community-tier default ``"anonymous"`` rather than rejecting the call.
        """
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "clone",
                "--src-namespace",
                "ns-a",
                "--src-id",
                "team-a",
                "--dst-namespace",
                "ns-b",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["user_id"] == "anonymous"


# --------------------------------------------------------------------------- #
# `references` verb
# --------------------------------------------------------------------------- #


class TestReferencesVerb:
    """AC7-AC9 + AC29 — inbound-ref listing per format, empty-set rendering."""

    def test_references_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["references", "tool-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "agent-a" in result.stdout

    def test_references_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "references", "tool-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert any(e["id"] == "agent-a" for e in payload)

    def test_references_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "yaml", "references", "tool-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        assert any(e["id"] == "agent-a" for e in payload)

    def test_references_empty_json(self, runner: CliRunner, catalog_root: Path) -> None:
        # model-a has zero inbound refs.
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "references", "model-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == []

    def test_references_empty_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["references", "model-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "(no entries)" in result.stdout

    def test_references_missing_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["references", "tool-a"],
        )
        assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# `resolve` verb
# --------------------------------------------------------------------------- #


class TestResolveVerb:
    """AC10-AC12 + AC30 — resolve with kind-guard and ref-populate + error paths."""

    def test_resolve_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["resolve", "agent", "agent-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "AgentModel" in result.stdout

    def test_resolve_json_ref_populated(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "resolve",
                "agent",
                "agent-a",
                "--namespace",
                "ns-a",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        # The __ref__ marker in linked was populated by the resolver.
        assert payload["linked"]["provider"] == "openai"
        assert "__ref__" not in payload.get("linked", {})

    def test_resolve_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "yaml",
                "resolve",
                "agent",
                "agent-a",
                "--namespace",
                "ns-a",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        assert payload["provider"] == "openai"

    def test_resolve_not_found(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["resolve", "agent", "nope", "--namespace", "ns-a"],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_resolve_kind_mismatch(self, runner: CliRunner, catalog_root: Path) -> None:
        # team-a is kind=team but caller asks resolve agent.
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["resolve", "agent", "team-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 1
        assert "has kind=team" in result.stderr

    def test_resolve_invalid_kind_is_usage_error(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["resolve", "nonsense", "anything", "--namespace", "ns-a"],
        )
        assert result.exit_code == 2

    def test_resolve_missing_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["resolve", "agent", "agent-a"],
        )
        assert result.exit_code == 2

    def test_resolve_dangling_ref_surfaces_validation_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """AC30 — dangling __ref__ written bypassing ``Catalog.create`` surfaces exit 1."""
        root = tmp_path / "catalog"
        root.mkdir()
        catalog = Catalog(YamlEntryRepository(root))
        catalog.create(
            Entry(
                id="team-d",
                kind="team",
                namespace="ns-d",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            )
        )
        # Hand-write an agent file with a dangling __ref__, bypassing
        # ``Catalog.create`` (which would reject it). Use the YAML repo's raw
        # layout: root/ns-d/agent/agent-d.yaml.
        agent_dir = root / "ns-d" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        raw = {
            "id": "agent-d",
            "kind": "agent",
            "namespace": "ns-d",
            "user_id": "alice",
            "model_type": _AGENT_TYPE,
            "description": "",
            "payload": {"linked": {"__ref__": "does-not-exist"}},
        }
        (agent_dir / "agent-d.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
        # Force a fresh Catalog wrapper — old instance is unused here.
        _ = catalog
        result = runner.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(root)]
            + ["resolve", "agent", "agent-d", "--namespace", "ns-d"],
        )
        assert result.exit_code == 1
        assert "does-not-exist" in result.stderr


# --------------------------------------------------------------------------- #
# `load-team` verb
# --------------------------------------------------------------------------- #


class TestLoadTeamVerb:
    """AC13-AC14 — load-team delegates to Catalog.load_team and renders a TeamCard."""

    def test_load_team_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "load-team", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        # TeamCard has name/members; we assert on "name" field presence.
        assert payload["name"] == "team"

    def test_load_team_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["load-team", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "TeamCard" in result.stdout

    def test_load_team_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "yaml", "load-team", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        assert payload["name"] == "team"

    def test_load_team_no_team_entry(self, runner: CliRunner, tmp_path: Path) -> None:
        root = tmp_path / "catalog"
        root.mkdir()
        result = runner.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(root)]
            + ["load-team", "--namespace", "does-not-exist"],
        )
        assert result.exit_code == 1
        assert "no team entry" in result.stderr

    def test_load_team_missing_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["load-team"],
        )
        assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# `schema` verb
# --------------------------------------------------------------------------- #


class TestSchemaVerb:
    """AC17-AC19 + AC31-AC32 — JSON Schema export with allowlist gate."""

    def test_schema_happy_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "schema", "akgentic.catalog.models.entry.Entry"],
        )
        assert result.exit_code == 0, result.stderr
        body = json.loads(result.stdout)
        assert body.get("title") == "Entry"
        assert body.get("type") == "object"

    def test_schema_table_falls_through_to_json(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        # AC18: table falls through to JSON — same bytes as json.
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["schema", "akgentic.catalog.models.entry.Entry"],
        )
        assert result.exit_code == 0, result.stderr
        body = json.loads(result.stdout)
        assert body.get("title") == "Entry"

    def test_schema_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "yaml", "schema", "akgentic.catalog.models.entry.Entry"],
        )
        assert result.exit_code == 0, result.stderr
        body = yaml.safe_load(result.stdout)
        assert body.get("title") == "Entry"

    def test_schema_rejects_non_allowlisted(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["schema", "pydantic.BaseModel"],
        )
        assert result.exit_code == 1
        assert "outside allowlist" in result.stderr

    def test_schema_missing_module(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["schema", "akgentic.this.does.not.exist"],
        )
        assert result.exit_code == 1
        # Rich console may wrap long stderr lines; collapse whitespace before asserting.
        normalized = " ".join(result.stderr.split())
        assert "could not be imported" in normalized


# --------------------------------------------------------------------------- #
# `model-types` verb
# --------------------------------------------------------------------------- #


class TestModelTypesVerb:
    """AC20-AC22 + AC33 — reflection verb + helper factoring pin."""

    def test_model_types_json_includes_entry(self, runner: CliRunner, catalog_root: Path) -> None:
        # The test process has imported akgentic.catalog.models.entry by now
        # (the test module imports Entry directly at the top).
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "model-types"],
        )
        assert result.exit_code == 0, result.stderr
        paths = json.loads(result.stdout)
        assert isinstance(paths, list)
        assert "akgentic.catalog.models.entry.Entry" in paths

    def test_model_types_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "yaml", "model-types"],
        )
        assert result.exit_code == 0, result.stderr
        paths = yaml.safe_load(result.stdout)
        assert "akgentic.catalog.models.entry.Entry" in paths

    def test_model_types_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["model-types"],
        )
        assert result.exit_code == 0, result.stderr
        assert "akgentic.catalog.models.entry.Entry" in result.stdout


# --------------------------------------------------------------------------- #
# Helper-factoring regression pin (Task 1 / AC34)
# --------------------------------------------------------------------------- #


class TestHelperFactoringPin:
    """AC21 + AC34 — the shared helper is importable from resolver and returns akgentic.* paths."""

    def test_enumerate_helper_lives_on_resolver(self) -> None:
        import akgentic.catalog.models.entry  # noqa: F401 — force-import to populate sys.modules
        from akgentic.catalog.resolver import enumerate_allowlisted_model_types

        paths = enumerate_allowlisted_model_types()
        assert isinstance(paths, list)
        assert all(p.startswith("akgentic.") for p in paths)
        assert "akgentic.catalog.models.entry.Entry" in paths

    def test_rest_endpoint_still_works_post_move(self, tmp_path: Path) -> None:
        """REST ``GET /catalog/model_types`` still returns allowlisted paths post-move."""
        pytest.importorskip("fastapi")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from akgentic.catalog.api._errors import add_exception_handlers
        from akgentic.catalog.api.router import router, set_catalog

        repo = YamlEntryRepository(tmp_path / "catalog")
        catalog = Catalog(repo)
        app = FastAPI()
        app.include_router(router)
        set_catalog(catalog)
        add_exception_handlers(app)
        client = TestClient(app)
        import akgentic.catalog.models.entry  # noqa: F401

        response = client.get("/catalog/model_types")
        assert response.status_code == 200
        paths = response.json()
        assert "akgentic.catalog.models.entry.Entry" in paths
