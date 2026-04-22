"""Tests for HTTP client with retry logic and LLM provider factory (providers.py)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel
from pydantic_ai import NativeOutput
from pydantic_ai.retries import AsyncTenacityTransport

from akgentic.llm.config import ModelConfig
from akgentic.llm.providers import (
    _is_retryable_http_error,
    _supports_native_output,
    create_http_client,
    create_model,
    create_model_settings,
    get_output_type,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_status_error(
    status_code: int, headers: dict[str, str] | None = None
) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError for a given status code."""
    headers = headers or {}
    request = httpx.Request("GET", "https://api.example.com/test")
    response = httpx.Response(status_code=status_code, headers=headers, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


class _TestModel(BaseModel):
    """Test Pydantic model for output type tests."""

    name: str
    value: int


# ---------------------------------------------------------------------------
# _supports_native_output unit tests
# ---------------------------------------------------------------------------


class TestSupportsNativeOutput:
    """Unit tests for _supports_native_output predicate (AC: 1)."""

    def test_openai_supports_native_output(self) -> None:
        """OpenAI provider supports native structured output."""
        config = ModelConfig(provider="openai", model="gpt-4o")
        assert _supports_native_output(config) is True

    def test_azure_supports_native_output(self) -> None:
        """Azure provider supports native structured output."""
        config = ModelConfig(provider="azure", model="gpt-4o")
        assert _supports_native_output(config) is True

    def test_anthropic_supports_native_output(self) -> None:
        """Anthropic provider supports native structured output."""
        config = ModelConfig(provider="anthropic", model="claude-3-5-sonnet-20241022")
        assert _supports_native_output(config) is True

    def test_nvidia_openai_model_supports_native_output(self) -> None:
        """NVIDIA with openai/* prefix supports native output."""
        config = ModelConfig(provider="nvidia", model="openai/gpt-4o-mini")
        assert _supports_native_output(config) is True

    def test_nvidia_non_openai_model_no_native_output(self) -> None:
        """NVIDIA with non-openai models does not support native output."""
        config = ModelConfig(provider="nvidia", model="meta/llama-3.1-70b-instruct")
        assert _supports_native_output(config) is False

    def test_google_gla_no_native_output(self) -> None:
        """Google GLA provider does not support native output."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash")
        assert _supports_native_output(config) is False

    def test_mistral_no_native_output(self) -> None:
        """Mistral provider does not support native output."""
        config = ModelConfig(provider="mistral", model="mistral-large-latest")
        assert _supports_native_output(config) is False


# ---------------------------------------------------------------------------
# get_output_type unit tests
# ---------------------------------------------------------------------------


class TestGetOutputType:
    """Unit tests for get_output_type function (AC: 2)."""

    def test_openai_wraps_with_native_output(self) -> None:
        """OpenAI returns NativeOutput wrapper."""
        config = ModelConfig(provider="openai", model="gpt-4o")
        result = get_output_type(config, _TestModel)
        assert isinstance(result, NativeOutput)

    def test_azure_wraps_with_native_output(self) -> None:
        """Azure returns NativeOutput wrapper."""
        config = ModelConfig(provider="azure", model="gpt-4o")
        result = get_output_type(config, _TestModel)
        assert isinstance(result, NativeOutput)

    def test_anthropic_wraps_with_native_output(self) -> None:
        """Anthropic returns NativeOutput wrapper."""
        config = ModelConfig(provider="anthropic", model="claude-3-5-sonnet-20241022")
        result = get_output_type(config, _TestModel)
        assert isinstance(result, NativeOutput)

    def test_nvidia_openai_wraps_with_native_output(self) -> None:
        """NVIDIA with openai/* prefix returns NativeOutput wrapper."""
        config = ModelConfig(provider="nvidia", model="openai/gpt-4o-mini")
        result = get_output_type(config, _TestModel)
        assert isinstance(result, NativeOutput)

    def test_google_gla_returns_raw_type(self) -> None:
        """Google GLA returns raw type without wrapper."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash")
        result = get_output_type(config, _TestModel)
        assert result is _TestModel

    def test_mistral_returns_raw_type(self) -> None:
        """Mistral returns raw type without wrapper."""
        config = ModelConfig(provider="mistral", model="mistral-large-latest")
        result = get_output_type(config, _TestModel)
        assert result is _TestModel

    def test_nvidia_non_openai_returns_raw_type(self) -> None:
        """NVIDIA with non-openai models returns raw type."""
        config = ModelConfig(provider="nvidia", model="meta/llama-3.1-70b-instruct")
        result = get_output_type(config, _TestModel)
        assert result is _TestModel


# ---------------------------------------------------------------------------
# create_model_settings unit tests
# ---------------------------------------------------------------------------


class TestCreateModelSettings:
    """Unit tests for create_model_settings function (AC: 3)."""

    def test_native_provider_no_parallel_tool_calls_override(self) -> None:
        """Native providers don't get parallel_tool_calls disabled."""
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.7)
        settings = create_model_settings(config)
        assert settings is not None
        assert settings.get("temperature") == 0.7
        assert "parallel_tool_calls" not in settings

    def test_non_native_provider_disables_parallel_tool_calls(self) -> None:
        """Non-native providers get parallel_tool_calls=False."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash", temperature=0.5)
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["parallel_tool_calls"] is False
        assert settings["temperature"] == 0.5

    def test_mistral_disables_parallel_tool_calls(self) -> None:
        """Mistral gets parallel_tool_calls=False."""
        config = ModelConfig(provider="mistral", model="mistral-large-latest")
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["parallel_tool_calls"] is False

    def test_nvidia_non_openai_disables_parallel_tool_calls(self) -> None:
        """NVIDIA non-openai models get parallel_tool_calls=False."""
        config = ModelConfig(
            provider="nvidia", model="meta/llama-3.1-70b-instruct", max_tokens=1000
        )
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["parallel_tool_calls"] is False
        assert settings["max_tokens"] == 1000

    def test_preserves_temperature(self) -> None:
        """Settings preserve temperature from config."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash", temperature=0.9)
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["temperature"] == 0.9

    def test_preserves_max_tokens(self) -> None:
        """Settings preserve max_tokens from config."""
        config = ModelConfig(provider="mistral", model="mistral-large-latest", max_tokens=2048)
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["max_tokens"] == 2048

    def test_preserves_seed(self) -> None:
        """Settings preserve seed from config."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash", seed=42)
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["seed"] == 42

    def test_all_params_none_returns_none(self) -> None:
        """Returns None when no parameters are set for native provider."""
        config = ModelConfig(provider="openai", model="gpt-4o")
        settings = create_model_settings(config)
        assert settings is None

    def test_all_params_none_non_native_returns_settings_with_parallel_false(self) -> None:
        """Returns settings with parallel_tool_calls=False for non-native even with no other params."""
        config = ModelConfig(provider="google-gla", model="gemini-2.0-flash")
        settings = create_model_settings(config)
        assert settings is not None
        assert settings["parallel_tool_calls"] is False


# ---------------------------------------------------------------------------
# _is_retryable_http_error unit tests
# ---------------------------------------------------------------------------


class TestIsRetryableHttpError:
    """Unit tests for the _is_retryable_http_error predicate."""

    def test_429_is_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(429)) is True

    def test_500_is_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(500)) is True

    def test_502_is_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(502)) is True

    def test_503_is_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(503)) is True

    def test_504_is_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(504)) is True

    def test_400_not_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(400)) is False

    def test_401_not_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(401)) is False

    def test_403_not_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(403)) is False

    def test_404_not_retryable(self) -> None:
        assert _is_retryable_http_error(_make_status_error(404)) is False

    def test_non_http_error_not_retryable(self) -> None:
        assert _is_retryable_http_error(ValueError("boom")) is False

    def test_connect_error_is_retryable(self) -> None:
        assert _is_retryable_http_error(httpx.ConnectError("refused")) is True

    def test_remote_protocol_error_is_retryable(self) -> None:
        assert _is_retryable_http_error(httpx.RemoteProtocolError("peer closed")) is True

    def test_read_error_is_retryable(self) -> None:
        assert _is_retryable_http_error(httpx.ReadError("read failed")) is True

    def test_write_error_is_retryable(self) -> None:
        assert _is_retryable_http_error(httpx.WriteError("write failed")) is True

    def test_timeout_not_retryable(self) -> None:
        assert _is_retryable_http_error(httpx.TimeoutException("timeout")) is False


# ---------------------------------------------------------------------------
# create_http_client tests
# ---------------------------------------------------------------------------


class TestCreateHttpClient:
    """Tests for create_http_client factory function."""

    def test_returns_async_client(self) -> None:
        """create_http_client returns an httpx.AsyncClient."""
        client = create_http_client()
        assert isinstance(client, httpx.AsyncClient)

    def test_transport_is_async_tenacity_transport(self) -> None:
        """Client uses pydantic-ai's AsyncTenacityTransport."""
        client = create_http_client()
        assert isinstance(client._transport, AsyncTenacityTransport)

    def test_custom_timeout(self) -> None:
        """Client timeout reflects timeout_s parameter."""
        client = create_http_client(timeout_s=45.0)
        assert client._timeout.read == 45.0

    def test_default_timeout(self) -> None:
        """Client uses 120s default timeout."""
        client = create_http_client()
        assert client._timeout.read == 120.0

    @pytest.mark.asyncio
    async def test_client_usable_as_context_manager(self) -> None:
        """Client works as an async context manager."""
        async with create_http_client() as client:
            assert isinstance(client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_retry_on_429_then_success(self) -> None:
        """Client retries on 429, succeeds on third attempt."""
        request = httpx.Request("GET", "https://api.example.com/test")
        responses = [
            httpx.Response(429, request=request),
            httpx.Response(429, request=request),
            httpx.Response(200, request=request),
        ]
        call_count = 0

        async def fake_handle(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        client = create_http_client(max_attempts=5, exp_multiplier=0.01, exp_max_s=0.05)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(
            client._transport.wrapped, "handle_async_request", side_effect=fake_handle
        ):
            response = await client.get("https://api.example.com/test")

        assert response.status_code == 200
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_500_then_success(self) -> None:
        """Client retries on 500, succeeds on second attempt."""
        request = httpx.Request("GET", "https://api.example.com/test")
        responses = [
            httpx.Response(500, request=request),
            httpx.Response(200, request=request),
        ]
        call_count = 0

        async def fake_handle(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        client = create_http_client(max_attempts=3, exp_multiplier=0.01, exp_max_s=0.05)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(
            client._transport.wrapped, "handle_async_request", side_effect=fake_handle
        ):
            response = await client.get("https://api.example.com/test")

        assert response.status_code == 200
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_enforced(self) -> None:
        """Client raises after max_attempts exhausted."""
        call_count = 0

        async def always_500(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, request=req)

        client = create_http_client(max_attempts=3, exp_multiplier=0.01, exp_max_s=0.05)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(
            client._transport.wrapped, "handle_async_request", side_effect=always_500
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("https://api.example.com/test")

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self) -> None:
        """Client raises immediately on 400 (no retry)."""
        call_count = 0

        async def always_400(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, request=req)

        client = create_http_client(max_attempts=5, exp_multiplier=0.01, exp_max_s=0.05)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(
            client._transport.wrapped, "handle_async_request", side_effect=always_400
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("https://api.example.com/test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_404(self) -> None:
        """Client raises immediately on 404 (no retry)."""
        call_count = 0

        async def always_404(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(404, request=req)

        client = create_http_client(max_attempts=5, exp_multiplier=0.01, exp_max_s=0.05)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(
            client._transport.wrapped, "handle_async_request", side_effect=always_404
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("https://api.example.com/test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_success_without_retry(self) -> None:
        """Client returns 200 immediately with no retries."""
        call_count = 0

        async def success(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, request=req)

        client = create_http_client(max_attempts=5)
        assert isinstance(client._transport, AsyncTenacityTransport)
        with patch.object(client._transport.wrapped, "handle_async_request", side_effect=success):
            response = await client.get("https://api.example.com/test")

        assert response.status_code == 200
        assert call_count == 1


# ---------------------------------------------------------------------------
# create_model factory tests
# ---------------------------------------------------------------------------


class TestCreateModel:
    """Unit tests for the create_model factory function."""

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_create_openai_model(self, mock_model_cls, mock_provider_cls) -> None:
        """create_model returns an OpenAIChatModel for provider='openai'."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.7, max_tokens=1000)

        result = create_model(config, http_client=mock_client)

        mock_model_cls.assert_called_once()
        call_kwargs = mock_model_cls.call_args.kwargs
        assert call_kwargs["model_name"] == "gpt-4o"
        assert result is mock_model_cls.return_value

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_openai_model_settings_temperature(self, mock_model_cls, mock_provider_cls) -> None:
        """Temperature is passed via OpenAIChatModelSettings for OpenAI."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o", temperature=0.5)

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs["settings"]
        assert settings is not None
        assert settings["temperature"] == 0.5

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_openai_reasoning_effort(self, mock_model_cls, mock_provider_cls) -> None:
        """reasoning_effort maps to openai_reasoning_effort in settings."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="o1", reasoning_effort="high")

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs["settings"]
        assert settings is not None
        assert settings["openai_reasoning_effort"] == "high"

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_openai_seed_passed_in_settings(self, mock_model_cls, mock_provider_cls) -> None:
        """seed is included in OpenAIChatModelSettings when set."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o", seed=42)

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs["settings"]
        assert settings is not None
        assert settings["seed"] == 42

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_openai_http_client_passed_to_provider(self, mock_model_cls, mock_provider_cls) -> None:
        """http_client is passed to OpenAIProvider."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o")

        create_model(config, http_client=mock_client)

        mock_provider_cls.assert_called_once_with(http_client=mock_client)

    # ------------------------------------------------------------------
    # Azure
    # ------------------------------------------------------------------

    @patch.dict("os.environ", {"AZURE_OPENAI_ENDPOINT": "https://my-azure.openai.azure.com"})
    @patch("pydantic_ai.providers.azure.AzureProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_create_azure_model(self, mock_model_cls, mock_provider_cls) -> None:
        """create_model returns an OpenAIChatModel for provider='azure'."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="azure", model="gpt-4o")

        result = create_model(config, http_client=mock_client)

        mock_model_cls.assert_called_once()
        assert mock_model_cls.call_args.kwargs["model_name"] == "gpt-4o"
        assert result is mock_model_cls.return_value

    @patch.dict("os.environ", {"AZURE_OPENAI_ENDPOINT": "https://my-azure.openai.azure.com"})
    @patch("pydantic_ai.providers.azure.AzureProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_azure_uses_endpoint_from_env(self, mock_model_cls, mock_provider_cls) -> None:
        """Azure provider uses AZURE_OPENAI_ENDPOINT environment variable."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="azure", model="gpt-4o")

        create_model(config, http_client=mock_client)

        mock_provider_cls.assert_called_once_with(
            azure_endpoint="https://my-azure.openai.azure.com",
            http_client=mock_client,
        )

    @patch.dict("os.environ", {}, clear=False)
    def test_azure_raises_when_endpoint_unset(self) -> None:
        """Azure provider raises ValueError when AZURE_OPENAI_ENDPOINT is not set."""
        import os

        os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="azure", model="gpt-4o")

        with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
            create_model(config, http_client=mock_client)

    # ------------------------------------------------------------------
    # Anthropic
    # ------------------------------------------------------------------

    @patch("pydantic_ai.providers.anthropic.AnthropicProvider")
    @patch("pydantic_ai.models.anthropic.AnthropicModel")
    def test_create_anthropic_model(self, mock_model_cls, mock_provider_cls) -> None:
        """create_model returns an AnthropicModel for provider='anthropic'."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",
            temperature=0.5,
        )

        result = create_model(config, http_client=mock_client)

        mock_model_cls.assert_called_once()
        assert mock_model_cls.call_args.kwargs["model_name"] == "claude-3-5-sonnet-20241022"
        assert result is mock_model_cls.return_value

    @patch("pydantic_ai.providers.anthropic.AnthropicProvider")
    @patch("pydantic_ai.models.anthropic.AnthropicModel")
    def test_anthropic_http_client_passed_to_provider(
        self, mock_model_cls, mock_provider_cls
    ) -> None:
        """http_client is passed to AnthropicProvider."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="anthropic", model="claude-3-5-sonnet-20241022")

        create_model(config, http_client=mock_client)

        mock_provider_cls.assert_called_once_with(http_client=mock_client)

    # ------------------------------------------------------------------
    # Google
    # ------------------------------------------------------------------

    def test_create_google_model(self) -> None:
        """create_model returns a GoogleModel for provider='google-gla'."""
        import sys

        mock_google_model = MagicMock()
        mock_google_provider = MagicMock()
        mock_models_google = MagicMock(GoogleModel=mock_google_model)
        mock_providers_google = MagicMock(GoogleProvider=mock_google_provider)

        with patch.dict(
            sys.modules,
            {
                "pydantic_ai.models.google": mock_models_google,
                "pydantic_ai.providers.google": mock_providers_google,
            },
        ):
            mock_client = MagicMock(spec=httpx.AsyncClient)
            config = ModelConfig(provider="google-gla", model="gemini-2.0-flash")

            result = create_model(config, http_client=mock_client)

        mock_google_model.assert_called_once()
        assert mock_google_model.call_args.kwargs["model_name"] == "gemini-2.0-flash"
        assert result is mock_google_model.return_value

    def test_google_http_client_passed_to_provider(self) -> None:
        """http_client is passed to GoogleProvider."""
        import sys

        mock_google_model = MagicMock()
        mock_google_provider = MagicMock()
        mock_models_google = MagicMock(GoogleModel=mock_google_model)
        mock_providers_google = MagicMock(GoogleProvider=mock_google_provider)

        with patch.dict(
            sys.modules,
            {
                "pydantic_ai.models.google": mock_models_google,
                "pydantic_ai.providers.google": mock_providers_google,
            },
        ):
            mock_client = MagicMock(spec=httpx.AsyncClient)
            config = ModelConfig(provider="google-gla", model="gemini-2.0-flash")

            create_model(config, http_client=mock_client)

        mock_google_provider.assert_called_once_with(http_client=mock_client)

    # ------------------------------------------------------------------
    # Mistral
    # ------------------------------------------------------------------

    @patch("pydantic_ai.providers.mistral.MistralProvider")
    @patch("pydantic_ai.models.mistral.MistralModel")
    def test_create_mistral_model(self, mock_model_cls, mock_provider_cls) -> None:
        """create_model returns a MistralModel for provider='mistral'."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="mistral", model="mistral-large-latest")

        result = create_model(config, http_client=mock_client)

        mock_model_cls.assert_called_once()
        assert mock_model_cls.call_args.kwargs["model_name"] == "mistral-large-latest"
        assert result is mock_model_cls.return_value

    @patch("pydantic_ai.providers.mistral.MistralProvider")
    @patch("pydantic_ai.models.mistral.MistralModel")
    def test_mistral_http_client_passed_to_provider(
        self, mock_model_cls, mock_provider_cls
    ) -> None:
        """http_client is passed to MistralProvider."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="mistral", model="mistral-large-latest")

        create_model(config, http_client=mock_client)

        mock_provider_cls.assert_called_once_with(http_client=mock_client)

    # ------------------------------------------------------------------
    # NVIDIA
    # ------------------------------------------------------------------

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_create_nvidia_model(self, mock_model_cls, mock_provider_cls) -> None:
        """create_model returns an OpenAIChatModel for provider='nvidia'."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="nvidia", model="meta/llama-3.1-70b-instruct")

        result = create_model(config, http_client=mock_client)

        mock_model_cls.assert_called_once()
        assert mock_model_cls.call_args.kwargs["model_name"] == "meta/llama-3.1-70b-instruct"
        assert result is mock_model_cls.return_value

    @patch.dict("os.environ", {"NVIDIA_BASE_URL": "https://custom.nvidia.com/v1"})
    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_nvidia_uses_base_url_from_env(self, mock_model_cls, mock_provider_cls) -> None:
        """NVIDIA provider uses NVIDIA_BASE_URL environment variable."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="nvidia", model="meta/llama-3.1-70b-instruct")

        create_model(config, http_client=mock_client)

        mock_provider_cls.assert_called_once_with(
            base_url="https://custom.nvidia.com/v1",
            http_client=mock_client,
        )

    @patch.dict("os.environ", {}, clear=False)
    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_nvidia_default_base_url(self, mock_model_cls, mock_provider_cls) -> None:
        """NVIDIA provider defaults to integrate.api.nvidia.com when env var unset."""
        import os

        os.environ.pop("NVIDIA_BASE_URL", None)
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="nvidia", model="meta/llama-3.1-70b-instruct")

        create_model(config, http_client=mock_client)

        call_kwargs = mock_provider_cls.call_args.kwargs
        assert call_kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_unknown_provider_raises_value_error(self) -> None:
        """Unknown provider raises ValueError with helpful message."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        # ModelConfig validates provider via Literal; bypass with object
        config = ModelConfig(provider="openai", model="gpt-4o")
        object.__setattr__(config, "provider", "unknown-provider")

        with pytest.raises(ValueError) as exc_info:
            create_model(config, http_client=mock_client)

        assert "Unsupported provider: unknown-provider" in str(exc_info.value)
        assert "Supported providers:" in str(exc_info.value)

    # ------------------------------------------------------------------
    # HTTP client auto-creation
    # ------------------------------------------------------------------

    @patch("akgentic.llm.providers.create_http_client")
    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_http_client_created_when_none(
        self, mock_model_cls, mock_provider_cls, mock_create_client
    ) -> None:
        """create_http_client() is called when http_client=None and passed to provider."""
        mock_create_client.return_value = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o")

        create_model(config)  # no http_client argument

        mock_create_client.assert_called_once()
        mock_provider_cls.assert_called_once_with(http_client=mock_create_client.return_value)

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_provided_http_client_not_replaced(self, mock_model_cls, mock_provider_cls) -> None:
        """Provided http_client is passed through without creating a new one."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o")

        with patch("akgentic.llm.providers.create_http_client") as mock_create_client:
            create_model(config, http_client=mock_client)
            mock_create_client.assert_not_called()

        mock_provider_cls.assert_called_once_with(http_client=mock_client)

    # ------------------------------------------------------------------
    # Optional parameters
    # ------------------------------------------------------------------

    @patch("pydantic_ai.providers.openai.OpenAIProvider")
    @patch("pydantic_ai.models.openai.OpenAIChatModel")
    def test_none_optional_params_produce_no_settings(
        self, mock_model_cls, mock_provider_cls
    ) -> None:
        """When temperature/max_tokens/seed are all None, settings=None is passed."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="openai", model="gpt-4o")
        # Defaults are all None for optional params

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs.get("settings")
        assert settings is None

    @patch("pydantic_ai.providers.anthropic.AnthropicProvider")
    @patch("pydantic_ai.models.anthropic.AnthropicModel")
    def test_max_tokens_passed_in_settings(self, mock_model_cls, mock_provider_cls) -> None:
        """max_tokens is included in ModelSettings when set."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(
            provider="anthropic", model="claude-3-5-sonnet-20241022", max_tokens=512
        )

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs["settings"]
        assert settings is not None
        assert settings["max_tokens"] == 512

    @patch("pydantic_ai.providers.mistral.MistralProvider")
    @patch("pydantic_ai.models.mistral.MistralModel")
    def test_seed_passed_in_settings(self, mock_model_cls, mock_provider_cls) -> None:
        """seed is included in ModelSettings when set."""
        mock_client = MagicMock(spec=httpx.AsyncClient)
        config = ModelConfig(provider="mistral", model="mistral-large-latest", seed=42)

        create_model(config, http_client=mock_client)

        settings = mock_model_cls.call_args.kwargs["settings"]
        assert settings is not None
        assert settings["seed"] == 42
