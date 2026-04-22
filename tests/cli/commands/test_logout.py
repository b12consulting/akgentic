"""Tests for :mod:`akgentic.infra.cli.commands.logout`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from akgentic.infra.cli.auth import TokenCacheEntry, save_token_cache
from akgentic.infra.cli.commands import logout as logout_module


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    test_app = typer.Typer()
    logout_module.register(test_app)
    return test_app


@pytest.fixture(autouse=True)
def _reset_overrides() -> Any:
    logout_module._CREDENTIALS_DIR_OVERRIDE = None
    logout_module._CONFIG_PATH_OVERRIDE = None
    yield
    logout_module._CREDENTIALS_DIR_OVERRIDE = None
    logout_module._CONFIG_PATH_OVERRIDE = None


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AKGENTIC_PROFILE", raising=False)


def _write_auth_config(path: Path, profile_name: str = "acme-prod") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n"
        "    auth:\n"
        "      type: oidc\n"
        "      issuer: https://issuer.example.com\n"
        "      client_id: akgentic-cli\n",
        encoding="utf-8",
    )


def _write_noauth_config(path: Path, profile_name: str = "oss-local") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://oss.example.com\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# AC #9 bullet 3 — logout wipes an existing cache file
# ---------------------------------------------------------------------------


def test_logout_wipes_cache_file(app: typer.Typer, runner: CliRunner, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_auth_config(config_path)
    credentials_dir = tmp_path / "credentials"
    # Pre-populate cache.
    save_token_cache(
        "acme-prod",
        TokenCacheEntry(
            access_token="a",
            refresh_token="r",
            expires_at=1_700_000_000 + 3600,
        ),
        credentials_dir=credentials_dir,
    )
    assert (credentials_dir / "acme-prod.json").exists()

    logout_module._CONFIG_PATH_OVERRIDE = config_path
    logout_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "Logged out" in result.stdout
    assert "acme-prod" in result.stdout
    assert not (credentials_dir / "acme-prod.json").exists()


# ---------------------------------------------------------------------------
# AC #9 bullet 4 — logout is idempotent
# ---------------------------------------------------------------------------


def test_logout_is_idempotent(app: typer.Typer, runner: CliRunner, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_auth_config(config_path)
    credentials_dir = tmp_path / "credentials"
    save_token_cache(
        "acme-prod",
        TokenCacheEntry(access_token="a", refresh_token="r", expires_at=1_700_000_000 + 3600),
        credentials_dir=credentials_dir,
    )

    logout_module._CONFIG_PATH_OVERRIDE = config_path
    logout_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir

    first = runner.invoke(app, [])
    assert first.exit_code == 0, first.output
    assert "Logged out" in first.stdout

    second = runner.invoke(app, [])
    assert second.exit_code == 0, second.output
    assert "Already logged out" in second.stdout


# ---------------------------------------------------------------------------
# AC #9 bullet 5 — logout on a no-auth profile is a benign no-op
# ---------------------------------------------------------------------------


def test_logout_no_auth_profile_is_noop(
    app: typer.Typer, runner: CliRunner, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_noauth_config(config_path)
    credentials_dir = tmp_path / "credentials"
    # Simulate a stale cache that must NOT be touched.
    credentials_dir.mkdir()
    cache_file = credentials_dir / "oss-local.json"
    cache_file.write_text('{"sentinel": true}')

    logout_module._CONFIG_PATH_OVERRIDE = config_path
    logout_module._CREDENTIALS_DIR_OVERRIDE = credentials_dir

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    # Stdout (not stderr — benign no-op per AC #2).
    assert "no auth configured" in result.stdout
    # Filesystem untouched.
    assert cache_file.exists()
    assert cache_file.read_text() == '{"sentinel": true}'
