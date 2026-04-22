"""Tests for :mod:`akgentic.infra.cli.commands.login`.

All network I/O runs through :class:`httpx.MockTransport`; all filesystem
I/O runs through ``tmp_path`` via the :mod:`login` module's seam constants.
No real ``~/.akgentic/``, no real device-code flow.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
from typer.testing import CliRunner

from akgentic.infra.cli.commands import login as login_module

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


def _write_config(path: Path, *, auth_profile: bool, profile_name: str = "acme-prod") -> None:
    auth_block = (
        f"    auth:\n      type: oidc\n      issuer: {ISSUER}\n      client_id: akgentic-cli\n"
        if auth_profile
        else ""
    )
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n" + auth_block,
        encoding="utf-8",
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    test_app = typer.Typer()
    login_module.register(test_app)
    return test_app


@pytest.fixture(autouse=True)
def _reset_overrides() -> Any:
    login_module._CREDENTIALS_DIR_OVERRIDE = None
    login_module._CONFIG_PATH_OVERRIDE = None
    login_module._HTTP_CLIENT_OVERRIDE = None
    login_module._SLEEP_OVERRIDE = None
    yield
    login_module._CREDENTIALS_DIR_OVERRIDE = None
    login_module._CONFIG_PATH_OVERRIDE = None
    login_module._HTTP_CLIENT_OVERRIDE = None
    login_module._SLEEP_OVERRIDE = None


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AKGENTIC_PROFILE", raising=False)


def _install_success_transport(
    call_log: dict[str, int],
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(DISCOVERY_URL_PATH):
            call_log["discovery"] = call_log.get("discovery", 0) + 1
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            call_log["device_auth"] = call_log.get("device_auth", 0) + 1
            return httpx.Response(200, json=_device_auth_body())
        if str(request.url) == TOKEN_ENDPOINT:
            call_log["token"] = call_log.get("token", 0) + 1
            return httpx.Response(200, json=_token_body())
        raise AssertionError(f"unexpected URL: {request.url}")

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# AC #9 bullet 1 — login happy path on an auth-enabled profile
# ---------------------------------------------------------------------------


def test_login_happy_path_auth_profile(app: typer.Typer, runner: CliRunner, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, auth_profile=True)
    credentials_dir = tmp_path / "credentials"
    call_log: dict[str, int] = {}
    transport = _install_success_transport(call_log)

    login_module._CONFIG_PATH_OVERRIDE = config_path
    login_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir
    login_module._HTTP_CLIENT_OVERRIDE = httpx.Client(transport=transport)
    login_module._SLEEP_OVERRIDE = lambda _s: None

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    # stdout has the confirmation line, NOT the user code or URL.
    assert "Logged in" in result.stdout
    assert "acme-prod" in result.stdout
    assert "USER-CODE" not in result.stdout
    assert "verify.example.com" not in result.stdout
    # stderr has the user code and verification URL.
    assert "USER-CODE" in result.stderr
    assert "verify.example.com" in result.stderr
    # Cache file written with mode 0600 (Story 21.3 invariant — re-asserted).
    cache_file = credentials_dir / "acme-prod.json"
    assert cache_file.exists()
    mode = stat.S_IMODE(cache_file.stat().st_mode)
    assert mode == 0o600, oct(mode)
    # Network calls: discovery + device-auth + token.
    assert call_log.get("discovery", 0) >= 1
    assert call_log.get("device_auth", 0) == 1
    assert call_log.get("token", 0) == 1


# ---------------------------------------------------------------------------
# AC #9 bullet 2 — login on a no-auth profile: non-zero exit, no device-code
# ---------------------------------------------------------------------------


def test_login_no_auth_profile_exits_nonzero(
    app: typer.Typer, runner: CliRunner, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, auth_profile=False, profile_name="oss-local")
    credentials_dir = tmp_path / "credentials"

    def _fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no-auth profile should not contact any URL: {request.url}")

    login_module._CONFIG_PATH_OVERRIDE = config_path
    login_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir
    login_module._HTTP_CLIENT_OVERRIDE = httpx.Client(transport=httpx.MockTransport(_fail))
    login_module._SLEEP_OVERRIDE = lambda _s: None

    result = runner.invoke(app, [])

    assert result.exit_code != 0
    assert "no auth configured" in result.stderr
    # No cache file was written.
    assert not (credentials_dir / "oss-local.json").exists()
    # Neither stdout nor stderr should mention the device-code URL/user code,
    # because the device-code flow never ran.
    assert "USER-CODE" not in result.stderr
    assert "USER-CODE" not in result.stdout


# ---------------------------------------------------------------------------
# AC #6 + AC #9 bullet 11 — ``--profile`` flag selection
# ---------------------------------------------------------------------------


def test_login_profile_flag_selects_profile(
    app: typer.Typer, runner: CliRunner, tmp_path: Path
) -> None:
    """`--profile` overrides the default profile and is honored by login."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "default_profile: foo\n"
        "profiles:\n"
        "  foo:\n"
        "    endpoint: https://oss.example.com\n"
        "  bar:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        f"      issuer: {ISSUER}\n"
        "      client_id: akgentic-cli\n",
        encoding="utf-8",
    )
    credentials_dir = tmp_path / "credentials"
    call_log: dict[str, int] = {}
    transport = _install_success_transport(call_log)

    login_module._CONFIG_PATH_OVERRIDE = config_path
    login_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir
    login_module._HTTP_CLIENT_OVERRIDE = httpx.Client(transport=transport)
    login_module._SLEEP_OVERRIDE = lambda _s: None

    # --profile bar → auth-enabled profile succeeds.
    result = runner.invoke(app, ["--profile", "bar"])
    assert result.exit_code == 0, result.output
    assert "bar" in result.stdout
    assert (credentials_dir / "bar.json").exists()

    # --profile foo → no-auth profile exits non-zero; no cache for foo.
    login_module._HTTP_CLIENT_OVERRIDE = httpx.Client(transport=transport)
    result_foo = runner.invoke(app, ["--profile", "foo"])
    assert result_foo.exit_code != 0
    assert "no auth configured" in result_foo.stderr
    assert not (credentials_dir / "foo.json").exists()


def test_login_env_var_selects_profile(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AKGENTIC_PROFILE env var is honored when no --profile flag is given."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "profiles:\n"
        "  foo:\n"
        "    endpoint: https://oss.example.com\n"
        "  bar:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        f"      issuer: {ISSUER}\n"
        "      client_id: akgentic-cli\n",
        encoding="utf-8",
    )
    credentials_dir = tmp_path / "credentials"
    call_log: dict[str, int] = {}
    transport = _install_success_transport(call_log)

    login_module._CONFIG_PATH_OVERRIDE = config_path
    login_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir
    login_module._HTTP_CLIENT_OVERRIDE = httpx.Client(transport=transport)
    login_module._SLEEP_OVERRIDE = lambda _s: None
    monkeypatch.setenv("AKGENTIC_PROFILE", "bar")

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert (credentials_dir / "bar.json").exists()
