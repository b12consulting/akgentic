"""Tests for the v2 ``ak-catalog`` CLI bundle verbs — Story 17.3.

Exercises ``export``, ``import`` and ``import --dry-run`` through
:class:`typer.testing.CliRunner` against a YAML-backed tmp-dir ``Catalog``
seeded with a self-contained namespace (team + agent + tool, agent
referencing the tool).
"""

from __future__ import annotations

import json
import re
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
_FIXTURE_MODULE = "akgentic.catalog.tests_fixture_17_3"
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
    """Register throwaway Pydantic classes under an ``akgentic.*`` module path."""

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


def _seed_namespace(root: Path, namespace: str = "ns-a", user_id: str = "alice") -> None:
    catalog = Catalog(YamlEntryRepository(root))
    catalog.create(
        Entry(
            id="team-a",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            description="primary team",
            payload=_team_payload(),
        )
    )
    catalog.create(
        Entry(
            id="tool-a",
            kind="tool",
            namespace=namespace,
            user_id=user_id,
            model_type=_LEAF_TYPE,
            description="the tool",
            payload={"provider": "openai", "temperature": 0.0},
        )
    )
    catalog.create(
        Entry(
            id="agent-a",
            kind="agent",
            namespace=namespace,
            user_id=user_id,
            model_type=_AGENT_TYPE,
            description="agent referencing tool",
            payload={"provider": "openai", "temperature": 0.0, "linked": {"__ref__": "tool-a"}},
        )
    )


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    _seed_namespace(root)
    return root


def _base_args(catalog_root: Path) -> list[str]:
    return ["--backend", "yaml", "--root", str(catalog_root)]


# --------------------------------------------------------------------------- #
# `export` verb
# --------------------------------------------------------------------------- #


class TestExportVerb:
    """AC3-AC6 + AC23 — export verb shape, stdout fidelity, error paths."""

    def test_export_stdout_is_parseable_bundle(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert result.exit_code == 0, result.stderr
        payload = yaml.safe_load(result.stdout)
        # Story 18.2 — eight top-level keys including the header (adds
        # ``public`` after ``shareable``).
        assert set(payload.keys()) == {
            "namespace",
            "user_id",
            "name",
            "description",
            "properties",
            "shareable",
            "public",
            "entries",
        }
        assert payload["namespace"] == "ns-a"
        assert "team-a" in payload["entries"]
        assert "tool-a" in payload["entries"]
        assert "agent-a" in payload["entries"]

    def test_export_missing_namespace_is_usage_error(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["export"])
        assert result.exit_code == 2
        assert result.stdout == ""

    def test_export_empty_namespace_string_is_usage_error(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        result = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", ""]
        )
        assert result.exit_code == 2

    def test_export_unknown_namespace_is_validation_error(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # AC23 — point at an empty catalog dir; exporting any namespace fails.
        root = tmp_path / "catalog"
        root.mkdir()
        result = runner.invoke(
            cli_main.app,
            ["--backend", "yaml", "--root", str(root), "export", "--namespace", "nope"],
        )
        assert result.exit_code == 1
        assert "validation error:" in result.stderr

    def test_export_format_flag_ignored(self, runner: CliRunner, catalog_root: Path) -> None:
        # --format is a no-op on export; bundle is always YAML bytes.
        result_yaml = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "export", "--namespace", "ns-a"],
        )
        assert result_yaml.exit_code == 0
        # Output must still parse as YAML (the bundle format).
        payload = yaml.safe_load(result_yaml.stdout)
        assert payload["namespace"] == "ns-a"


# --------------------------------------------------------------------------- #
# `import` verb — persistence mode
# --------------------------------------------------------------------------- #


class TestImportPersistence:
    """AC7-AC11 + AC17-AC18 — round-trip + atomicity."""

    def test_round_trip_mutation(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        # AC17 — export → edit → import → re-export → verify.
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert export.exit_code == 0
        bundle = yaml.safe_load(export.stdout)
        assert bundle["namespace"] == "ns-a"
        bundle["entries"]["team-a"]["description"] = "edited description"
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(yaml.safe_dump(bundle, sort_keys=False))

        imp = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(bundle_path)])
        assert imp.exit_code == 0, imp.stderr
        assert imp.stdout == ""
        assert "imported" in imp.stderr
        assert "ns-a" in imp.stderr

        re_export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert re_export.exit_code == 0
        re_bundle = yaml.safe_load(re_export.stdout)
        assert re_bundle["entries"]["team-a"]["description"] == "edited description"
        # Other entries untouched — byte-equivalent dumps.
        for k in ("tool-a", "agent-a"):
            assert re_bundle["entries"][k] == bundle["entries"][k]

    def test_atomic_failure_leaves_namespace_untouched(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        # AC18 — broken bundle with dangling ref → exit 1 → export byte-equal to pre.
        before = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert before.exit_code == 0
        pre_bundle_text = before.stdout

        # Break the bundle: point agent-a.linked.__ref__ at a missing id.
        bundle = yaml.safe_load(pre_bundle_text)
        bundle["entries"]["agent-a"]["payload"]["linked"] = {"__ref__": "does-not-exist"}
        broken_path = tmp_path / "broken.yaml"
        broken_path.write_text(yaml.safe_dump(bundle, sort_keys=False))

        imp = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(broken_path)])
        assert imp.exit_code == 1
        assert "validation error:" in imp.stderr

        after = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert after.exit_code == 0
        assert after.stdout == pre_bundle_text


