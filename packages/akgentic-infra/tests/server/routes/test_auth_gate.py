"""Tests for the auth gate's 'never 401s on its own' invariant (ADR-034, AC3/AC7)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection

from akgentic.infra.server.auth import RequestUser
from akgentic.infra.server.middleware.require_auth import RequireAuthMiddleware
from akgentic.infra.server.routes._auth_dep import require_authenticated_principal


class _RaisingAuth:
    """A resolver that always rejects with 401."""

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        raise HTTPException(status_code=401, detail="bad credentials")


def _gated_app() -> FastAPI:
    """App with a single gated route Depends(require_authenticated_principal)."""
    app = FastAPI()

    @app.get("/guarded")
    def guarded(principal: str = Depends(require_authenticated_principal)) -> dict[str, str]:
        return {"principal": principal}

    return app


def test_gate_passes_anonymous_with_no_middleware() -> None:
    """Community shape (no middleware): the gate resolves anonymous and passes."""
    client = TestClient(_gated_app())
    resp = client.get("/guarded")
    assert resp.status_code == 200  # never 401s on its own
    assert resp.json()["principal"] == "anonymous"


def test_raising_resolver_behind_middleware_is_401_pre_routing() -> None:
    """A raising resolver behind the shared middleware returns 401 before the gate runs."""
    app = _gated_app()
    app.state.services = SimpleNamespace(auth=_RaisingAuth())
    app.add_middleware(RequireAuthMiddleware)
    client = TestClient(app)
    resp = client.get("/guarded")
    assert resp.status_code == 401
