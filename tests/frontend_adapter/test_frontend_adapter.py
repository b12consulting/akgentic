"""Tests for the frontend adapter plugin system (Story 3.1)."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest
from akgentic.core.messages import Message
from fastapi import FastAPI
from fastapi.testclient import TestClient

from akgentic.infra.server.routes.frontend_adapter import (
    FrontendAdapter,
    UnknownPayload,
    WrappedWsEvent,
    load_frontend_adapter,
)
from akgentic.infra.server.settings import ServerSettings

# ---------------------------------------------------------------------------
# Stub adapter for testing
# ---------------------------------------------------------------------------


class _StubAdapter:
    """Test adapter that satisfies the FrontendAdapter protocol."""

    def __init__(self) -> None:
        self.registered: bool = False
        self.routes_app: FastAPI | None = None

    def register_routes(self, app: FastAPI) -> None:
        self.registered = True
        self.routes_app = app

    def wrap_ws_event(self, event: Message) -> WrappedWsEvent:
        return WrappedWsEvent(
            payload=UnknownPayload(
                type="stub",
                data={"wrapped": True},
            ),
        )


class _NotAnAdapter:
    """Class that does NOT implement the FrontendAdapter protocol."""

    def some_other_method(self) -> None:
        pass


def _make_stub_module() -> types.ModuleType:
    """Create a fake module containing the stub adapter classes."""
    mod = types.ModuleType("fake_adapter_module")
    mod._StubAdapter = _StubAdapter  # type: ignore[attr-defined]
    mod._NotAnAdapter = _NotAnAdapter  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# Task 1: FrontendAdapter protocol tests (AC #1, #6)
# ---------------------------------------------------------------------------


class TestFrontendAdapterProtocol:
    """Verify the FrontendAdapter protocol definition."""

    def test_protocol_has_register_routes(self) -> None:
        """AC #1: FrontendAdapter has register_routes method."""
        assert hasattr(FrontendAdapter, "register_routes")

    def test_protocol_has_wrap_ws_event(self) -> None:
        """AC #1: FrontendAdapter has wrap_ws_event method."""
        assert hasattr(FrontendAdapter, "wrap_ws_event")

    def test_stub_satisfies_protocol(self) -> None:
        """AC #1: A valid implementation is recognized as FrontendAdapter."""
        adapter = _StubAdapter()
        assert isinstance(adapter, FrontendAdapter)

    def test_non_adapter_does_not_satisfy_protocol(self) -> None:
        """AC #1: A class missing methods is not a FrontendAdapter."""
        obj = _NotAnAdapter()
        assert not isinstance(obj, FrontendAdapter)

    def test_protocol_is_importable_from_module(self) -> None:
        """AC #6: FrontendAdapter is importable from the module."""
        from akgentic.infra.server.routes.frontend_adapter import (
            FrontendAdapter as FrontendAdapterImported,
        )

        assert FrontendAdapterImported is FrontendAdapter


# ---------------------------------------------------------------------------
# Task 2: load_frontend_adapter tests (AC #3, #4, #5)
# ---------------------------------------------------------------------------


class TestLoadFrontendAdapter:
    """Verify the adapter loader utility."""

    def test_load_valid_fqdn(self) -> None:
        """AC #3: Valid FQDN loads and returns adapter instance."""
        mod = _make_stub_module()
        with patch(
            "akgentic.infra.server.routes.frontend_adapter.importlib.import_module",
            return_value=mod,
        ):
            adapter = load_frontend_adapter("fake_adapter_module._StubAdapter")
        assert isinstance(adapter, FrontendAdapter)

    def test_load_nonexistent_module_raises_import_error(self) -> None:
        """AC #4: Non-existent module raises ImportError with clear message."""
        with pytest.raises(ImportError, match="Cannot load frontend adapter"):
            load_frontend_adapter("nonexistent.module.Adapter")

    def test_load_nonexistent_class_raises_import_error(self) -> None:
        """AC #4: Non-existent class in valid module raises ImportError."""
        mod = _make_stub_module()
        with patch(
            "akgentic.infra.server.routes.frontend_adapter.importlib.import_module",
            return_value=mod,
        ):
            with pytest.raises(ImportError, match="Cannot load frontend adapter"):
                load_frontend_adapter("fake_adapter_module.NonExistentClass")

    def test_load_non_adapter_class_raises_type_error(self) -> None:
        """AC #5: Class not implementing protocol raises TypeError."""
        mod = _make_stub_module()
        with patch(
            "akgentic.infra.server.routes.frontend_adapter.importlib.import_module",
            return_value=mod,
        ):
            with pytest.raises(
                TypeError,
                match="does not implement FrontendAdapter protocol",
            ):
                load_frontend_adapter("fake_adapter_module._NotAnAdapter")

    def test_load_adapter_with_constructor_args_raises_type_error(self) -> None:
        """Adapter requiring constructor args raises clear TypeError."""
        mod = _make_stub_module()

        class _NeedsArgs:
            def __init__(self, required: str) -> None:
                pass

            def register_routes(self, app: FastAPI) -> None:
                pass

            def wrap_ws_event(self, event: Message) -> WrappedWsEvent:
                return WrappedWsEvent(
                    payload=UnknownPayload(type="stub", data={}),
                )

        mod._NeedsArgs = _NeedsArgs  # type: ignore[attr-defined]
        with patch(
            "akgentic.infra.server.routes.frontend_adapter.importlib.import_module",
            return_value=mod,
        ):
            with pytest.raises(TypeError, match="Cannot instantiate frontend adapter"):
                load_frontend_adapter("fake_adapter_module._NeedsArgs")

    def test_load_invalid_fqdn_no_module_path(self) -> None:
        """AC #4: FQDN without module path raises ImportError."""
        with pytest.raises(ImportError, match="invalid FQDN"):
            load_frontend_adapter("JustAClassName")

    def test_loader_is_importable_from_module(self) -> None:
        """AC #6: load_frontend_adapter is importable from the module."""
        from akgentic.infra.server.routes.frontend_adapter import (
            load_frontend_adapter as loader,
        )

        assert loader is load_frontend_adapter


