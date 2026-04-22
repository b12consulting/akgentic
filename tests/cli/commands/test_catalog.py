"""Tests for :mod:`akgentic.infra.cli.commands.catalog`.

Covers every entity x every verb happy path, the error matrix (404/409/422),
the ``--format`` flag matrix, the content-type inference rules, the
registration-count invariant, and the auto-auth inheritance test required by
AC #10 / AC #11 of Story 23.2.

All network I/O runs through :class:`httpx.MockTransport`; all filesystem I/O
runs through ``tmp_path`` via the module-level seams on
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
from akgentic.catalog.models import (
    AgentEntry,
    TeamEntry,
    TemplateEntry,
    ToolEntry,
)
from pydantic import BaseModel
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


# --- Payload factories ------------------------------------------------------
#
# These mirror the server-side admin-catalog test fixtures so request bodies
# are constructed from concrete entry models (Golden Rule #1 + AC #13).


def _template_payload(tid: str = "cli-tpl") -> TemplateEntry:
    return TemplateEntry(id=tid, template="Hello {name}, you are {role}.")


def _tool_payload(tid: str = "cli-tool") -> ToolEntry:
    return ToolEntry.model_validate(
        {
            "id": tid,
            "tool_class": "akgentic.tool.search.SearchTool",
            "tool": {"name": "search", "description": "Search the web"},
        }
    )


def _agent_payload(tid: str = "cli-agent") -> AgentEntry:
    return AgentEntry.model_validate(
        {
            "id": tid,
            "tool_ids": [],
            "card": {
                "role": "engineer",
                "description": "cli-catalog test agent",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "@Eng", "role": "Engineer"},
                "routes_to": [],
            },
        }
    )


def _team_payload(tid: str = "cli-team") -> TeamEntry:
    return TeamEntry.model_validate(
        {
            "id": tid,
            "name": f"CLI Team {tid}",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.core.messages.UserMessage"],
            "members": [{"agent_id": "human-proxy"}],
            "description": "cli-catalog test team",
        }
    )


EntryFactory = Callable[[str], BaseModel]

# (entity_name, payload_factory, query_field)
ENTITIES: list[tuple[str, EntryFactory, str]] = [
    ("templates", _template_payload, "placeholder"),
    ("tools", _tool_payload, "name"),
    ("agents", _agent_payload, "description"),
    ("teams", _team_payload, "name"),
]


# --- In-memory fake server --------------------------------------------------


class FakeServer:
    """Minimal admin-catalog fake routing /admin/catalog/<entity>[/id].

    Each instance owns a per-entity ``dict[id -> entry_dict]``. Incoming
    requests populate ``captured_requests`` for assertion. The fake echoes
    the request body on create/update (mirroring the real server contract).
    """

    def __init__(self) -> None:
        self.store: dict[str, dict[str, dict[str, Any]]] = {
            "templates": {},
            "tools": {},
            "agents": {},
            "teams": {},
        }
        self.captured_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.captured_requests.append(request)
        parts = request.url.path.rstrip("/").split("/")
        # Expect /admin/catalog/<entity>[/id]
        if len(parts) < 4 or parts[1] != "admin" or parts[2] != "catalog":
            return httpx.Response(404, json={"detail": "not an admin route", "errors": []})
        entity = parts[3]
        entry_id = parts[4] if len(parts) >= 5 else None
        if entity not in self.store:
            return httpx.Response(404, json={"detail": f"unknown entity {entity}", "errors": []})
        table = self.store[entity]
        if entry_id is None:
            return self._dispatch_collection(request, table)
        return self._dispatch_item(request, table, entity, entry_id)

    def _dispatch_collection(
        self,
        request: httpx.Request,
        table: dict[str, dict[str, Any]],
    ) -> httpx.Response:
        if request.method == "GET":
            q = request.url.params.get("q")
            entries = list(table.values())
            if q is not None:
                entries = [e for e in entries if q in json.dumps(e)]
            return httpx.Response(200, json=entries)
        if request.method == "POST":
            return self._create(request, table)
        return httpx.Response(405, json={"detail": "method not allowed", "errors": []})

    def _dispatch_item(
        self,
        request: httpx.Request,
        table: dict[str, dict[str, Any]],
        entity: str,
        entry_id: str,
    ) -> httpx.Response:
        if request.method == "GET":
            if entry_id not in table:
                return self._not_found(entity, entry_id)
            return httpx.Response(200, json=table[entry_id])
        if request.method == "PUT":
            return self._update(request, table, entity, entry_id)
        if request.method == "DELETE":
            if entry_id not in table:
                return self._not_found(entity, entry_id)
            table.pop(entry_id)
            return httpx.Response(204)
        return httpx.Response(405, json={"detail": "method not allowed", "errors": []})

    @staticmethod
    def _not_found(entity: str, entry_id: str) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": f"{entity} '{entry_id}' not found", "errors": []},
        )

    def _create(
        self,
        request: httpx.Request,
        table: dict[str, dict[str, Any]],
    ) -> httpx.Response:
        body = self._parse_body(request)
        if body is None:
            return httpx.Response(422, json={"detail": "malformed body", "errors": ["bad"]})
        entry_id = body.get("id")
        if not isinstance(entry_id, str):
            return httpx.Response(422, json={"detail": "missing id", "errors": ["id required"]})
        if entry_id in table:
            return httpx.Response(
                409, json={"detail": f"id {entry_id!r} already exists", "errors": []}
            )
        table[entry_id] = body
        return httpx.Response(201, json=body)

    def _update(
        self,
        request: httpx.Request,
        table: dict[str, dict[str, Any]],
        entity: str,
        entry_id: str,
    ) -> httpx.Response:
        body = self._parse_body(request)
        if body is None:
            return httpx.Response(422, json={"detail": "malformed body", "errors": ["bad"]})
        if entry_id not in table:
            return httpx.Response(
                404,
                json={"detail": f"{entity} '{entry_id}' not found", "errors": []},
            )
        body_id = body.get("id")
        if body_id != entry_id:
            return httpx.Response(
                409,
                json={
                    "detail": f"id mismatch: path {entry_id!r} vs body {body_id!r}",
                    "errors": [],
                },
            )
        table[entry_id] = body
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


def _install_fake_server(
    tmp_path: Path,
    *,
    pre_seed: list[tuple[str, dict[str, Any]]] | None = None,
) -> FakeServer:
    """Install a FakeServer wired via ``_HTTP_CLIENT_FACTORY_OVERRIDE``.

    Writes a no-auth config to ``tmp_path`` so the callback takes the
    config-file branch and the test seam is honoured.
    """
    config_path = tmp_path / "config.yaml"
    _write_noauth_config(config_path)
    main_module._CONFIG_PATH_OVERRIDE = config_path

    fake = FakeServer()
    for entity, entry in pre_seed or []:
        fake.store[entity][entry["id"]] = entry

    transport = httpx.MockTransport(fake.handler)

    def factory(profile: Any, **_kwargs: Any) -> httpx.Client:
        return httpx.Client(base_url=str(profile.endpoint), transport=transport)

    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory
    return fake


# ---------------------------------------------------------------------------
# Structural tests (AC #11)
# ---------------------------------------------------------------------------


def test_no_direct_httpx_and_no_apiclient_construction() -> None:
    """AC #7 / AC #11 — the module is a thin wire over ``_state.client``."""
    src = Path(catalog_module.__file__).read_text(encoding="utf-8")
    assert "import httpx" not in src
    assert "ApiClient(base_url" not in src


