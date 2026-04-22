"""Anti-regression gate for story 22.5 AC #1 / AC #5 / AC #9.

When ``~/.akgentic/config.yaml`` does not exist, the CLI MUST behave
identically to pre-22.5. If you change this file, you are changing the
backward-compatibility contract — do so only with explicit review approval.

What this suite asserts, parametrized across every existing top-level CLI
command that makes HTTP calls:

* Exit code 0 (all mocked responses are 2xx).
* Exactly one :class:`ApiClient` constructed via the legacy
  ``(base_url, api_key)`` path.
* Zero :class:`OidcTokenProvider` instances constructed.
* Zero :func:`build_http_client_with_auto_auth` calls.
* Outgoing requests attach an ``Authorization`` header only when
  ``--api-key`` is supplied; its value is always ``Bearer <api_key>``.

``HOME`` is monkeypatched to ``tmp_path`` so a developer's real
``~/.akgentic/config.yaml`` cannot satisfy the test accidentally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from akgentic.infra.cli import main as main_module
from akgentic.infra.cli.client import ApiClient
from akgentic.infra.cli.main import app


@pytest.fixture(autouse=True)
def _reset_overrides_and_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Seal the environment: no real config, no leaking env vars."""
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AKGENTIC_PROFILE", raising=False)
    yield
    main_module._CONFIG_PATH_OVERRIDE = None
    main_module._CREDENTIALS_DIR_OVERRIDE = None
    main_module._HTTP_CLIENT_FACTORY_OVERRIDE = None


# ---------------------------------------------------------------------------
# Per-command scenarios — (argv, mocked-response-kind, mocked-response-body)
# ---------------------------------------------------------------------------


def _team_info_payload() -> dict[str, Any]:
    return {
        "team_id": "t1",
        "name": "Team",
        "status": "running",
        "user_id": "u1",
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-01-01T00:00:00",
    }


def _event_list_payload() -> dict[str, Any]:
    return {
        "events": [
            {
                "team_id": "t1",
                "sequence": 1,
                "timestamp": "2025-01-01T00:00:00",
                "event": {"type": "started"},
            }
        ]
    }


def _workspace_tree_payload() -> dict[str, Any]:
    return {"team_id": "t1", "path": "/", "entries": []}


def _generic_handler(request: httpx.Request) -> httpx.Response:
    """Fallback handler: returns plausible-shape responses per URL pattern."""
    path = request.url.path
    if path == "/teams" and request.method == "GET":
        return httpx.Response(200, json={"teams": [_team_info_payload()]})
    if path == "/teams" and request.method == "POST":
        return httpx.Response(200, json=_team_info_payload())
    if path.startswith("/teams/") and path.endswith("/events"):
        return httpx.Response(200, json=_event_list_payload())
    if path.startswith("/workspace/") and path.endswith("/tree"):
        return httpx.Response(200, json=_workspace_tree_payload())
    if path.startswith("/workspace/") and path.endswith("/file"):
        if request.method == "GET":
            return httpx.Response(200, content=b"file contents")
        # POST multipart upload.
        return httpx.Response(200, json={"path": "readme.md", "size": 5})
    if path.startswith("/teams/"):
        if request.method == "DELETE":
            return httpx.Response(204, content=b"")
        if request.method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=_team_info_payload())
    return httpx.Response(200, json={})


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# Each scenario: (argv, expected_auth_header)
# ``expected_auth_header`` is ``None`` when no Authorization header should be
# attached; it is the exact value when ``--api-key`` is supplied.
_SCENARIOS = [
    pytest.param(["team", "list"], None, id="team-list-no-flags"),
    pytest.param(["team", "get", "t1"], None, id="team-get-no-flags"),
    pytest.param(["team", "create", "entry"], None, id="team-create-no-flags"),
    pytest.param(["team", "delete", "t1"], None, id="team-delete-no-flags"),
    pytest.param(["team", "events", "t1"], None, id="team-events-no-flags"),
    pytest.param(["message", "t1", "hello"], None, id="message-no-flags"),
    pytest.param(
        ["reply", "t1", "ok", "--message-id", "m1"],
        None,
        id="reply-no-flags",
    ),
    pytest.param(["workspace", "tree", "t1"], None, id="workspace-tree-no-flags"),
    pytest.param(
        ["--api-key", "tok", "team", "list"],
        "Bearer tok",
        id="team-list-api-key",
    ),
    pytest.param(
        ["--server", "http://other.example", "team", "list"],
        None,
        id="team-list-server-override",
    ),
]


