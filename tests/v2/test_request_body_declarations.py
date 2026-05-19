"""Request-body declaration tests for Story 16.4.

Covers the OpenAPI-visibility swap of ``POST /catalog/namespace/import`` and
``POST /catalog/namespace/validate`` from a raw ``Request`` read to a
``Body(..., media_type="application/yaml")`` declaration. Happy-path and
error-path behaviour for these endpoints is covered in
``test_api_router.py``; this file focuses on:

* OpenAPI schema advertises both request bodies (AC #1, #2).
* Missing body produces a consistent HTTP 422 (AC #8).
* Mismatched / missing ``Content-Type`` header still processes the body
  (AC #9 — ``media_type`` is documentation, not enforcement).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from akgentic.catalog.catalog import Catalog  # noqa: E402

_TEAM_TYPE = "akgentic.team.models.TeamCard"
_AGENT_TYPE = "akgentic.core.agent_card.AgentCard"


def _team_payload() -> dict[str, Any]:
    """Return a minimal valid ``TeamCard`` payload."""
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


def _agent_payload(name: str = "a") -> dict[str, Any]:
    """Return a minimal valid ``AgentCard`` payload."""
    return {
        "role": "r",
        "description": "",
        "skills": [],
        "agent_class": "akgentic.core.agent.Akgent",
        "config": {"name": name, "role": "r"},
        "routes_to": [],
        "metadata": {},
    }


def _build_bundle(namespace: str = "ns-body") -> str:
    """Return a minimal, structurally valid bundle YAML string."""
    import yaml as _yaml

    doc = {
        "namespace": namespace,
        "user_id": "alice",
        "entries": {
            "team": {
                "kind": "team",
                "model_type": _TEAM_TYPE,
                "description": "",
                "payload": _team_payload(),
            },
            "a": {
                "kind": "agent",
                "model_type": _AGENT_TYPE,
                "description": "",
                "payload": _agent_payload("a"),
            },
        },
    }
    return _yaml.safe_dump(doc, sort_keys=False)


# --- AC #1, #2: OpenAPI schema declares both request bodies ----------------


class TestOpenAPISchema:
    """``/openapi.json`` advertises ``requestBody`` for both POST endpoints."""

    def _openapi(self, api_client: tuple[TestClient, Catalog]) -> dict[str, Any]:
        client, _ = api_client
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema: dict[str, Any] = response.json()
        return schema

    def _request_body(self, schema: dict[str, Any], path: str) -> dict[str, Any]:
        """Extract the ``requestBody`` dict for ``POST {path}``."""
        op = schema["paths"][path]["post"]
        request_body = op.get("requestBody")
        assert request_body is not None, f"POST {path} has no requestBody"
        assert isinstance(request_body, dict)
        return request_body

    def test_import_declares_request_body(self, api_client: tuple[TestClient, Catalog]) -> None:
        schema = self._openapi(api_client)
        body = self._request_body(schema, "/catalog/namespace/import")
        assert body.get("required") is True
        assert "application/yaml" in body["content"]

    def test_validate_declares_request_body(self, api_client: tuple[TestClient, Catalog]) -> None:
        schema = self._openapi(api_client)
        body = self._request_body(schema, "/catalog/namespace/validate")
        assert body.get("required") is True
        assert "application/yaml" in body["content"]


# --- AC #8: missing body produces a consistent 422 --------------------------


class TestMissingBody:
    """Empty-body POSTs surface as HTTP 422 for both routes."""

    def test_import_missing_body_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.post("/catalog/namespace/import")
        assert response.status_code == 422

    def test_validate_missing_body_422(self, api_client: tuple[TestClient, Catalog]) -> None:
        client, _ = api_client
        response = client.post("/catalog/namespace/validate")
        assert response.status_code == 422


# --- AC #9: Content-Type header is documentation, not enforcement -----------


class TestContentTypeNotEnforced:
    """A valid bundle is processed under any non-JSON Content-Type.

    FastAPI's ``Body(bytes, media_type=...)`` reads the raw body when the
    request's ``Content-Type`` is anything other than ``application/json``;
    for ``application/json`` FastAPI interposes JSON parsing regardless of
    the ``media_type`` declaration. The ``application/json`` clash is a
    FastAPI invariant — not a handler-level contract — and is not exercised
    here. The tests below verify the remaining "header is not enforced"
    surface: ``application/yaml`` (declared default), ``text/plain``
    (mismatched but tolerated), and the httpx default (no explicit header).
    """

    def test_import_accepts_text_plain_content_type(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, _ = api_client
        yaml_text = _build_bundle(namespace="ns-body-imp-ct")
        response = client.post(
            "/catalog/namespace/import",
            content=yaml_text.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 201
        assert {e["id"] for e in response.json()} == {"team", "a"}

    def test_validate_accepts_text_plain_content_type(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        client, _ = api_client
        yaml_text = _build_bundle(namespace="ns-body-val-ct")
        response = client.post(
            "/catalog/namespace/validate",
            content=yaml_text.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_import_accepts_default_content_type(
        self, api_client: tuple[TestClient, Catalog]
    ) -> None:
        """httpx's default Content-Type for ``content=bytes`` is not JSON — the
        handler must still accept the body verbatim.
        """
        client, _ = api_client
        yaml_text = _build_bundle(namespace="ns-body-imp-noct")
        response = client.post(
            "/catalog/namespace/import",
            content=yaml_text.encode("utf-8"),
        )
        assert response.status_code == 201
