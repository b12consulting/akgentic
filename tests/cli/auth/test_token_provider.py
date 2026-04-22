"""Focused tests for ``_token_response_to_cache_entry``.

The single funnel that validates ``refresh_token`` presence and
``token_type == 'Bearer'`` before writing the token cache. Story 22.6 adds
the Bearer check; the ``refresh_token`` check was shipped in Story 22.3 and
its existing coverage in ``test_oidc.py`` is preserved.

All network I/O runs through :class:`httpx.MockTransport` — NO real network.
All filesystem I/O runs through ``tmp_path`` via the ``credentials_dir``
seam — NEVER ``~/.akgentic/``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pydantic
import pytest

from akgentic.infra.cli.auth import (
    OidcTokenProvider,
    ReAuthRequiredError,
    TokenCacheEntry,
    save_token_cache,
)
from akgentic.infra.cli.auth.oidc import OidcProtocolError, TokenResponse
from akgentic.infra.cli.config.profile import AuthConfig, ProfileConfig

# ---------------------------------------------------------------------------
# Test seams
# ---------------------------------------------------------------------------

ISSUER = "https://issuer.example.com"
DEVICE_AUTH_ENDPOINT = f"{ISSUER}/device-auth"
TOKEN_ENDPOINT = f"{ISSUER}/token"
PROFILE_NAME = "ent-prof"


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
) -> OidcTokenProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OidcTokenProvider(
        auth_profile,
        PROFILE_NAME,
        http_client=client,
        clock=clock if clock is not None else (lambda: 1_700_000_000),
        sleep=lambda _s: None,
        credentials_dir=credentials_dir,
    )


def _cache_file(credentials_dir: Path) -> Path:
    return credentials_dir / f"{PROFILE_NAME}.json"


# ---------------------------------------------------------------------------
# Happy path — Bearer (canonical and case-insensitive)
# ---------------------------------------------------------------------------


def test_token_type_bearer_writes_cache(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    provider = _make_provider(
        auth_profile,
        handler=lambda _r: httpx.Response(500),  # should not be used
        credentials_dir=tmp_credentials_dir,
    )
    response = TokenResponse(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        expires_in=3600,
        token_type="Bearer",
    )
    entry = provider._token_response_to_cache_entry(response)
    assert entry.access_token == "access-abc"
    assert entry.refresh_token == "refresh-xyz"
    assert entry.expires_at == 1_700_000_000 + 3600
    provider.close()


@pytest.mark.parametrize("token_type", ["bearer", "BEARER", "BeArEr"])
def test_token_type_case_insensitive_bearer(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path, token_type: str
) -> None:
    provider = _make_provider(
        auth_profile,
        handler=lambda _r: httpx.Response(500),
        credentials_dir=tmp_credentials_dir,
    )
    response = TokenResponse(
        access_token="a",
        refresh_token="r",
        expires_in=3600,
        token_type=token_type,
    )
    entry = provider._token_response_to_cache_entry(response)
    assert entry.access_token == "a"
    provider.close()


# ---------------------------------------------------------------------------
# Rejection — device-code path
# ---------------------------------------------------------------------------


def test_token_type_non_bearer_raises_oidc_protocol_error_device_code_path(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == DEVICE_AUTH_ENDPOINT:
            return _json_response(
                {
                    "device_code": "dc",
                    "user_code": "UC",
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
                    "token_type": "MAC",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
    )
    with pytest.raises(OidcProtocolError) as excinfo:
        provider.run_device_code_flow(on_user_code=lambda _a: None)
    message = str(excinfo.value)
    assert "Bearer" in message
    assert "'MAC'" in message
    # No cache file should have been created.
    assert not _cache_file(tmp_credentials_dir).exists()
    provider.close()


# ---------------------------------------------------------------------------
# Rejection — refresh path with pre-existing cache
# ---------------------------------------------------------------------------


def test_token_type_non_bearer_purges_existing_cache_on_refresh(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    now = 1_700_000_000
    # Pre-seed a valid cache on disk so the refresh path is exercised.
    save_token_cache(
        PROFILE_NAME,
        TokenCacheEntry(
            access_token="old-access",
            refresh_token="refresh-me",
            expires_at=now - 60,  # already expired — forces refresh
        ),
        credentials_dir=tmp_credentials_dir,
    )
    assert _cache_file(tmp_credentials_dir).exists()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, content=_discovery_body())
        if str(request.url) == TOKEN_ENDPOINT:
            return _json_response(
                {
                    "access_token": "new",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "token_type": "DPoP",
                }
            )
        raise AssertionError(request.url)

    provider = _make_provider(
        auth_profile,
        handler=handler,
        credentials_dir=tmp_credentials_dir,
        clock=lambda: now,
    )
    with pytest.raises(OidcProtocolError) as excinfo:
        provider.get_access_token()
    message = str(excinfo.value)
    assert "Bearer" in message
    assert "'DPoP'" in message
    # Prior cache file must be purged.
    assert _cache_file(tmp_credentials_dir).exists() is False
    provider.close()


# ---------------------------------------------------------------------------
# Structural rejection — missing token_type field
# ---------------------------------------------------------------------------


def test_token_response_missing_token_type_raises_validation_error() -> None:
    payload = {
        "access_token": "a",
        "refresh_token": "r",
        "expires_in": 3600,
    }
    with pytest.raises(pydantic.ValidationError) as excinfo:
        TokenResponse.model_validate(payload)
    assert "token_type" in str(excinfo.value)


def test_reauth_required_takes_precedence_over_bearer_check(
    auth_profile: ProfileConfig, tmp_credentials_dir: Path
) -> None:
    """When BOTH refresh_token is missing AND token_type is non-Bearer,
    ReAuthRequiredError (the refresh_token guard) surfaces first — the
    in-wire-order check documented in the funnel docstring."""
    provider = _make_provider(
        auth_profile,
        handler=lambda _r: httpx.Response(500),
        credentials_dir=tmp_credentials_dir,
    )
    response = TokenResponse(
        access_token="a",
        refresh_token=None,
        expires_in=3600,
        token_type="MAC",
    )
    with pytest.raises(ReAuthRequiredError):
        provider._token_response_to_cache_entry(response)
    provider.close()
