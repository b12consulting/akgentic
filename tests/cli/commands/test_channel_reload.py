"""Tests for :mod:`akgentic.infra.cli.commands.channel`.

Covers the full behavioural matrix from Story 23.3 AC #11 (200 success in
four body shapes, 404 community detection, 422 validation error, generic
non-2xx, the no-retry invariant, and auth-header absence on a community
profile) plus the structural invariants from AC #12 (no direct ``httpx``
import in the command module, exactly one new import + register line in
``main.py``, and idempotent registration on a fresh Typer).

All network I/O runs through :class:`httpx.MockTransport`; all filesystem
I/O runs through ``tmp_path`` via the module-level seams on
:mod:`akgentic.infra.cli.main` introduced by Story 22.5. No real
``~/.akgentic/``, no real HTTP.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
from typer.testing import CliRunner

from akgentic.infra.cli import main as main_module
from akgentic.infra.cli.commands import channel as channel_module
from akgentic.infra.cli.commands.channel import _COMMUNITY_MESSAGE
from akgentic.infra.cli.main import app

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_overrides_and_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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
    # On the installed click version, stderr is separated from stdout by
    # default (``result.stderr`` is available without the legacy
    # ``mix_stderr=False`` flag — matches the pattern used by
    # ``test_catalog.py``).
    return CliRunner()


def _write_noauth_config(path: Path, *, profile_name: str = "oss") -> None:
    path.write_text(
        "default_profile: " + profile_name + "\n"
        "profiles:\n"
        f"  {profile_name}:\n"
        "    endpoint: https://api.example.com\n",
        encoding="utf-8",
    )


Handler = Any  # typing shortcut for the mock-transport callback


def _install_mock(
    tmp_path: Path,
    handler: Handler,
) -> list[httpx.Request]:
    """Install a mock transport + capture request log via the 22.5 seams."""
    config_path = tmp_path / "config.yaml"
    _write_noauth_config(config_path)
    main_module._CONFIG_PATH_OVERRIDE = config_path

    captured: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def factory(profile: Any, **_kwargs: Any) -> httpx.Client:
        return httpx.Client(base_url=str(profile.endpoint), transport=transport)

    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = factory
    return captured


# ---------------------------------------------------------------------------
# Structural tests (AC #12)
# ---------------------------------------------------------------------------


def test_no_direct_httpx_and_no_apiclient_construction() -> None:
    """AC #2 / AC #12 — the module is a thin wire over ``_state.client``."""
    src = Path(channel_module.__file__).read_text(encoding="utf-8")
    assert "import httpx" not in src
    assert "ApiClient(base_url" not in src


def test_main_py_has_exactly_one_channel_import_and_register() -> None:
    """AC #10 / AC #12 — ``main.py`` gains exactly one import + one register call."""
    src = Path(main_module.__file__).read_text(encoding="utf-8")
    assert "from akgentic.infra.cli.commands import channel as channel_command" in src
    assert "channel_command.register(app)" in src
    # The identifier appears exactly twice in the source: once in the import
    # and once in the ``register(app)`` call.
    assert src.count("channel_command") == 2


def test_register_is_idempotent_on_fresh_typer() -> None:
    """AC #12 — ``register`` attaches the ``channel`` subgroup on a fresh app."""
    fresh_app = typer.Typer()
    channel_module.register(fresh_app)
    group_names = {g.name for g in fresh_app.registered_groups}
    assert "channel" in group_names


def test_channel_group_registered_on_main_app() -> None:
    """``ak channel`` is a top-level group on the production app."""
    channel_groups = [g for g in app.registered_groups if g.name == "channel"]
    assert len(channel_groups) == 1
    channel_app = channel_groups[0].typer_instance
    # The ``reload`` subcommand is registered as a command, not a group.
    command_names = {c.name for c in channel_app.registered_commands}
    assert "reload" in command_names


# ---------------------------------------------------------------------------
# _format_reload_summary unit tests (AC #4 — isolated formatting logic)
# ---------------------------------------------------------------------------


def test_format_reload_summary_with_channels_list() -> None:
    out = channel_module._format_reload_summary({"channels": ["slack", "discord"]})
    assert out == "Reloaded 2 channel(s): slack, discord"


def test_format_reload_summary_empty_list_fallback() -> None:
    assert channel_module._format_reload_summary({"channels": []}) == "Reloaded channels."


def test_format_reload_summary_missing_key_fallback() -> None:
    assert channel_module._format_reload_summary({"status": "ok"}) == "Reloaded channels."


def test_format_reload_summary_non_list_channels() -> None:
    assert channel_module._format_reload_summary({"channels": "nope"}) == "Reloaded channels."


