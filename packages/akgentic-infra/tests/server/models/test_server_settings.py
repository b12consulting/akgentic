"""Tests for ServerSettings and CommunitySettings models."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from akgentic.infra.server.settings import CommunitySettings, ServerSettings


class TestServerSettingsDefaults:
    """ServerSettings provides tier-agnostic configuration with defaults."""

    def test_default_host(self) -> None:
        """Default host is 0.0.0.0."""
        settings = ServerSettings()
        assert settings.host == "0.0.0.0"

    def test_default_port(self) -> None:
        """Default port is 8000."""
        settings = ServerSettings()
        assert settings.port == 8000

    def test_default_frontend_adapter(self) -> None:
        """Default frontend_adapter is None."""
        settings = ServerSettings()
        assert settings.frontend_adapter is None

    def test_no_workspaces_root_on_base(self) -> None:
        """ServerSettings does not have workspaces_root (community-specific)."""
        assert "workspaces_root" not in ServerSettings.model_fields


class TestServerSettingsEnvOverride:
    """ServerSettings loads from AKGENTIC_ prefixed env vars."""

    def test_host_from_env(self) -> None:
        """AKGENTIC_HOST overrides host field."""
        os.environ["AKGENTIC_HOST"] = "127.0.0.1"
        try:
            settings = ServerSettings()
            assert settings.host == "127.0.0.1"
        finally:
            del os.environ["AKGENTIC_HOST"]

    def test_port_from_env(self) -> None:
        """AKGENTIC_PORT overrides port field."""
        os.environ["AKGENTIC_PORT"] = "9000"
        try:
            settings = ServerSettings()
            assert settings.port == 9000
        finally:
            del os.environ["AKGENTIC_PORT"]

    def test_frontend_adapter_from_env(self) -> None:
        """AKGENTIC_FRONTEND_ADAPTER overrides frontend_adapter field."""
        os.environ["AKGENTIC_FRONTEND_ADAPTER"] = "my.adapter.Class"
        try:
            settings = ServerSettings()
            assert settings.frontend_adapter == "my.adapter.Class"
        finally:
            del os.environ["AKGENTIC_FRONTEND_ADAPTER"]


class TestServerSettingsLogLevel:
    """ServerSettings log_level field — validation and env override."""

    def test_default_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default log_level is INFO."""
        monkeypatch.delenv("AKGENTIC_LOG_LEVEL", raising=False)
        settings = ServerSettings()
        assert settings.log_level == "INFO"

    def test_log_level_from_env(self) -> None:
        """AKGENTIC_LOG_LEVEL overrides log_level field."""
        os.environ["AKGENTIC_LOG_LEVEL"] = "DEBUG"
        try:
            settings = ServerSettings()
            assert settings.log_level == "DEBUG"
        finally:
            del os.environ["AKGENTIC_LOG_LEVEL"]

    def test_log_level_case_insensitive(self) -> None:
        """AKGENTIC_LOG_LEVEL is normalized to uppercase."""
        os.environ["AKGENTIC_LOG_LEVEL"] = "debug"
        try:
            settings = ServerSettings()
            assert settings.log_level == "DEBUG"
        finally:
            del os.environ["AKGENTIC_LOG_LEVEL"]

    def test_invalid_log_level_falls_back(self) -> None:
        """Invalid AKGENTIC_LOG_LEVEL falls back to INFO with a warning."""

        os.environ["AKGENTIC_LOG_LEVEL"] = "TRACE"
        try:
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                settings = ServerSettings()
                assert settings.log_level == "INFO"
                assert len(w) == 1
                assert "TRACE" in str(w[0].message)
        finally:
            del os.environ["AKGENTIC_LOG_LEVEL"]


class TestServerSettingsModel:
    """ServerSettings extends BaseSettings."""

    def test_is_base_settings_subclass(self) -> None:
        """ServerSettings is a pydantic_settings BaseSettings subclass."""
        from pydantic_settings import BaseSettings

        assert issubclass(ServerSettings, BaseSettings)

    def test_field_descriptions_present(self) -> None:
        """All fields have descriptions."""
        for name, field_info in ServerSettings.model_fields.items():
            assert field_info.description is not None, f"Field {name} missing description"


# ---------------------------------------------------------------------------
# Reclassified from integration/test_adr003_tier_agnostic.py — TestSettingsHierarchy
# Pure model field inspection; no real app needed.
# ---------------------------------------------------------------------------


