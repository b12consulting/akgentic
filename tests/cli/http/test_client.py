"""Tests for :func:`akgentic.infra.cli.http.build_http_client` (Story 21.2).

All tests use ``httpx.MockTransport`` — no real network, no live server.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from akgentic.infra.cli.auth import TokenProvider
from akgentic.infra.cli.config.profile import AuthConfig, ProfileConfig
from akgentic.infra.cli.http import (
    AuthenticationError,
    InvalidClientConfigurationError,
    ServerConfigurationError,
    build_http_client,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeTokenProvider:
    """Test double for :class:`TokenProvider`.

    In constant mode it returns ``constant_token`` every call. In sequence
    mode, it iterates ``sequence`` and returns the successive values (AC #8's
    per-request fetch test relies on this).
    """

    def __init__(
        self,
        *,
        constant_token: str | None = None,
        sequence: list[str] | None = None,
    ) -> None:
        if (constant_token is None) == (sequence is None):
            raise ValueError("Pass exactly one of constant_token or sequence")
        self._constant = constant_token
        self._sequence_iter: Iterator[str] | None = iter(sequence) if sequence else None

    def get_access_token(self) -> str:
        if self._constant is not None:
            return self._constant
        assert self._sequence_iter is not None
        return next(self._sequence_iter)


@pytest.fixture
def oss_profile() -> ProfileConfig:
    return ProfileConfig(endpoint="https://oss.example.invalid")  # type: ignore[arg-type]


@pytest.fixture
def auth_profile() -> ProfileConfig:
    return ProfileConfig(
        endpoint="https://enterprise.example.invalid",  # type: ignore[arg-type]
        auth=AuthConfig(
            type="oidc",
            issuer="https://example.invalid/realms/acme",  # type: ignore[arg-type]
            client_id="akgentic-cli",
        ),
    )


def _ok_transport(capture: list[httpx.Request]) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        capture.append(request)
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(_handler)


def _status_transport(status: int) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    return httpx.MockTransport(_handler)


# ---------------------------------------------------------------------------
# AC #4: TokenProvider is a runtime_checkable Protocol
# ---------------------------------------------------------------------------


def test_token_provider_is_runtime_checkable() -> None:
    assert isinstance(FakeTokenProvider(constant_token="x"), TokenProvider)


# ---------------------------------------------------------------------------
# AC #2: OSS profile sends NO Authorization header
# ---------------------------------------------------------------------------


def test_oss_profile_sends_no_authorization_header(oss_profile: ProfileConfig) -> None:
    captured: list[httpx.Request] = []
    client = build_http_client(
        oss_profile,
        profile_name="oss",
        transport=_ok_transport(captured),
    )
    try:
        response = client.get("/anything")
    finally:
        client.close()

    assert response.status_code == 200
    assert len(captured) == 1
    assert "Authorization" not in captured[0].headers


# ---------------------------------------------------------------------------
# AC #5 + #6: 401 on OSS profile raises ServerConfigurationError
# ---------------------------------------------------------------------------


def test_oss_profile_401_raises_server_configuration_error(
    oss_profile: ProfileConfig,
) -> None:
    client = build_http_client(
        oss_profile,
        profile_name="oss-local",
        transport=_status_transport(401),
    )
    try:
        with pytest.raises(ServerConfigurationError) as excinfo:
            client.get("/protected")
    finally:
        client.close()

    msg = str(excinfo.value)
    # Operator-actionable message — mentions the profile and "profile configuration".
    assert "oss-local" in msg
    assert "profile configuration" in msg
    # MUST NOT tell the operator to "login" — this is a config error, not a creds error.
    assert "login" not in msg.lower()
    assert excinfo.value.profile_name == "oss-local"


# ---------------------------------------------------------------------------
# AC #3: Auth-enabled profile attaches Authorization: Bearer <token>
# ---------------------------------------------------------------------------


def test_auth_profile_attaches_bearer_token(auth_profile: ProfileConfig) -> None:
    captured: list[httpx.Request] = []
    client = build_http_client(
        auth_profile,
        token_provider=FakeTokenProvider(constant_token="tok-1"),
        profile_name="ent",
        transport=_ok_transport(captured),
    )
    try:
        client.get("/me")
    finally:
        client.close()

    assert captured[0].headers.get("Authorization") == "Bearer tok-1"


# ---------------------------------------------------------------------------
# AC #3: Bearer token is fetched per-request (not frozen at build time)
# ---------------------------------------------------------------------------


def test_auth_profile_fetches_token_per_request(auth_profile: ProfileConfig) -> None:
    captured: list[httpx.Request] = []
    provider = FakeTokenProvider(sequence=["tok-A", "tok-B"])
    client = build_http_client(
        auth_profile,
        token_provider=provider,
        profile_name="ent",
        transport=_ok_transport(captured),
    )
    try:
        client.get("/first")
        client.get("/second")
    finally:
        client.close()

    assert captured[0].headers.get("Authorization") == "Bearer tok-A"
    assert captured[1].headers.get("Authorization") == "Bearer tok-B"


# ---------------------------------------------------------------------------
# AC #5 + #6: 401 on auth-enabled profile raises AuthenticationError (no retry)
# ---------------------------------------------------------------------------


def test_auth_profile_401_raises_authentication_error(
    auth_profile: ProfileConfig,
) -> None:
    calls: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    client = build_http_client(
        auth_profile,
        token_provider=FakeTokenProvider(constant_token="bad-token"),
        profile_name="ent",
        transport=httpx.MockTransport(_handler),
    )
    try:
        with pytest.raises(AuthenticationError) as excinfo:
            client.get("/me")
    finally:
        client.close()

    assert not isinstance(excinfo.value, ServerConfigurationError)
    # No retry in 21.2 — exactly one request was made.
    assert len(calls) == 1
    assert excinfo.value.profile_name == "ent"


# ---------------------------------------------------------------------------
# AC #6: Non-401 responses are NOT translated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 500])
def test_non_401_responses_are_not_translated(oss_profile: ProfileConfig, status: int) -> None:
    client = build_http_client(
        oss_profile,
        profile_name="oss",
        transport=_status_transport(status),
    )
    try:
        response = client.get("/boom")
    finally:
        client.close()

    # No translation — the raw Response surfaces, status unchanged.
    assert response.status_code == status


# ---------------------------------------------------------------------------
# AC #7: API-misuse guards (factory-time)
# ---------------------------------------------------------------------------


def test_oss_profile_with_token_provider_raises_invalid_config(
    oss_profile: ProfileConfig,
) -> None:
    with pytest.raises(InvalidClientConfigurationError) as excinfo:
        build_http_client(
            oss_profile,
            token_provider=FakeTokenProvider(constant_token="nope"),
            profile_name="oss",
        )
    msg = str(excinfo.value)
    assert "token_provider" in msg
    assert "auth" in msg


def test_auth_profile_without_token_provider_raises_invalid_config(
    auth_profile: ProfileConfig,
) -> None:
    with pytest.raises(InvalidClientConfigurationError) as excinfo:
        build_http_client(
            auth_profile,
            token_provider=None,
            profile_name="ent",
        )
    msg = str(excinfo.value)
    assert "TokenProvider" in msg or "token_provider" in msg


# ---------------------------------------------------------------------------
# AC #1: base_url and returned type
# ---------------------------------------------------------------------------


def test_client_is_bound_to_profile_endpoint(oss_profile: ProfileConfig) -> None:
    captured: list[httpx.Request] = []
    client = build_http_client(
        oss_profile,
        profile_name="oss",
        transport=_ok_transport(captured),
    )
    try:
        assert isinstance(client, httpx.Client)
        client.get("/ping")
    finally:
        client.close()

    # base_url + relative path resolve to the profile endpoint host.
    assert captured[0].url.host == "oss.example.invalid"
    assert captured[0].url.path == "/ping"


def test_build_without_profile_name_falls_back_to_placeholder(
    oss_profile: ProfileConfig,
) -> None:
    """Defensive: callers that forget to pass profile_name still get a usable error."""
    client = build_http_client(
        oss_profile,
        transport=_status_transport(401),
    )
    try:
        with pytest.raises(ServerConfigurationError) as excinfo:
            client.get("/x")
    finally:
        client.close()
    assert excinfo.value.profile_name == "<unknown>"