def test_format_reload_summary_mixed_element_types() -> None:
    """Defensive: ``channels`` present but non-string elements → generic message."""
    assert (
        channel_module._format_reload_summary({"channels": ["slack", 42]}) == "Reloaded channels."
    )


def test_format_reload_summary_empty_dict() -> None:
    assert channel_module._format_reload_summary({}) == "Reloaded channels."


# ---------------------------------------------------------------------------
# Behavioural cases — 200 success (AC #11)
# ---------------------------------------------------------------------------


def test_reload_200_with_channels_in_body(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"channels": ["slack", "discord"]})

    captured = _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 0, result.stderr
    assert "Reloaded 2 channel(s): slack, discord" in result.stdout
    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert captured[0].url.path == "/admin/channels/reload"


def test_reload_200_with_empty_channels_list(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"channels": []})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "Reloaded channels.\n"


def test_reload_200_with_empty_body(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "Reloaded channels.\n"


def test_reload_200_with_unexpected_body_shape(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout == "Reloaded channels.\n"


# ---------------------------------------------------------------------------
# Behavioural cases — errors (AC #11)
# ---------------------------------------------------------------------------


# AC #5 verbatim message — character-for-character identity is required.
# Imported from the module under test so the invariant is enforced by a single
# source of truth (the tests fail the moment the production constant drifts).
EXPECTED_404_MESSAGE = _COMMUNITY_MESSAGE


def test_reload_404_community_server(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 1
    # stderr must contain the exact AC #5 message, no suffix variation.
    assert EXPECTED_404_MESSAGE in result.stderr
    # Belt-and-braces: assert the verbatim literal, not just the imported
    # constant — this catches any future drift where both the constant and
    # the test import are changed together but drift from the spec.
    assert (
        "`ak channel reload` requires an enterprise deployment; "
        "the current profile points at a community server."
    ) in result.stderr
    # No ``HTTP 404:`` prefix should appear — the community-detection path
    # replaces the generic rendering.
    assert "HTTP 404" not in result.stderr


def test_reload_422_validation_error(runner: CliRunner, tmp_path: Path) -> None:
    detail = "channel 'slack' definition invalid: missing field 'webhook'"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": detail})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 1
    assert f"HTTP 422: {detail}" in result.stderr


def test_reload_500_generic_non_2xx(runner: CliRunner, tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "upstream reload failure"})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 1
    assert "HTTP 500: upstream reload failure" in result.stderr


def test_reload_no_retry_on_500(runner: CliRunner, tmp_path: Path) -> None:
    """AC #8 / AC #11 — on 500 the CLI issues exactly one HTTP call, no retry."""
    call_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"detail": "boom"})

    captured = _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 1
    assert call_count == 1
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Auth inheritance — no-auth profile omits Authorization (AC #11 bullet 9)
# ---------------------------------------------------------------------------


def test_reload_community_profile_omits_auth_header(
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A profile without an ``auth:`` block must NOT send Authorization."""
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers.copy())
        return httpx.Response(200, json={"channels": ["ok"]})

    _install_mock(tmp_path, handler)
    result = runner.invoke(app, ["channel", "reload"])

    assert result.exit_code == 0, result.stderr
    assert captured_headers
    # httpx stores headers case-insensitively; checking the lowercase key is
    # sufficient and matches the test pattern in test_catalog.py.
    assert "authorization" not in captured_headers[-1]


# ---------------------------------------------------------------------------
# ApiClient.reload_channels — unit-level body coercion (AC #3)
# ---------------------------------------------------------------------------


def test_api_client_reload_channels_empty_body_returns_empty_dict() -> None:
    """When the server returns 200 with an empty body, return ``{}``."""
    from akgentic.infra.cli.client import ApiClient  # noqa: PLC0415

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.example.com", transport=transport)
    api = ApiClient(http_client=client)
    try:
        assert api.reload_channels() == {}
    finally:
        api.close()
        client.close()


def test_api_client_reload_channels_non_dict_body_returns_empty_dict() -> None:
    """When the server returns a JSON list (not a dict), return ``{}``."""
    from akgentic.infra.cli.client import ApiClient  # noqa: PLC0415

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(["a", "b"]).encode())

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.example.com", transport=transport)
    api = ApiClient(http_client=client)
    try:
        assert api.reload_channels() == {}
    finally:
        api.close()
        client.close()


def test_api_client_reload_channels_dict_body_returned_verbatim() -> None:
    """A JSON dict body is returned as-is (coerced to ``dict``)."""
    from akgentic.infra.cli.client import ApiClient  # noqa: PLC0415

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"channels": ["slack"], "extra": "info"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="https://api.example.com", transport=transport)
    api = ApiClient(http_client=client)
    try:
        assert api.reload_channels() == {"channels": ["slack"], "extra": "info"}
    finally:
        api.close()
        client.close()
