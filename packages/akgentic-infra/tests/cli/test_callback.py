"""Tests for the Typer callback in :mod:`akgentic.infra.cli.main`.

Covers story 22.5 ACs #1 / #2 / #3 / #4 / #6 / #7 / #8 branches. All network
I/O runs through :class:`httpx.MockTransport`; all filesystem I/O runs through
``tmp_path`` via the module-level seam constants. No real ``~/.akgentic/``,
no real device-code flow.

The dedicated backward-compat regression test for AC #9 lives in
:mod:`test_callback_no_config_regression`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from akgentic.infra.cli import main as main_module
from akgentic.infra.cli.client import ApiClient
from akgentic.infra.cli.main import app

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.example.com"
DEVICE_AUTH_ENDPOINT = f"{ISSUER}/device-auth"
TOKEN_ENDPOINT = f"{ISSUER}/token"
DISCOVERY_URL_PATH = "/.well-known/openid-configuration"


def _discovery_body() -> bytes:
    return json.dumps(
        {
            "device_authorization_endpoint": DEVICE_AUTH_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
        }
    ).encode("utf-8")


def _device_auth_body() -> dict[str, Any]:
    return {
        "device_code": "dev-code",
        "user_code": "USER-CODE",
        "verification_uri": "https://verify.example.com",
        "verification_uri_complete": "https://verify.example.com?code=USER-CODE",
        "expires_in": 600,
        "interval": 1,
    }


def _token_body() -> dict[str, Any]:
    return {
        "access_token": "access-token-1",
        "refresh_token": "refresh-token-1",
        "expires_in": 3600,
        "token_type": "Bearer",
    }


def _write_noauth_config(path: Path, profile_name: str = "oss") -> None:
    path.write_text(
        f"profiles:\n  {profile_name}:\n    endpoint: https://api.example.com\n",
        encoding="utf-8",
    )


def _write_auth_config(path: Path, profile_name: str = "acme-prod") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        f"      issuer: {ISSUER}\n"
        "      client_id: akgentic-cli\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_overrides_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None
    monkeypatch.delenv("AKGENTIC_PROFILE", raising=False)
    yield
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None


@pytest.fixture
def runner() -> CliRunner:
    # Click 8.3 removed the ``mix_stderr`` kwarg; ``result.stderr`` is always
    # captured separately.
    return CliRunner()


def _mock_api_client() -> MagicMock:
    """A MagicMock that impersonates :class:`ApiClient` for command bodies."""
    mock = MagicMock(spec=ApiClient)
    mock.list_teams.return_value = []
    return mock


# ---------------------------------------------------------------------------
# AC #1 + AC #5 — no config file → legacy path
# ---------------------------------------------------------------------------


def _install_recording_api_client(
    constructed: list[dict[str, Any]],
) -> MagicMock:
    """Replace :class:`ApiClient` in main with a recording factory.

    The returned MagicMock impersonates the ApiClient instance; the caller
    configures its ``list_teams.return_value`` as needed.
    """
    instance = _mock_api_client()

    def factory(*args: Any, **kwargs: Any) -> MagicMock:
        # Normalize positional args to the canonical (base_url, api_key) names.
        kw = dict(kwargs)
        if args:
            kw.setdefault("base_url", args[0])
        if len(args) > 1:
            kw.setdefault("api_key", args[1])
        constructed.append(kw)
        return instance

    return factory  # type: ignore[return-value]


class TestNoConfigLegacyPath:
    def test_no_config_no_flags_uses_legacy_defaults(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Config absent + no flags → ``ApiClient("http://localhost:8000", None)``."""
        main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"
        constructed: list[dict[str, Any]] = []

        with (
            patch(
                "akgentic.infra.cli.main.ApiClient",
                side_effect=_install_recording_api_client(constructed),
            ),
            patch(
                "akgentic.infra.cli.auth.OidcTokenProvider",
                side_effect=AssertionError("OIDC must not engage"),
            ),
        ):
            result = runner.invoke(app, ["team", "list"])

        assert result.exit_code == 0, result.stderr
        assert len(constructed) == 1
        assert constructed[0].get("base_url") == "http://localhost:8000"
        assert constructed[0].get("api_key") is None
        assert constructed[0].get("http_client") is None
        assert main_module._state.profile_name is None

    def test_no_config_with_server_and_api_key_flags(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"
        constructed: list[dict[str, Any]] = []

        with (
            patch(
                "akgentic.infra.cli.main.ApiClient",
                side_effect=_install_recording_api_client(constructed),
            ),
            patch(
                "akgentic.infra.cli.auth.OidcTokenProvider",
                side_effect=AssertionError("OIDC must not engage"),
            ),
        ):
            result = runner.invoke(
                app,
                ["--server", "http://example.com", "--api-key", "xyz", "team", "list"],
            )

        assert result.exit_code == 0, result.stderr
        assert constructed[-1].get("base_url") == "http://example.com"
        assert constructed[-1].get("api_key") == "xyz"


# ---------------------------------------------------------------------------
# AC #2 — --profile with no config → exit 1, stderr message
# ---------------------------------------------------------------------------


class TestProfileFlagWithoutConfig:
    def test_profile_flag_without_config_exits_nonzero(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"
        result = runner.invoke(app, ["--profile", "foo", "team", "list"])
        assert result.exit_code != 0
        assert "no config file" in result.stderr.lower()
        assert "~/.akgentic/config.yaml" in result.stderr


# ---------------------------------------------------------------------------
# AC #1 / #4 — config + api-key override bypasses OIDC entirely
# ---------------------------------------------------------------------------


class TestConfigWithApiKeyOverride:
    def test_api_key_flag_bypasses_oidc(self, runner: CliRunner, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_auth_config(config_path)
        main_module._CONFIG_PATH_OVERRIDE = config_path

        constructed: list[dict[str, Any]] = []

        with (
            patch(
                "akgentic.infra.cli.main.ApiClient",
                side_effect=_install_recording_api_client(constructed),
            ),
            patch(
                "akgentic.infra.cli.auth.OidcTokenProvider",
                side_effect=AssertionError("--api-key must preempt OIDC"),
            ),
            patch(
                "akgentic.infra.cli.main.build_http_client_with_auto_auth",
                side_effect=AssertionError("factory must not be called when --api-key set"),
            ),
        ):
            result = runner.invoke(
                app,
                ["--api-key", "preresolved", "team", "list"],
            )

        assert result.exit_code == 0, result.stderr
        assert constructed[-1].get("api_key") == "preresolved"
        # --server not supplied: use profile.endpoint (pydantic HttpUrl normalizes
        # the value with a trailing slash).
        assert constructed[-1].get("base_url", "").startswith("https://api.example.com")


# ---------------------------------------------------------------------------
# AC #4 — --server override rewires profile-driven base_url
# ---------------------------------------------------------------------------


class TestConfigWithServerOverride:
    def test_server_flag_overrides_profile_endpoint(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config_path = tmp_path / "config.yaml"
        _write_noauth_config(config_path, profile_name="oss-local")
        main_module._CONFIG_PATH_OVERRIDE = config_path

        captured_urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(200, json={"teams": []})

        transport = httpx.MockTransport(handler)

        def factory(profile: Any, **_kwargs: Any) -> httpx.Client:
            # Production factory uses profile.endpoint; test stub mimics it.
            return httpx.Client(base_url=str(profile.endpoint), transport=transport)

        main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory

        result = runner.invoke(app, ["--server", "http://override.local", "team", "list"])
        assert result.exit_code == 0, result.stderr
        # Request should hit the override, not the profile endpoint.
        assert any("override.local" in url for url in captured_urls)
        assert not any("api.example.com" in url for url in captured_urls)


# ---------------------------------------------------------------------------
# AC #2 — --profile flag selects profile via resolve_profile precedence
# ---------------------------------------------------------------------------


class TestProfileFlagSelection:
    def test_profile_flag_selects_named_profile(self, runner: CliRunner, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "profiles:\n"
            "  alpha:\n"
            "    endpoint: http://alpha.local\n"
            "  beta:\n"
            "    endpoint: http://beta.local\n",
            encoding="utf-8",
        )
        main_module._CONFIG_PATH_OVERRIDE = config_path

        seen_endpoints: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_endpoints.append(str(request.url))
            return httpx.Response(200, json={"teams": []})

        transport = httpx.MockTransport(handler)

        def factory(profile: Any, **_kwargs: Any) -> httpx.Client:
            return httpx.Client(base_url=str(profile.endpoint), transport=transport)

        main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory

        result = runner.invoke(app, ["--profile", "beta", "team", "list"])
        assert result.exit_code == 0, result.stderr
        assert any("beta.local" in u for u in seen_endpoints)
        assert main_module._state.profile_name == "beta"


# ---------------------------------------------------------------------------
# AC #8 — malformed config surfaces as non-zero exit with stderr message
# ---------------------------------------------------------------------------


class TestMalformedConfig:
    def test_malformed_config_exits_nonzero(self, runner: CliRunner, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        # "profiles" is required; this will fail validation.
        config_path.write_text("not_profiles: []\n", encoding="utf-8")
        main_module._CONFIG_PATH_OVERRIDE = config_path

        result = runner.invoke(app, ["team", "list"])
        assert result.exit_code != 0
        # Operator-facing prefix, does not leak Python symbols.
        assert "profile config" in result.stderr.lower()


# ---------------------------------------------------------------------------
# AC #6 — no-auth profile never attaches Authorization header
# ---------------------------------------------------------------------------


class TestNoAuthProfileNoAuthHeader:
    def test_no_auth_profile_omits_authorization(self, runner: CliRunner, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_noauth_config(config_path, profile_name="oss-local")
        main_module._CONFIG_PATH_OVERRIDE = config_path
        main_module._CREDENTIALS_DIR_OVERRIDE = tmp_path / "credentials"

        captured_headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.append(request.headers.copy())
            return httpx.Response(200, json={"teams": []})

        transport = httpx.MockTransport(handler)

        # Use the real factory with an injected transport so the test exercises
        # production code paths (AC #6 is an end-to-end invariant, not a stub
        # assertion).
        def factory(profile: Any, **kwargs: Any) -> httpx.Client:
            from akgentic.infra.cli.http import build_http_client_with_auto_auth

            return build_http_client_with_auto_auth(profile, transport=transport, **kwargs)

        main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory

        with patch(
            "akgentic.infra.cli.auth.OidcTokenProvider",
            side_effect=AssertionError("no-auth profile must not construct OIDC"),
        ):
            result = runner.invoke(app, ["team", "list"])

        assert result.exit_code == 0, result.stderr
        assert len(captured_headers) >= 1
        for headers in captured_headers:
            assert "authorization" not in {k.lower() for k in headers}


# ---------------------------------------------------------------------------
# AC #7 — auth profile + empty cache → device-code + command succeeds
# ---------------------------------------------------------------------------


class TestAuthProfileDeviceCodeFlow:
    def test_device_code_flow_on_empty_cache(self, runner: CliRunner, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _write_auth_config(config_path, profile_name="acme-prod")
        credentials_dir = tmp_path / "credentials"
        main_module._CONFIG_PATH_OVERRIDE = config_path
        main_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir

        call_log: dict[str, int] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            url = str(request.url)
            if path.endswith(DISCOVERY_URL_PATH):
                call_log["discovery"] = call_log.get("discovery", 0) + 1
                return httpx.Response(200, content=_discovery_body())
            if url == DEVICE_AUTH_ENDPOINT:
                call_log["device_auth"] = call_log.get("device_auth", 0) + 1
                return httpx.Response(200, json=_device_auth_body())
            if url == TOKEN_ENDPOINT:
                call_log["token"] = call_log.get("token", 0) + 1
                return httpx.Response(200, json=_token_body())
            # Business endpoint.
            call_log["business"] = call_log.get("business", 0) + 1
            return httpx.Response(200, json={"teams": []})

        transport = httpx.MockTransport(handler)

        def factory(profile: Any, **kwargs: Any) -> httpx.Client:
            from akgentic.infra.cli.http import build_http_client_with_auto_auth

            return build_http_client_with_auto_auth(profile, transport=transport, **kwargs)

        main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory

        result = runner.invoke(app, ["team", "list"])

        assert result.exit_code == 0, result.stderr
        # Cache file exists with mode 0600.
        cache_file = credentials_dir / "acme-prod.json"
        assert cache_file.exists()
        import stat

        mode = stat.S_IMODE(cache_file.stat().st_mode)
        assert mode == 0o600, oct(mode)
        # stderr carries device-code instructions.
        assert "USER-CODE" in result.stderr
        # Discovery + device-auth + token + at least one business request.
        assert call_log.get("discovery", 0) >= 1
        assert call_log.get("device_auth", 0) >= 1
        assert call_log.get("token", 0) >= 1
        assert call_log.get("business", 0) >= 1
