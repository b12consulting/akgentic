"""Route-level authorization tests for the import owner-or-admin gate (ADR-028 §Decision 8).

``POST /admin/catalog/namespace/import`` is the one mutating catalog route whose
target namespace is body-carried, so the Story 31.1 path/query gate cannot see
it. Story 31.3 closes that hole with ``require_import_owner_or_admin`` — a
body-reading dependency applying the same owner-or-admin predicate, distinguishing
create-new (allow) from overwrite-existing (gate).

These tests assert HTTP authorization *behaviour* (200/201 vs 403, who-can-do-
what, body-read safety) via the FastAPI ``TestClient`` against the live
``/admin/catalog/*`` mount. The ADR-023 identity seam (``get_request_user``) is
overridden per-test through ``app.dependency_overrides`` to play different
callers. They never assert on docstring/ADR string content (CLAUDE.md Golden
Rule #8).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import yaml
from akgentic.catalog.models.entry import Entry
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.routes._catalog_authz import require_import_owner_or_admin

# --- Fixtures ---------------------------------------------------------------

_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"
_PROMPT_TYPE = "akgentic.llm.PromptTemplate"


def _seed_meta_namespace(app: FastAPI, namespace: str, user_id: str) -> None:
    """Create a ``kind="meta"`` anchor entry owning ``namespace`` for ``user_id``.

    A meta entry is the cheapest anchor that ``_resolve_namespace_owner`` reads
    (team→meta fallback). Created straight through the wired catalog instance so
    the gate's ``app.state.services.catalog`` lookup sees it.
    """
    catalog = app.state.services.catalog
    catalog.create(
        Entry(
            id="_meta",
            kind="meta",
            namespace=namespace,
            user_id=user_id,
            model_type=_META_TYPE,
            description="seed meta anchor",
            payload={"name": namespace, "description": "seed"},
        )
    )


def _seed_prompt(app: FastAPI, namespace: str, prompt_id: str, user_id: str) -> None:
    """Create a ``kind="prompt"`` entry owned by ``user_id`` (so export has content)."""
    catalog = app.state.services.catalog
    catalog.create(
        Entry(
            id=prompt_id,
            kind="prompt",
            namespace=namespace,
            user_id=user_id,
            model_type=_PROMPT_TYPE,
            description="seed prompt",
            payload={"template": "hello", "params": {}},
        )
    )


def _override_user(app: FastAPI, user_id: str, roles: list[str]) -> None:
    """Override the ADR-023 identity seam to play ``user_id`` with ``roles``."""
    app.dependency_overrides[get_request_user] = lambda: RequestUser(user_id=user_id, roles=roles)


def _export_bundle(client: TestClient, namespace: str) -> str:
    """Export ``namespace`` to a bundle YAML string via the live export route."""
    resp = client.get(f"/admin/catalog/namespace/{namespace}/export")
    assert resp.status_code == 200, resp.text
    return resp.text


def _rewrite_bundle_namespace(bundle: str, new_namespace: str, new_user_id: str) -> str:
    """Return ``bundle`` with its root ``namespace`` (and ``user_id``) rewritten.

    Mirrors the clone flow: export → rewrite the root ``namespace`` (and the
    per-bundle ``user_id`` so the uniform-owner invariant in ``dump_namespace``
    is honoured by ``import_namespace_yaml``) → re-import into a new namespace.
    """
    doc = yaml.safe_load(bundle)
    assert isinstance(doc, dict)
    doc["namespace"] = new_namespace
    doc["user_id"] = new_user_id
    return yaml.safe_dump(doc, sort_keys=False)


def _minimal_bundle(namespace: str, user_id: str) -> str:
    """Hand-crafted minimal valid bundle for the brand-new-ns / body-safety cases.

    Carries a ``meta`` anchor (required by ``import_namespace_yaml`` — a bundle
    needs at least one team or meta entry) plus a prompt to prove the handler
    persisted body content.
    """
    doc = {
        "namespace": namespace,
        "user_id": user_id,
        "entries": {
            "_meta": {
                "kind": "meta",
                "model_type": _META_TYPE,
                "description": "fresh meta",
                "payload": {"name": namespace, "description": "fresh"},
            },
            "p1": {
                "kind": "prompt",
                "model_type": _PROMPT_TYPE,
                "description": "fresh prompt",
                "payload": {"template": "hi", "params": {}},
            },
        },
    }
    return yaml.safe_dump(doc, sort_keys=False)


def _post_import(client: TestClient, body: str) -> object:
    """POST a bundle to the import route with the YAML content type."""
    return client.post(
        "/admin/catalog/namespace/import",
        content=body,
        headers={"content-type": "application/yaml"},
    )


@pytest.fixture()
def import_client(client: TestClient) -> Iterator[TestClient]:
    """Client with alice/bob namespaces seeded (meta + prompt anchors)."""
    app = client.app
    _seed_meta_namespace(app, "alice-ns", "alice")
    _seed_prompt(app, "alice-ns", "p1", "alice")
    _seed_meta_namespace(app, "bob-ns", "bob")
    _seed_prompt(app, "bob-ns", "p1", "bob")
    yield client
    app.dependency_overrides.pop(get_request_user, None)


# --- AC #10: owner Save (overwrite-own) -------------------------------------


def test_owner_can_import_over_own_namespace(import_client: TestClient) -> None:
    """AC #10: alice re-importing alice-ns (overwrite-own) → allowed (201)."""
    app = import_client.app
    _override_user(app, "alice", [])
    bundle = _export_bundle(import_client, "alice-ns")
    resp = _post_import(import_client, bundle)
    assert resp.status_code == 201, resp.text


