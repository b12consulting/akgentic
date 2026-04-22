"""Integration test fixtures — real app, real actors, real LLM (no mocks).

Smoke tests (marked ``@pytest.mark.smoke``) run WITHOUT ``OPENAI_API_KEY``
by monkey-patching ``akgentic.llm.providers.create_model`` to return
``pydantic_ai.models.test.TestModel`` for deterministic, offline responses.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from akgentic.infra.adapters.shared.channel_parser_registry import ChannelParserRegistry
from akgentic.infra.adapters.community.local_ingestion import LocalIngestion
from akgentic.infra.adapters.community.yaml_channel_registry import YamlChannelRegistry
from akgentic.infra.server.app import _build_app, create_app
from akgentic.infra.server.deps import CommunityServices
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

from ._helpers import seed_integration_catalog

if TYPE_CHECKING:
    from .test_channels import StubChannelAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


# Re-export for backward compat with existing test imports
_seed_integration_catalog = seed_integration_catalog


def _test_model_factory(
    config: Any, http_client: Any = None,  # noqa: ANN401
) -> TestModel:
    """Drop-in replacement for ``create_model`` returning a ``TestModel``.

    Returns a TestModel that produces a StructuredOutput with a single response
    message directed to @Human (matching the integration test team topology).
    """
    return TestModel(
        call_tools=[],
        custom_output_args={
            "messages": [
                {
                    "message_type": "response",
                    "message": "Hello from TestModel",
                    "recipient": "@Human",
                },
            ],
        },
    )


def _test_get_output_type(
    config: Any, output_type: Any,  # noqa: ANN401
) -> Any:  # noqa: ANN401
    """No NativeOutput wrapping — TestModel does not support it."""
    return output_type


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _load_dotenv() -> None:
    """Load .env once per session (makes OPENAI_API_KEY available if present)."""
    load_dotenv(_PROJECT_ROOT / ".env")


@pytest.fixture(scope="session")
def openai_api_key() -> str:
    """Return OPENAI_API_KEY or skip — used only by LLM-dependent fixtures."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set — required for LLM integration tests")
    return key


@pytest.fixture()
def integration_settings(tmp_path: Path, openai_api_key: str) -> CommunitySettings:
    """CommunitySettings backed by tmp_path with a seeded integration catalog.

    Depends on ``openai_api_key`` so LLM-dependent tests are skipped when absent.
    """
    settings = CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=tmp_path / "catalog",
    )
    seed_integration_catalog(settings.catalog_path)
    return settings


@pytest.fixture()
def integration_services(
    integration_settings: CommunitySettings,
) -> Generator[CommunityServices, None, None]:
    """Wired community services with real actor system — shuts down on teardown."""
    services = wire_community(integration_settings)
    yield services
    services.actor_system.shutdown()


@pytest.fixture()
def integration_team_service(
    integration_services: CommunityServices,
) -> TeamService:
    """TeamService wired to integration services."""
    return TeamService(services=integration_services)


@pytest.fixture()
def integration_app(
    integration_settings: CommunitySettings,
    integration_services: CommunityServices,
) -> Generator[FastAPI, None, None]:
    """FastAPI app backed by real actors and real LLM."""
    application = create_app(integration_services, integration_settings)
    yield application


@pytest.fixture()
def integration_client(integration_app: FastAPI) -> TestClient:
    """Sync HTTP test client hitting a real FastAPI app."""
    return TestClient(integration_app)


# ---------------------------------------------------------------------------
# V1 Adapter Fixtures
# ---------------------------------------------------------------------------

V1_ADAPTER_FQDN = "akgentic.infra.server.routes.frontend_adapter.angular_v1.AngularV1Adapter"


@pytest.fixture()
def v1_adapter_settings(tmp_path: Path) -> CommunitySettings:
    """CommunitySettings with V1 frontend adapter enabled."""
    settings = CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=tmp_path / "catalog",
        frontend_adapter=V1_ADAPTER_FQDN,
    )
    seed_integration_catalog(settings.catalog_path)
    return settings


@pytest.fixture()
def v1_adapter_app(
    v1_adapter_settings: CommunitySettings,
) -> Generator[FastAPI, None, None]:
    """FastAPI app with V1 frontend adapter loaded."""
    services = wire_community(v1_adapter_settings)
    application = create_app(services, v1_adapter_settings)
    yield application
    services.actor_system.shutdown()


@pytest.fixture()
def v1_adapter_client(v1_adapter_app: FastAPI) -> TestClient:
    """Sync HTTP test client hitting a V1-adapter-enabled FastAPI app."""
    return TestClient(v1_adapter_app)


# ---------------------------------------------------------------------------
# Channel Integration Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_adapter() -> StubChannelAdapter:
    """Shared StubChannelAdapter instance for inspecting delivered messages."""
    from .test_channels import StubChannelAdapter as _StubChannelAdapter

    return _StubChannelAdapter()


@pytest.fixture()
def channel_parser_registry(
    test_adapter: StubChannelAdapter,
) -> ChannelParserRegistry:
    """ChannelParserRegistry with test stubs manually injected."""
    from .test_channels import StubChannelParser

    registry = ChannelParserRegistry(channels_config={})
    parser = StubChannelParser()
    registry._parsers[parser.channel_name] = parser
    registry._adapters.append(test_adapter)
    return registry