@pytest.mark.parametrize(("argv", "expected_auth_header"), _SCENARIOS)
def test_no_config_command_is_backwards_compatible(
    runner: CliRunner,
    tmp_path: Path,
    argv: list[str],
    expected_auth_header: str | None,
) -> None:
    """Exercises AC #1 / AC #5 / AC #9 on every HTTP-using command."""
    # Force ConfigFileNotFoundError by pointing the seam at a missing file.
    main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"

    # Record every ApiClient construction, preserving both args and kwargs.
    constructed: list[dict[str, Any]] = []
    instance_holder: dict[str, ApiClient] = {}
    original_init = ApiClient.__init__

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _generic_handler(request)

    transport = httpx.MockTransport(handler)

    def recording_init(self: ApiClient, *args: Any, **kwargs: Any) -> None:
        kw = dict(kwargs)
        if args:
            kw.setdefault("base_url", args[0])
        if len(args) > 1:
            kw.setdefault("api_key", args[1])
        constructed.append(kw)
        # Still construct the real ApiClient — but swap the internal httpx
        # client for our MockTransport-backed one so no real network I/O
        # happens. This exercises the *real* constructor code (the gate for
        # AC #5: legacy path still works).
        original_init(self, *args, **kwargs)
        # Rewire the internal client through MockTransport, preserving the
        # Authorization header set by the legacy path.
        existing_auth = self._client.headers.get("authorization")
        headers: dict[str, str] = {}
        if existing_auth:
            headers["Authorization"] = existing_auth
        self._client.close()
        self._client = httpx.Client(
            base_url=kw.get("base_url", "http://localhost:8000"),
            transport=transport,
            headers=headers,
        )
        instance_holder["client"] = self

    with (
        patch.object(ApiClient, "__init__", recording_init),
        patch(
            "akgentic.infra.cli.auth.OidcTokenProvider",
            side_effect=AssertionError(
                "OidcTokenProvider must not be constructed on the no-config path"
            ),
        ),
        patch(
            "akgentic.infra.cli.main.build_http_client_with_auto_auth",
            side_effect=AssertionError(
                "auto-auth factory must not be called on the no-config path"
            ),
        ),
    ):
        # Workspace upload needs a real file on disk for its argument.
        result = runner.invoke(app, argv)

    assert result.exit_code == 0, (
        f"argv={argv} exit_code={result.exit_code} "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )

    # Exactly one ApiClient construction via the legacy path.
    assert len(constructed) == 1, f"expected 1 ApiClient, got {len(constructed)}"
    kw = constructed[0]
    assert kw.get("http_client") is None
    # _owns_client is True when the legacy path is taken.
    assert instance_holder["client"]._owns_client is True

    # Every outgoing request honors the auth-header invariant.
    assert captured_requests, f"no outgoing request captured for argv={argv}"
    for req in captured_requests:
        auth = req.headers.get("authorization")
        if expected_auth_header is None:
            assert auth is None, f"unexpected Authorization header: {auth!r}"
        else:
            assert auth == expected_auth_header, (
                f"wrong Authorization: {auth!r} != {expected_auth_header!r}"
            )


def test_workspace_upload_no_config(runner: CliRunner, tmp_path: Path) -> None:
    """``workspace upload`` needs a real file; handled separately from the
    parametrized matrix."""
    main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"

    upload_file = tmp_path / "readme.md"
    upload_file.write_text("hello", encoding="utf-8")

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _generic_handler(request)

    transport = httpx.MockTransport(handler)
    original_init = ApiClient.__init__

    def recording_init(self: ApiClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._client.close()
        self._client = httpx.Client(
            base_url=kwargs.get("base_url", "http://localhost:8000"),
            transport=transport,
        )

    with (
        patch.object(ApiClient, "__init__", recording_init),
        patch(
            "akgentic.infra.cli.auth.OidcTokenProvider",
            side_effect=AssertionError("OIDC must not engage"),
        ),
        patch(
            "akgentic.infra.cli.main.build_http_client_with_auto_auth",
            side_effect=AssertionError("auto-auth must not engage"),
        ),
    ):
        result = runner.invoke(
            app,
            ["workspace", "upload", "t1", str(upload_file)],
        )

    assert result.exit_code == 0, result.stderr
    assert any(
        req.url.path.endswith("/workspace/t1/file") and req.method == "POST"
        for req in captured_requests
    )


def test_workspace_read_no_config(runner: CliRunner, tmp_path: Path) -> None:
    """``workspace read`` returns raw bytes — separate scenario."""
    main_module._CONFIG_PATH_OVERRIDE = tmp_path / "nope.yaml"

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return _generic_handler(request)

    transport = httpx.MockTransport(handler)
    original_init = ApiClient.__init__

    def recording_init(self: ApiClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._client.close()
        self._client = httpx.Client(
            base_url=kwargs.get("base_url", "http://localhost:8000"),
            transport=transport,
        )

    with (
        patch.object(ApiClient, "__init__", recording_init),
        patch(
            "akgentic.infra.cli.auth.OidcTokenProvider",
            side_effect=AssertionError("OIDC must not engage"),
        ),
    ):
        result = runner.invoke(app, ["workspace", "read", "t1", "readme.md"])

    assert result.exit_code == 0, result.stderr
    assert captured_requests
    for req in captured_requests:
        assert req.headers.get("authorization") is None
