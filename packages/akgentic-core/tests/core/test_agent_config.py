"""Tests for agent configuration classes.

Tests BaseConfig, PrivateConfig, AgentConfig, and ReadOnlyField
per Story 1.4 acceptance criteria.
"""

from __future__ import annotations

import uuid

from akgentic.core.agent_config import (
    AgentConfig,
    BaseConfig,
    ReadOnlyField,
)


class TestBaseConfig:
    """Tests for BaseConfig class (AC: 1, 5)."""

    def test_default_instantiation(self) -> None:
        """BaseConfig() creates instance with all None defaults."""
        config = BaseConfig()
        assert config.name == ""
        assert config.role == ""
        assert config.squad_id is None

    def test_explicit_values(self) -> None:
        """BaseConfig accepts explicit field values."""
        config = BaseConfig(name="test-agent", role="worker")
        assert config.name == "test-agent"
        assert config.role == "worker"
        assert config.squad_id is None

    def test_with_squad_id(self) -> None:
        """BaseConfig accepts squad_id UUID."""
        squad = uuid.uuid4()
        config = BaseConfig(name="agent", role="processor", squad_id=squad)
        assert config.squad_id == squad

    def test_serialization_to_dict(self) -> None:
        """BaseConfig serializes to dict for StartMessage payload (AC: 7)."""
        squad = uuid.uuid4()
        config = BaseConfig(name="worker-1", role="processor", squad_id=squad)
        data = config.model_dump()
        assert isinstance(data, dict)
        assert data["name"] == "worker-1"
        assert data["role"] == "processor"
        # UUID serialized as string
        assert "__model__" in data  # SerializableBaseModel adds this


class TestCustomConfig:
    """Tests for extending BaseConfig (AC: 2, 5)."""

    def test_custom_config_extends_base(self) -> None:
        """Custom config classes can extend BaseConfig with additional fields."""

        class WorkerConfig(BaseConfig):
            max_tasks: int = 10
            timeout_seconds: float = 30.0

        config = WorkerConfig(name="worker", max_tasks=5)
        assert config.name == "worker"
        assert config.max_tasks == 5
        assert config.timeout_seconds == 30.0
        assert config.role == ""

    def test_custom_config_serialization(self) -> None:
        """Custom configs serialize including extended fields."""

        class WorkerConfig(BaseConfig):
            max_tasks: int = 10

        config = WorkerConfig(name="worker", max_tasks=5)
        data = config.model_dump()
        assert data["name"] == "worker"
        assert data["max_tasks"] == 5


class TestAgentConfigAlias:
    """Tests for AgentConfig type alias (AC: 2)."""

    def test_agent_config_is_base_config(self) -> None:
        """AgentConfig type alias resolves to BaseConfig."""
        assert AgentConfig is BaseConfig

    def test_agent_config_instantiation(self) -> None:
        """AgentConfig can be used as BaseConfig."""
        config: AgentConfig = AgentConfig(name="agent", role="worker")
        assert isinstance(config, BaseConfig)


class TestReadOnlyField:
    """Tests for ReadOnlyField helper function."""

    def test_readonly_field_sets_schema_extra(self) -> None:
        """ReadOnlyField sets json_schema_extra with readOnly=True."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            value: str = ReadOnlyField(default="test")

        schema = TestModel.model_json_schema()
        assert schema["properties"]["value"].get("readOnly") is True

    def test_readonly_field_preserves_kwargs(self) -> None:
        """ReadOnlyField preserves other Field kwargs."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            value: str = ReadOnlyField(default="default_value", description="A test field")

        schema = TestModel.model_json_schema()
        assert schema["properties"]["value"]["default"] == "default_value"
        assert schema["properties"]["value"]["description"] == "A test field"
