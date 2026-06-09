"""Route-level authorization tests for the owner-or-admin catalog gate (ADR-028).

These tests assert HTTP authorization *behaviour* (200/204 vs 403, who-can-do-
what) via the FastAPI ``TestClient`` against the live ``/admin/catalog/*``
mount. The ADR-023 identity seam (``get_request_user``) is overridden per-test
through ``app.dependency_overrides`` to play different callers. They never
assert on docstring/ADR string content (CLAUDE.md Golden Rule #8).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from akgentic.catalog.models.entry import Entry
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.routes._catalog_authz import require_namespace_owner_or_admin

# --- Fixtures ---------------------------------------------------------------

_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


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
    """Create a deletable ``kind="prompt"`` entry owned by ``user_id``.

    Sub-entries must match the namespace anchor's ``user_id`` (catalog
    ownership rule), so callers seed the prompt with the same owner.
    """
    catalog = app.state.services.catalog
    catalog.create(
        Entry(
            id=prompt_id,
            kind="prompt",
            namespace=namespace,
            user_id=user_id,
            model_type="akgentic.llm.PromptTemplate",
            description="seed prompt",
            payload={"template": "hello", "params": {}},
        )
    )


@pytest.fixture()
def gated_client(client: TestClient) -> Iterator[TestClient]:
    """Client with alice/bob/ownerless namespaces seeded for gate tests.

    * ``alice-ns`` — meta anchor owned by ``alice``, plus a prompt to mutate.
    * ``bob-ns`` — meta anchor owned by ``bob``, plus a prompt to mutate.
    * ``ownerless-ns`` — no team and no meta entry (owner unresolvable).
    """
    app = client.app
    _seed_meta_namespace(app, "alice-ns", "alice")
    _seed_prompt(app, "alice-ns", "p1", "alice")
    _seed_meta_namespace(app, "bob-ns", "bob")
    _seed_prompt(app, "bob-ns", "p1", "bob")
    yield client
    app.dependency_overrides.pop(get_request_user, None)


def _override_user(app: FastAPI, user_id: str, roles: list[str]) -> None:
    """Override the ADR-023 identity seam to play ``user_id`` with ``roles``."""
    app.dependency_overrides[get_request_user] = lambda: RequestUser(user_id=user_id, roles=roles)


def _has_namespace_delete_route(app: FastAPI) -> bool:
    """Whether the pinned catalog exposes ``DELETE /admin/catalog/namespace/{namespace}``.

    The namespace-delete route is added by akgentic-catalog Story 27.1. It is
    present in the catalog pinned in this workspace but absent from older pins
    (e.g. a CI dependency wheelhouse built before the root-repo
    ``akgentic-catalog`` submodule pointer is bumped). Tests that exercise that
    route gate on this so they pass in both situations without editing the
    catalog submodule (Golden Rule #4); the route's gate-attachment is asserted
    forward-compatibly by ``test_gated_route_carries_dependency``.
    """
    return _find_route(app, "DELETE", "/admin/catalog/namespace/{namespace}") is not None


def _put_prompt_body(namespace: str, prompt_id: str, user_id: str) -> dict[str, object]:
    """A valid ``Entry`` body for ``PUT /{kind}/{id}`` matching the seeded prompt."""
    return {
        "id": prompt_id,
        "kind": "prompt",
        "namespace": namespace,
        "user_id": user_id,
        "model_type": "akgentic.llm.PromptTemplate",
        "description": "updated",
        "payload": {"template": "updated", "params": {}},
    }


# --- AC #8: owner allowed ---------------------------------------------------


def test_owner_can_put_entry(gated_client: TestClient) -> None:
    """AC #8: alice (owner of alice-ns) PUTs an entry → 200."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.put(
        "/admin/catalog/prompt/p1",
        params={"namespace": "alice-ns"},
        json=_put_prompt_body("alice-ns", "p1", "alice"),
    )
    assert resp.status_code == 200


