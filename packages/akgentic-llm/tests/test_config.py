"""Tests for configuration models."""

import pytest
from pydantic import ValidationError

from akgentic.llm.config import ModelConfig, ReactAgentConfig, RuntimeConfig, UsageLimits


class TestModelConfig:
    """Test ModelConfig model."""

    def test_valid_config(self):
        """Test valid configuration creation."""
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.7, max_tokens=1000)
        assert config.provider == "openai"
        assert config.model == "gpt-4o"
        assert config.temperature == 0.7
        assert config.max_tokens == 1000

    def test_temperature_validation_too_low(self):
        """Test temperature below 0.0 raises error."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="openai", model="gpt-4o", temperature=-0.1)

    def test_temperature_validation_too_high(self):
        """Test temperature above 2.0 raises error."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="openai", model="gpt-4o", temperature=2.1)

    def test_temperature_boundary_low(self):
        """Test temperature at lower boundary (0.0) is valid."""
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.0)
        assert config.temperature == 0.0

    def test_temperature_boundary_high(self):
        """Test temperature at upper boundary (2.0) is valid."""
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=2.0)
        assert config.temperature == 2.0

    def test_optional_fields_none(self):
        """Test optional fields can be None."""
        config = ModelConfig(provider="openai", model="gpt-4o")
        assert config.temperature is None
        assert config.max_tokens is None
        assert config.seed is None
        assert config.reasoning_effort is None

    def test_all_providers(self):
        """Test all supported providers."""
        providers = ["openai", "azure", "nvidia", "google-gla", "mistral", "anthropic"]
        for provider in providers:
            config = ModelConfig(provider=provider, model="test-model")  # type: ignore
            assert config.provider == provider

    def test_invalid_provider(self):
        """Test invalid provider raises error."""
        with pytest.raises(ValidationError):
            ModelConfig(provider="invalid", model="test-model")  # type: ignore

    def test_reasoning_effort_values(self):
        """Test reasoning effort valid values."""
        for effort in ["low", "medium", "high"]:
            config = ModelConfig(provider="openai", model="gpt-4o", reasoning_effort=effort)  # type: ignore
            assert config.reasoning_effort == effort

    def test_serialization(self):
        """Test model serialization."""
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.7, seed=42)
        data = config.model_dump()
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o"
        assert data["temperature"] == 0.7
        assert data["seed"] == 42

    def test_json_serialization(self):
        """Test JSON serialization."""
        config = ModelConfig(
            provider="anthropic", model="claude-3-5-sonnet-20241022", temperature=0.7
        )
        json_str = config.model_dump_json()
        assert "anthropic" in json_str
        assert "claude-3-5-sonnet-20241022" in json_str


class TestUsageLimits:
    """Test UsageLimits model."""

    def test_all_limits_none(self):
        """Test limits can be None (unlimited)."""
        limits = UsageLimits(
            request_limit=None,
            tool_calls_limit=None,
            input_tokens_limit=None,
            output_tokens_limit=None,
            total_tokens_limit=None,
        )
        assert limits.request_limit is None
        assert limits.tool_calls_limit is None
        assert limits.input_tokens_limit is None
        assert limits.output_tokens_limit is None
        assert limits.total_tokens_limit is None

    def test_specific_limits(self):
        """Test setting specific limits."""
        limits = UsageLimits(request_limit=10, total_tokens_limit=5000)
        assert limits.request_limit == 10
        assert limits.total_tokens_limit == 5000

    def test_all_limits_set(self):
        """Test all limits can be set."""
        limits = UsageLimits(
            request_limit=100,
            tool_calls_limit=50,
            input_tokens_limit=2000,
            output_tokens_limit=1000,
            total_tokens_limit=3000,
        )
        assert limits.request_limit == 100
        assert limits.tool_calls_limit == 50
        assert limits.input_tokens_limit == 2000
        assert limits.output_tokens_limit == 1000
        assert limits.total_tokens_limit == 3000

    def test_default_request_limit(self):
        """Test default request_limit is 50."""
        limits = UsageLimits()
        assert limits.request_limit == 50

    def test_invalid_negative_limit(self):
        """Test negative limits raise error."""
        with pytest.raises(ValidationError):
            UsageLimits(request_limit=-1)

    def test_invalid_zero_limit(self):
        """Test zero limits raise error."""
        with pytest.raises(ValidationError):
            UsageLimits(total_tokens_limit=0)

    def test_serialization(self):
        """Test model serialization."""
        limits = UsageLimits(request_limit=10, total_tokens_limit=5000)
        data = limits.model_dump()
        assert data["request_limit"] == 10
        assert data["total_tokens_limit"] == 5000


