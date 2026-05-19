"""Tests for :mod:`akgentic.infra.cli.commands.catalog` (v2 shape).

Covers the happy-path CRUD matrix, error responses, and the ``--namespace``
query-parameter wiring for every v2 catalog kind. The test fixture mimics
the v2 unified router's URL contract (``/admin/catalog/{kind}`` collection,
``/admin/catalog/{kind}/{id}?namespace=<ns>`` per-entry) so the CLI speaks
v2 end-to-end.

All network I/O runs through :class:`httpx.MockTransport`; all filesystem
I/O runs through ``tmp_path`` via the module-level seams on
:mod:`akgentic.infra.cli.main`. No real ``~/.akgentic/``, no real HTTP.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
import yaml
from akgentic.catalog.models import Entry
from typer.testing import CliRunner

from akgentic.infra.cli import main as main_module
from akgentic.infra.cli.auth import TokenCacheEntry, save_token_cache
from akgentic.infra.cli.commands import catalog as catalog_module
from akgentic.infra.cli.main import app

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_overrides_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None
    monkeypatch.delenv("AKGENTIC_PROFILE", raising=False)
    yield
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_noauth_config(path: Path, *, profile_name: str = "oss") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n",
        encoding="utf-8",
    )


def _write_auth_config(path: Path, *, profile_name: str = "acme-prod") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        "      issuer: https://issuer.example.com\n"
        "      client_id: akgentic-cli\n",
        encoding="utf-8",
    )


# --- v2 Entry payload factories ---------------------------------------------


_MODEL_TYPE_BY_KIND: dict[str, str] = {
    "team": "akgentic.team.models.TeamCard",
    "agent": "akgentic.agent.config.AgentConfig",
    "tool": "akgentic.tool.tool_card.ToolCard",
    "model": "akgentic.llm.model.ModelConfig",
    "prompt": "akgentic.llm.prompt.PromptTemplate",
}


def _entry_payload(kind: str, entry_id: str, namespace: str = "test-ns") -> dict[str, Any]:
    """Build a v2 ``Entry`` dict for ``kind`` — minimal, JSON-serialisable."""
    return {
        "id": entry_id,
        "kind": kind,
        "namespace": namespace,
        "model_type": _MODEL_TYPE_BY_KIND[kind],
        "description": f"cli-catalog test {kind} {entry_id}",
        "payload": {"name": f"{kind}-{entry_id}"},
    }


EntryFactory = Callable[[str], dict[str, Any]]

# (kind, payload_factory)
KINDS: list[tuple[str, EntryFactory]] = [
    ("team", lambda tid: _entry_payload("team", tid)),
    ("agent", lambda tid: _entry_payload("agent", tid)),
    ("tool", lambda tid: _entry_payload("tool", tid)),
    ("model", lambda tid: _entry_payload("model", tid)),
    ("prompt", lambda tid: _entry_payload("prompt", tid)),
]


# --- In-memory fake v2 catalog server ---------------------------------------


class FakeServer:
    """Minimal v2 admin-catalog fake speaking /admin/catalog/{kind}[/id]?namespace=<ns>.

    Stores entries as ``{kind: {(namespace, id): entry_dict}}``. Each
    incoming request populates ``captured_requests`` for assertion.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
            "team": {},
            "agent": {},
            "tool": {},
            "model": {},
            "prompt": {},
        }
        self.captured_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.captured_requests.append(request)
        parts = request.url.path.rstrip("/").split("/")
        # Expect /admin/catalog/<kind>[/id]
        if len(parts) < 4 or parts[1] != "admin" or parts[2] != "catalog":
            return httpx.Response(404, json={"detail": "not an admin route", "errors": []})
        if parts[3] == "namespace" and len(parts) >= 5 and parts[4] == "import":
            return self._import_namespace(request)
        kind = parts[3]
        entry_id = parts[4] if len(parts) >= 5 else None
        if kind not in self.store:
            return httpx.Response(404, json={"detail": f"unknown kind {kind}", "errors": []})
        if entry_id is None:
            return self._dispatch_collection(request, kind)
        return self._dispatch_item(request, kind, entry_id)

    def _dispatch_collection(
        self,
        request: httpx.Request,
        kind: str,
    ) -> httpx.Response:
        table = self.store[kind]
        if request.method == "GET":
            namespace = request.url.params.get("namespace")
            if namespace is None:
                entries = list(table.values())
            else:
                entries = [e for (ns, _id), e in table.items() if ns == namespace]
            return httpx.Response(200, json=entries)
        if request.method == "POST":
            return self._create(request, kind)
        return httpx.Response(405, json={"detail": "method not allowed", "errors": []})

    def _dispatch_item(
        self,
        request: httpx.Request,
        kind: str,
        entry_id: str,
    ) -> httpx.Response:
        table = self.store[kind]
        namespace = request.url.params.get("namespace")
        if namespace is None:
            return httpx.Response(
                422, json={"detail": "namespace query parameter is required", "errors": []}
            )
        key = (namespace, entry_id)
        if request.method == "GET":
            if key not in table:
                return self._not_found(kind, entry_id, namespace)
            return httpx.Response(200, json=table[key])
        if request.method == "PUT":
            return self._update(request, kind, entry_id, namespace)
        if request.method == "DELETE":
            if key not in table:
                return self._not_found(kind, entry_id, namespace)
            table.pop(key)
            return httpx.Response(204)
        return httpx.Response(405, json={"detail": "method not allowed", "errors": []})

    @staticmethod
    def _not_found(kind: str, entry_id: str, namespace: str) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "detail": f"{kind} '{entry_id}' not found in namespace {namespace!r}",
                "errors": [],
            },
        )

    def _create(
        self,
        request: httpx.Request,
        kind: str,
    ) -> httpx.Response:
        body = self._parse_body(request)
        if body is None:
            return httpx.Response(422, json={"detail": "malformed body", "errors": ["bad"]})
        entry_id = body.get("id")
        namespace = body.get("namespace")
        if not isinstance(entry_id, str) or not isinstance(namespace, str):
            return httpx.Response(
                422,
                json={"detail": "missing id or namespace", "errors": ["id and namespace required"]},
            )
        table = self.store[kind]
        key = (namespace, entry_id)
        if key in table:
            return httpx.Response(
                409, json={"detail": f"id {entry_id!r} already exists", "errors": []}
            )
        table[key] = body
        return httpx.Response(201, json=body)

    def _update(
        self,
        request: httpx.Request,
        kind: str,
        entry_id: str,
        namespace: str,
    ) -> httpx.Response:
        table = self.store[kind]
        body = self._parse_body(request)
        if body is None:
            return httpx.Response(422, json={"detail": "malformed body", "errors": ["bad"]})
        key = (namespace, entry_id)
        if key not in table:
            return self._not_found(kind, entry_id, namespace)
        body_id = body.get("id")
        if body_id != entry_id:
            return httpx.Response(
                409,
                json={
                    "detail": f"id mismatch: path {entry_id!r} vs body {body_id!r}",
                    "errors": [],
                },
            )
        table[key] = body
        return httpx.Response(200, json=body)

    @staticmethod
    def _parse_body(request: httpx.Request) -> dict[str, Any] | None:
        try:
            parsed = json.loads(request.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _import_namespace(self, request: httpx.Request) -> httpx.Response:
        """Fake ``POST /admin/catalog/namespace/import`` — parse a YAML bundle.

        Mirrors the real route's contract: a single ``application/yaml``
        bundle in, a JSON array of imported v2 ``Entry`` objects out (201).
        """
        if request.method != "POST":
            return httpx.Response(405, json={"detail": "method not allowed", "errors": []})
        try:
            bundle = yaml.safe_load(request.content.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError):
            return httpx.Response(422, json={"detail": "failed to parse bundle YAML", "errors": []})
        if not isinstance(bundle, dict) or not isinstance(bundle.get("entries"), dict):
            return httpx.Response(422, json={"detail": "malformed bundle", "errors": []})
        namespace = bundle.get("namespace", "imported-ns")
        imported: list[dict[str, Any]] = []
        for entry_id, raw in bundle["entries"].items():
            entry = {**raw, "id": entry_id, "namespace": namespace}
            self.store.setdefault(entry["kind"], {})[(namespace, entry_id)] = entry
            imported.append(entry)
        return httpx.Response(201, json=imported)


def _install_fake_server(
    tmp_path: Path,
    *,
    pre_seed: list[tuple[str, dict[str, Any]]] | None = None,
) -> FakeServer:
    """Install a FakeServer wired via ``_HTTP_CLIENT_FACTORY_OVERRIDE``."""
    config_path = tmp_path / "config.yaml"
    _write_noauth_config(config_path)
    main_module._CONFIG_PATH_OVERRIDE = config_path

    fake = FakeServer()
    for kind, entry in pre_seed or []:
        key = (entry["namespace"], entry["id"])
        fake.store[kind][key] = entry

    transport = httpx.MockTransport(fake.handler)

    def factory(profile: Any, **_kwargs: Any) -> httpx.Client:
        return httpx.Client(base_url=str(profile.endpoint), transport=transport)

    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory
    return fake


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


def test_no_direct_httpx_and_no_apiclient_construction() -> None:
    """The module is a thin wire over ``_state.client``."""
    src = Path(catalog_module.__file__).read_text(encoding="utf-8")
    assert "import httpx" not in src
    assert "ApiClient(base_url" not in src


def test_main_py_has_exactly_one_catalog_import_and_register() -> None:
    """``main.py`` gains exactly one import + one register call."""
    src = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "from akgentic.infra.cli.commands import catalog as catalog_command" in src
    assert "catalog_command.register(app)" in src


@pytest.mark.parametrize("skipped_kind", ["team", "agent", "tool", "model", "prompt"])
def test_registration_removal_eliminates_only_that_kind(skipped_kind: str) -> None:
    """Skipping one call eliminates exactly that subgroup."""
    fresh_catalog = typer.Typer(name="catalog")
    for kind in ("team", "agent", "tool", "model", "prompt"):
        if kind == skipped_kind:
            continue
        catalog_module._register_kind_commands(fresh_catalog, kind)

    names = {group.name for group in fresh_catalog.registered_groups}
    expected = {"team", "agent", "tool", "model", "prompt"} - {skipped_kind}
    assert names == expected


def test_all_kinds_registered_on_main_app() -> None:
    """``ak catalog`` gains all v2 kinds on the main app."""
    catalog_groups = [g for g in app.registered_groups if g.name == "catalog"]
    assert len(catalog_groups) == 1
    catalog_app = catalog_groups[0].typer_instance
    subnames = {g.name for g in catalog_app.registered_groups}
    assert subnames == {"team", "agent", "tool", "model", "prompt"}


def test_import_command_registered_on_catalog_group() -> None:
    """``ak catalog import`` is registered as a command on the catalog group."""
    catalog_groups = [g for g in app.registered_groups if g.name == "catalog"]
    assert len(catalog_groups) == 1
    catalog_app = catalog_groups[0].typer_instance
    command_names = {c.name for c in catalog_app.registered_commands}
    assert "import" in command_names


# ---------------------------------------------------------------------------
# list (happy + namespace filter forwarding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_list_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("list-1")
    fake = _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(app, ["catalog", kind, "list"])

    assert result.exit_code == 0, result.stderr
    assert "ID" in result.stdout  # upper-cased table header
    assert "list-1" in result.stdout
    assert any(r.url.path == f"/admin/catalog/{kind}" for r in fake.captured_requests)


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_list_happy_path_json(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("list-json")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(app, ["--format", "json", "catalog", kind, "list"])
    assert result.exit_code == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, list)
    assert any(entry["id"] == "list-json" for entry in decoded)


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_list_with_namespace_forwards_filter(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("ns-target")
    fake = _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(
        app, ["catalog", kind, "list", "--namespace", payload["namespace"]]
    )
    assert result.exit_code == 0, result.stderr
    list_requests = [r for r in fake.captured_requests if r.url.path == f"/admin/catalog/{kind}"]
    assert list_requests
    assert list_requests[-1].url.params.get("namespace") == payload["namespace"]


# ---------------------------------------------------------------------------
# get (happy + yaml render + 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_get_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("get-1")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(
        app,
        ["catalog", kind, "get", "get-1", "--namespace", payload["namespace"]],
    )
    assert result.exit_code == 0, result.stderr
    assert "get-1" in result.stdout


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_get_happy_path_yaml(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("get-yaml")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(
        app,
        [
            "--format",
            "yaml",
            "catalog",
            kind,
            "get",
            "get-yaml",
            "--namespace",
            payload["namespace"],
        ],
    )
    assert result.exit_code == 0, result.stderr
    loaded = yaml.safe_load(result.stdout)
    assert isinstance(loaded, dict)
    assert loaded["id"] == "get-yaml"


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_get_unknown_404(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    _install_fake_server(tmp_path)
    result = runner.invoke(
        app, ["catalog", kind, "get", "nope", "--namespace", "test-ns"]
    )
    assert result.exit_code == 1
    assert "HTTP 404" in result.stderr
    assert "nope" in result.stderr


# ---------------------------------------------------------------------------
# create (file yaml + stdin json + duplicate 409 + malformed 422 + bad ext)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_create_file_yaml_forwards_content_type(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("create-yaml")
    body_path = tmp_path / "body.yaml"
    # Ship JSON-shaped bytes through the YAML path — the fake server reads
    # JSON only; this still proves the outbound Content-Type header.
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", kind, "create", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    assert "create-yaml" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/yaml"


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_create_file_json(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("create-json")
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", kind, "create", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    assert "create-json" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/json"


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_create_stdin_defaults_to_json(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("stdin-json")
    result = runner.invoke(
        app, ["catalog", kind, "create"], input=json.dumps(payload)
    )
    assert result.exit_code == 0, result.stderr
    assert "stdin-json" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/json"


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_create_duplicate_409(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("dup")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", kind, "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "HTTP 409" in result.stderr
    assert "dup" in result.stderr


def test_create_malformed_body_422(runner: CliRunner, tmp_path: Path) -> None:
    """A malformed body → server 422 → CLI exits 1 with detail."""
    _install_fake_server(tmp_path)
    body_path = tmp_path / "bad.json"
    body_path.write_text("not-json-at-all-{{", encoding="utf-8")

    result = runner.invoke(app, ["catalog", "team", "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "HTTP 422" in result.stderr
    assert "malformed body" in result.stderr


def test_create_unsupported_extension_no_http(runner: CliRunner, tmp_path: Path) -> None:
    """``.txt`` exits non-zero; no HTTP call is made."""
    fake = _install_fake_server(tmp_path)
    body_path = tmp_path / "body.txt"
    body_path.write_text("ignored", encoding="utf-8")

    result = runner.invoke(app, ["catalog", "team", "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "Unsupported file extension 'txt'" in result.stderr
    assert "use .yaml, .yml, or .json" in result.stderr
    assert fake.captured_requests == []


# ---------------------------------------------------------------------------
# update (file json + unknown 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_update_file_json_happy_path(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("upd")
    fake = _install_fake_server(tmp_path, pre_seed=[(kind, payload)])
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "catalog",
            kind,
            "update",
            "upd",
            "--namespace",
            payload["namespace"],
            "--file",
            str(body_path),
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "upd" in result.stdout
    put = [r for r in fake.captured_requests if r.method == "PUT"][-1]
    assert put.headers["content-type"] == "application/json"
    assert put.url.params.get("namespace") == payload["namespace"]


# ---------------------------------------------------------------------------
# delete (happy + structured payload + unknown 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_delete_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("del")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(
        app,
        ["catalog", kind, "delete", "del", "--namespace", payload["namespace"]],
    )
    assert result.exit_code == 0, result.stderr
    assert f"Deleted {kind} 'del'." in result.stdout


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_delete_json_format_structured_payload(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    payload = factory("del-j")
    _install_fake_server(tmp_path, pre_seed=[(kind, payload)])

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "catalog",
            kind,
            "delete",
            "del-j",
            "--namespace",
            payload["namespace"],
        ],
    )
    assert result.exit_code == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert decoded == {"kind": kind, "id": "del-j", "status": "deleted"}


@pytest.mark.parametrize(("kind", "factory"), KINDS)
def test_delete_unknown_404(
    runner: CliRunner,
    tmp_path: Path,
    kind: str,
    factory: EntryFactory,
) -> None:
    _install_fake_server(tmp_path)
    result = runner.invoke(
        app, ["catalog", kind, "delete", "ghost", "--namespace", "test-ns"]
    )
    assert result.exit_code == 1
    assert "HTTP 404" in result.stderr


# ---------------------------------------------------------------------------
# Auto-auth inheritance
# ---------------------------------------------------------------------------


def test_list_inherits_bearer_auth_from_profile(runner: CliRunner, tmp_path: Path) -> None:
    """A config with auth:, a pre-seeded cache → outbound Bearer."""
    config_path = tmp_path / "config.yaml"
    _write_auth_config(config_path, profile_name="acme-prod")
    credentials_dir = tmp_path / "credentials"
    save_token_cache(
        "acme-prod",
        TokenCacheEntry(
            access_token="test-access-token",
            refresh_token="rt",
            expires_at=99_999_999_999,
        ),
        credentials_dir=credentials_dir,
    )
    # Verify the cache file was created with expected restrictive permissions.
    cache_file = credentials_dir / "acme-prod.json"
    assert cache_file.exists()
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600

    main_module._CONFIG_PATH_OVERRIDE = config_path
    main_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir

    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers.copy())
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)

    def factory(profile: Any, **kwargs: Any) -> httpx.Client:
        from akgentic.infra.cli.http import build_http_client_with_auto_auth  # noqa: PLC0415

        return build_http_client_with_auto_auth(profile, transport=transport, **kwargs)

    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory

    result = runner.invoke(app, ["catalog", "team", "list"])
    assert result.exit_code == 0, result.stderr
    assert captured_headers
    assert captured_headers[-1].get("authorization") == "Bearer test-access-token"


# ---------------------------------------------------------------------------
# import (namespace bundle: file yaml + stdin + bad ext + error response)
# ---------------------------------------------------------------------------


def _bundle_yaml(namespace: str = "imported-ns") -> str:
    """Build a minimal namespace-bundle YAML document for import tests."""
    return yaml.safe_dump(
        {
            "namespace": namespace,
            "user_id": "anonymous",
            "entries": {
                "imp-team": {
                    "kind": "team",
                    "model_type": _MODEL_TYPE_BY_KIND["team"],
                    "description": "imported team",
                    "payload": {"name": "team-imp"},
                },
                "imp-agent": {
                    "kind": "agent",
                    "model_type": _MODEL_TYPE_BY_KIND["agent"],
                    "description": "imported agent",
                    "payload": {"name": "agent-imp"},
                },
            },
        }
    )


def test_import_file_yaml_happy_path(runner: CliRunner, tmp_path: Path) -> None:
    """``catalog import --file bundle.yaml`` POSTs the bundle as application/yaml."""
    fake = _install_fake_server(tmp_path)
    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text(_bundle_yaml(), encoding="utf-8")

    result = runner.invoke(app, ["catalog", "import", "--file", str(bundle_path)])

    assert result.exit_code == 0, result.stderr
    assert "imp-team" in result.stdout
    assert "imp-agent" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.url.path == "/admin/catalog/namespace/import"
    assert post.headers["content-type"] == "application/yaml"


def test_import_file_yml_extension_accepted(runner: CliRunner, tmp_path: Path) -> None:
    """A ``.yml`` extension is accepted just like ``.yaml``."""
    _install_fake_server(tmp_path)
    bundle_path = tmp_path / "bundle.yml"
    bundle_path.write_text(_bundle_yaml(), encoding="utf-8")

    result = runner.invoke(app, ["catalog", "import", "--file", str(bundle_path)])
    assert result.exit_code == 0, result.stderr


def test_import_stdin_happy_path(runner: CliRunner, tmp_path: Path) -> None:
    """``catalog import`` with no ``--file`` reads the bundle from stdin."""
    fake = _install_fake_server(tmp_path)

    result = runner.invoke(app, ["catalog", "import"], input=_bundle_yaml())

    assert result.exit_code == 0, result.stderr
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.url.path == "/admin/catalog/namespace/import"
    assert post.headers["content-type"] == "application/yaml"


def test_import_json_format_returns_entry_list(runner: CliRunner, tmp_path: Path) -> None:
    """``--format json`` renders the imported entries as a JSON array."""
    _install_fake_server(tmp_path)
    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text(_bundle_yaml(), encoding="utf-8")

    result = runner.invoke(
        app, ["--format", "json", "catalog", "import", "--file", str(bundle_path)]
    )
    assert result.exit_code == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, list)
    assert {e["id"] for e in decoded} == {"imp-team", "imp-agent"}


def test_import_unsupported_extension_no_http(runner: CliRunner, tmp_path: Path) -> None:
    """A non-YAML ``--file`` extension exits 1; no HTTP call is made."""
    fake = _install_fake_server(tmp_path)
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["catalog", "import", "--file", str(bundle_path)])
    assert result.exit_code == 1
    assert "Unsupported file extension 'json'" in result.stderr
    assert "requires .yaml or .yml" in result.stderr
    assert fake.captured_requests == []


def test_import_malformed_bundle_422(runner: CliRunner, tmp_path: Path) -> None:
    """A bundle missing ``entries`` → server 422 → CLI exits 1 with detail."""
    _install_fake_server(tmp_path)
    bundle_path = tmp_path / "bundle.yaml"
    bundle_path.write_text(yaml.safe_dump({"namespace": "x"}), encoding="utf-8")

    result = runner.invoke(app, ["catalog", "import", "--file", str(bundle_path)])
    assert result.exit_code == 1
    assert "HTTP 422" in result.stderr
    assert "malformed bundle" in result.stderr


# ---------------------------------------------------------------------------
# Wire shape — the Entry model survives a round trip
# ---------------------------------------------------------------------------


def test_entry_payload_validates_as_v2_entry() -> None:
    """Sanity check that the test factories produce valid v2 Entry dicts."""
    payload = _entry_payload("team", "rt-team")
    entry = Entry.model_validate(payload)
    assert entry.kind == "team"
    assert entry.namespace == "test-ns"
    assert entry.id == "rt-team"