def test_owner_can_delete_entry(gated_client: TestClient) -> None:
    """AC #8: alice (owner) DELETEs an entry → 204."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.delete("/admin/catalog/prompt/p1", params={"namespace": "alice-ns"})
    assert resp.status_code == 204


def test_owner_can_put_namespace_meta(gated_client: TestClient) -> None:
    """AC #8: alice (owner) PUTs namespace meta → 200 (update path)."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.put(
        "/admin/catalog/namespace/alice-ns/meta",
        json={"name": "Alice NS", "description": "renamed"},
    )
    assert resp.status_code == 200


# --- AC #9: non-owner forbidden --------------------------------------------


def test_non_owner_put_entry_forbidden(gated_client: TestClient) -> None:
    """AC #9: alice mutating bob-ns → 403 with the gate's detail."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.put(
        "/admin/catalog/prompt/p1",
        params={"namespace": "bob-ns"},
        json=_put_prompt_body("bob-ns", "p1", "bob"),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


def test_non_owner_delete_entry_forbidden(gated_client: TestClient) -> None:
    """AC #9: alice deleting in bob-ns → 403."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.delete("/admin/catalog/prompt/p1", params={"namespace": "bob-ns"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


def test_non_owner_put_meta_forbidden(gated_client: TestClient) -> None:
    """AC #9: alice editing bob-ns meta → 403 (namespace from path)."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.put(
        "/admin/catalog/namespace/bob-ns/meta",
        json={"name": "hijack", "description": "nope"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


# --- AC #10: admin bypass ---------------------------------------------------


def test_admin_bypasses_ownership_gate(gated_client: TestClient) -> None:
    """AC #10: carol (admin, not owner) is not blocked by the owner-or-admin *gate*.

    The gate's admin branch returns before any owner lookup, so carol is never
    ``403``'d — that is the invariant this test protects, and it holds in every
    catalog pin.

    The exact non-403 outcome depends on the pinned catalog's stamp-on-write
    half (akgentic-catalog Story 27.2): with Story 31.2's per-request
    ``Catalog.as_caller`` scope active, a catalog that stamps records carol as
    the entry owner on write (ADR-028 §Decision 7) and then rejects the update
    because carol's stamped ``user_id`` mismatches bob-ns's anchor owner (bob) —
    surfaced as ``409``. A catalog pin without Story 27.2 does not stamp, so the
    update succeeds (``200``). Either way the *gate* did not block carol.

    A clean admin mutation that succeeds end-to-end (DELETE, which does not
    stamp) is covered by ``test_admin_can_delete_any_namespace`` and
    ``test_admin_bypasses_on_unresolvable_owner``.
    """
    app = gated_client.app
    _override_user(app, "carol", ["admin"])
    resp = gated_client.put(
        "/admin/catalog/prompt/p1",
        params={"namespace": "bob-ns"},
        json=_put_prompt_body("bob-ns", "p1", "bob"),
    )
    # Gate passed (not 403). 409 = catalog's post-stamp ownership check (pin
    # includes Story 27.2); 200 = catalog without stamp-on-write (older pin).
    assert resp.status_code != 403
    assert resp.status_code in (200, 409)


def test_admin_bypasses_on_unresolvable_owner(gated_client: TestClient) -> None:
    """AC #10/#12: admin bypasses before owner resolution even when unresolvable."""
    app = gated_client.app
    _override_user(app, "carol", ["admin"])
    # ownerless-ns has no anchor; admin still bypasses (returns before lookup).
    # A 404 (entry not found) proves the gate passed and the handler ran.
    resp = gated_client.delete(
        "/admin/catalog/prompt/does-not-exist", params={"namespace": "ownerless-ns"}
    )
    assert resp.status_code == 404


# --- AC #11: community anonymous --------------------------------------------


def test_community_anonymous_owns_seeded_namespace(client: TestClient) -> None:
    """AC #11: with no identity override, anonymous mutates an anonymous-owned ns.

    The community default ``RequestUser(user_id="anonymous", roles=[])`` owns
    the conftest-seeded ``test-team`` namespace (its team entry's ``user_id``
    defaults to ``anonymous``), so the owner check passes — community behaviour
    is byte-unchanged, no 403.
    """
    app = client.app
    _seed_prompt(app, "test-team", "p-anon", "anonymous")
    # No dependency override → community default identity.
    resp = client.delete("/admin/catalog/prompt/p-anon", params={"namespace": "test-team"})
    assert resp.status_code == 204


