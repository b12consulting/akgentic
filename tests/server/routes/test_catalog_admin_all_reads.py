"""Route-level tests for the admin ``?all=true`` unscoped catalog reads (ADR-028 §Decision 9).

These tests assert HTTP *read* behaviour — which namespaces / entries a caller
can see (200 vs 404, namespace-list membership) — via the FastAPI
``TestClient`` against the live ``/admin/catalog/*`` mount. The ADR-023 identity
seam (``get_request_user``) is overridden per-test through
``app.dependency_overrides`` to play different callers.

The lever under test is the router-level ``scope_catalog_caller_identity``
dependency: when an admin opts into ``?all=true`` on a ``GET``, infra runs the
read unscoped (does not enter ``Catalog.as_caller``), so the catalog's
visibility filter is bypassed and foreign/private content becomes visible. For
everyone else — non-admins, and admins by default — the read runs scoped to
owner+public, byte-unchanged from today.

They never assert on docstring / ADR string content (CLAUDE.md Golden Rule #8);
behaviour is the guard.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from akgentic.catalog.models.entry import Entry
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user

# --- Fixtures ---------------------------------------------------------------

_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _seed_meta_namespace(app: FastAPI, namespace: str, user_id: str) -> None:
    """Create a ``kind="meta"`` anchor entry owning ``namespace`` for ``user_id``.

    A meta entry both anchors namespace ownership and makes the namespace
    surface in ``GET /namespaces`` (which unions team + meta entries). Created
    straight through the wired catalog instance so the live app sees it.
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
    """Create a ``kind="prompt"`` entry owned by ``user_id`` (private by default)."""
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
def seeded_client(client: TestClient) -> Iterator[TestClient]:
    """Client with a carol-owned and a bob-owned (private) namespace seeded.

    * ``carol-ns`` — meta anchor + prompt owned by ``carol`` (the admin subject).
    * ``bob-ns`` — meta anchor + prompt owned by ``bob`` (the foreign namespace
      that should appear only when an admin opts into ``?all=true``).
    """
    app = client.app
    _seed_meta_namespace(app, "carol-ns", "carol")
    _seed_prompt(app, "carol-ns", "p1", "carol")
    _seed_meta_namespace(app, "bob-ns", "bob")
    _seed_prompt(app, "bob-ns", "p1", "bob")
    yield client
    app.dependency_overrides.pop(get_request_user, None)


def _override_user(app: FastAPI, user_id: str, roles: list[str]) -> None:
    """Override the ADR-023 identity seam to play ``user_id`` with ``roles``."""
    app.dependency_overrides[get_request_user] = lambda: RequestUser(user_id=user_id, roles=roles)


def _namespace_names(resp_json: list[dict[str, object]]) -> set[str]:
    """Collect the namespace identifiers from a ``GET /namespaces`` response.

    A ``NamespaceSummary`` row uses ``name`` for the namespace's display name,
    which equals the namespace identifier here (the seed meta payload sets
    ``name`` to the namespace string). Match on that.
    """
    return {str(row["name"]) for row in resp_json}


# --- AC #7 / #8: admin list scoping ----------------------------------------


def test_admin_all_true_sees_foreign_namespace(seeded_client: TestClient) -> None:
    """AC #7: admin carol + ``all=true`` sees bob-ns (a scoped read would hide it)."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get("/admin/catalog/namespaces", params={"all": "true"})
    assert resp.status_code == 200
    names = _namespace_names(resp.json())
    assert "bob-ns" in names
    assert "carol-ns" in names


def test_admin_default_scoped_hides_foreign_namespace(seeded_client: TestClient) -> None:
    """AC #8: admin carol with no ``all`` sees only her own — bob-ns hidden."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get("/admin/catalog/namespaces")
    assert resp.status_code == 200
    names = _namespace_names(resp.json())
    assert "bob-ns" not in names
    assert "carol-ns" in names


def test_admin_all_false_scoped_hides_foreign_namespace(seeded_client: TestClient) -> None:
    """AC #8: admin carol + explicit ``all=false`` behaves like the default (scoped)."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get("/admin/catalog/namespaces", params={"all": "false"})
    assert resp.status_code == 200
    names = _namespace_names(resp.json())
    assert "bob-ns" not in names
    assert "carol-ns" in names


# --- AC #9: non-admin fail-safe --------------------------------------------


def test_non_admin_all_true_is_scoped(seeded_client: TestClient) -> None:
    """AC #9: non-admin alice + ``all=true`` behaves exactly like ``all=false``.

    The param is silently ineffective for a non-admin — bob-ns is not shown and
    there is no 403.
    """
    app = seeded_client.app
    _override_user(app, "alice", [])
    resp = seeded_client.get("/admin/catalog/namespaces", params={"all": "true"})
    assert resp.status_code == 200
    names = _namespace_names(resp.json())
    assert "bob-ns" not in names


# --- AC #12: community unaffected -------------------------------------------


def test_community_all_true_unchanged(seeded_client: TestClient) -> None:
    """AC #12: with no override (community), ``all=true`` is ineffective (no admin role).

    Anonymous owns nothing seeded here, so a scoped community read sees neither
    carol-ns nor bob-ns; ``all=true`` does not change that (anonymous never has
    the ``admin`` role). The request still succeeds with 200.
    """
    # No dependency override -> community default RequestUser(anonymous, roles=[]).
    resp = seeded_client.get("/admin/catalog/namespaces", params={"all": "true"})
    assert resp.status_code == 200
    names = _namespace_names(resp.json())
    assert "bob-ns" not in names
    assert "carol-ns" not in names


# --- AC #10 / #11: entry-get matrix ----------------------------------------


def test_admin_all_true_opens_foreign_entry(seeded_client: TestClient) -> None:
    """AC #10: admin carol + ``all=true`` opens bob's private entry → 200."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get(
        "/admin/catalog/prompt/p1", params={"namespace": "bob-ns", "all": "true"}
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "p1"