class TestAgentRuntimeConfig:
    """Test AgentRuntimeConfig model."""

    def test_defaults(self):
        """Test default values."""
        config = RuntimeConfig()
        assert config.retries == 3
        assert config.end_strategy == "exhaustive"
        assert config.parallel_tool_calls is True
        assert config.http_client_config.timeout == 120.0
        assert config.http_client_config.max_retries == 5
        assert config.http_client_config.backoff_multiplier == 0.5
        assert config.http_client_config.backoff_max == 60.0

    def test_custom_values(self):
        """Test custom configuration."""
        from akgentic.llm.config import HttpClientConfig

        config = RuntimeConfig(
            retries=5,
            end_strategy="early",
            parallel_tool_calls=False,
            http_client_config=HttpClientConfig(timeout=60.0, max_retries=3),
        )
        assert config.retries == 5
        assert config.end_strategy == "early"
        assert config.parallel_tool_calls is False
        assert config.http_client_config.timeout == 60.0
        assert config.http_client_config.max_retries == 3

    def test_invalid_strategy(self):
        """Test invalid end_strategy raises error."""
        with pytest.raises(ValidationError):
            RuntimeConfig(end_strategy="invalid")  # type: ignore

    def test_negative_retries_invalid(self):
        """Test negative retries raise error."""
        with pytest.raises(ValidationError):
            RuntimeConfig(retries=-1)

    def test_zero_retries_valid(self):
        """Test zero retries is valid."""
        config = RuntimeConfig(retries=0)
        assert config.retries == 0

    def test_serialization(self):
        """Test model serialization."""
        from akgentic.llm.config import HttpClientConfig

        config = RuntimeConfig(retries=5, http_client_config=HttpClientConfig(timeout=90.0))
        data = config.model_dump()
        assert data["retries"] == 5
        assert data["http_client_config"]["timeout"] == 90.0


class TestReactAgentConfig:
    """Test ReactAgentConfig model."""

    def test_full_config(self):
        """Test complete agent configuration."""
        from akgentic.llm.config import HttpClientConfig

        config = ReactAgentConfig(
            model_cfg=ModelConfig(provider="openai", model="gpt-4o", temperature=0.7),
            usage_limits=UsageLimits(request_limit=10, total_tokens_limit=5000),
            runtime_cfg=RuntimeConfig(retries=5, http_client_config=HttpClientConfig(timeout=60.0)),
        )
        assert config.model_cfg.provider == "openai"
        assert config.model_cfg.model == "gpt-4o"
        assert config.runtime_cfg.retries == 5
        assert config.runtime_cfg.http_client_config.timeout == 60.0
        assert config.model_cfg.temperature == 0.7
        assert config.usage_limits.request_limit == 10  # type: ignore
        assert config.usage_limits.total_tokens_limit == 5000  # type: ignore
        assert config.runtime_cfg.retries == 5

    def test_defaults(self):
        """Test default values."""
        config = ReactAgentConfig()
        assert config.model_cfg.provider == "openai"
        assert config.model_cfg.model == "gpt-5.2"
        assert config.usage_limits is not None
        assert config.usage_limits.request_limit == 50
        assert config.runtime_cfg.retries == 3
        assert config.runtime_cfg.http_client_config.timeout == 120.0

    def test_minimal_config(self):
        """Test minimal configuration."""
        config = ReactAgentConfig(
            model_cfg=ModelConfig(provider="anthropic", model="claude-3-5-sonnet-20241022")
        )
        assert config.model_cfg.provider == "anthropic"
        assert config.model_cfg.model == "claude-3-5-sonnet-20241022"
        # Defaults should be set
        assert config.usage_limits is not None
        assert config.usage_limits.request_limit == 50
        assert config.runtime_cfg is not None

    def test_serialization(self):
        """Test model serialization."""
        config = ReactAgentConfig(
            model_cfg=ModelConfig(provider="openai", model="gpt-4o"),
            usage_limits=UsageLimits(request_limit=10),
        )
        data = config.model_dump()
        assert data["model_cfg"]["provider"] == "openai"
        assert data["usage_limits"]["request_limit"] == 10

    def test_json_serialization(self):
        """Test JSON serialization."""
        config = ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4o"))
        json_str = config.model_dump_json()
        assert "openai" in json_str
        assert "gpt-4o" in json_str

    def test_nested_validation(self):
        """Test nested validation errors propagate."""
        with pytest.raises(ValidationError):
            ReactAgentConfig(
                model_cfg=ModelConfig(provider="openai", model="gpt-4o", temperature=3.0)
            )