class TestSettingsHierarchy:
    """Verify ServerSettings / CommunitySettings hierarchy."""

    def test_server_settings_has_only_tier_agnostic_fields(self) -> None:
        """ServerSettings has only tier-agnostic fields."""
        server_fields = set(ServerSettings.model_fields.keys())
        expected = {
            "host",
            "port",
            "log_level",
            "cors_origins",
            "frontend_adapter",
            "shutdown_drain_timeout",
            "shutdown_pre_drain_delay",
            "ws_reader_pool_size",
        }
        assert server_fields == expected, (
            f"ServerSettings fields mismatch: got {server_fields}, expected {expected}"
        )

    def test_community_settings_extends_server_settings(self) -> None:
        """CommunitySettings is a subclass of ServerSettings."""
        assert issubclass(CommunitySettings, ServerSettings)
        community_own_fields = set(CommunitySettings.model_fields.keys()) - set(
            ServerSettings.model_fields.keys()
        )
        assert "workspaces_root" in community_own_fields
        assert "event_store_path" in community_own_fields
        assert "catalog_path" in community_own_fields

    def test_community_settings_no_literal_fields(self) -> None:
        """No Literal['yaml'] fields on either settings class."""
        import typing

        for cls in (ServerSettings, CommunitySettings):
            for field_name, field_info in cls.model_fields.items():
                annotation = field_info.annotation
                origin = typing.get_origin(annotation)
                if origin is typing.Literal:
                    args = typing.get_args(annotation)
                    assert "yaml" not in args, f"{cls.__name__}.{field_name} has Literal['yaml']"

    def test_community_settings_wires_functional_app(self, tmp_path: Path) -> None:
        """End-to-end: CommunitySettings -> wire -> create_app -> team works."""
        from akgentic.infra.server.app import create_app
        from akgentic.infra.wiring import wire_community

        settings = CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
        )
        # Seed a minimal catalog
        _seed_minimal_catalog(settings.catalog_path)
        services = wire_community(settings)
        try:
            from fastapi.testclient import TestClient

            app = create_app(services, settings)
            client = TestClient(app)

            resp = client.get("/teams/")
            assert resp.status_code == 200
            assert "teams" in resp.json()
        finally:
            services.actor_system.shutdown()


def _seed_minimal_catalog(catalog_root: Path) -> None:
    """Seed minimal catalog for settings hierarchy test."""
    import yaml

    def _write_yaml(path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, default_flow_style=False))

    _write_yaml(
        catalog_root / "agents" / "human-proxy.yaml",
        {
            "id": "human-proxy",
            "tool_ids": [],
            "card": {
                "role": "Human",
                "description": "Human user interface",
                "skills": [],
                "agent_class": "akgentic.agent.HumanProxy",
                "config": {"name": "@Human", "role": "Human"},
                "routes_to": ["@Manager"],
            },
        },
    )
    _write_yaml(
        catalog_root / "agents" / "manager.yaml",
        {
            "id": "manager",
            "tool_ids": [],
            "card": {
                "role": "Manager",
                "description": "Test manager",
                "skills": ["coordination"],
                "agent_class": "akgentic.agent.BaseAgent",
                "config": {"name": "@Manager", "role": "Manager"},
                "routes_to": [],
            },
        },
    )
    _write_yaml(
        catalog_root / "teams" / "test-team.yaml",
        {
            "id": "test-team",
            "name": "Test Team",
            "entry_point": "human-proxy",
            "message_types": ["akgentic.core.messages.UserMessage"],
            "members": [
                {"agent_id": "human-proxy"},
                {"agent_id": "manager"},
            ],
            "profiles": [],
        },
    )
    (catalog_root / "templates").mkdir(parents=True, exist_ok=True)
    (catalog_root / "tools").mkdir(parents=True, exist_ok=True)


