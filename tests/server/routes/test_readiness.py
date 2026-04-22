"""Tests for the /readiness endpoint (Story 14.4)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from akgentic.infra.server.routes.readiness import router


def _create_test_app(*, draining: bool) -> FastAPI:
    """Create a minimal FastAPI app with the readiness router and draining flag."""
    app = FastAPI()
    app.state.draining = draining
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_readiness_returns_200_when_not_draining() -> None:
    """GET /readiness returns 200 with {"status": "ready"} when not draining (AC #1)."""
    app = _create_test_app(draining=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_draining() -> None:
    """GET /readiness returns 503 with {"status": "draining"} when draining (AC #2)."""
    app = _create_test_app(draining=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/readiness")
    assert resp.status_code == 503
    assert resp.json() == {"status": "draining"}


@pytest.mark.asyncio
async def test_readiness_response_content_type_is_json() -> None:
    """GET /readiness responses have application/json content type (AC #6)."""
    app = _create_test_app(draining=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/readiness")
    assert resp.headers["content-type"] == "application/json"

    app_drain = _create_test_app(draining=True)
    transport_drain = ASGITransport(app=app_drain)
    async with AsyncClient(transport=transport_drain, base_url="http://test") as client:
        resp_drain = await client.get("/readiness")
    assert resp_drain.headers["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_readiness_defaults_to_ready_when_draining_attr_missing() -> None:
    """GET /readiness returns 200 when app.state.draining is not set (defensive getattr)."""
    app = FastAPI()
    # Intentionally do NOT set app.state.draining
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}
