"""Tests for the admin-catalog router at /admin/catalog/<entity>.

Covers every entity × every verb plus the ADR-022 §D2 error matrix:

* ``404`` — ``EntryNotFoundError`` → via global handler
* ``409`` — ``CatalogValidationError`` → via global handler
* ``422`` — Pydantic validation error → via FastAPI's default handler
* ``201`` on create, ``204`` on delete, ``200`` default
* Structured log line on every mutation; reads stay silent at INFO.

All tests use real ``TierServices`` (YAML-backed catalogs, ``NoAuth``) via the
top-level ``client`` fixture — no mocks. Payloads are constructed as Pydantic
models (no ``dict[str, Any]`` per AC #11 / Golden Rule #1); the one exception
is the 422-malformed-body test, which uses a narrowly typed raw payload so the
missing-field path can actually be exercised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import yaml
from akgentic.catalog.models import (
    AgentEntry,
    TeamEntry,
    TemplateEntry,
    ToolEntry,
)
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

# --- Payload factories (reused across parametrised tests) --------------------


def _template_payload(tid: str = "admin-tpl") -> TemplateEntry:
    """A minimal valid TemplateEntry with placeholders ``name`` and ``role``."""
    return TemplateEntry(id=tid, template="Hello {name}, you are {role}.")


def _tool_payload(tid: str = "admin-tool") -> ToolEntry:
    """A minimal valid ToolEntry."""
    return ToolEntry.model_validate(
        {
            "id": tid,
            "tool_class": "akgentic.tool.search.SearchTool",
            "tool": {"name": "search", "description": "Search the web"},
        }
    )


def _agent_payload(tid: str = "admin-agent") -> AgentEntry:
    """A minimal valid AgentEntry."""
    return AgentEntry.model_validate(
        {
            "id": tid,
            "tool_ids": [],
            "card": {
                "role": "engineer",
                "description": "admin-router test agent",
                "skills": ["coding"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "@Eng", "role": "Engineer"},
                "routes_to": [],
            },
        }
    )


def _team_payload(tid: str = "admin-team") -> TeamEntry:
    """A minimal valid TeamEntry referencing the seeded ``human-proxy``."""
    return TeamEntry.model_validate(
        {
            "id": tid,
            "name": f"Admin Team {tid}",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.core.messages.UserMessage"],
            "members": [{"agent_id": "human-proxy"}],
        }
    )


EntryFactory = Callable[[str], BaseModel]

# Each tuple: (entity_name, payload_factory, query_field_for_search_test)
ENTITIES: list[tuple[str, EntryFactory, str]] = [
    ("templates", _template_payload, "placeholder"),
    ("tools", _tool_payload, "name"),
    ("agents", _agent_payload, "description"),
    ("teams", _team_payload, "name"),
]


def _seed(client: TestClient, entity: str, payload: BaseModel) -> None:
    """POST a payload to the admin router (idempotent: allow 201 or 409)."""
    resp = client.post(f"/admin/catalog/{entity}", json=payload.model_dump())
    assert resp.status_code in (201, 409)


# --- Registration-count & mount --------------------------------------------


def test_all_four_entities_mounted(app: FastAPI) -> None:
    """All four entities register the five expected prefixes each."""
    paths = {r.path for r in app.routes if r.path.startswith("/admin/catalog/")}
    for entity in ("templates", "tools", "agents", "teams"):
        assert f"/admin/catalog/{entity}" in paths
        assert f"/admin/catalog/{entity}/{{entry_id}}" in paths


@pytest.mark.parametrize("skipped_entity", ["templates", "tools", "agents", "teams"])
def test_registration_removal_eliminates_only_that_entity(skipped_entity: str) -> None:
    """Skipping one entity registration eliminates only that entity's routes.

    Parametrised over every entity in turn: build a fresh router + fresh app,
    register the three non-skipped entities via ``_register_entity_routes``,
    mount, and assert the skipped entity's prefix contributes zero routes while
    each registered entity contributes exactly five (AC #9 updated spec).
    """
    from akgentic.catalog.models import (
        AgentQuery,
        TeamQuery,
        TemplateQuery,
        ToolQuery,
    )

    from akgentic.infra.server.routes.admin_catalog import (
        _get_agent_catalog,
        _get_team_catalog,
        _get_template_catalog,
        _get_tool_catalog,
        _register_entity_routes,
    )

    partial_router = APIRouter(prefix="/admin/catalog")
    if skipped_entity != "templates":
        _register_entity_routes(
            partial_router,
            entity_name="templates",
            get_catalog=_get_template_catalog,
            entry_model=TemplateEntry,
            query_from_q=lambda s: TemplateQuery(placeholder=s),
        )
    if skipped_entity != "tools":
        _register_entity_routes(
            partial_router,
            entity_name="tools",
            get_catalog=_get_tool_catalog,
            entry_model=ToolEntry,
            query_from_q=lambda s: ToolQuery(name=s),
        )
    if skipped_entity != "agents":
        _register_entity_routes(
            partial_router,
            entity_name="agents",
            get_catalog=_get_agent_catalog,
            entry_model=AgentEntry,
            query_from_q=lambda s: AgentQuery(description=s),
        )
    if skipped_entity != "teams":
        _register_entity_routes(
            partial_router,
            entity_name="teams",
            get_catalog=_get_team_catalog,
            entry_model=TeamEntry,
            query_from_q=lambda s: TeamQuery(name=s),
        )

    app = FastAPI()
    app.include_router(partial_router)

    skipped_paths = sorted(
        {r.path for r in app.routes if r.path.startswith(f"/admin/catalog/{skipped_entity}")}
    )
    assert skipped_paths == []

    for other in ("templates", "tools", "agents", "teams"):
        if other == skipped_entity:
            continue
        other_routes = [r for r in app.routes if r.path.startswith(f"/admin/catalog/{other}")]
        # Five verbs per entity: list, get, create, update, delete
        assert len(other_routes) == 5, (
            f"expected 5 routes for {other}, got {len(other_routes)}: "
            f"{[r.path for r in other_routes]}"
        )


# --- List (happy + ?q=) ------------------------------------------------------


@pytest.mark.parametrize(("entity", "payload_factory", "query_field"), ENTITIES)
def test_list_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    query_field: str,
) -> None:
    """GET /admin/catalog/{entity} returns a list (may include seeded entries)."""
    _seed(client, entity, payload_factory(f"list-{entity}"))
    resp = client.get(f"/admin/catalog/{entity}")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    ids = [e["id"] for e in body]
    assert f"list-{entity}" in ids


@pytest.mark.parametrize(("entity", "payload_factory", "query_field"), ENTITIES)
def test_list_with_q_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    query_field: str,
) -> None:
    """GET /admin/catalog/{entity}?q=... returns filtered results via search.

    Per-entity query semantics (from admin_catalog.py docstring):

    * ``templates`` → ``TemplateQuery(placeholder=s)``: exact-match on a
      placeholder NAME present in the template. Our template is
      ``"Hello {name}, you are {role}."`` so ``q="name"`` matches; anything
      else (e.g., ``"Hello"``) would not.
    * ``tools``/``teams`` → ``ToolQuery(name=s)``/``TeamQuery(name=s)``:
      substring match on ``name``.
    * ``agents`` → ``AgentQuery(description=s)``: substring match on
      ``description``.
    """
    _seed(client, entity, payload_factory(f"q-{entity}"))
    q_value = {
        "templates": "name",
        "tools": "search",
        "agents": "admin-router",
        "teams": f"Admin Team q-{entity}",
    }[entity]
    resp = client.get(f"/admin/catalog/{entity}", params={"q": q_value})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    ids = [e["id"] for e in body]
    assert f"q-{entity}" in ids


# --- Get ---------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "payload_factory", "query_field"), ENTITIES)
def test_get_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    query_field: str,
) -> None:
    """GET /admin/catalog/{entity}/{id} returns the entry body."""
    _seed(client, entity, payload_factory(f"get-{entity}"))
    resp = client.get(f"/admin/catalog/{entity}/get-{entity}")
    assert resp.status_code == 200
    assert resp.json()["id"] == f"get-{entity}"


@pytest.mark.parametrize(("entity", "_factory", "_qfield"), ENTITIES)
def test_get_unknown_returns_404_with_error_shape(
    client: TestClient, entity: str, _factory: EntryFactory, _qfield: str
) -> None:
    """Unknown id → 404 via the global handler, with ErrorResponse shape."""
    resp = client.get(f"/admin/catalog/{entity}/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    # Global handler: {"detail": "...", "errors": [...]}
    assert set(body.keys()) == {"detail", "errors"}
    assert "not found" in body["detail"].lower()


# --- Create ------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_create_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """POST returns 201, echoes the entry, and subsequent GET retrieves it."""
    payload = payload_factory(f"create-{entity}")
    resp = client.post(f"/admin/catalog/{entity}", json=payload.model_dump())
    assert resp.status_code == 201
    assert resp.json()["id"] == f"create-{entity}"

    resp = client.get(f"/admin/catalog/{entity}/create-{entity}")
    assert resp.status_code == 200
    assert resp.json()["id"] == f"create-{entity}"


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_create_duplicate_returns_409(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """Creating an entry with an already-used id → 409."""
    payload = payload_factory(f"dup-{entity}")
    first = client.post(f"/admin/catalog/{entity}", json=payload.model_dump())
    assert first.status_code == 201
    second = client.post(f"/admin/catalog/{entity}", json=payload.model_dump())
    assert second.status_code == 409
    body = second.json()
    assert set(body.keys()) == {"detail", "errors"}
    assert f"dup-{entity}" in body["detail"]


def test_create_malformed_body_returns_422(client: TestClient) -> None:
    """Posting a TeamEntry missing the required ``entry_point`` → 422.

    This test uses a raw payload (narrow ``str | list[str]`` union) by design:
    the whole point is to bypass the Pydantic model so the missing-field path
    through FastAPI's default 422 handler is exercised.
    """
    bad_payload: dict[str, str | list[str]] = {
        "id": "malformed",
        "name": "Malformed",
        # entry_point missing
        "message_types": ["akgentic.core.messages.UserMessage"],
    }
    resp = client.post("/admin/catalog/teams", json=bad_payload)
    assert resp.status_code == 422


# --- Update ------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_update_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """PUT replaces the entry; subsequent GET reflects the change."""
    eid = f"upd-{entity}"
    _seed(client, entity, payload_factory(eid))

    updated = payload_factory(eid).model_dump()
    # Tweak a descriptive field per entity so we can observe the change
    if entity == "templates":
        updated["template"] = "Updated {name}."
    elif entity == "tools":
        updated["tool"]["description"] = "updated-description"
    elif entity == "agents":
        updated["card"]["description"] = "updated-description"
    elif entity == "teams":
        updated["description"] = "updated-description"

    resp = client.put(f"/admin/catalog/{entity}/{eid}", json=updated)
    assert resp.status_code == 200
    assert resp.json()["id"] == eid

    got = client.get(f"/admin/catalog/{entity}/{eid}").json()
    if entity == "templates":
        assert got["template"].startswith("Updated")
    elif entity == "tools":
        assert got["tool"]["description"] == "updated-description"
    elif entity == "agents":
        assert got["card"]["description"] == "updated-description"
    elif entity == "teams":
        assert got["description"] == "updated-description"


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_update_unknown_returns_404(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """PUT on an unknown id → 404 via the global handler."""
    resp = client.put(
        f"/admin/catalog/{entity}/unknown-id",
        json=payload_factory("unknown-id").model_dump(),
    )
    assert resp.status_code == 404


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_update_id_mismatch_returns_409(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """PUT with a path id that differs from the body id → 409."""
    eid = f"mis-{entity}"
    _seed(client, entity, payload_factory(eid))
    # Body claims a different id than the URL path
    mismatched = payload_factory("different-id")
    resp = client.put(f"/admin/catalog/{entity}/{eid}", json=mismatched.model_dump())
    assert resp.status_code == 409


# --- Delete ------------------------------------------------------------------


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_delete_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """DELETE returns 204; subsequent GET returns 404."""
    eid = f"del-{entity}"
    _seed(client, entity, payload_factory(eid))
    resp = client.delete(f"/admin/catalog/{entity}/{eid}")
    assert resp.status_code == 204

    resp = client.get(f"/admin/catalog/{entity}/{eid}")
    assert resp.status_code == 404


@pytest.mark.parametrize(("entity", "_factory", "_qfield"), ENTITIES)
def test_delete_unknown_returns_404(
    client: TestClient, entity: str, _factory: EntryFactory, _qfield: str
) -> None:
    """DELETE of an unknown id → 404 via the global handler."""
    resp = client.delete(f"/admin/catalog/{entity}/does-not-exist")
    assert resp.status_code == 404


# --- Structured mutation logging --------------------------------------------


def test_mutation_logging_emits_exactly_three_info_records(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Create/Update/Delete → exactly three INFO records with structured extras."""
    caplog.set_level(logging.INFO, logger="akgentic.infra.server.routes.admin_catalog")

    payload = _team_payload("log-team")
    body = payload.model_dump()

    resp = client.post("/admin/catalog/teams", json=body)
    assert resp.status_code == 201
    resp = client.put("/admin/catalog/teams/log-team", json=body)
    assert resp.status_code == 200
    resp = client.delete("/admin/catalog/teams/log-team")
    assert resp.status_code == 204

    info_records = [
        r
        for r in caplog.records
        if r.name == "akgentic.infra.server.routes.admin_catalog" and r.levelname == "INFO"
    ]
    assert len(info_records) == 3
    ops = [getattr(r, "operation") for r in info_records]
    assert ops == ["create", "update", "delete"]
    for r in info_records:
        assert getattr(r, "entity_type") == "teams"
        assert getattr(r, "entity_id") == "log-team"
        assert getattr(r, "principal_id") == "anonymous"


def test_reads_do_not_emit_info_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """GET list + GET by id emit zero INFO records from the admin_catalog logger."""
    caplog.set_level(logging.INFO, logger="akgentic.infra.server.routes.admin_catalog")

    # Seed one entry so the GET-by-id path hits 200, not 404
    _seed(client, "teams", _team_payload("silent-team"))
    caplog.clear()

    resp = client.get("/admin/catalog/teams")
    assert resp.status_code == 200
    resp = client.get("/admin/catalog/teams/silent-team")
    assert resp.status_code == 200

    info_records = [
        r
        for r in caplog.records
        if r.name == "akgentic.infra.server.routes.admin_catalog" and r.levelname == "INFO"
    ]
    assert info_records == []


# --- Auth boundary (AC #7b) -------------------------------------------------


def test_admin_catalog_module_does_not_import_auth_protocol() -> None:
    """Module source does NOT reference ``akgentic.infra.protocols.auth``.

    Textual check (AC #9 updated spec) — covers both ``from X import ...`` and
    plain ``import X`` with a single substring match, avoiding the blind spot
    of an ``ast.ImportFrom``-only walk.
    """
    import akgentic.infra.server.routes.admin_catalog as m

    src = Path(m.__file__).read_text()
    assert "akgentic.infra.protocols.auth" not in src


# --- YAML request-body support (ADR-022 §D4) --------------------------------


def _post_yaml_resp(
    client: TestClient, entity: str, body_dict: dict[str, object]
) -> httpx.Response:
    return client.post(
        f"/admin/catalog/{entity}",
        content=yaml.safe_dump(body_dict).encode(),
        headers={"Content-Type": "application/yaml"},
    )


def _put_yaml_resp(
    client: TestClient, entity: str, entry_id: str, body_dict: dict[str, object]
) -> httpx.Response:
    return client.put(
        f"/admin/catalog/{entity}/{entry_id}",
        content=yaml.safe_dump(body_dict).encode(),
        headers={"Content-Type": "application/yaml"},
    )


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_yaml_post_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """POST application/yaml → 201; subsequent GET returns the posted entry."""
    payload = payload_factory(f"yaml-post-{entity}").model_dump()
    resp = _post_yaml_resp(client, entity, payload)
    assert resp.status_code == 201, resp.text
    got = client.get(f"/admin/catalog/{entity}/yaml-post-{entity}")
    assert got.status_code == 200
    # Round-trip: posted dict equals the stored entry (operator-owned fields)
    assert got.json() == payload


@pytest.mark.parametrize(("entity", "payload_factory", "_qfield"), ENTITIES)
def test_yaml_put_happy_path(
    client: TestClient,
    entity: str,
    payload_factory: EntryFactory,
    _qfield: str,
) -> None:
    """PUT application/yaml → 200; GET reflects the mutated field."""
    eid = f"yaml-put-{entity}"
    _seed(client, entity, payload_factory(eid))
    updated = payload_factory(eid).model_dump()
    if entity == "templates":
        updated["template"] = "Updated {name} via YAML."
    elif entity == "tools":
        updated["tool"]["description"] = "updated-via-yaml"
    elif entity == "agents":
        updated["card"]["description"] = "updated-via-yaml"
    elif entity == "teams":
        updated["description"] = "updated-via-yaml"

    resp = _put_yaml_resp(client, entity, eid, updated)
    assert resp.status_code == 200, resp.text

    got = client.get(f"/admin/catalog/{entity}/{eid}").json()
    if entity == "templates":
        assert "YAML" in got["template"]
    elif entity == "tools":
        assert got["tool"]["description"] == "updated-via-yaml"
    elif entity == "agents":
        assert got["card"]["description"] == "updated-via-yaml"
    elif entity == "teams":
        assert got["description"] == "updated-via-yaml"


def test_yaml_post_malformed_returns_422(client: TestClient) -> None:
    """Malformed YAML body on POST → 422 with ``invalid YAML body:`` prefix."""
    resp = client.post(
        "/admin/catalog/templates",
        content=b"{unclosed: [\n",
        headers={"Content-Type": "application/yaml"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"].startswith("invalid YAML body: ")


def test_yaml_put_malformed_returns_422(client: TestClient) -> None:
    """Malformed YAML body on PUT → 422 with ``invalid YAML body:`` prefix."""
    _seed(client, "templates", _template_payload("put-malformed"))
    resp = client.put(
        "/admin/catalog/templates/put-malformed",
        content=b"{unclosed: [\n",
        headers={"Content-Type": "application/yaml"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"].startswith("invalid YAML body: ")


def test_post_unsupported_content_type_returns_415(client: TestClient) -> None:
    """POST with ``text/plain`` → 415 with ``unsupported Content-Type:`` prefix."""
    resp = client.post(
        "/admin/catalog/templates",
        content=b"plain body",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    body = resp.json()
    assert body["detail"].startswith("unsupported Content-Type: ")


def test_yaml_post_with_charset_parameter_accepted(client: TestClient) -> None:
    """``application/yaml; charset=utf-8`` is treated as ``application/yaml``."""
    payload = _template_payload("yaml-charset").model_dump()
    resp = client.post(
        "/admin/catalog/templates",
        content=yaml.safe_dump(payload).encode(),
        headers={"Content-Type": "application/yaml; charset=utf-8"},
    )
    assert resp.status_code == 201, resp.text


def test_yaml_x_yaml_alias_accepted(client: TestClient) -> None:
    """``application/x-yaml`` alias is accepted on POST."""
    payload = _template_payload("yaml-alias").model_dump()
    resp = client.post(
        "/admin/catalog/templates",
        content=yaml.safe_dump(payload).encode(),
        headers={"Content-Type": "application/x-yaml"},
    )
    assert resp.status_code == 201, resp.text