# ---------------------------------------------------------------------------
# Task 3: create_app() integration tests (AC #2, #3)
# ---------------------------------------------------------------------------


class TestCreateAppAdapterIntegration:
    """Verify adapter loading in create_app()."""

    def test_no_adapter_when_frontend_adapter_is_none(
        self,
        community_services: MagicMock,
        team_service: MagicMock,
    ) -> None:
        """AC #2: frontend_adapter=None means no adapter loaded."""
        settings = ServerSettings(frontend_adapter=None)
        app = self._create_app(community_services, team_service, settings)
        assert not hasattr(app.state, "frontend_adapter")

    def test_no_adapter_when_frontend_adapter_is_empty(
        self,
        community_services: MagicMock,
        team_service: MagicMock,
    ) -> None:
        """AC #2: frontend_adapter="" means no adapter loaded."""
        settings = ServerSettings(frontend_adapter="")
        app = self._create_app(community_services, team_service, settings)
        assert not hasattr(app.state, "frontend_adapter")

    def test_adapter_loaded_with_valid_fqdn(
        self,
        community_services: MagicMock,
        team_service: MagicMock,
    ) -> None:
        """AC #3: Valid FQDN loads adapter and calls register_routes."""
        settings = ServerSettings(
            frontend_adapter="fake_adapter_module._StubAdapter",
        )
        stub = _StubAdapter()
        with patch(
            "akgentic.infra.server.app.load_frontend_adapter",
            return_value=stub,
        ):
            app = self._create_app(community_services, team_service, settings)

        assert app.state.frontend_adapter is stub
        assert stub.registered
        assert stub.routes_app is app

    def test_invalid_fqdn_raises_error(
        self,
        community_services: MagicMock,
        team_service: MagicMock,
    ) -> None:
        """AC #3: Invalid FQDN raises clear error during app creation."""
        settings = ServerSettings(
            frontend_adapter="nonexistent.module.BadAdapter",
        )
        with pytest.raises(ImportError):
            self._create_app(community_services, team_service, settings)

    def test_v2_routes_always_mounted(
        self,
        community_services: MagicMock,
        team_service: MagicMock,
    ) -> None:
        """AC #3: V2 routes are mounted regardless of adapter config."""
        settings = ServerSettings(
            frontend_adapter="fake_adapter_module._StubAdapter",
        )
        with patch(
            "akgentic.infra.server.app.load_frontend_adapter",
            return_value=_StubAdapter(),
        ):
            app = self._create_app(community_services, team_service, settings)

        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/teams/" in route_paths or any("/teams" in p for p in route_paths)

    @staticmethod
    def _create_app(
        services: MagicMock,
        team_service: MagicMock,
        settings: ServerSettings,
    ) -> FastAPI:
        from akgentic.infra.server.app import _build_app

        return _build_app(services, team_service, settings)

    @pytest.fixture()
    def community_services(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture()
    def team_service(self) -> MagicMock:
        return MagicMock()


# ---------------------------------------------------------------------------
# Task 4: WebSocket adapter integration tests (AC #1)
# ---------------------------------------------------------------------------


class TestWebSocketAdapterIntegration:
    """Verify WebSocket route behavior with and without adapter."""

    def test_ws_sends_v2_format_without_adapter(self, client: TestClient) -> None:
        """AC #1: No adapter — events sent in V2 format (existing behavior)."""
        resp = client.post("/teams/", json={"catalog_namespace": "test-team"})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        with client.websocket_connect(f"/ws/{team_id}") as ws:
            _trigger_subscriber_event(client, team_id)
            data = ws.receive_json(mode="text")
            assert isinstance(data, dict)
            assert "__model__" in data

    def test_ws_calls_wrap_ws_event_when_adapter_present(
        self,
        client_with_adapter: TestClient,
    ) -> None:
        """AC #1: Adapter present — wrap_ws_event is called."""
        resp = client_with_adapter.post(
            "/teams/",
            json={"catalog_namespace": "test-team"},
        )
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        with client_with_adapter.websocket_connect(f"/ws/{team_id}") as ws:
            _trigger_subscriber_event(client_with_adapter, team_id)
            data = ws.receive_json(mode="text")
            assert isinstance(data, dict)
            assert "payload" in data
            assert data["payload"].get("type") == "stub"
            assert data["payload"]["data"].get("wrapped") is True

    @pytest.fixture()
    def client_with_adapter(
        self,
        app: FastAPI,
    ) -> TestClient:
        """TestClient with a stub adapter set on app.state."""
        app.state.frontend_adapter = _StubAdapter()
        return TestClient(app)


def _trigger_subscriber_event(client: TestClient, team_id: str) -> None:
    """Send a message to the team to trigger an orchestrator event."""
    import time

    time.sleep(0.3)
    client.post(f"/teams/{team_id}/message", json={"content": "hello"})
