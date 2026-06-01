"""Tests for the v2 ``ak-catalog`` CLI — Story 17.1 foundation.

Every verb is exercised through :class:`typer.testing.CliRunner` against a
YAML-backed tmp-dir ``Catalog`` seeded with a team + model + agent in the same
namespace. The tests pin exit codes, stdout/stderr discipline, error messages,
and the format-switching behaviour required by ACs 12–27.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from akgentic.catalog.catalog import Catalog
from akgentic.catalog.cli import main as cli_main
from akgentic.catalog.models.entry import Entry
from akgentic.catalog.repositories.yaml import YamlEntryRepository

_TEAM_TYPE = "akgentic.team.models.TeamCard"


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


@pytest.fixture
def runner() -> CliRunner:
    """CliRunner with stderr separated from stdout so we can pin both."""
    return CliRunner()


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    """Seed a YAML catalog at ``tmp_path/catalog`` with team + model + agent.

    The CLI is then invoked with ``--root`` pointing at the same directory.
    """
    root = tmp_path / "catalog"
    root.mkdir()
    catalog = Catalog(YamlEntryRepository(root))
    # Team must land first — it is the bootstrap for the namespace.
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
            id="model-a",
            kind="model",
            namespace="ns-a",
            user_id="alice",
            model_type="akgentic.catalog.tests_fixture_17_1.LeafModel",
            description="some model",
            payload={"provider": "openai", "temperature": 0.0},
        )
    )
    catalog.create(
        Entry(
            id="agent-a",
            kind="agent",
            namespace="ns-a",
            user_id="alice",
            model_type="akgentic.catalog.tests_fixture_17_1.AgentModel",
            description="some agent",
            payload={"provider": "openai", "temperature": 0.0},
        )
    )
    return root


@pytest.fixture(autouse=True)
def _register_fixture_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register throwaway pydantic model types used by seed entries."""
    import types

    from pydantic import BaseModel

    class LeafModel(BaseModel):
        provider: str = "openai"
        temperature: float = 0.0

    class AgentModel(BaseModel):
        provider: str = "openai"
        temperature: float = 0.0
        linked: LeafModel | None = None

    module = types.ModuleType("akgentic.catalog.tests_fixture_17_1")
    module.LeafModel = LeafModel  # type: ignore[attr-defined]
    module.AgentModel = AgentModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "akgentic.catalog.tests_fixture_17_1", module)


def _base_args(catalog_root: Path) -> list[str]:
    return ["--backend", "yaml", "--root", str(catalog_root)]


# --------------------------------------------------------------------------- #
# Entry-point + state regression pins
# --------------------------------------------------------------------------- #


class TestEntryPoint:
    """AC2 + AC25 — the ``ak-catalog`` console-script resolves to cli.main:app."""

    def test_entry_point_resolves_to_v2(self) -> None:
        eps = importlib.metadata.entry_points(group="console_scripts")
        names = {ep.name: ep.value for ep in eps}
        assert names.get("ak-catalog") == "akgentic.catalog.cli.main:app"


class TestCliState:
    """AC26 — CliState survives a model_dump / model_validate round-trip."""

    def test_roundtrip(self) -> None:
        state = cli_main.CliState(
            backend="mongo",
            root=Path("/tmp/catalog"),
            uri="mongodb://localhost:27017",
            db="akgentic",
            output_format="json",
        )
        dumped = state.model_dump()
        restored = cli_main.CliState.model_validate(dumped)
        assert restored == state


# --------------------------------------------------------------------------- #
# `list` verb
# --------------------------------------------------------------------------- #


class TestListVerb:
    """AC12 + AC13 — list filters and renders per --format."""

    def test_list_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["team", "list", "--namespace", "ns-a"]
        )
        assert result.exit_code == 0, result.stderr
        assert "team-a" in result.stdout
        assert "ns-a" in result.stdout

    def test_list_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "agent", "list", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert payload[0]["id"] == "agent-a"
        assert payload[0]["kind"] == "agent"

    def test_list_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "yaml", "model", "list", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        assert payload[0]["id"] == "model-a"

    def test_list_empty_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["team", "list", "--namespace", "nowhere"],
        )
        assert result.exit_code == 0, result.stderr
        assert "(no entries)" in result.stdout

    def test_list_empty_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "team", "list", "--namespace", "nowhere"],
        )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    def test_list_user_id_set_tri_state(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "agent", "list", "--user-id-set", "true"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert all(e["user_id"] is not None for e in payload)

    def test_list_bad_user_id_set(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "list", "--user-id-set", "maybe"],
        )
        assert result.exit_code == 2
        assert "--user-id-set" in result.stderr


# --------------------------------------------------------------------------- #
# `get` verb
# --------------------------------------------------------------------------- #