# --- AC #11: non-owner overwrite forbidden ----------------------------------


def test_non_owner_import_over_existing_namespace_forbidden(import_client: TestClient) -> None:
    """AC #11: alice importing over bob-owned bob-ns → 403 with the gate's detail."""
    app = import_client.app
    # Export bob-ns as bob so we get a valid bundle, then attempt as alice.
    _override_user(app, "bob", [])
    bundle = _export_bundle(import_client, "bob-ns")
    _override_user(app, "alice", [])
    resp = _post_import(import_client, bundle)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


# --- AC #12: admin overwrite allowed ----------------------------------------


def test_admin_can_import_over_any_namespace(import_client: TestClient) -> None:
    """AC #12: carol (admin) importing over bob-ns → allowed (gate returns before lookup)."""
    app = import_client.app
    _override_user(app, "bob", [])
    bundle = _export_bundle(import_client, "bob-ns")
    _override_user(app, "carol", ["admin"])
    resp = _post_import(import_client, bundle)
    # Admin branch returns before any owner lookup → never 403.
    assert resp.status_code != 403
    assert resp.status_code == 201, resp.text


# --- AC #13: first save of a brand-new namespace by a non-admin --------------


def test_non_admin_can_create_brand_new_namespace(import_client: TestClient) -> None:
    """AC #13: alice importing a brand-new namespace → allowed (create; owner None)."""
    app = import_client.app
    _override_user(app, "alice", [])
    bundle = _minimal_bundle("brand-new-ns", "alice")
    resp = _post_import(import_client, bundle)
    assert resp.status_code != 403
    assert resp.status_code == 201, resp.text


# --- AC #14: community anonymous --------------------------------------------


def test_community_anonymous_import_allowed(client: TestClient) -> None:
    """AC #14: anonymous (no override) importing an anonymous-owned/new ns → allowed.

    The community default ``RequestUser(user_id="anonymous", roles=[])`` either
    owns the namespace (anonymous-owned) or creates it (owner None) — both allow.
    No 403; community behaviour byte-unchanged.
    """
    # No dependency override → community default identity (anonymous).
    bundle = _minimal_bundle("anon-import-ns", "anonymous")
    resp = _post_import(client, bundle)
    assert resp.status_code != 403
    assert resp.status_code == 201, resp.text


# --- AC #8: body-read safety (no double-consume / no starvation) -------------


def test_gate_does_not_starve_handler_body(import_client: TestClient) -> None:
    """AC #8: one authorized import passes the gate AND the handler reads the body.

    The dependency's ``await request.body()`` does not starve the handler —
    Starlette caches the body, so ``import_namespace`` still parses it and
    persists the entries. Proven by the entries existing in the catalog after a
    single request (the gate authorized AND the handler ran on the same request).
    """
    app = import_client.app
    _override_user(app, "alice", [])
    bundle = _minimal_bundle("body-safety-ns", "alice")
    resp = _post_import(import_client, bundle)
    assert resp.status_code == 201, resp.text
    # The handler ran on the same request: its entries are now persisted.
    catalog = app.state.services.catalog
    entry = catalog.get("body-safety-ns", "p1")
    assert entry.namespace == "body-safety-ns"
    assert entry.kind == "prompt"


# --- AC #9: malformed body fails open to the handler ------------------------


def test_non_yaml_body_not_500_not_403_defers_to_handler(import_client: TestClient) -> None:
    """AC #9: a non-parseable body is neither 500'd nor 403'd by the gate.

    The gate cannot extract a namespace, so it allows; the catalog handler then
    returns its documented 4xx for a bad bundle. The invariant this test
    protects: the gate produces neither 500 nor 403.
    """
    app = import_client.app
    _override_user(app, "alice", [])
    resp = _post_import(import_client, "this: is: not: valid: yaml: : :\n\t- broken")
    assert resp.status_code != 500
    assert resp.status_code != 403
    assert 400 <= resp.status_code < 500


