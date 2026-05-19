"""Tests for Story 18.4 — ``create_app(caller_user_id_header=...)`` middleware.

Covers the three scenarios from AC8 (default off, header missing,
header set with a multi-tenant fixture) and the AC9 concurrency check
(two concurrent requests with different ``X-User-Id`` headers each see
their own caller via Python's ``contextvars`` semantics).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

from akgentic.catalog.api._settings import CatalogRouterSettings
from akgentic.catalog.catalog import Catalog
from akgentic.catalog.models.entry import Entry

from .conftest import make_meta_entry

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_NAMESPACE_META_TYPE = "akgentic.catalog.models.namespace_meta.NamespaceMeta"


def _team_payload() -> dict[str, Any]:
    return {
        "name": "team",
        "description": "",
        "entry_point": {
            "card": {
                "role": "entry",
                "description": "",
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


def _seed_team(catalog: Catalog, namespace: str, user_id: str) -> None:
    catalog.create(
        Entry(
            id="team",
            kind="team",
            namespace=namespace,
            user_id=user_id,
            model_type=_TEAM_TYPE,
            payload=_team_payload(),
        )
    )


def _seed_prompt(catalog: Catalog, namespace: str, id: str, user_id: str) -> None:
    catalog.create(
        Entry(
            id=id,
            kind="prompt",
            namespace=namespace,
            user_id=user_id,
            model_type=_NAMESPACE_META_TYPE,
            payload={"name": id, "description": "", "properties": {}},
        )
    )


def _build_app(
    tmp_path: Path,
    *,
    caller_user_id_header: str | None,
) -> FastAPI:
    """Create a YAML-backed app via ``create_app`` and seed the multi-tenant fixture.

    Three namespaces:
    * ``tenant-A`` — private, owner ``alice``, one prompt ``p-A``.
    * ``tenant-B`` — private, owner ``bob``, one prompt ``p-B``.
    * ``global`` — public (``meta.public=True``), owner ``admin``, one prompt ``p-G``.
    """
    pytest.importorskip("fastapi")

    from akgentic.catalog.api.app import create_app

    app = create_app(
        backend="yaml",
        yaml_base_path=tmp_path,
        router_settings=CatalogRouterSettings(expose_generic_kind_crud=True),
        caller_user_id_header=caller_user_id_header,
    )
    # Reach for the catalog the router was wired with via the module-level
    # singleton (set_catalog) — we use it to seed the fixture.
    from akgentic.catalog.api.router import _get_catalog

    catalog = _get_catalog()

    _seed_team(catalog, "tenant-A", user_id="alice")
    _seed_prompt(catalog, "tenant-A", "p-A", user_id="alice")

    _seed_team(catalog, "tenant-B", user_id="bob")
    _seed_prompt(catalog, "tenant-B", "p-B", user_id="bob")

    _seed_team(catalog, "global", user_id="admin")
    catalog.create(make_meta_entry("global", public=True, user_id="admin", name="global"))
    _seed_prompt(catalog, "global", "p-G", user_id="admin")
    return app


class TestCreateAppCallerHeaderDefault:
    """``caller_user_id_header=None`` — no middleware, byte-identical to today."""

    def test_default_header_arg_is_none(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app = _build_app(tmp_path, caller_user_id_header=None)
        client = TestClient(app)
        # Even when the request carries an X-User-Id header, the lack of
        # middleware registration means the contextvar stays None and no
        # filtering is applied.
        response = client.get("/catalog/prompt", params={}, headers={"X-User-Id": "alice"})
        assert response.status_code == 200
        ids = sorted(e["id"] for e in response.json())
        # All three prompts visible because no filter is applied.
        assert ids == ["p-A", "p-B", "p-G"]


class TestCreateAppCallerHeaderConfigured:
    """``caller_user_id_header="X-User-Id"`` — middleware registered, contextvar set per-request."""

    def test_request_omits_header_returns_full_result(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app = _build_app(tmp_path, caller_user_id_header="X-User-Id")
        client = TestClient(app)
        # Header missing on request → middleware leaves contextvar at None
        # → no filter applied (preserves community tier).
        response = client.get("/catalog/prompt")
        assert response.status_code == 200
        ids = sorted(e["id"] for e in response.json())
        assert ids == ["p-A", "p-B", "p-G"]

    def test_request_with_header_filters_to_owner_plus_public(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app = _build_app(tmp_path, caller_user_id_header="X-User-Id")
        client = TestClient(app)
        response = client.get("/catalog/prompt", headers={"X-User-Id": "alice"})
        assert response.status_code == 200
        ids = sorted(e["id"] for e in response.json())
        # alice sees: p-A (owner) + p-G (public namespace). NOT p-B (bob's private).
        assert ids == ["p-A", "p-G"]

    def test_request_with_empty_header_value_does_not_apply_filter(self, tmp_path: Path) -> None:
        """Empty-string header value behaves as missing — no ``as_caller`` enter."""
        from fastapi.testclient import TestClient

        app = _build_app(tmp_path, caller_user_id_header="X-User-Id")
        client = TestClient(app)
        response = client.get("/catalog/prompt", headers={"X-User-Id": ""})
        assert response.status_code == 200
        ids = sorted(e["id"] for e in response.json())
        # Empty header → contextvar stays None → all prompts visible.
        assert ids == ["p-A", "p-B", "p-G"]

    def test_request_with_whitespace_header_value_passes_through(self, tmp_path: Path) -> None:
        """Whitespace-only header is non-empty — passed through verbatim.

        Per AC8: whitespace-only values are not stripped. The middleware
        treats them as a non-empty caller identity (passed verbatim to
        ``Catalog.as_caller``); the upstream tier MUST sanitise. No
        entry will match the literal whitespace string, so the result
        is the public-namespace projection only.
        """
        from fastapi.testclient import TestClient

        app = _build_app(tmp_path, caller_user_id_header="X-User-Id")
        client = TestClient(app)
        response = client.get("/catalog/prompt", headers={"X-User-Id": "   "})
        assert response.status_code == 200
        ids = sorted(e["id"] for e in response.json())
        # Whitespace caller owns nothing; only the public namespace's
        # entries are visible (p-G).
        assert ids == ["p-G"]


class TestCreateAppCallerHeaderConcurrency:
    """AC9 — concurrent requests with different headers see isolated callers."""

    async def test_concurrent_requests_isolated(self, tmp_path: Path) -> None:
        pytest.importorskip("httpx")
        import httpx

        app = _build_app(tmp_path, caller_user_id_header="X-User-Id")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            alice_resp, bob_resp = await asyncio.gather(
                client.get("/catalog/prompt", headers={"X-User-Id": "alice"}),
                client.get("/catalog/prompt", headers={"X-User-Id": "bob"}),
            )
        assert alice_resp.status_code == 200
        assert bob_resp.status_code == 200
        alice_ids = sorted(e["id"] for e in alice_resp.json())
        bob_ids = sorted(e["id"] for e in bob_resp.json())
        # alice sees p-A + p-G; bob sees p-B + p-G; neither sees the other's private.
        assert alice_ids == ["p-A", "p-G"]
        assert bob_ids == ["p-B", "p-G"]
