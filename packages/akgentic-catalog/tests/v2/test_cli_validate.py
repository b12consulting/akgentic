"""Tests for the v2 ``ak-catalog`` CLI validate verb — Story 17.4.

Exercises both flavors of ``ak-catalog validate``:

* Persisted-state flavor: ``ak-catalog validate --namespace <ns>`` delegates
  to :meth:`Catalog.validate_namespace` against the YAML-backed tmp-dir
  ``Catalog`` seeded with a self-contained namespace (team + agent + tool).
* Dry-run bundle flavor: ``ak-catalog validate <bundle-file>`` delegates to
  :meth:`Catalog.validate_namespace_yaml` against a tmp YAML bundle file.

The error-class parametrisations (AC17 / AC18) construct minimally-broken
namespaces — one case per check surfaced by
:func:`akgentic.catalog.validation.validate_entries` plus the resolver's
ref-cycle and allowlist errors.
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
from akgentic.catalog.models.queries import EntryQuery
from akgentic.catalog.repositories.yaml import YamlEntryRepository

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_FIXTURE_MODULE = "akgentic.catalog.tests_fixture_17_4"
_LEAF_TYPE = f"{_FIXTURE_MODULE}.LeafModel"
_AGENT_TYPE = f"{_FIXTURE_MODULE}.AgentModel"
_REQUIRED_TYPE = f"{_FIXTURE_MODULE}.RequiredFieldModel"


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

    class RequiredFieldModel(BaseModel):
        # No defaults — forces transient validation failure when required fields are absent.
        must_be_present: str

    module = types.ModuleType(_FIXTURE_MODULE)
    module.LeafModel = LeafModel  # type: ignore[attr-defined]
    module.AgentModel = AgentModel  # type: ignore[attr-defined]
    module.RequiredFieldModel = RequiredFieldModel  # type: ignore[attr-defined]
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
# Broken-namespace construction helpers (used by both flavors of AC17 / AC18)
# --------------------------------------------------------------------------- #


def _broken_bundles() -> dict[str, tuple[dict[str, Any], str]]:
    """Return ``{case_id: (bundle_doc, expected_error_substring)}`` per error class.

    Each bundle is a self-contained document-level dict (``namespace`` /
    ``user_id`` / ``entries``) that, when written to YAML and fed through
    ``ak-catalog validate <bundle-file>``, surfaces the named error class.
    """
    ns = "ns-a"
    user = "alice"

    def _entry(
        kind: str,
        model_type: str,
        payload: dict[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "kind": kind,
            "model_type": model_type,
            "description": "",
            "payload": payload,
        }
        base.update(extra)
        return base

    cases: dict[str, tuple[dict[str, Any], str]] = {
        # Zero team entries — only a tool + an agent.
        "missing_team": (
            {
                "namespace": ns,
                "user_id": user,
                "entries": {
                    "tool-a": _entry("tool", _LEAF_TYPE, {}),
                    "agent-a": _entry("agent", _AGENT_TYPE, {"linked": {"__ref__": "tool-a"}}),
                },
            },
            "has no team entry and no meta entry",
        ),
        # Two team entries under the same namespace.
        "multiple_teams": (
            {
                "namespace": ns,
                "user_id": user,
                "entries": {
                    "team-a": _entry("team", _TEAM_TYPE, _team_payload()),
                    "team-b": _entry("team", _TEAM_TYPE, _team_payload()),
                },
            },
            "multiple team entries",
        ),
        # NOTE: user_id mismatch is not reachable through the bundle parser
        # because ``load_namespace`` stamps the document-level ``user_id`` onto
        # every parsed entry uniformly. This class is covered in the persisted
        # flavor only (see ``_persisted_cases`` below). Similarly, ref-cycle
        # detection requires both targets to be resolvable via the live
        # repository (``populate_refs`` walks the repo, not the parsed bundle);
        # in a dry-run bundle flavor a cycle surfaces as a dangling-ref error,
        # so the cycle class also lives only in the persisted flavor matrix.
        # Dangling ref: agent-a points at an id absent from bundle + persisted.
        "dangling_ref": (
            {
                "namespace": ns,
                "user_id": user,
                "entries": {
                    "team-a": _entry("team", _TEAM_TYPE, _team_payload()),
                    "agent-a": _entry(
                        "agent", _AGENT_TYPE, {"linked": {"__ref__": "does-not-exist"}}
                    ),
                },
            },
            "dangling ref",
        ),
        # Allowlist violation: a tool declaring a non-akgentic.* model_type.
        # load_namespace -> Entry(**data) -> AllowlistedPath rejects it ->
        # CatalogValidationError, which validate_namespace_yaml captures as a
        # global error on the ``entry '<id>' is invalid`` wrapper.
        "allowlist_violation": (
            {
                "namespace": ns,
                "user_id": user,
                "entries": {
                    "team-a": _entry("team", _TEAM_TYPE, _team_payload()),
                    "tool-a": _entry("tool", "mypackage.Evil", {}),
                },
            },
            "outside allowlist",
        ),
        # Transient validation failure: payload missing required field.
        "transient_validation": (
            {
                "namespace": ns,
                "user_id": user,
                "entries": {
                    "team-a": _entry("team", _TEAM_TYPE, _team_payload()),
                    "tool-a": _entry("tool", _REQUIRED_TYPE, {}),
                },
            },
            "does not validate",
        ),
    }
    return cases


def _write_bundle(path: Path, bundle_doc: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(bundle_doc, sort_keys=False))


def _seed_raw_entries(root: Path, namespace: str, entries: list[Entry]) -> None:
    """Write ``entries`` into a YAML-backed repository bypassing Catalog.create."""
    repo = YamlEntryRepository(root)
    for entry in entries:
        repo.put(entry)


# --------------------------------------------------------------------------- #
# Happy paths (AC15 + AC16 + AC22 + AC23)
# --------------------------------------------------------------------------- #


class TestValidateHappy:
    """AC15 / AC16 / AC22 / AC23 — persisted + bundle happy paths + formats."""

    @pytest.mark.parametrize("fmt", ["table", "json", "yaml"])
    def test_persisted_happy(self, runner: CliRunner, catalog_root: Path, fmt: str) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", fmt, "validate", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0, result.stderr
        assert result.stderr == ""
        if fmt == "json":
            payload = json.loads(result.stdout)
            assert payload["ok"] is True
            assert payload["namespace"] == "ns-a"
            assert payload["global_errors"] == []
            assert payload["entry_issues"] == []
        elif fmt == "yaml":
            payload = yaml.safe_load(result.stdout)
            assert payload["ok"] is True
            assert payload["namespace"] == "ns-a"
            assert payload["global_errors"] == []
            assert payload["entry_issues"] == []
        else:
            assert "ok: True" in result.stdout
            assert "ns-a" in result.stdout

    def test_bundle_happy(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        # Export via the 17.3 export verb -> validate the same bundle.
        export = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["export", "--namespace", "ns-a"]
        )
        assert export.exit_code == 0
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text(export.stdout)

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "validate", str(bundle_path)],
        )
        assert result.exit_code == 0, result.stderr
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["namespace"] == "ns-a"

    def test_persisted_empty_namespace(self, runner: CliRunner, tmp_path: Path) -> None:
        # AC15 — empty backend -> ok=False + AC11 stderr line.
        root = tmp_path / "catalog"
        root.mkdir()
        result = runner.invoke(
            cli_main.app,
            [
                "--backend",
                "yaml",
                "--root",
                str(root),
                "--format",
                "json",
                "validate",
                "--namespace",
                "nope",
            ],
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["namespace"] == "nope"
        assert payload["global_errors"]
        assert re.search(
            r"validation failed: \d+ global error\(s\), \d+ entry issue\(s\)",
            result.stderr,
        )


# --------------------------------------------------------------------------- #
# Bundle non-destructiveness (AC19)
# --------------------------------------------------------------------------- #


class TestBundleNonDestructive:
    """AC19 — a failing bundle validate leaves the persisted namespace untouched."""

    def test_broken_bundle_does_not_mutate_repo(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        catalog = Catalog(YamlEntryRepository(catalog_root))
        before = catalog.list(EntryQuery(namespace="ns-a"))
        bundle, _ = _broken_bundles()["dangling_ref"]
        bundle_path = tmp_path / "broken.yaml"
        _write_bundle(bundle_path, bundle)

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["validate", str(bundle_path)],
        )
        assert result.exit_code == 1

        after = Catalog(YamlEntryRepository(catalog_root)).list(EntryQuery(namespace="ns-a"))
        # Byte-equivalent entries (same ids, same payloads).
        assert [(e.id, e.payload) for e in before] == [(e.id, e.payload) for e in after]


# --------------------------------------------------------------------------- #
# Error-class coverage — bundle flavor (AC17)
# --------------------------------------------------------------------------- #


class TestBundleErrorClasses:
    """AC17 — one parametrised case per error class, bundle flavor."""

    @pytest.mark.parametrize("case_id", list(_broken_bundles().keys()))
    def test_bundle_error_class(
        self,
        runner: CliRunner,
        catalog_root: Path,
        tmp_path: Path,
        case_id: str,
    ) -> None:
        bundle, substring = _broken_bundles()[case_id]
        bundle_path = tmp_path / f"{case_id}.yaml"
        _write_bundle(bundle_path, bundle)

        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "validate", str(bundle_path)],
        )
        assert result.exit_code == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        # substring must appear in at least one error surface.
        haystack = (
            " ".join(payload["global_errors"])
            + " "
            + " ".join(err for issue in payload["entry_issues"] for err in issue["errors"])
        )
        assert substring in haystack, f"expected substring {substring!r} in report; got {payload!r}"
        assert re.search(
            r"validation failed: \d+ global error\(s\), \d+ entry issue\(s\)",
            result.stderr,
        )


# --------------------------------------------------------------------------- #
# Error-class coverage — persisted flavor (AC18)
# --------------------------------------------------------------------------- #


def _persisted_cases() -> dict[str, tuple[list[Entry], str]]:
    """Return ``{case_id: (entries_to_seed, expected_error_substring)}`` for
    error classes reachable through direct repository seeding (bypassing
    ``Catalog.create``). Classes that require Entry-construction-level
    invalid data (``allowlist_violation``) are omitted — their coverage
    lives in the bundle matrix only.
    """
    ns = "ns-a"
    user = "alice"

    def _e(**kw: Any) -> Entry:
        base: dict[str, Any] = {
            "namespace": ns,
            "user_id": user,
            "description": "",
            "payload": {},
        }
        base.update(kw)
        return Entry(**base)

    cases: dict[str, tuple[list[Entry], str]] = {
        "missing_team": (
            [
                _e(id="tool-a", kind="tool", model_type=_LEAF_TYPE),
                _e(
                    id="agent-a",
                    kind="agent",
                    model_type=_AGENT_TYPE,
                    payload={"linked": {"__ref__": "tool-a"}},
                ),
            ],
            "has no team entry and no meta entry",
        ),
        "multiple_teams": (
            [
                _e(id="team-a", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
                _e(id="team-b", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
            ],
            "multiple team entries",
        ),
        "mismatched_user_id": (
            [
                _e(id="team-a", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
                _e(
                    id="tool-a",
                    kind="tool",
                    model_type=_LEAF_TYPE,
                    user_id="bob",  # type: ignore[arg-type]
                ),
            ],
            "user_id",
        ),
        "ref_cycle": (
            [
                _e(id="team-a", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
                _e(
                    id="agent-a",
                    kind="agent",
                    model_type=_AGENT_TYPE,
                    payload={"linked": {"__ref__": "agent-b"}},
                ),
                _e(
                    id="agent-b",
                    kind="agent",
                    model_type=_AGENT_TYPE,
                    payload={"linked": {"__ref__": "agent-a"}},
                ),
            ],
            "cycle",
        ),
        "dangling_ref": (
            [
                _e(id="team-a", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
                _e(
                    id="agent-a",
                    kind="agent",
                    model_type=_AGENT_TYPE,
                    payload={"linked": {"__ref__": "does-not-exist"}},
                ),
            ],
            "dangling ref",
        ),
        "transient_validation": (
            [
                _e(id="team-a", kind="team", model_type=_TEAM_TYPE, payload=_team_payload()),
                _e(id="tool-a", kind="tool", model_type=_REQUIRED_TYPE),
            ],
            "does not validate",
        ),
    }
    return cases


class TestPersistedErrorClasses:
    """AC18 — one parametrised case per error class, persisted flavor."""

    @pytest.mark.parametrize("case_id", list(_persisted_cases().keys()))
    def test_persisted_error_class(
        self,
        runner: CliRunner,
        tmp_path: Path,
        case_id: str,
    ) -> None:
        entries, substring = _persisted_cases()[case_id]
        root = tmp_path / "catalog"
        root.mkdir()
        _seed_raw_entries(root, "ns-a", entries)

        result = runner.invoke(
            cli_main.app,
            [
                "--backend",
                "yaml",
                "--root",
                str(root),
                "--format",
                "json",
                "validate",
                "--namespace",
                "ns-a",
            ],
        )
        assert result.exit_code == 1, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        haystack = (
            " ".join(payload["global_errors"])
            + " "
            + " ".join(err for issue in payload["entry_issues"] for err in issue["errors"])
        )
        assert substring in haystack, f"expected substring {substring!r} in report; got {payload!r}"
        assert re.search(
            r"validation failed: \d+ global error\(s\), \d+ entry issue\(s\)",
            result.stderr,
        )


# --------------------------------------------------------------------------- #
# Argument exclusivity + usage errors (AC20 + AC21)
# --------------------------------------------------------------------------- #


class TestArgumentExclusivity:
    """AC20 — zero-or-both args + empty-string namespace."""

    def test_zero_args(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["validate"])
        assert result.exit_code == 2
        assert "requires either --namespace" in result.stderr

    def test_both_args(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        bundle_path = tmp_path / "bundle.yaml"
        bundle_path.write_text("dummy: true\n")
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["validate", "--namespace", "ns-a", str(bundle_path)],
        )
        assert result.exit_code == 2
        assert "not both" in result.stderr

    def test_empty_namespace(self, runner: CliRunner, catalog_root: Path) -> None:
        result = runner.invoke(
            cli_main.app, _base_args(catalog_root) + ["validate", "--namespace", ""]
        )
        assert result.exit_code == 2


class TestBundlePreflightErrors:
    """AC21 — bundle-file pre-flight errors (missing / directory / utf-8 / yaml)."""

    def test_missing_file(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.yaml"
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["validate", str(missing)])
        assert result.exit_code == 2
        assert "file not found" in result.stderr

    def test_directory_not_file(
        self, runner: CliRunner, catalog_root: Path, tmp_path: Path
    ) -> None:
        d = tmp_path / "a-directory"
        d.mkdir()
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["validate", str(d)])
        assert result.exit_code == 2
        assert "file not found" in result.stderr

    def test_non_utf8_file(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        bad = tmp_path / "garbage.yaml"
        bad.write_bytes(b"\xff\xfe\x00\x00\xff\xff")
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["validate", str(bad)])
        assert result.exit_code == 2
        assert "not valid UTF-8" in result.stderr

    def test_malformed_yaml(self, runner: CliRunner, catalog_root: Path, tmp_path: Path) -> None:
        bad = tmp_path / "malformed.yaml"
        bad.write_text(":\n-[[[\n")
        result = runner.invoke(cli_main.app, _base_args(catalog_root) + ["validate", str(bad)])
        assert result.exit_code == 2
        assert "YAML parse error" in result.stderr


# --------------------------------------------------------------------------- #
# Stdout discipline (AC23)
# --------------------------------------------------------------------------- #


class TestStdoutDiscipline:
    """AC23 — stdout carries only the report; stderr carries summary on failure."""

    def test_happy_stdout_has_report_stderr_empty(
        self, runner: CliRunner, catalog_root: Path
    ) -> None:
        result = runner.invoke(
            cli_main.app,
            _base_args(catalog_root) + ["--format", "json", "validate", "--namespace", "ns-a"],
        )
        assert result.exit_code == 0
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["ok"] is True

    def test_failure_stdout_has_report_stderr_has_summary(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        root = tmp_path / "catalog"
        root.mkdir()
        result = runner.invoke(
            cli_main.app,
            [
                "--backend",
                "yaml",
                "--root",
                str(root),
                "--format",
                "json",
                "validate",
                "--namespace",
                "nope",
            ],
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert re.search(
            r"validation failed: \d+ global error\(s\), \d+ entry issue\(s\)",
            result.stderr,
        )
