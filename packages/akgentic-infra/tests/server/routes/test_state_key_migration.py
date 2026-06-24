"""Behaviour tests for the Story 34.2 ``app.state`` → ``StateKey`` migration.

These are *behaviour* tests for the migrated consumer sites (ADR-030 §Decision 2,
§Validation): a **required** dependency raises ``LookupError`` (not
``AttributeError``) when exercised against an app that never ran
``_store_state``, and returns the wired service after the normal app factory; a
**soft** slot still yields ``None`` when unset and the route path tolerates it
exactly as before. No assertion checks for a comment/docstring/ADR string
(Golden Rule #8); the concrete-type headline is left to ``mypy --strict`` and
``test_state_key.py``'s ``assert_type`` coverage.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from akgentic.infra.server.app import _store_state
from akgentic.infra.server.deps import TierServices
from akgentic.infra.server.routes.teams import get_team_service
from akgentic.infra.server.routes.webhook import get_channel_parser_registry
from akgentic.infra.server.routes.webhook import router as webhook_router
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import ServerSettings
from akgentic.infra.server.state_keys import CHANNEL_PARSERS, FRONTEND_ADAPTER
from akgentic.infra.worker.deps import WorkerServices
from akgentic.infra.worker.routes.teams import get_services as worker_get_services
from akgentic.infra.worker.state_keys import SERVICES as WORKER_SERVICES
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.server.routes.test_webhook_route import StubIngestion


class _RequestStub:
    """Minimal ``Request``-shaped source exposing ``.app`` for ``StateKey._state``.

    ``StateKey._state`` resolves ``source.app.state`` for any non-``FastAPI``
    source, so a stub carrying the target app is enough to drive a dependency
    getter without spinning up a real request scope (mirrors
    ``test_state_key.py``'s ``_AppStub``).
    """

    def __init__(self, app: FastAPI) -> None:
        self.app = app


# --- AC 20/22: required consumer raises LookupError on a bare app ----------


def test_get_team_service_raises_lookup_error_on_bare_app() -> None:
    """A bare FastAPI (never ran ``_store_state``) makes the required server
    dependency raise ``LookupError``, not ``AttributeError``."""
    request = cast(Request, _RequestStub(FastAPI()))
    with pytest.raises(LookupError):
        get_team_service(request)


def test_get_team_service_returns_wired_service(client: TestClient) -> None:
    """After the normal ``create_app`` (which runs ``_store_state``), the
    required server dependency returns the wired ``TeamService``."""
    request = cast(Request, _RequestStub(cast(FastAPI, client.app)))
    assert isinstance(get_team_service(request), TeamService)


def test_worker_get_services_raises_lookup_error_on_bare_app() -> None:
    """The worker ``get_services`` raises ``LookupError`` on a bare worker app
    that never had its ``services`` slot set."""
    request = cast(Request, _RequestStub(FastAPI()))
    with pytest.raises(LookupError):
        worker_get_services(request)


def test_worker_get_services_returns_wired_services() -> None:
    """Once the worker ``SERVICES`` slot is set, ``get_services`` returns it."""
    app = FastAPI()
    services = cast(WorkerServices, MagicMock())
    WORKER_SERVICES.set(app, services)
    request = cast(Request, _RequestStub(app))
    assert worker_get_services(request) is services


# --- AC 19/23: required parser-registry raises; surviving soft slot stays None


def test_get_channel_parser_registry_raises_when_unset() -> None:
    """``CHANNEL_PARSERS`` is required: the webhook dependency raises
    ``LookupError`` (via ``.require()``) when the producer never set the slot,
    rather than returning a silent ``None``."""
    request = cast(Request, _RequestStub(FastAPI()))
    with pytest.raises(LookupError):
        get_channel_parser_registry(request)


def test_frontend_adapter_get_is_none_when_unset() -> None:
    """The soft ``FRONTEND_ADAPTER`` slot resolves to ``None`` when unset, so the
    WS handler runs its unwrapped-event-send path exactly as before."""
    request = cast(Request, _RequestStub(FastAPI()))
    assert FRONTEND_ADAPTER.get(request) is None


def _build_webhook_app_without_parser_registry() -> FastAPI:
    """Webhook app with the other required slots set but the parser registry left
    unset, so ``CHANNEL_PARSERS.require`` raises ``LookupError`` at request time."""
    app = FastAPI()
    # The other two required webhook slots are present so the failure is isolated
    # to the missing parser registry — FastAPI resolves all three dependencies up
    # front, before the handler body.
    app.state.channel_registry = MagicMock()
    app.state.ingestion = StubIngestion()
    app.include_router(webhook_router)
    return app


def test_webhook_returns_500_when_parser_registry_unset() -> None:
    """With no parser registry configured the webhook surfaces a 500: the
    required ``CHANNEL_PARSERS.require()`` raises ``LookupError`` in the
    dependency, which an ASGI server turns into a 500 (no ``HTTPException`` and
    no "Channel parsing not configured" detail). ``raise_server_exceptions=False``
    makes the TestClient return the server's 500 instead of re-raising."""
    client = TestClient(
        _build_webhook_app_without_parser_registry(),
        raise_server_exceptions=False,
    )
    resp = client.post("/webhook/some-channel", json={"text": "hi"})
    assert resp.status_code == 500


# --- AC 1: producer leaves the parser-registry slot unset when absent --------


def test_store_state_leaves_channel_parsers_unset_when_services_lacks_it() -> None:
    """``_store_state`` must NOT raise when the services container has no
    ``channel_parser_registry`` attribute (a base ``TierServices`` deployment has
    no channel parsers): it simply leaves the slot unset. Because
    ``CHANNEL_PARSERS`` is now required, a later read raises ``LookupError``
    rather than reading back a silent ``None``."""
    app = FastAPI()
    services = MagicMock(spec=["channel_registry", "ingestion"])
    services.channel_registry = MagicMock()
    services.ingestion = StubIngestion()

    _store_state(
        app,
        cast(TierServices, services),
        cast(TeamService, MagicMock()),
        ServerSettings(),
    )

    with pytest.raises(LookupError):
        CHANNEL_PARSERS.require(app)