def test_main_py_has_exactly_one_catalog_import_and_register() -> None:
    """AC #12 — ``main.py`` gains exactly one import + one register call."""
    src = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "from akgentic.infra.cli.commands import catalog as catalog_command" in src
    assert "catalog_command.register(app)" in src


@pytest.mark.parametrize("skipped_entity", ["templates", "tools", "agents", "teams"])
def test_registration_removal_eliminates_only_that_entity(skipped_entity: str) -> None:
    """AC #2 / AC #11 — skipping one call eliminates exactly that subgroup."""
    fresh_catalog = typer.Typer(name="catalog")
    for entity in ("templates", "tools", "agents", "teams"):
        if entity == skipped_entity:
            continue
        catalog_module._register_entity_commands(fresh_catalog, entity)

    names = {group.name for group in fresh_catalog.registered_groups}
    expected = {"templates", "tools", "agents", "teams"} - {skipped_entity}
    assert names == expected


def test_all_four_entities_registered_on_main_app() -> None:
    """AC #1 / AC #6 — ``ak catalog`` gains all four entities on the main app."""
    catalog_groups = [g for g in app.registered_groups if g.name == "catalog"]
    assert len(catalog_groups) == 1
    catalog_app = catalog_groups[0].typer_instance
    subnames = {g.name for g in catalog_app.registered_groups}
    assert subnames == {"templates", "tools", "agents", "teams"}


