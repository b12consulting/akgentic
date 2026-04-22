"""Tests for :func:`build_http_client_with_auto_auth` (Story 21.4 auto-auth).

All network I/O runs through :class:`httpx.MockTransport`. All filesystem
I/O runs through ``tmp_path`` via the ``credentials_dir`` seam.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from akgentic.infra.cli.auth import TokenCacheEntry, save_token_cache
from akgentic.infra.cli.config.profile import AuthConfig, ProfileConfig
from akgentic.infra.cli.http import (
    AuthenticationError,
    ServerConfigurationError,
    build_http_client_with_auto_auth,
)

ISSUER = "https://issuer.example.com"
DEVICE_AUTH_ENDPOINT = f"{ISSUER}/device-auth"
TOKEN_ENDPOINT = f"{ISSUER}/token"
BUSINESS_URL = "https://api.example.com/teams"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def noauth_profile() -> ProfileConfig:
    return ProfileConfig(endpoint="https://oss.example.com")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Handlers — route by URL path; count calls via closure dict.
# ---------------------------------------------------------------------------


def _discovery_response() -> httpx.Response:
    body = json.dumps(
        {
            "device_authorization_endpoint": DEVICE_AUTH_ENDPOINT,
            "token_endpoint": TOKEN_ENDPOINT,
        }
    ).encode("utf-8")
    return httpx.Response(200, content=body)


def _device_auth_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "device_code": "dev-code",
            "user_code": "USER-CODE",
            "verification_uri": "https://verify.example.com",
            "expires_in": 600,
            "interval": 1,
        },
    )


def _token_response(access_token: str = "access-1") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": access_token,
            "refresh_token": "refresh-1",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )


def _is_discovery(request: httpx.Request) -> bool:
    return request.url.path.endswith("/.well-known/openid-configuration")


def _is_device_auth(request: httpx.Request) -> bool:
    return str(request.url) == DEVICE_AUTH_ENDPOINT


def _is_token(request: httpx.Request) -> bool:
    return str(request.url) == TOKEN_ENDPOINT


def _is_business(request: httpx.Request) -> bool:
    return not (_is_discovery(request) or _is_device_auth(request) or _is_token(request))


# ---------------------------------------------------------------------------
# AC #3 + AC #9 bullet 6 — auto-auth on first command (happy path)
# ---------------------------------------------------------------------------


def test_auto_auth_happy_path_401_then_200(auth_profile: ProfileConfig, tmp_path: Path) -> None:
    """On first use: cache missing → pre-flight device code → client returns 200."""
    credentials_dir = tmp_path / "credentials"
    counts: dict[str, int] = {"business": 0, "device_auth": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            counts["device_auth"] += 1
            return _device_auth_response()
        if _is_token(request):
            counts["token"] += 1
            return _token_response()
        # business
        counts["business"] += 1
        assert request.headers.get("Authorization", "").startswith("Bearer ")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    resp = client.get("/teams")
    client.close()

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert counts["device_auth"] == 1
    assert counts["business"] == 1
    # Cache persisted with mode 0600.
    cache_file = credentials_dir / "ent.json"
    assert cache_file.exists()
    assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# AC #3 + AC #9 bullet 7 — retry-once semantics (no infinite loop)
# ---------------------------------------------------------------------------


def test_auto_auth_retry_once_then_surface_auth_error(
    auth_profile: ProfileConfig, tmp_path: Path
) -> None:
    """Two 401s in a row → AuthenticationError; exactly one device-code run."""
    credentials_dir = tmp_path / "credentials"
    # Pre-populate the cache so pre-flight does NOT run device-code — we
    # want to test the in-flight retry-once path specifically.
    save_token_cache(
        "ent",
        TokenCacheEntry(access_token="old-token", refresh_token="r-1", expires_at=9_999_999_999),
        credentials_dir=credentials_dir,
    )
    counts: dict[str, int] = {"business": 0, "device_auth": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            counts["device_auth"] += 1
            return _device_auth_response()
        if _is_token(request):
            counts["token"] += 1
            return _token_response("access-2")
        counts["business"] += 1
        # Always return 401 — simulate server revoking tokens.
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    with pytest.raises(AuthenticationError):
        client.get("/teams")
    client.close()

    # Exactly one device-code run; exactly two business requests (1 + 1 retry).
    assert counts["device_auth"] == 1
    assert counts["business"] == 2


# ---------------------------------------------------------------------------
# AC #3 + AC #9 bullet 6 — retry-once: 401 → re-auth → 200
# ---------------------------------------------------------------------------


def test_auto_auth_in_flight_401_then_200_after_reauth(
    auth_profile: ProfileConfig, tmp_path: Path
) -> None:
    credentials_dir = tmp_path / "credentials"
    save_token_cache(
        "ent",
        TokenCacheEntry(access_token="old", refresh_token="r", expires_at=9_999_999_999),
        credentials_dir=credentials_dir,
    )
    counts: dict[str, int] = {"business": 0, "device_auth": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            counts["device_auth"] += 1
            return _device_auth_response()
        if _is_token(request):
            return _token_response("new-token")
        counts["business"] += 1
        if counts["business"] == 1:
            return httpx.Response(401)
        # Second call should carry the new token.
        assert request.headers.get("Authorization") == "Bearer new-token"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    resp = client.get("/teams")
    client.close()

    assert resp.status_code == 200
    assert counts["device_auth"] == 1
    assert counts["business"] == 2


# ---------------------------------------------------------------------------
# AC #3 + AC #9 bullet 8 — pre-flight on missing cache
# ---------------------------------------------------------------------------


def test_preflight_runs_device_code_on_missing_cache(
    auth_profile: ProfileConfig, tmp_path: Path
) -> None:
    credentials_dir = tmp_path / "credentials"
    assert not credentials_dir.exists()
    counts: dict[str, int] = {"business": 0, "device_auth": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            counts["device_auth"] += 1
            return _device_auth_response()
        if _is_token(request):
            return _token_response()
        # Business request — no 401 in this variant.
        counts["business"] += 1
        assert request.headers.get("Authorization", "").startswith("Bearer ")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    resp = client.get("/teams")
    client.close()

    assert resp.status_code == 200
    assert counts["device_auth"] == 1  # pre-flight only
    assert counts["business"] == 1  # NOT retried
    assert (credentials_dir / "ent.json").exists()


# ---------------------------------------------------------------------------
# AC #3 + AC #9 bullet 9 — pre-flight on invalid_grant refresh
# ---------------------------------------------------------------------------


def test_preflight_runs_device_code_on_invalid_grant_refresh(
    auth_profile: ProfileConfig, tmp_path: Path
) -> None:
    credentials_dir = tmp_path / "credentials"
    # Cache has an EXPIRED access token → get_access_token attempts refresh.
    save_token_cache(
        "ent",
        TokenCacheEntry(
            access_token="expired-token",
            refresh_token="stale-refresh",
            expires_at=0,  # far in the past
        ),
        credentials_dir=credentials_dir,
    )
    counts: dict[str, int] = {
        "token_refresh": 0,
        "token_device": 0,
        "device_auth": 0,
        "business": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            counts["device_auth"] += 1
            return _device_auth_response()
        if _is_token(request):
            # Disambiguate: a refresh grant carries `grant_type=refresh_token`
            # in the body; a device-code poll carries
            # `grant_type=urn:ietf:params:oauth:grant-type:device_code`.
            body = request.content.decode("utf-8")
            if "grant_type=refresh_token" in body:
                counts["token_refresh"] += 1
                return httpx.Response(400, json={"error": "invalid_grant"})
            counts["token_device"] += 1
            return _token_response("new-token")
        counts["business"] += 1
        assert request.headers.get("Authorization") == "Bearer new-token"
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    resp = client.get("/teams")
    client.close()

    assert resp.status_code == 200
    # Refresh was attempted once and rejected; device-code ran once to recover.
    assert counts["token_refresh"] == 1
    assert counts["device_auth"] == 1
    assert counts["token_device"] == 1
    assert counts["business"] == 1
    # Cache was rewritten.
    cache_file = credentials_dir / "ent.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_bytes())
    assert data["access_token"] == "new-token"


# ---------------------------------------------------------------------------
# AC #4 + AC #9 bullet 10 — auto-auth disabled for no-auth profile
# ---------------------------------------------------------------------------


def test_auto_auth_disabled_for_no_auth_profile(
    noauth_profile: ProfileConfig, tmp_path: Path
) -> None:
    """OSS profile: 401 → ServerConfigurationError; no device-code, no Authorization header."""
    credentials_dir = tmp_path / "credentials"
    counts: dict[str, int] = {"business": 0, "oidc": 0}
    seen_headers: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request) or _is_device_auth(request) or _is_token(request):
            counts["oidc"] += 1
            raise AssertionError("no-auth profile must not contact OIDC endpoints")
        counts["business"] += 1
        seen_headers.append(dict(request.headers))
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(handler)
    client = build_http_client_with_auto_auth(
        noauth_profile,
        profile_name="oss",
        credentials_dir=credentials_dir,
        transport=transport,
    )
    with pytest.raises(ServerConfigurationError):
        client.get("/teams")
    client.close()

    assert counts["oidc"] == 0
    assert counts["business"] == 1  # NOT retried
    assert "authorization" not in {k.lower() for k in seen_headers[0].keys()}


# ---------------------------------------------------------------------------
# AC #3 — no-auth path and auth path do not share state (independent clients)
# ---------------------------------------------------------------------------


def test_auto_auth_factory_calls_are_independent(
    auth_profile: ProfileConfig, noauth_profile: ProfileConfig, tmp_path: Path
) -> None:
    credentials_dir = tmp_path / "credentials"

    def auth_handler(request: httpx.Request) -> httpx.Response:
        if _is_discovery(request):
            return _discovery_response()
        if _is_device_auth(request):
            return _device_auth_response()
        if _is_token(request):
            return _token_response()
        return httpx.Response(200, json={"ok": True})

    def oss_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    auth_client = build_http_client_with_auto_auth(
        auth_profile,
        profile_name="ent",
        credentials_dir=credentials_dir,
        transport=httpx.MockTransport(auth_handler),
    )
    oss_client = build_http_client_with_auto_auth(
        noauth_profile,
        profile_name="oss",
        credentials_dir=credentials_dir,
        transport=httpx.MockTransport(oss_handler),
    )

    assert auth_client is not oss_client
    r1 = auth_client.get("/a")
    r2 = oss_client.get("/b")
    assert r1.status_code == 200
    assert r2.status_code == 200
    auth_client.close()
    oss_client.close()