class TestCommunitySettingsDefaults:
    """CommunitySettings extends ServerSettings with community-specific fields."""

    def test_inherits_server_settings(self) -> None:
        """CommunitySettings is a subclass of ServerSettings."""
        assert issubclass(CommunitySettings, ServerSettings)

    def test_default_workspaces_root(self) -> None:
        """Default workspaces_root is Path('workspaces')."""
        settings = CommunitySettings()
        assert settings.workspaces_root == Path("workspaces")

    def test_default_event_store_path(self) -> None:
        """Default event_store_path is Path('data/event_store')."""
        settings = CommunitySettings()
        assert settings.event_store_path == Path("data/event_store")

    def test_default_catalog_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default catalog_path is Path('data/catalog')."""
        monkeypatch.delenv("AKGENTIC_CATALOG_PATH", raising=False)
        settings = CommunitySettings()
        assert settings.catalog_path == Path("data/catalog")

    def test_inherits_base_fields(self) -> None:
        """CommunitySettings inherits host, port, cors_origins, shutdown fields."""
        settings = CommunitySettings()
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000
        assert settings.cors_origins == ["*"]
        assert settings.shutdown_drain_timeout == 30
        assert settings.shutdown_pre_drain_delay == 0

    def test_event_store_path_from_env(self) -> None:
        """AKGENTIC_EVENT_STORE_PATH overrides event_store_path field."""
        os.environ["AKGENTIC_EVENT_STORE_PATH"] = "/tmp/events"
        try:
            settings = CommunitySettings()
            assert settings.event_store_path == Path("/tmp/events")
        finally:
            del os.environ["AKGENTIC_EVENT_STORE_PATH"]

    def test_catalog_path_from_env(self) -> None:
        """AKGENTIC_CATALOG_PATH overrides catalog_path field."""
        os.environ["AKGENTIC_CATALOG_PATH"] = "/tmp/catalog"
        try:
            settings = CommunitySettings()
            assert settings.catalog_path == Path("/tmp/catalog")
        finally:
            del os.environ["AKGENTIC_CATALOG_PATH"]

    def test_workspaces_root_from_env(self) -> None:
        """AKGENTIC_WORKSPACES_ROOT overrides workspaces_root field."""
        os.environ["AKGENTIC_WORKSPACES_ROOT"] = "/tmp/ws"
        try:
            settings = CommunitySettings()
            assert settings.workspaces_root == Path("/tmp/ws")
        finally:
            del os.environ["AKGENTIC_WORKSPACES_ROOT"]

    def test_default_channel_registry_path(self) -> None:
        """Default channel_registry_path is None."""
        settings = CommunitySettings()
        assert settings.channel_registry_path is None

    def test_channel_registry_path_from_env(self) -> None:
        """AKGENTIC_CHANNEL_REGISTRY_PATH overrides channel_registry_path field."""
        os.environ["AKGENTIC_CHANNEL_REGISTRY_PATH"] = "/tmp/reg.yaml"
        try:
            settings = CommunitySettings()
            assert settings.channel_registry_path == Path("/tmp/reg.yaml")
        finally:
            del os.environ["AKGENTIC_CHANNEL_REGISTRY_PATH"]

    def test_field_descriptions_present(self) -> None:
        """All CommunitySettings fields have descriptions."""
        for name, field_info in CommunitySettings.model_fields.items():
            assert field_info.description is not None, f"Field {name} missing description"


class TestShutdownSettings:
    """Tests for shutdown_drain_timeout and shutdown_pre_drain_delay fields."""

    def test_default_shutdown_drain_timeout(self) -> None:
        """Default shutdown_drain_timeout is 30."""
        settings = ServerSettings()
        assert settings.shutdown_drain_timeout == 30

    def test_default_shutdown_pre_drain_delay(self) -> None:
        """Default shutdown_pre_drain_delay is 0."""
        settings = ServerSettings()
        assert settings.shutdown_pre_drain_delay == 0

    def test_shutdown_drain_timeout_from_env(self) -> None:
        """AKGENTIC_SHUTDOWN_DRAIN_TIMEOUT overrides shutdown_drain_timeout field."""
        os.environ["AKGENTIC_SHUTDOWN_DRAIN_TIMEOUT"] = "60"
        try:
            settings = ServerSettings()
            assert settings.shutdown_drain_timeout == 60
        finally:
            del os.environ["AKGENTIC_SHUTDOWN_DRAIN_TIMEOUT"]

    def test_shutdown_pre_drain_delay_from_env(self) -> None:
        """AKGENTIC_SHUTDOWN_PRE_DRAIN_DELAY overrides shutdown_pre_drain_delay field."""
        os.environ["AKGENTIC_SHUTDOWN_PRE_DRAIN_DELAY"] = "5"
        try:
            settings = ServerSettings()
            assert settings.shutdown_pre_drain_delay == 5
        finally:
            del os.environ["AKGENTIC_SHUTDOWN_PRE_DRAIN_DELAY"]

    def test_shutdown_fields_have_descriptions(self) -> None:
        """Both shutdown fields have non-None descriptions."""
        fields = ServerSettings.model_fields
        assert fields["shutdown_drain_timeout"].description is not None
        assert fields["shutdown_pre_drain_delay"].description is not None

    def test_shutdown_drain_timeout_rejects_negative(self) -> None:
        """shutdown_drain_timeout rejects negative values (ge=0 constraint)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="shutdown_drain_timeout"):
            ServerSettings(shutdown_drain_timeout=-1)

    def test_shutdown_pre_drain_delay_rejects_negative(self) -> None:
        """shutdown_pre_drain_delay rejects negative values (ge=0 constraint)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="shutdown_pre_drain_delay"):
            ServerSettings(shutdown_pre_drain_delay=-1)


class TestWsReaderPoolSizeSetting:
    """Tests for ws_reader_pool_size field (story 21-2)."""

    def test_default_ws_reader_pool_size(self) -> None:
        """Default ws_reader_pool_size is 256."""
        settings = ServerSettings()
        assert settings.ws_reader_pool_size == 256

    def test_ws_reader_pool_size_from_env(self) -> None:
        """AKGENTIC_WS_READER_POOL_SIZE overrides ws_reader_pool_size field."""
        os.environ["AKGENTIC_WS_READER_POOL_SIZE"] = "64"
        try:
            settings = ServerSettings()
            assert settings.ws_reader_pool_size == 64
        finally:
            del os.environ["AKGENTIC_WS_READER_POOL_SIZE"]

    def test_ws_reader_pool_size_has_description(self) -> None:
        """ws_reader_pool_size has a non-None description."""
        field = ServerSettings.model_fields["ws_reader_pool_size"]
        assert field.description is not None

    def test_ws_reader_pool_size_rejects_zero(self) -> None:
        """ws_reader_pool_size rejects 0 (ge=1 constraint)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="ws_reader_pool_size"):
            ServerSettings(ws_reader_pool_size=0)

    def test_ws_reader_pool_size_rejects_negative(self) -> None:
        """ws_reader_pool_size rejects negative values."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="ws_reader_pool_size"):
            ServerSettings(ws_reader_pool_size=-1)