# --- AC #12: owner unresolvable --------------------------------------------


def test_unresolvable_owner_non_admin_forbidden(gated_client: TestClient) -> None:
    """AC #12: non-admin against an anchor-less namespace → 403 (fail closed)."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.delete(
        "/admin/catalog/prompt/whatever", params={"namespace": "ownerless-ns"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


# --- AC #13: reads ungated --------------------------------------------------


def test_non_owner_get_entry_not_gated(gated_client: TestClient) -> None:
    """AC #13: a non-owner GET is never ``403``'d by the owner-or-admin *gate*.

    The gate only attaches to the modify + delete routes, so a read is never
    gated. Since Story 31.2 turned on the per-request ``Catalog.as_caller``
    scope, the catalog's visibility filter (ADR-009 §D2) is now active: alice
    reading a private entry she does not own in bob-ns sees a ``404`` (the
    filter hides it), not the previously-unfiltered ``200``. The invariant this
    test protects is that the *gate* does not produce the rejection — the
    ``404`` is visibility, not authorization.
    """
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.get("/admin/catalog/prompt/p1", params={"namespace": "bob-ns"})
    assert resp.status_code != 403
    # Visibility filter (now active under as_caller) hides bob's private entry.
    assert resp.status_code == 404


def test_non_owner_list_namespaces_not_gated(gated_client: TestClient) -> None:
    """AC #13: GET /namespaces is never 403'd by the mutation gate."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.get("/admin/catalog/namespaces")
    assert resp.status_code == 200


def test_non_owner_export_not_gated(gated_client: TestClient) -> None:
    """AC #13: GET /namespace/{ns}/export is never 403'd by the mutation gate."""
    app = gated_client.app
    _override_user(app, "alice", [])
    resp = gated_client.get("/admin/catalog/namespace/bob-ns/export")
    assert resp.status_code != 403


# --- AC #14: creates ungated ------------------------------------------------


def test_create_entry_not_gated(gated_client: TestClient) -> None:
    """AC #14: POST /{kind} with a body-only namespace is not 403'd, no 422.

    A non-owner alice creating a brand-new entry in a brand-new namespace
    succeeds — creates carry the namespace in the body and acquire no
    ``namespace`` path/query requirement from the gate.
    """
    app = gated_client.app
    _override_user(app, "alice", [])
    body = {
        "id": "fresh-team",
        "kind": "team",
        "namespace": "fresh-create-ns",
        "user_id": "alice",
        "model_type": "akgentic.team.models.TeamCard",
        "description": "fresh",
        "payload": {
            "name": "Fresh",
            "description": "fresh",
            "entry_point": {
                "card": {
                    "role": "Human",
                    "description": "Human",
                    "skills": [],
                    "agent_class": "akgentic.core.agent.Akgent",
                    "config": {"name": "@Human", "role": "Human"},
                    "routes_to": [],
                },
                "headcount": 1,
                "members": [],
            },
            "members": [],
            "message_types": [{"__type__": "akgentic.core.messages.UserMessage"}],
            "agent_profiles": [],
        },
    }
    resp = gated_client.post("/admin/catalog/team", json=body)
    assert resp.status_code != 403
    assert resp.status_code != 422
    assert resp.status_code == 201


def test_clone_not_gated(gated_client: TestClient) -> None:
    """AC #14: POST /clone (body-only) is not 403'd by the gate (no namespace param)."""
    app = gated_client.app
    _override_user(app, "alice", [])
    body = {
        "src_namespace": "alice-ns",
        "src_id": "p1",
        "dst_namespace": "alice-ns",
        "dst_user_id": "alice",
    }
    resp = gated_client.post("/admin/catalog/clone", json=body)
    # Not 403 from the gate, not 422 for a missing `namespace` param.
    assert resp.status_code != 403
    assert resp.status_code != 422


