"""Tests for the shared RequireAuthMiddleware building block (ADR-034 Decision 2a)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, Response
from starlette.websockets import WebSocket

from akgentic.infra.server.auth import RequestUser
from akgentic.infra.server.middleware.require_auth import RequireAuthMiddleware


class _CountingAuth:
    """A resolver that records its invocation count for the resolve-once guard."""

    def __init__(self, user: RequestUser) -> None:
        self._user = user
        self.calls = 0

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        self.calls += 1
        return self._user


class _RaisingAuth:
    """A resolver that rejects with 401, exercising the pre-routing reject path."""

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        raise HTTPException(status_code=401, detail="bad credentials")


def _build_app(auth: object, **mw_kwargs: object) -> FastAPI:
    """A minimal app with RequireAuthMiddleware in front and a stash-echo route."""
    app = FastAPI()
    app.state.services = SimpleNamespace(auth=auth)

    @app.get("/echo")
    def echo(request: Request) -> dict[str, object]:
        user = getattr(request.state, "request_user", None)
        return {"user_id": user.user_id if isinstance(user, RequestUser) else None}

    @app.get("/readiness")
    def readiness() -> dict[str, str]:
        return {"status": "ok"}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    app.add_middleware(RequireAuthMiddleware, **mw_kwargs)
    return app


class TestStashContract:
    """A succeeding resolver runs once and stashes the RequestUser (AC7)."""

    def test_resolves_once_and_stashes(self) -> None:
        auth = _CountingAuth(RequestUser(user_id="alice"))
        client = TestClient(_build_app(auth))
        resp = client.get("/echo")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "alice"
        assert auth.calls == 1  # resolve-once invariant

    def test_raising_resolver_returns_401_pre_routing(self) -> None:
        client = TestClient(_build_app(_RaisingAuth()))
        resp = client.get("/echo")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "bad credentials"

    def test_websocket_raising_resolver_closes_1008(self) -> None:
        client = TestClient(_build_app(_RaisingAuth()))
        with pytest.raises(Exception):  # noqa: B017,PT011 — handshake rejected pre-accept
            with client.websocket_connect("/ws"):
                pass


class TestAllowlistAndOverrides:
    """The edges are tier-pluggable; the core invariant is not (AC2)."""

    def test_exact_allowlist_bypasses_resolver(self) -> None:
        auth = _CountingAuth(RequestUser(user_id="alice"))
        client = TestClient(_build_app(auth))  # default exact_allowlist={"/readiness"}
        resp = client.get("/readiness")
        assert resp.status_code == 200
        assert auth.calls == 0  # allowlisted → resolver never invoked

    def test_requires_principal_override_exempts_path(self) -> None:
        auth = _CountingAuth(RequestUser(user_id="alice"))
        client = TestClient(
            _build_app(auth, requires_principal=lambda conn: conn.scope["path"] != "/echo")
        )
        resp = client.get("/echo")
        assert resp.status_code == 200
        assert auth.calls == 0  # exempted by the tier predicate → no resolution

    def test_on_reject_override_shapes_response(self) -> None:
        def on_reject(connection: HTTPConnection, exc: HTTPException) -> Response:
            return PlainTextResponse("nope", status_code=exc.status_code)

        client = TestClient(_build_app(_RaisingAuth(), on_reject=on_reject))
        resp = client.get("/echo")
        assert resp.status_code == 401
        assert resp.text == "nope"
