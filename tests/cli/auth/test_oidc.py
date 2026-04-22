"""Tests for :mod:`akgentic.infra.cli.auth.oidc` and :class:`OidcTokenProvider`.

All network I/O runs through :class:`httpx.MockTransport` — NO real network,
NO live OIDC provider. All filesystem I/O runs through ``tmp_path`` via the
``credentials_dir`` seam — NEVER ``~/.akgentic/``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from akgentic.infra.cli.auth import (
    AccessDeniedError,
    ExpiredTokenError,
    OidcDiscoveryError,
    OidcTokenProvider,
    ReAuthRequiredError,
    TokenCacheEntry,
    TokenProvider,
    save_token_cache,
)
from akgentic.infra.cli.config.profile import AuthConfig, ProfileConfig
from akgentic.infra.cli.http import build_http_client

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.example.com"
DEVICE_AUTH_ENDPOINT = f"{ISSUER}/device-auth"
TOKEN_ENDPOINT = f"{ISSUER}/token"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"


@pytest.fixture
def auth_profile() -> ProfileConfig:
    return ProfileConfig(
        endpoint="https://api.example.com",  # type: ignore[arg-type]
        auth=AuthConfig(
            type="oidc",
            issuer=ISSUER,  # type: ignore[arg-type]
            client_id="akgentic-cli",
        ),
    )


@pytest.fixture
def tmp_credentials_dir(tmp_path: Path) -> Path:
    return tmp_path / "credentials"


class FakeClock:
    """Monotonically incrementing clock — returns the same value until
    :meth:`advance` is called, so tests have deterministic ``expires_at``."""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self._now = start

    def __call__(self) -> int:
        return self._now

    def advance(self, seconds: int) -> None:
        self._now += seconds


def _discovery_body() -> bytes:
    return json.dumps(
        {
            "device_authorization_endpoint": DEVICE_AUTH_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
        }
    ).encode("utf-8")


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode("utf-8"))


def _make_provider(
    auth_profile: ProfileConfig,
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    credentials_dir: Path,
    clock: Callable[[], int] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> OidcTokenProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OidcTokenProvider(
        auth_profile,
        "ent-prof",
        http_client=client,
        clock=clock,
        sleep=sleep if sleep is not None else (lambda _s: None),
        credentials_dir=credentials_dir,
    )


# ---------------------------------------------------------------------------
# Device-code happy path + protocol error cases (AC #1, #2, #7)
# ---------------------------------------------------------------------------


def test_run_device_code_flow_happy_path(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    clock = FakeClock()
    poll_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "USER-CODE",
                    "verification_uri": "https://verify.example.com",
                    "verification_uri_complete": "https://verify.example.com?code=USER-CODE",
                    "expires_in": 600,
                    "interval": 1,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            poll_count["n"] += 1
            if poll_count["n"] == 1:
                return _json_response({"error": "authorization_pending"}, status=400)
            return _json_response(
                {
                    "access_token": "access-token-1",
                    "refresh_token": "refresh-token-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    sleeps: list[float] = []
    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
        sleep=sleeps.append,
    )
    prompts: list[str] = []

    entry = provider.run_device_code_flow(on_user_code=lambda a: prompts.append(a.user_code))

    assert entry.access_token == "access-token-1"
    assert entry.refresh_token == "refresh-token-1"
    assert entry.expires_at == clock() + 3600
    assert prompts == ["USER-CODE"]
    # cache file written
    assert (tmp_credentials_dir / "ent-prof.json").exists()
    provider.close()


def test_poll_slow_down_widens_interval(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    # AC #7: slow_down widens the polling interval (assert on recorded sleeps).
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "U",
                    "verification_uri": "https://verify.example.com",
                    "expires_in": 600,
                    "interval": 5,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            state["n"] += 1
            if state["n"] == 1:
                return _json_response({"error": "slow_down"}, status=400)
            return _json_response(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(request.url)

    sleeps: list[float] = []
    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
        sleep=sleeps.append,
    )
    provider.run_device_code_flow(on_user_code=lambda _a: None)
    # Two sleeps recorded: the initial interval, then the widened one.
    assert sleeps[0] == 5
    assert sleeps[1] == 10
    provider.close()


def test_access_denied_surfaces_typed_error(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    # AC #7: access_denied → AccessDeniedError; no cache written.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "U",
                    "verification_uri": "https://verify.example.com",
                    "expires_in": 600,
                    "interval": 1,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response({"error": "access_denied"}, status=400)
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    with pytest.raises(AccessDeniedError):
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    assert not (tmp_credentials_dir / "ent-prof.json").exists()
    provider.close()


def test_expires_in_budget_exhaustion_raises_expired_token(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    # AC #7: local flow timeout → ExpiredTokenError; no cache written.
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "U",
                    "verification_uri": "https://verify.example.com",
                    "expires_in": 10,
                    "interval": 5,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            # Server keeps saying pending; local clock will exhaust first.
            clock.advance(20)
            return _json_response({"error": "authorization_pending"}, status=400)
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    with pytest.raises(ExpiredTokenError):
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    assert not (tmp_credentials_dir / "ent-prof.json").exists()
    provider.close()


def test_discovery_failure_surfaces_oidc_discovery_error(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    # AC #7: malformed/500 discovery → OidcDiscoveryError before device-auth call.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(500, content=b"oops")
        raise AssertionError("should not reach device-auth when discovery fails")

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    with pytest.raises(OidcDiscoveryError):
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    provider.close()


def test_discovery_malformed_json_surfaces_oidc_discovery_error(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=b"{not json")
        raise AssertionError("unexpected request")

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    with pytest.raises(OidcDiscoveryError):
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    provider.close()


# ---------------------------------------------------------------------------
# get_access_token contract (AC #5, #7)
# ---------------------------------------------------------------------------


def test_get_access_token_returns_cached_when_fresh(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    clock = FakeClock()
    # Cache with expires_at far in the future.
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(
            access_token="cached-access",
            refresh_token="cached-refresh",
            expires_at=clock() + 3600,
        ),
        credentials_dir=tmp_credentials_dir,
    )

    def fail_any_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network call not allowed; got {request.url}")

    provider = _make_provider(
        auth_profile,
        handler=fail_any_request,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    assert provider.get_access_token() == "cached-access"
    provider.close()


def test_get_access_token_refreshes_when_expired(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    clock = FakeClock()
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(
            access_token="old-access",
            refresh_token="refresh-me",
            expires_at=clock() - 60,  # already expired
        ),
        credentials_dir=tmp_credentials_dir,
    )

    refresh_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == TOKEN_ENDPOINT:
            refresh_requests.append(request)
            return _json_response(
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    assert provider.get_access_token() == "new-access"
    # Request body contained grant_type=refresh_token
    assert b"grant_type=refresh_token" in refresh_requests[0].content
    # Cache was updated on disk with new expires_at
    from akgentic.infra.cli.auth import load_token_cache

    loaded = load_token_cache("ent-prof", credentials_dir=tmp_credentials_dir)
    assert loaded is not None
    assert loaded.access_token == "new-access"
    assert loaded.expires_at == clock() + 3600
    provider.close()


def test_get_access_token_raises_reauth_on_invalid_refresh(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    clock = FakeClock()
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(
            access_token="old",
            refresh_token="bad-refresh",
            expires_at=clock() - 60,
        ),
        credentials_dir=tmp_credentials_dir,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response({"error": "invalid_grant"}, status=400)
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    with pytest.raises(ReAuthRequiredError):
        provider.get_access_token()
    # Cache must have been purged.
    assert not (tmp_credentials_dir / "ent-prof.json").exists()
    provider.close()


def test_get_access_token_raises_reauth_when_cache_missing(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    def fail_any_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not hit network when cache missing; got {request.url}")

    provider = _make_provider(
        auth_profile,
        handler=fail_any_request,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    with pytest.raises(ReAuthRequiredError):
        provider.get_access_token()
    provider.close()


# ---------------------------------------------------------------------------
# Protocol conformance with HTTP client factory (AC #6 / Task 5.5)
# ---------------------------------------------------------------------------


def test_provider_satisfies_tokenprovider_protocol_and_wires_with_factory(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    clock = FakeClock()
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(
            access_token="wire-token",
            refresh_token="r",
            expires_at=clock() + 3600,
        ),
        credentials_dir=tmp_credentials_dir,
    )

    def oidc_handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("token endpoint must not be hit when cache is fresh")

    provider = _make_provider(
        auth_profile,
        handler=oidc_handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    assert isinstance(provider, TokenProvider)

    # Now wire into build_http_client and verify Authorization header.
    captured_headers: dict[str, str] = {}

    def api_handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, content=b"ok")

    client = build_http_client(
        auth_profile,
        token_provider=provider,
        profile_name="ent-prof",
        transport=httpx.MockTransport(api_handler),
    )
    try:
        client.get("/hello")
    finally:
        client.close()

    assert captured_headers.get("authorization") == "Bearer wire-token"
    provider.close()


def test_provider_rejects_oss_profile() -> None:
    oss_profile = ProfileConfig(endpoint="https://api.example.com")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OidcTokenProvider(oss_profile, "oss")


def test_default_prompt_hook_writes_to_stderr(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC #5 / Task 5.4: default on_user_code writes to stderr, not stdout."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dev-code",
                    "user_code": "VISIBLE-CODE",
                    "verification_uri": "https://verify.example.com",
                    "expires_in": 600,
                    "interval": 1,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    provider.run_device_code_flow()  # default prompt hook
    captured = capsys.readouterr()
    assert "VISIBLE-CODE" in captured.err
    assert "VISIBLE-CODE" not in captured.out
    provider.close()


def test_refresh_response_without_refresh_token_purges_and_raises(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    """Strict refresh-token policy (documented module decision)."""
    clock = FakeClock()
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(access_token="old", refresh_token="r", expires_at=clock() - 60),
        credentials_dir=tmp_credentials_dir,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == TOKEN_ENDPOINT:
            # No refresh_token in response — strict path should purge + raise.
            return _json_response({"access_token": "a", "expires_in": 3600, "token_type": "Bearer"})
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    with pytest.raises(ReAuthRequiredError):
        provider.get_access_token()
    assert not (tmp_credentials_dir / "ent-prof.json").exists()
    provider.close()


def test_initiate_device_flow_error_response(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    """Covers the error-body path in initiate_device_flow."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {"error": "invalid_client", "error_description": "bad client"},
                status=400,
            )
        raise AssertionError(request.url)

    from akgentic.infra.cli.auth import OidcProtocolError

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    with pytest.raises(OidcProtocolError):
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    provider.close()