# ---------------------------------------------------------------------------
# list (AC #10: happy + --q forwarding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_list_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("list-1").model_dump()
    fake = _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["catalog", entity, "list"])

    assert result.exit_code == 0, result.stderr
    assert "ID" in result.stdout  # upper-cased table header
    assert "list-1" in result.stdout
    assert any(r.url.path == f"/admin/catalog/{entity}" for r in fake.captured_requests)


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_list_happy_path_json(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("list-json").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["--format", "json", "catalog", entity, "list"])
    assert result.exit_code == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert isinstance(decoded, list)
    assert any(entry["id"] == "list-json" for entry in decoded)


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_list_with_q_forwards_query(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("q-target").model_dump()
    fake = _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["catalog", entity, "list", "--q", "q-target"])
    assert result.exit_code == 0, result.stderr
    # The CLI forwarded ?q=q-target to the server.
    list_requests = [r for r in fake.captured_requests if r.url.path == f"/admin/catalog/{entity}"]
    assert list_requests
    assert list_requests[-1].url.params.get("q") == "q-target"


# ---------------------------------------------------------------------------
# get (happy + yaml render + 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_get_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("get-1").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["catalog", entity, "get", "get-1"])
    assert result.exit_code == 0, result.stderr
    assert "get-1" in result.stdout


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_get_happy_path_yaml(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("get-yaml").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["--format", "yaml", "catalog", entity, "get", "get-yaml"])
    assert result.exit_code == 0, result.stderr
    loaded = yaml.safe_load(result.stdout)
    assert isinstance(loaded, dict)
    assert loaded["id"] == "get-yaml"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_get_unknown_404(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    _install_fake_server(tmp_path)
    result = runner.invoke(app, ["catalog", entity, "get", "nope"])
    assert result.exit_code == 1
    assert "HTTP 404" in result.stderr
    assert "nope" in result.stderr


# ---------------------------------------------------------------------------
# create (file yaml + stdin json + duplicate 409 + malformed 422 + bad ext)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_file_yaml_forwards_content_type(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("create-yaml").model_dump()
    body_path = tmp_path / "body.yaml"
    # Ship JSON-shaped bytes through the YAML path — the fake server reads
    # JSON only; this still proves the outbound Content-Type header.
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", entity, "create", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    assert "create-yaml" in result.stdout
    # Outbound Content-Type header check.
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/yaml"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_file_yml_forwards_yaml_content_type(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("create-yml").model_dump()
    body_path = tmp_path / "body.yml"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", entity, "create", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/yaml"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_file_json(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("create-json").model_dump()
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", entity, "create", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    assert "create-json" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/json"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_stdin_defaults_to_json(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("stdin-json").model_dump()
    result = runner.invoke(app, ["catalog", entity, "create"], input=json.dumps(payload))
    assert result.exit_code == 0, result.stderr
    assert "stdin-json" in result.stdout
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/json"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_stdin_yaml_format_flips_content_type(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    fake = _install_fake_server(tmp_path)
    payload = factory("stdin-yaml").model_dump()
    result = runner.invoke(
        app,
        ["--format", "yaml", "catalog", entity, "create"],
        input=json.dumps(payload),
    )
    assert result.exit_code == 0, result.stderr
    post = [r for r in fake.captured_requests if r.method == "POST"][-1]
    assert post.headers["content-type"] == "application/yaml"


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_create_duplicate_409(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("dup").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", entity, "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "HTTP 409" in result.stderr
    assert "dup" in result.stderr


def test_create_malformed_body_422(runner: CliRunner, tmp_path: Path) -> None:
    """AC #10 — a malformed body → server 422 → CLI exits 1 with detail."""
    _install_fake_server(tmp_path)
    body_path = tmp_path / "bad.json"
    body_path.write_text("not-json-at-all-{{", encoding="utf-8")

    result = runner.invoke(app, ["catalog", "templates", "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "HTTP 422" in result.stderr
    assert "malformed body" in result.stderr


def test_create_unsupported_extension_no_http(runner: CliRunner, tmp_path: Path) -> None:
    """AC #4 — ``.txt`` exits non-zero; no HTTP call is made."""
    fake = _install_fake_server(tmp_path)
    body_path = tmp_path / "body.txt"
    body_path.write_text("ignored", encoding="utf-8")

    result = runner.invoke(app, ["catalog", "templates", "create", "--file", str(body_path)])
    assert result.exit_code == 1
    assert "Unsupported file extension 'txt'" in result.stderr
    assert "use .yaml, .yml, or .json" in result.stderr
    assert fake.captured_requests == []


# ---------------------------------------------------------------------------
# update (file json + unknown 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_update_file_json_happy_path(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("upd").model_dump()
    fake = _install_fake_server(tmp_path, pre_seed=[(entity, payload)])
    body_path = tmp_path / "body.json"
    body_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["catalog", entity, "update", "upd", "--file", str(body_path)])
    assert result.exit_code == 0, result.stderr
    assert "upd" in result.stdout
    put = [r for r in fake.captured_requests if r.method == "PUT"][-1]
    assert put.headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# delete (happy + structured payload + unknown 404)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_delete_happy_path_table(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("del").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["catalog", entity, "delete", "del"])
    assert result.exit_code == 0, result.stderr
    singular = entity.rstrip("s")
    assert f"Deleted {singular} 'del'." in result.stdout


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_delete_json_format_structured_payload(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    payload = factory("del-j").model_dump()
    _install_fake_server(tmp_path, pre_seed=[(entity, payload)])

    result = runner.invoke(app, ["--format", "json", "catalog", entity, "delete", "del-j"])
    assert result.exit_code == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert decoded == {"entity": entity, "id": "del-j", "status": "deleted"}


@pytest.mark.parametrize(("entity", "factory", "_qfield"), ENTITIES)
def test_delete_unknown_404(
    runner: CliRunner,
    tmp_path: Path,
    entity: str,
    factory: EntryFactory,
    _qfield: str,
) -> None:
    _install_fake_server(tmp_path)
    result = runner.invoke(app, ["catalog", entity, "delete", "ghost"])
    assert result.exit_code == 1
    assert "HTTP 404" in result.stderr


# ---------------------------------------------------------------------------
# Auto-auth inheritance (AC #11)
# ---------------------------------------------------------------------------


def test_list_inherits_bearer_auth_from_profile(runner: CliRunner, tmp_path: Path) -> None:
    """AC #11 — a config with auth:, a pre-seeded cache → outbound Bearer."""
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

    result = runner.invoke(app, ["catalog", "templates", "list"])
    assert result.exit_code == 0, result.stderr
    assert captured_headers
    assert captured_headers[-1].get("authorization") == "Bearer test-access-token"