def test_namespace_import_not_gated(gated_client: TestClient) -> None:
    """AC #14: POST /namespace/import (body-only) is not 403'd, no missing-namespace 422."""
    app = gated_client.app
    _override_user(app, "alice", [])
    bundle = "header:\n  bundle_version: 1\nentries: []\n"
    resp = gated_client.post(
        "/admin/catalog/namespace/import",
        content=bundle,
        headers={"content-type": "application/yaml"},
    )
    assert resp.status_code != 403


# --- AC #15: all gated routes carry the gate --------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/catalog/{kind}/{id}"),
        ("DELETE", "/catalog/{kind}/{id}"),
        ("PUT", "/catalog/namespace/{namespace}/meta"),
        # The namespace-delete route from akgentic-catalog Story 27.1. It is
        # present in the catalog pinned in this workspace, so the forward-
        # compatible attachment binds the gate to it here; if a future pointer
        # rollback removed it, ``_find_route`` would return None and this
        # parametrization would surface the regression rather than silently
        # passing.
        ("DELETE", "/catalog/namespace/{namespace}"),
    ],
)
def test_gated_route_carries_dependency(client: TestClient, method: str, path: str) -> None:
    """AC #15: each gated route carries the owner-or-admin dependency.

    Asserts the dependency is part of the route *definition* (``route.dependencies``)
    so it travels with the route when enterprise transplants it — not merely
    present on the live ``dependant``.
    """
    route = _find_route(client.app, method, "/admin" + path)
    if route is None and path == "/catalog/namespace/{namespace}":
        # akgentic-catalog Story 27.1 route — absent in older catalog pins
        # (e.g. CI's wheelhouse before the submodule pointer bump). When the
        # route is present (this workspace) the gate-attachment is asserted; a
        # rollback that removed the route surfaces here as a skip, not a pass.
        pytest.skip(
            "namespace-delete route (akgentic-catalog Story 27.1) not in this "
            "catalog pin; activates once the akgentic-catalog submodule pointer "
            "is bumped",
        )
    assert route is not None, f"route {method} /admin{path} not found"
    gate_calls = [d.dependency for d in route.dependencies]
    assert require_namespace_owner_or_admin in gate_calls


def _find_route(app: FastAPI, method: str, app_path: str) -> APIRoute | None:
    """Return the ``APIRoute`` matching ``method`` + full app path, or None."""
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == app_path and method in route.methods:
            return route
    return None


# --- AC #15: namespace-delete route behaviour (route present in pinned catalog) ---


def test_owner_can_delete_namespace(gated_client: TestClient) -> None:
    """AC #15: alice (owner) DELETEs her own namespace → 204."""
    app = gated_client.app
    if not _has_namespace_delete_route(app):
        pytest.skip(
            "namespace-delete route (akgentic-catalog Story 27.1) not in this "
            "catalog pin; activates once the submodule pointer is bumped",
        )
    _override_user(app, "alice", [])
    resp = gated_client.delete("/admin/catalog/namespace/alice-ns")
    assert resp.status_code == 204


def test_non_owner_delete_namespace_forbidden(gated_client: TestClient) -> None:
    """AC #15: alice deleting bob's namespace → 403 from the gate."""
    app = gated_client.app
    if not _has_namespace_delete_route(app):
        pytest.skip(
            "namespace-delete route (akgentic-catalog Story 27.1) not in this "
            "catalog pin; activates once the submodule pointer is bumped",
        )
    _override_user(app, "alice", [])
    resp = gated_client.delete("/admin/catalog/namespace/bob-ns")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


def test_admin_can_delete_any_namespace(gated_client: TestClient) -> None:
    """AC #15: carol (admin, not owner) DELETEs bob's namespace → 204."""
    app = gated_client.app
    if not _has_namespace_delete_route(app):
        pytest.skip(
            "namespace-delete route (akgentic-catalog Story 27.1) not in this "
            "catalog pin; activates once the submodule pointer is bumped",
        )
    _override_user(app, "carol", ["admin"])
    resp = gated_client.delete("/admin/catalog/namespace/bob-ns")
    assert resp.status_code == 204