# --------------------------------------------------------------------------- #
# `import --dry-run` — success + failure
# --------------------------------------------------------------------------- #


class TestImportDryRun:
    """AC12-AC13 + AC19-AC21 — dry-run delegation, report rendering, exit-code map."""

    def test_dry_run_happy_json(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        # AC19 — export → dry-run --format json → ok=True, empty errors.
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(export.stdout)

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "import", str(bundle_path), "--dry-run"],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["global_errors"] == []
        assert payload["entry_issues"] == []
        assert result.stderr == ""

    def test_dry_run_failure_json(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        # AC20 — broken bundle → exit 1, stdout valid JSON with ok=false,
        # stderr carries the AC13 summary line.
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        bundle = yaml.safe_load(export.stdout)
        bundle["entries"]["agent-a"]["payload"]["linked"] = {"__ref__": "does-not-exist"}
        broken_path = tmp_path / "broken.yaml"
        broken_path.write_text(yaml.safe_dump(bundle, sort_keys=False))

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "import", str(broken_path), "--dry-run"],
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        has_error = bool(payload["global_errors"]) or any(
            i["errors"] for i in payload["entry_issues"]
        )
        assert has_error
        assert re.search(
            r"validation failed: \d+ global error\(s\), \d+ entry issue\(s\)", result.stderr
        )
        # AC20 — no writes performed: re-export equals pre-export.
        before_re = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert before_re.stdout == export.stdout

    @pytest.mark.parametrize("fmt", ["table", "json", "yaml"])
    def test_dry_run_happy_format_coverage(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path, fmt: str
    ) -> None:
        # AC21 — parametrised over all formats.
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(export.stdout)

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", fmt, "import", str(bundle_path), "--dry-run"],
        )
        assert result.exit_code == 0, result.stderr
        if fmt == "json":
            payload = json.loads(result.stdout)
            assert payload["ok"] is True
            assert payload["namespace"] == "ns-a"
        elif fmt == "yaml":
            payload = yaml.safe_load(result.stdout)
            assert payload["ok"] is True
            assert payload["namespace"] == "ns-a"
        else:
            assert "ok: True" in result.stdout
            assert "ns-a" in result.stdout


# --------------------------------------------------------------------------- #
# Usage errors
# --------------------------------------------------------------------------- #


class TestImportUsageErrors:
    """AC9 + AC11 + AC22 — path/parse/encoding failure shapes."""

    def test_missing_file(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(missing)])
        assert result.exit_code == 2
        assert "file not found" in result.stderr

    def test_directory_not_file(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        d = tmp_path / "a-directory"
        d.mkdir()
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(d)])
        assert result.exit_code == 2
        assert "file not found" in result.stderr

    def test_non_utf8_file(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        bad = tmp_path / "garbage.yaml"
        bad.write_bytes(b"\xff\xfe\x00\x00\xff\xff")
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(bad)])
        assert result.exit_code == 2
        assert "not valid UTF-8" in result.stderr

    def test_malformed_yaml(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        bad = tmp_path / "malformed.yaml"
        bad.write_text(":\n-[[[\n")
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["import", str(bad)])
        assert result.exit_code == 2
        assert "YAML parse error" in result.stderr


# --------------------------------------------------------------------------- #
# Stdout discipline
# --------------------------------------------------------------------------- #


class TestStdoutDiscipline:
    """AC24 — stream routing pins."""

    def test_persistence_stdout_empty_success_on_stderr(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(export.stdout)
        result = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["import", str(bundle_path)]
        )
        assert result.exit_code == 0, result.stderr
        assert result.stdout == ""
        assert "imported" in result.stderr

    def test_dry_run_json_stderr_empty_stdout_has_report(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(export.stdout)
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root)
            + ["--format", "json", "import", str(bundle_path), "--dry-run"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert result.stderr == ""