def test_admin_default_404s_foreign_entry(seeded_client: TestClient) -> None:
    """AC #10 (converse): admin carol with no ``all`` is scoped → bob's entry 404s."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get("/admin/catalog/prompt/p1", params={"namespace": "bob-ns"})
    assert resp.status_code == 404


def test_non_admin_all_true_404s_foreign_entry(seeded_client: TestClient) -> None:
    """AC #11: non-admin alice + ``all=true`` still 404s bob's private entry (scoped)."""
    app = seeded_client.app
    _override_user(app, "alice", [])
    resp = seeded_client.get(
        "/admin/catalog/prompt/p1", params={"namespace": "bob-ns", "all": "true"}
    )
    assert resp.status_code == 404


# --- AC #13: list + search reads honour the lever (GET list; search stays scoped) ---


def test_admin_all_true_list_includes_foreign_entries(seeded_client: TestClient) -> None:
    """AC #13: admin carol + ``all=true`` on ``GET /{kind}`` lists bob's entry."""
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.get("/admin/catalog/prompt", params={"all": "true"})
    assert resp.status_code == 200
    owners = {row["user_id"] for row in resp.json()}
    assert "bob" in owners
    assert "carol" in owners


def test_non_admin_all_true_list_excludes_foreign_entries(seeded_client: TestClient) -> None:
    """AC #13: non-admin alice + ``all=true`` on ``GET /{kind}`` does NOT list bob's entry."""
    app = seeded_client.app
    _override_user(app, "alice", [])
    resp = seeded_client.get("/admin/catalog/prompt", params={"all": "true"})
    assert resp.status_code == 200
    owners = {row["user_id"] for row in resp.json()}
    assert "bob" not in owners


def test_search_stays_scoped_under_all_true(seeded_client: TestClient) -> None:
    """AC #13: ``POST .../search`` uses a write method, so it stays scoped even with ``all=true``.

    The unscoping lever is GET-only (writes always run scoped so ownership
    stamping is never affected — AC #5/#6). Search therefore behaves like a
    scoped read: admin carol does not see bob's private entry through search.
    """
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.post(
        "/admin/catalog/prompt/search", params={"all": "true"}, json={"kind": "prompt"}
    )
    assert resp.status_code == 200
    owners = {row["user_id"] for row in resp.json()}
    assert "bob" not in owners
    assert "carol" in owners


# --- AC #5 / #6: writes are NOT widened by ?all=true -----------------------


def test_write_with_all_true_still_gated(seeded_client: TestClient) -> None:
    """AC #5: a non-owner non-admin ``DELETE`` carrying ``?all=true`` is still 403'd.

    ``all`` is reads-only; the write gate ignores it. alice deleting in bob-ns
    is forbidden with or without ``?all=true``.
    """
    app = seeded_client.app
    _override_user(app, "alice", [])
    resp = seeded_client.delete(
        "/admin/catalog/prompt/p1", params={"namespace": "bob-ns", "all": "true"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not authorized to modify this namespace"


def test_admin_write_with_all_true_runs_scoped(seeded_client: TestClient) -> None:
    """AC #6: an admin ``DELETE`` with ``?all=true`` runs scoped (not unscoped).

    The unscoping lever is GET-only, so a write never takes the unscoped
    branch — it always runs under ``Catalog.as_caller(user.user_id)``. The
    owner-or-admin *gate* passes for an admin (no 403), but the delete handler
    then runs scoped: carol cannot see bob's private prompt under
    ``as_caller("carol")``, so the handler's lookup 404s — exactly as a scoped
    read would. Had ``all=true`` unscoped the write, carol would have seen and
    deleted bob's entry (204); the 404 proves the write stayed scoped despite
    ``?all=true``. (A 404 also confirms the gate passed: the handler ran.)
    """
    app = seeded_client.app
    _override_user(app, "carol", ["admin"])
    resp = seeded_client.delete(
        "/admin/catalog/prompt/p1", params={"namespace": "bob-ns", "all": "true"}
    )
    # Gate passed (not 403); scoped handler hides bob's private entry (404).
    # NOT 204 — a 204 would mean the write ran unscoped and saw bob's entry.
    assert resp.status_code == 404