def test_namespaceless_body_not_403_defers_to_handler(import_client: TestClient) -> None:
    """AC #9: a parseable body with no root ``namespace`` is treated as create (allow).

    No extractable namespace ⇒ no existing owner ⇒ create ⇒ allow. The gate does
    not 403; the catalog handler applies its own validation.
    """
    app = import_client.app
    _override_user(app, "alice", [])
    body = "header:\n  bundle_version: 1\nentries: []\n"
    resp = _post_import(import_client, body)
    assert resp.status_code != 403
    assert resp.status_code != 500


def test_non_utf8_body_not_500_not_403(import_client: TestClient) -> None:
    """AC #9: a non-UTF-8 body is neither 500'd nor 403'd by the gate."""
    app = import_client.app
    _override_user(app, "alice", [])
    resp = import_client.post(
        "/admin/catalog/namespace/import",
        content=b"\xff\xfe\x00bad-bytes",
        headers={"content-type": "application/yaml"},
    )
    assert resp.status_code != 500
    assert resp.status_code != 403


# --- AC #15: clone flow end-to-end (export → rewrite → re-import) ------------


def test_clone_flow_creates_namespace_owned_by_caller(import_client: TestClient) -> None:
    """AC #15: authenticated caller clones a seeded ns into a new ns they own.

    Reproduces the clone = export → rewrite root namespace → re-import flow as an
    authenticated caller (``gpiroux``). The create is authorized (new namespace,
    owner None), and — given the pinned stamp-on-write catalog (Story 27.2) with
    Story 31.2's ``Catalog.as_caller`` scope active — the persisted entries are
    owned by ``gpiroux``, not ``anonymous`` (the reported clone-owned-by-anonymous
    bug, proven closed). The authorize/create leg is asserted regardless of the
    catalog pin; the persisted-owner leg is valid for the current pin.
    """
    app = import_client.app
    # Seed a source namespace owned by anonymous (community-style), then clone
    # it as gpiroux into a fresh namespace.
    _seed_meta_namespace(app, "src-anon-ns", "anonymous")
    _seed_prompt(app, "src-anon-ns", "p1", "anonymous")

    _override_user(app, "gpiroux", [])
    bundle = _export_bundle(import_client, "src-anon-ns")
    rewritten = _rewrite_bundle_namespace(bundle, "cloned-ns", "gpiroux")
    resp = _post_import(import_client, rewritten)
    assert resp.status_code == 201, resp.text

    # Persisted-owner leg: stamp-on-write (Story 27.2) under as_caller (31.2)
    # records gpiroux as the owner of the cloned entries — not anonymous.
    catalog = app.state.services.catalog
    cloned = catalog.get("cloned-ns", "p1")
    assert cloned.user_id == "gpiroux"


# --- AC #16: import gate attached to exactly one route ----------------------


def _find_route(app: FastAPI, method: str, app_path: str) -> APIRoute | None:
    """Return the ``APIRoute`` matching ``method`` + full app path, or None."""
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == app_path and method in route.methods:
            return route
    return None


def test_import_route_carries_import_gate(client: TestClient) -> None:
    """AC #6/#16: the import route carries ``require_import_owner_or_admin``.

    Asserted at ``route.dependencies`` level so the gate is part of the route
    *definition* and travels with the ``APIRoute`` when enterprise transplants
    it (tier verification is the enterprise epic's job, not this story).
    """
    route = _find_route(client.app, "POST", "/admin/catalog/namespace/import")
    assert route is not None
    gate_calls = [d.dependency for d in route.dependencies]
    assert require_import_owner_or_admin in gate_calls


def test_import_gate_attached_to_no_other_route(client: TestClient) -> None:
    """AC #5/#16: ``require_import_owner_or_admin`` is on the import route ONLY."""
    carrying: list[str] = []
    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if any(d.dependency is require_import_owner_or_admin for d in route.dependencies):
            carrying.append(f"{sorted(route.methods)} {route.path}")
    assert carrying == ["['POST'] /admin/catalog/namespace/import"]


# --- AC #16: other body-carried creates remain ungated by the import gate ----


def test_create_entry_not_carrying_import_gate(client: TestClient) -> None:
    """AC #16: POST /{kind} does not carry the import gate (stays ungated)."""
    route = _find_route(client.app, "POST", "/admin/catalog/{kind}")
    assert route is not None
    assert all(d.dependency is not require_import_owner_or_admin for d in route.dependencies)


def test_clone_route_not_carrying_import_gate(client: TestClient) -> None:
    """AC #16: POST /clone does not carry the import gate (stays ungated)."""
    route = _find_route(client.app, "POST", "/admin/catalog/clone")
    assert route is not None
    assert all(d.dependency is not require_import_owner_or_admin for d in route.dependencies)