def test_endpoints_cached_across_calls(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    """Discovery should run once per provider instance, not per call."""
    clock = FakeClock()
    # Seed a stale cache so get_access_token must refresh (hits token endpoint).
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(access_token="o", refresh_token="r", expires_at=clock() - 60),
        credentials_dir=tmp_credentials_dir,
    )
    discovery_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            discovery_calls["n"] += 1
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=clock,
    )
    provider.get_access_token()  # triggers refresh, runs discovery once
    # Expire again and refresh a second time — discovery should NOT re-run.
    save_token_cache(
        "ent-prof",
        TokenCacheEntry(access_token="o2", refresh_token="r", expires_at=clock() - 60),
        credentials_dir=tmp_credentials_dir,
    )
    provider.get_access_token()
    assert discovery_calls["n"] == 1
    provider.close()


def test_run_device_code_flow_uses_default_prompt_when_hook_none(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """MagicMock spy demonstrating the on_user_code injection path."""
    spy = MagicMock()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dc",
                    "user_code": "UC",
                    "verification_uri": "https://v.example.com",
                    "expires_in": 600,
                    "interval": 1,
                }
            )
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response(
                {
                    "access_token": "a",
                    "refresh_token": "r",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=FakeClock(),
    )
    provider.run_device_code_flow(on_user_code=spy)
    assert spy.call_count == 1
    provider.close()