class TestGetVerb:
    """AC14 + AC15 — get with format switch and error codes."""

    def test_get_happy_table(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "get", "agent-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "agent-a" in result.stdout

    def test_get_json(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "agent", "get", "agent-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["id"] == "agent-a"
        assert payload["kind"] == "agent"

    def test_get_yaml(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "yaml", "model", "get", "model-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        assert payload["id"] == "model-a"

    def test_get_not_found(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "get", "nope", "--namespace", "ns-a"],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_get_kind_mismatch(self, runner: CliRunner, catalog_root: Path) -> None:
        # The team-a entry exists under ns-a but has kind=team; asking the
        # `agent` sub-app for it must raise the kind-mismatch error.
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "get", "team-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 1
        assert "has kind=team" in result.stderr

    def test_get_missing_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["agent", "get", "agent-a"])
        assert result.exit_code == 2

    def test_get_native_value_entry_renders_standard_shape(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        """Story 26.1 / AC 15 — ``ak-catalog <kind> get`` on a NativeValue entry
        renders the standard entry shape (``model_type``, ``payload``) without
        crashing or special-casing the kind.
        """
        # Seed a NativeValue entry under ns-a using the same backend the CLI
        # will read from.
        native_catalog = Catalog(YamlEntryRepository(catalog_root))
        native_catalog.create(
            Entry(
                id="id_native_prompt",
                kind="prompt",
                namespace="ns-a",
                user_id="alice",
                model_type="akgentic.catalog.NativeValue",
                description="shared prompt body",
                payload={"value": "You are a helpful assistant."},
            )
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "prompt",
                "get",
                "id_native_prompt",
                "--namespace",
                "ns-a",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["id"] == "id_native_prompt"
        assert payload["model_type"] == "akgentic.catalog.NativeValue"
        assert payload["payload"] == {"value": "You are a helpful assistant."}


# --------------------------------------------------------------------------- #
# `create` and `update` verbs
# --------------------------------------------------------------------------- #


def _write_entry_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


class TestCreateVerb:
    """AC16 — create from a single-entry YAML file."""

    def test_create_happy(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        entry_file = tmp_path / "new.yaml"
        _write_entry_yaml(
            entry_file,
            {
                "id": "agent-b",
                "kind": "agent",
                "namespace": "ns-a",
                "user_id": "alice",
                "model_type": "akgentic.catalog.tests_fixture_17_1.AgentModel",
                "description": "another agent",
                "payload": {"provider": "openai", "temperature": 0.1},
            },
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "agent", "create", str(entry_file)],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["id"] == "agent-b"

    def test_create_duplicate_id(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        entry_file = tmp_path / "dup.yaml"
        _write_entry_yaml(
            entry_file,
            {
                "id": "agent-a",  # already exists
                "kind": "agent",
                "namespace": "ns-a",
                "user_id": "alice",
                "model_type": "akgentic.catalog.tests_fixture_17_1.AgentModel",
                "description": "dup",
                "payload": {"provider": "openai", "temperature": 0.0},
            },
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "create", str(entry_file)],
        )
        assert result.exit_code == 1
        assert "validation error" in result.stderr

    def test_create_missing_file(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "create", str(tmp_path / "nope.yaml")],
        )
        assert result.exit_code == 2
        assert "file not found" in result.stderr

    def test_create_bad_yaml(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        entry_file = tmp_path / "bad.yaml"
        entry_file.write_text("this is: not: valid: : yaml::\n  -")
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "create", str(entry_file)],
        )
        assert result.exit_code == 2

    def test_create_validation_error(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        entry_file = tmp_path / "invalid.yaml"
        # Missing required field ``model_type`` → Pydantic ValidationError.
        _write_entry_yaml(
            entry_file,
            {
                "id": "agent-x",
                "kind": "agent",
                "namespace": "ns-a",
                "payload": {},
            },
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "create", str(entry_file)],
        )
        assert result.exit_code == 2


class TestUpdateVerb:
    """AC17 — update reuses (namespace, id) from the file body."""

    def test_update_happy(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        entry_file = tmp_path / "upd.yaml"
        _write_entry_yaml(
            entry_file,
            {
                "id": "agent-a",
                "kind": "agent",
                "namespace": "ns-a",
                "user_id": "alice",
                "model_type": "akgentic.catalog.tests_fixture_17_1.AgentModel",
                "description": "updated description",
                "payload": {"provider": "openai", "temperature": 0.5},
            },
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "agent", "update", str(entry_file)],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["description"] == "updated description"

    def test_update_missing(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        entry_file = tmp_path / "missing.yaml"
        _write_entry_yaml(
            entry_file,
            {
                "id": "agent-missing",
                "kind": "agent",
                "namespace": "ns-a",
                "user_id": "alice",
                "model_type": "akgentic.catalog.tests_fixture_17_1.AgentModel",
                "description": "",
                "payload": {"provider": "openai", "temperature": 0.0},
            },
        )
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "update", str(entry_file)],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr


# --------------------------------------------------------------------------- #
# `delete` verb
# --------------------------------------------------------------------------- #


class TestDeleteVerb:
    """AC18 — delete with referrer surfacing on inbound-ref blockers."""

    def test_delete_happy(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["model", "delete", "model-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert "deleted model model-a" in result.stderr

    def test_delete_not_found(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["agent", "delete", "nope", "--namespace", "ns-a"],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_delete_referrer_surfacing(self, runner: CliRunner, tmp_path: Path) -> None:
        """A tool referenced by an agent cannot be deleted; referrers listed."""
        root = tmp_path / "catalog"
        root.mkdir()
        catalog = Catalog(YamlEntryRepository(root))
        # Team + tool + agent referencing tool via __ref__ sentinel.
        catalog.create(
            Entry(
                id="team-r",
                kind="team",
                namespace="ns-r",
                user_id="alice",
                model_type=_TEAM_TYPE,
                payload=_team_payload(),
            )
        )
        catalog.create(
            Entry(
                id="tool-r",
                kind="tool",
                namespace="ns-r",
                user_id="alice",
                model_type="akgentic.catalog.tests_fixture_17_1.LeafModel",
                description="the tool",
                payload={"provider": "openai", "temperature": 0.0},
            )
        )
        catalog.create(
            Entry(
                id="agent-r",
                kind="agent",
                namespace="ns-r",
                user_id="alice",
                model_type="akgentic.catalog.tests_fixture_17_1.AgentModel",
                description="the agent",
                payload={"linked": {"__ref__": "tool-r"}},
            )
        )
        runner_local = CliRunner()
        result = runner_local.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(root)]
            + ["tool", "delete", "tool-r", "--namespace", "ns-r"],
        )
        assert result.exit_code == 1
        # The blocking referrer (agent-r) should appear in stderr or stdout —
        # the CLI renders referrers per --format to stdout after the error
        # message on stderr.
        combined = result.stderr + result.stdout
        assert "agent-r" in combined
        # The tool must still be present in the repository.
        assert catalog.get("ns-r", "tool-r").id == "tool-r"


# --------------------------------------------------------------------------- #
# `search` verb
# --------------------------------------------------------------------------- #


class TestSearchVerb:
    """AC19 — search covers every EntryQuery filter."""

    def test_search_description_contains(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + [
                "--format",
                "json",
                "agent",
                "search",
                "--description-contains",
                "some",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert any(e["id"] == "agent-a" for e in payload)

    def test_search_by_id(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "model", "search", "--id", "model-a"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["id"] == "model-a"

    def test_search_yaml_empty(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "yaml", "model", "search", "--id", "zzz"],
        )
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# Backend wiring
# --------------------------------------------------------------------------- #


class TestBackendWiring:
    """AC7 + AC11 + AC24 — backend option validation and extras handling."""

    def test_mongo_missing_uri(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli_main.app, ["--backend", "mongo", "--db", "akgentic", "team", "list"]
        )
        assert result.exit_code == 2
        assert "--uri is required" in result.stderr

    def test_mongo_missing_db(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli_main.app,
            ["--backend", "mongo", "--uri", "mongodb://localhost:27017", "team", "list"],
        )
        assert result.exit_code == 2
        assert "--db is required" in result.stderr

    def test_mongo_malformed_uri(self, runner: CliRunner) -> None:
        result = runner.invoke(
            cli_main.app,
            ["--backend", "mongo", "--uri", "http://x", "--db", "x", "team", "list"],
        )
        assert result.exit_code == 2
        assert "mongodb://" in result.stderr

    def test_yaml_accepts_mongo_options_silently(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        result = runner.invoke(
            cli_main.app,
            [
                "--backend",
                "yaml",
                "--root",
                str(catalog_root),
                "--uri",
                "mongodb://localhost",
                "--db",
                "akgentic",
                "team",
                "list",
                "--namespace",
                "ns-a",
            ],
        )
        assert result.exit_code == 0, result.stderr

    def test_yaml_creates_missing_root(self, runner: CliRunner, tmp_path: Path) -> None:
        new_root = tmp_path / "new-root"
        assert not new_root.exists()
        result = runner.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(new_root), "team", "list"],
        )
        assert result.exit_code == 0, result.stderr
        assert new_root.exists()

    def test_invalid_backend(self, runner: CliRunner) -> None:
        # Use a backend name that is not in the accepted set (yaml/mongo/postgres);
        # the guard rejects it with exit 2.
        result = runner.invoke(cli_main.app, ["--backend", "sqlite", "team", "list"])
        assert result.exit_code == 2

    def test_invalid_format(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "csv", "team", "list"],
        )
        assert result.exit_code == 2

    def test_mongo_extra_missing(self, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hide pymongo from sys.modules to force an ImportError in _build_catalog.

        The CLI must catch it and surface the 'optional extra' message.
        """
        # Block both pymongo and the repositories.mongo module that imports
        # pymongo at attribute access (TYPE_CHECKING guards the top).
        import builtins as _builtins

        real_import = _builtins.__import__

        def _fake_import(
            name: str,
            globals_: Any = None,
            locals_: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            if name.startswith("pymongo") or name == "pymongo":
                raise ImportError("pymongo is not installed")
            if name == "akgentic.catalog.repositories.mongo":
                raise ImportError("pymongo is not installed")
            return real_import(name, globals_, locals_, fromlist, level)

        monkeypatch.setattr(_builtins, "__import__", _fake_import)
        # Drop any cached modules so the fake import hook is actually consulted.
        for key in list(sys.modules):
            if key.startswith("akgentic.catalog.repositories.mongo") or key.startswith("pymongo"):
                monkeypatch.delitem(sys.modules, key, raising=False)

        result = runner.invoke(
            cli_main.app,
            [
                "--backend",
                "mongo",
                "--uri",
                "mongodb://localhost",
                "--db",
                "x",
                "team",
                "list",
            ],
        )
        assert result.exit_code == 2
        assert "optional extra" in result.stderr


# --------------------------------------------------------------------------- #
# Stdout / stderr discipline
# --------------------------------------------------------------------------- #


class TestIoDiscipline:
    """AC21 — success status messages go to stderr, data to stdout."""

    def test_delete_success_message_on_stderr(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["model", "delete", "model-a", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0
        assert "deleted" in result.stderr
        assert "deleted" not in result.stdout


# --------------------------------------------------------------------------- #
# `meta` sub-app — Story 17.2
# --------------------------------------------------------------------------- #


_NAMESPACE_META_TYPE_CLI = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _meta_yaml_file(tmp_path: Path, namespace: str, name: str = "Tenant 42") -> Path:
    """Write a single-entry YAML file describing a kind=meta entry."""
    file = tmp_path / f"{namespace}-meta.yaml"
    file.write_text(
        yaml.safe_dump(
            {
                "id": "_meta",
                "kind": "meta",
                "namespace": namespace,
                "user_id": "alice",
                "model_type": _NAMESPACE_META_TYPE_CLI,
                "description": "namespace metadata",
                "payload": {"name": name, "description": "", "properties": {}},
            }
        )
    )
    return file


class TestMetaSubApp:
    """Story 17.2 AC11 — the ``meta`` sub-app is registered with standard verbs."""

    def test_meta_subapp_registered(self, runner: CliRunner, catalog_root: Path) -> None:
        """Top-level ``--help`` lists ``meta`` as one of the registered sub-apps."""
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["--help"])
        assert result.exit_code == 0
        # The sub-apps are listed in the help text. ``meta`` must be among them.
        assert "meta" in result.stdout

    def test_meta_create_then_get(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        """``meta create`` succeeds and ``meta get`` returns it."""
        meta_file = _meta_yaml_file(tmp_path, "ns-a", name="Display A")
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["meta", "create", str(meta_file)],
        )
        assert result.exit_code == 0
        # Verify get returns the stored entry.
        result_get = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "meta", "get", "_meta", "--namespace", "ns-a"],
        )
        assert result_get.exit_code == 0
        data = json.loads(result_get.stdout)
        assert data["id"] == "_meta"
        assert data["kind"] == "meta"

    def test_meta_create_twice_rejected(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        """A second ``meta create`` for the same namespace exits 1 with pinned msg."""
        first_file = _meta_yaml_file(tmp_path, "ns-a", name="First")
        first_result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["meta", "create", str(first_file)],
        )
        assert first_result.exit_code == 0
        # Second meta entry with a different id but same namespace should fail.
        second_file = tmp_path / "ns-a-meta-extra.yaml"
        second_file.write_text(
            yaml.safe_dump(
                {
                    "id": "meta-extra",
                    "kind": "meta",
                    "namespace": "ns-a",
                    "user_id": "alice",
                    "model_type": _NAMESPACE_META_TYPE_CLI,
                    "description": "second meta",
                    "payload": {"name": "Second", "description": "", "properties": {}},
                }
            )
        )
        second_result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["meta", "create", str(second_file)],
        )
        assert second_result.exit_code == 1
        assert "already has a meta entry" in second_result.stderr