@pytest.fixture()
def channel_registry_instance(tmp_path: Path) -> YamlChannelRegistry:
    """YamlChannelRegistry backed by tmp_path."""
    return YamlChannelRegistry(registry_path=tmp_path / "channel-registry.yaml")


@pytest.fixture()
def channel_ingestion(
    integration_team_service: TeamService,
) -> LocalIngestion:
    """LocalIngestion wired to the integration TeamService."""
    return LocalIngestion(team_service=integration_team_service)


@pytest.fixture()
def channel_app(
    integration_services: CommunityServices,
    integration_team_service: TeamService,
    integration_settings: CommunitySettings,
    channel_parser_registry: ChannelParserRegistry,
    channel_registry_instance: YamlChannelRegistry,
    channel_ingestion: LocalIngestion,
) -> FastAPI:
    """FastAPI app with webhook wiring for channel integration tests.

    Uses _build_app with overridden channel deps on the services container.
    """
    integration_services.channel_parser_registry = channel_parser_registry
    integration_services.channel_registry = channel_registry_instance
    integration_services.ingestion = channel_ingestion
    return _build_app(
        integration_services,
        integration_team_service,
        integration_settings,
    )


@pytest.fixture()
def channel_client(channel_app: FastAPI) -> TestClient:
    """Sync HTTP test client hitting a channel-enabled FastAPI app."""
    return TestClient(channel_app)


# ---------------------------------------------------------------------------
# Smoke Test Fixtures (TestModel — no OPENAI_API_KEY required)
# ---------------------------------------------------------------------------


@pytest.fixture()
def smoke_settings(tmp_path: Path) -> CommunitySettings:
    """CommunitySettings for smoke tests — no OPENAI_API_KEY dependency.

    Explicitly sets ``catalog_path`` so the env var ``AKGENTIC_CATALOG_PATH``
    (loaded from .env) does not override the seeded test catalog.
    """
    catalog_dir = tmp_path / "catalog"
    seed_integration_catalog(catalog_dir)
    return CommunitySettings(
        workspaces_root=tmp_path / "workspaces",
        event_store_path=tmp_path / "event_store",
        catalog_path=catalog_dir,
    )


@pytest.fixture()
def smoke_services(
    smoke_settings: CommunitySettings,
) -> Generator[CommunityServices, None, None]:
    """Wired community services with TestModel injection — no real LLM calls."""
    with (
        patch("akgentic.llm.agent.create_model", side_effect=_test_model_factory),
        patch("akgentic.llm.agent.get_output_type", side_effect=_test_get_output_type),
    ):
        services = wire_community(smoke_settings)
        yield services
        services.actor_system.shutdown()


@pytest.fixture()
def smoke_app(
    smoke_settings: CommunitySettings,
    smoke_services: CommunityServices,
) -> Generator[FastAPI, None, None]:
    """FastAPI app backed by real actors and TestModel LLM."""
    application = create_app(smoke_services, smoke_settings)
    yield application


@pytest.fixture()
def smoke_client(smoke_app: FastAPI) -> TestClient:
    """Sync HTTP test client hitting a TestModel-backed FastAPI app."""
    return TestClient(smoke_app)


# ---------------------------------------------------------------------------
# Shared CLI/REPL Integration Test Fixtures
# ---------------------------------------------------------------------------


def _get_free_port() -> int:
    """Bind to port 0 to get a free port from the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(autouse=True)
def _httpx_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch httpx.Client to follow redirects by default.

    The CLI's ApiClient creates httpx.Client without follow_redirects=True,
    but FastAPI redirects /teams -> /teams/ (trailing-slash redirect).
    This patch ensures the CLI tests work against a real server.
    """
    _original_init = httpx.Client.__init__

    def _patched_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("follow_redirects", True)
        _original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_init)


@pytest.fixture()
def cli_server(integration_app: FastAPI) -> Generator[str, None, None]:
    """Start the integration app on a real TCP port via uvicorn in a daemon thread.

    Yields the base URL ``http://127.0.0.1:{port}``.
    """
    port = _get_free_port()
    config = uvicorn.Config(
        app=integration_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to be ready
    deadline = time.monotonic() + 10.0
    url = f"http://127.0.0.1:{port}"
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("uvicorn server did not start within 10 seconds")

    yield url

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def smoke_server(smoke_app: FastAPI) -> Generator[str, None, None]:
    """Start the smoke (TestModel) app on a real TCP port via uvicorn.

    Same as ``cli_server`` but uses ``smoke_app`` — no OPENAI_API_KEY required.
    Yields the base URL ``http://127.0.0.1:{port}``.
    """
    port = _get_free_port()
    config = uvicorn.Config(
        app=smoke_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10.0
    url = f"http://127.0.0.1:{port}"
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    else:
        pytest.fail("smoke uvicorn server did not start within 10 seconds")

    yield url

    server.should_exit = True
    thread.join(timeout=5)
