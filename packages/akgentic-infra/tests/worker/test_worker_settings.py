"""Tests for WorkerSettings configuration model."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from akgentic.infra.worker.settings import WorkerSettings


class TestDefaultValues:
    """WorkerSettings must have sensible defaults matching ADR-017 Decision 2."""

    def test_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AKGENTIC_WORKER_HOST", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_PORT", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_LOG_LEVEL", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_WORKSPACES_ROOT", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_SHUTDOWN_DRAIN_TIMEOUT", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_SHUTDOWN_PRE_DRAIN_DELAY", raising=False)
        monkeypatch.delenv("AKGENTIC_WORKER_WORKER_LABELS", raising=False)
        settings = WorkerSettings()
        assert settings.host == "0.0.0.0"
        assert settings.port == 8001
        assert settings.log_level == "INFO"
        assert settings.workspaces_root == Path("/data/workspaces")
        assert settings.shutdown_drain_timeout == 30
        assert settings.shutdown_pre_drain_delay == 0
        assert settings.worker_labels == {}


class TestEnvVarOverride:
    """WorkerSettings must load overrides from AKGENTIC_WORKER_ prefixed env vars."""

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AKGENTIC_WORKER_HOST", "127.0.0.1")
        monkeypatch.setenv("AKGENTIC_WORKER_PORT", "9999")
        monkeypatch.setenv("AKGENTIC_WORKER_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("AKGENTIC_WORKER_WORKSPACES_ROOT", "/tmp/ws")
        monkeypatch.setenv("AKGENTIC_WORKER_SHUTDOWN_DRAIN_TIMEOUT", "60")
        monkeypatch.setenv("AKGENTIC_WORKER_SHUTDOWN_PRE_DRAIN_DELAY", "5")
        monkeypatch.setenv(
            "AKGENTIC_WORKER_WORKER_LABELS", '{"gpu": "true", "region": "eu"}'
        )

        settings = WorkerSettings()
        assert settings.host == "127.0.0.1"
        assert settings.port == 9999
        assert settings.log_level == "DEBUG"
        assert settings.workspaces_root == Path("/tmp/ws")
        assert settings.shutdown_drain_timeout == 60
        assert settings.shutdown_pre_drain_delay == 5
        assert settings.worker_labels == {"gpu": "true", "region": "eu"}


class TestLogLevelNormalization:
    """Log level validator must normalize case and reject invalid values."""

    def test_log_level_normalizes_to_uppercase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AKGENTIC_WORKER_LOG_LEVEL", "debug")
        settings = WorkerSettings()
        assert settings.log_level == "DEBUG"

    def test_log_level_invalid_falls_back_to_info(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AKGENTIC_WORKER_LOG_LEVEL", "bogus")
        with pytest.warns(UserWarning, match="Invalid AKGENTIC_WORKER_LOG_LEVEL"):
            settings = WorkerSettings()
        assert settings.log_level == "INFO"


class TestConstraintValidation:
    """Constrained fields must reject invalid values."""

    def test_shutdown_drain_timeout_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="shutdown_drain_timeout"):
            WorkerSettings(shutdown_drain_timeout=-1)

    def test_shutdown_pre_drain_delay_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="shutdown_pre_drain_delay"):
            WorkerSettings(shutdown_pre_drain_delay=-1)


class TestModelStructure:
    """WorkerSettings model metadata and structure."""

    def test_is_base_settings_subclass(self) -> None:
        from pydantic_settings import BaseSettings

        assert issubclass(WorkerSettings, BaseSettings)

    def test_field_descriptions_present(self) -> None:
        for name, field_info in WorkerSettings.model_fields.items():
            assert field_info.description is not None, f"Field {name} missing description"

    def test_has_only_tier_agnostic_fields(self) -> None:
        fields = set(WorkerSettings.model_fields.keys())
        expected = {
            "host",
            "port",
            "log_level",
            "workspaces_root",
            "shutdown_drain_timeout",
            "shutdown_pre_drain_delay",
            "worker_labels",
        }
        assert fields == expected, (
            f"WorkerSettings fields mismatch: got {fields}, expected {expected}"
        )
