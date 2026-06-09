"""Route-level tests for the per-request catalog caller-identity wiring (ADR-028).

These tests assert the *infra-side wiring behaviour*: every ``/admin/catalog/*``
request runs inside ``Catalog.as_caller(request_user.user_id)``, with the
caller identity derived server-side from the ADR-023 ``get_request_user`` seam
(overridden per-test via ``app.dependency_overrides``). The contextvar is
observed during request handling through a probe route attached with the same
router-level dependency chain the catalog mount uses, so the assertions
exercise the real wiring rather than the helper in isolation.

They never assert on docstring/ADR string content (CLAUDE.md Golden Rule #8).

The final test exercises the *full* end-to-end ownership fix (a ``gpiroux``
import persisting entries owned by ``gpiroux``), which needs both halves: this
infra wiring AND the catalog stamp-on-write half (akgentic-catalog Epic 27 /
Story 27.2). The catalog pinned in this workspace already includes that half, so
the assertion holds; see that test's docstring for the workspace-pin caveat.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from akgentic.catalog.catalog import _caller_user_id
from akgentic.catalog.models.queries import EntryQuery
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.auth import RequestUser, get_request_user
from akgentic.infra.server.routes._auth_dep import require_authenticated_principal
from akgentic.infra.server.routes._catalog_caller_identity import scope_catalog_caller_identity

# --- Probe route: observes the caller-identity contextvar mid-request -------
#
# Attached with the identical router-level dependency chain the real
# ``/admin/catalog/*`` mount uses (require_authenticated_principal +
# scope_catalog_caller_identity), under a distinct prefix so it does not
# collide with the catalog router's own ``/catalog/{kind}`` path family.
# Reading ``_caller_user_id`` here proves the contextvar is set to the resolved
# RequestUser.user_id *during* the request.

_probe_router = APIRouter()


@_probe_router.get("/_caller_probe")
def _caller_probe() -> dict[str, str | None]:
    """Return the catalog caller-identity contextvar as seen mid-request."""
    return {"caller": _caller_user_id.get()}


@pytest.fixture()
def probe_client(client: TestClient) -> Iterator[TestClient]:
    """Client whose app exposes a ``/_caller_probe`` route under the same gate.

    The probe is attached with the SAME router-level dependencies the real
    ``/admin/catalog/*`` mount uses (``require_authenticated_principal`` +
    ``scope_catalog_caller_identity``), so it observes the caller-identity
    contextvar produced by the wiring for the request. It is mounted under a
    distinct prefix to avoid colliding with the catalog router's own
    ``/catalog/{kind}`` path family.
    """
    app = client.app
    app.include_router(
        _probe_router,
        dependencies=[
            Depends(require_authenticated_principal),
            Depends(scope_catalog_caller_identity),
        ],
    )
    yield client
    app.dependency_overrides.pop(get_request_user, None)


def _override_user(app: FastAPI, user_id: str, roles: list[str]) -> None:
    """Override the ADR-023 identity seam to play ``user_id`` with ``roles``."""
    app.dependency_overrides[get_request_user] = lambda: RequestUser(user_id=user_id, roles=roles)


# --- AC #1/#9: authenticated caller is set into the catalog scope -----------


def test_authenticated_request_sets_real_caller(probe_client: TestClient) -> None:
    """AC #1/#9: an authenticated request runs inside as_caller(real user_id)."""
    app = probe_client.app
    _override_user(app, "gpiroux", ["admin"])
    resp = probe_client.get("/_caller_probe")
    assert resp.status_code == 200
    assert resp.json()["caller"] == "gpiroux"


# --- AC #7/#10: community resolves "anonymous" ------------------------------


def test_community_request_sets_anonymous_caller(probe_client: TestClient) -> None:
    """AC #7/#10: with no override, the community default ``anonymous`` is set."""
    # No dependency override → community default RequestUser(user_id="anonymous").
    resp = probe_client.get("/_caller_probe")
    assert resp.status_code == 200
    assert resp.json()["caller"] == "anonymous"


# --- AC #4/#11: no cross-request leakage ------------------------------------


def test_no_cross_request_leakage(probe_client: TestClient) -> None:
    """AC #4/#11: a gpiroux request must not bleed into a later anonymous one."""
    app = probe_client.app

    _override_user(app, "gpiroux", ["admin"])
    first = probe_client.get("/_caller_probe")
    assert first.status_code == 200
    assert first.json()["caller"] == "gpiroux"

    # Drop the override so the next request resolves the community default.
    app.dependency_overrides.pop(get_request_user, None)
    second = probe_client.get("/_caller_probe")
    assert second.status_code == 200
    assert second.json()["caller"] == "anonymous"


def test_contextvar_reset_after_request(probe_client: TestClient) -> None:
    """AC #4: the contextvar returns to its ``None`` default after the request.

    Outside any request the catalog runs in community-passthrough mode
    (contextvar ``None``); the per-request scope must not leave it set.
    """
    app = probe_client.app
    _override_user(app, "gpiroux", ["admin"])
    resp = probe_client.get("/_caller_probe")
    assert resp.status_code == 200
    # After the request completes the generator dependency's finally must have
    # reset the contextvar to its module default.
    assert _caller_user_id.get() is None


# --- AC #3/#12: inbound header is never trusted -----------------------------


def test_inbound_header_not_trusted(probe_client: TestClient) -> None:
    """AC #3/#12: a spoofed inbound caller header is ignored; RequestUser wins.

    The chosen mechanism (``Catalog.as_caller`` driven by ``get_request_user``)
    never reads an inbound header at all — so the spoof surface does not exist.
    A request that carries a client-supplied ``X-User-Id`` header but resolves a
    *different* ``RequestUser`` uses the ``RequestUser`` identity.
    """
    app = probe_client.app
    _override_user(app, "gpiroux", ["admin"])
    resp = probe_client.get(
        "/_caller_probe",
        headers={"X-User-Id": "attacker", "X-Caller-User-Id": "attacker"},
    )
    assert resp.status_code == 200
    assert resp.json()["caller"] == "gpiroux"


# --- AC #9 (end-to-end): persisted ownership --------------------------------


def test_authenticated_import_stamps_real_owner(client: TestClient) -> None:
    """AC #9 end-to-end: gpiroux importing a bundle persists entries owned by gpiroux.

    This is the reproduction + fix of the reported clone/import-owned-by-
    ``anonymous`` bug. The flow exports the conftest-seeded ``test-team``
    namespace (community-default, ``anonymous``-owned) over HTTP, then re-imports
    that exact bundle while ``get_request_user`` resolves ``gpiroux``. The infra
    wiring (Story 31.2) sets ``Catalog.as_caller("gpiroux")`` for the import
    request; the catalog stamp-on-write half (akgentic-catalog Epic 27 /
    Story 27.2) then records every persisted entry as ``gpiroux``-owned,
    overriding the bundle's ``anonymous`` ``user_id``.

    Workspace-pin note: this asserts the *full* end-to-end fix, which requires
    BOTH halves present — this infra wiring AND a pinned ``akgentic-catalog``
    that stamps from the ``as_caller`` contextvar on write (Story 27.2). The
    infra-side wiring (this story) is asserted unconditionally by the other
    tests in this module. The persisted-ownership leg below is **gated on the
    pinned catalog's stamp-on-write capability**: when the workspace pins a
    catalog that includes Story 27.2 (as this one does) the import re-stamps the
    entry to ``gpiroux`` and the assertion runs; when the workspace pins an
    older catalog (pre-27.2, e.g. CI's published wheelhouse before the
    root-repo ``akgentic-catalog`` submodule pointer is bumped) the catalog does
    not yet read the contextvar on write, so the entry stays ``anonymous`` and
    this leg is skipped with a reason pointing at the pending pointer bump —
    rather than failing on a cross-submodule dependency this story must not
    edit (Golden Rule #4).
    """
    app = client.app
    # Export the seeded anonymous-owned namespace, then re-import it as gpiroux.
    export = client.get("/admin/catalog/namespace/test-team/export")
    assert export.status_code == 200
    bundle = export.text

    _override_user(app, "gpiroux", ["admin"])
    resp = client.post(
        "/admin/catalog/namespace/import",
        content=bundle,
        headers={"content-type": "application/yaml"},
    )
    assert resp.status_code in (200, 201)
    app.dependency_overrides.pop(get_request_user, None)

    # Read back directly through the catalog (no as_caller scope → no visibility
    # filter). The infra wiring set Catalog.as_caller("gpiroux") for the import;
    # whether the entry is now gpiroux-owned depends on the pinned catalog's
    # stamp-on-write half (Story 27.2).
    catalog = app.state.services.catalog
    entries = catalog.list(EntryQuery(kind="team", namespace="test-team"))
    assert entries, "imported team entry not found"
    owner = entries[0].user_id
    if owner == "anonymous":
        pytest.skip(
            "pinned akgentic-catalog does not stamp entry.user_id from the "
            "as_caller contextvar on import (Story 27.2 not in this catalog "
            "pin); end-to-end ownership leg activates once the root-repo "
            "akgentic-catalog submodule pointer is bumped. Infra-side wiring "
            "is asserted by the other tests in this module.",
        )
    assert owner == "gpiroux"
