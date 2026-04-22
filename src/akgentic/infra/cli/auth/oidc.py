"""OIDC device-authorization-grant flow (RFC 8628) for the akgentic CLI.

Implements ADR-021 §Decision 2 — the CLI's only supported OIDC flow is the
device-code flow. This module provides pure protocol plumbing (discovery,
initiate, poll) and a typed exception hierarchy; the persistent-state glue
(cache I/O + ``OidcTokenProvider``) lives in :mod:`.cache` and
:mod:`.token_provider`.

Design decisions (documented per Story 21.3's "Decisions the Dev agent must
make"):

* **Discovery seam.** :func:`discover_endpoints` fetches
  ``{issuer}/.well-known/openid-configuration`` once per call. The higher-level
  orchestrator in :class:`OidcTokenProvider` caches the resolved
  :class:`OidcEndpoints` for the instance lifetime, so refresh calls do not
  re-fetch ``.well-known``.
* **Module-level function vs. method for the orchestrator.** The orchestrator
  lives on :class:`OidcTokenProvider` (see :mod:`.token_provider`); it already
  holds profile, clock, sleep, http_client, credentials_dir.
* **Strict vs. lenient refresh_token.** See :mod:`.token_provider` — the
  strict path is chosen there.
* **No network I/O outside the injected ``httpx.Client``.** Tests inject an
  :class:`httpx.MockTransport`; there is no module-level global client.

Golden Rule #1: every wire payload (:class:`DeviceAuthorizationResponse`,
:class:`TokenResponse`, :class:`OidcErrorResponse`, :class:`OidcEndpoints`)
is a Pydantic model. ``response.json()`` into a raw dict is forbidden.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from pydantic import BaseModel, ValidationError

# ---------------------------------------------------------------------------
# Typed error hierarchy (AC #2)
# ---------------------------------------------------------------------------


class OidcProtocolError(Exception):
    """Base class for all OIDC protocol-layer errors raised by this module."""


class AuthorizationPendingError(OidcProtocolError):
    """RFC 8628 ``authorization_pending`` — internal polling signal."""


class SlowDownError(OidcProtocolError):
    """RFC 8628 ``slow_down`` — internal polling signal; widens the interval."""


class AccessDeniedError(OidcProtocolError):
    """RFC 8628 ``access_denied`` — end user denied consent."""


class ExpiredTokenError(OidcProtocolError):
    """RFC 8628 ``expired_token`` / local budget exhaustion.

    Raised when the device-code flow's ``expires_in`` budget is exhausted
    before the user completed consent.
    """


class OidcDiscoveryError(OidcProtocolError):
    """``.well-known/openid-configuration`` fetch failed or returned bad JSON."""


# ---------------------------------------------------------------------------
# Typed wire payloads (AC #1, #8 — Golden Rule #1)
# ---------------------------------------------------------------------------


class OidcEndpoints(BaseModel):
    """Resolved OIDC endpoints for an issuer.

    Populated by :func:`discover_endpoints` from the issuer's
    ``.well-known/openid-configuration`` document.
    """

    device_authorization_endpoint: str
    token_endpoint: str


class DeviceAuthorizationResponse(BaseModel):
    """RFC 8628 §3.2 device-authorization response."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    expires_in: int
    interval: int = 5  # RFC 8628 default when server omits it


class TokenResponse(BaseModel):
    """RFC 6749 §5.1 token response (success).

    We keep the shape narrow and strict — OIDC servers return more fields
    (``scope``, ``id_token``, ...) but the CLI cache only stores the three
    fields specified by ADR-021. Extra fields are ignored by Pydantic's
    default behavior.
    """

    access_token: str
    refresh_token: str | None = None
    expires_in: int
    token_type: str


class OidcErrorResponse(BaseModel):
    """RFC 6749 §5.2 / RFC 8628 §3.5 error response."""

    error: str
    error_description: str | None = None


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------


def _parse_error_response(response: httpx.Response) -> OidcErrorResponse | None:
    """Parse a non-2xx OIDC response body as :class:`OidcErrorResponse`.

    Returns ``None`` when the body is not valid JSON or does not match the
    OIDC error schema; callers then surface a generic protocol error.
    """
    try:
        return OidcErrorResponse.model_validate_json(response.content)
    except ValidationError:
        return None


def discover_endpoints(issuer: str, *, client: httpx.Client) -> OidcEndpoints:
    """Fetch ``{issuer}/.well-known/openid-configuration`` and extract endpoints.

    Args:
        issuer: Issuer base URL (string form of ``AuthConfig.issuer``).
        client: Injected ``httpx.Client`` (test seam — pass one backed by
            :class:`httpx.MockTransport` in tests).

    Returns:
        A validated :class:`OidcEndpoints`.

    Raises:
        OidcDiscoveryError: Network error, non-2xx status, or a response body
            that fails Pydantic validation (missing endpoint fields, etc.).
    """
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise OidcDiscoveryError(
            f"Failed to fetch OIDC discovery document from {url}: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise OidcDiscoveryError(
            f"OIDC discovery document at {url} returned HTTP {response.status_code}"
        )

    try:
        return OidcEndpoints.model_validate_json(response.content)
    except ValidationError as exc:
        raise OidcDiscoveryError(
            f"OIDC discovery document at {url} is missing required endpoints: {exc}"
        ) from exc


def initiate_device_flow(
    endpoints: OidcEndpoints,
    client_id: str,
    *,
    client: httpx.Client,
    scope: str = "openid offline_access",
) -> DeviceAuthorizationResponse:
    """POST to ``device_authorization_endpoint`` and parse the response.

    Raises:
        OidcProtocolError: Network error or a non-2xx response whose body is
            not a valid :class:`DeviceAuthorizationResponse`.
    """
    try:
        response = client.post(
            endpoints.device_authorization_endpoint,
            data={"client_id": client_id, "scope": scope},
        )
    except httpx.HTTPError as exc:
        raise OidcProtocolError(
            f"Device-authorization request to {endpoints.device_authorization_endpoint} failed: "
            f"{exc}"
        ) from exc

    if response.status_code >= 400:
        err = _parse_error_response(response)
        if err is not None:
            raise OidcProtocolError(
                f"Device-authorization request failed: {err.error}"
                + (f" — {err.error_description}" if err.error_description else "")
            )
        raise OidcProtocolError(
            f"Device-authorization request failed with HTTP {response.status_code}"
        )

    try:
        return DeviceAuthorizationResponse.model_validate_json(response.content)
    except ValidationError as exc:
        raise OidcProtocolError(
            f"Device-authorization response from {endpoints.device_authorization_endpoint} "
            f"is malformed: {exc}"
        ) from exc


def _classify_token_error(err: OidcErrorResponse) -> OidcProtocolError:
    """Map an OIDC token-endpoint error payload to a typed exception."""
    code = err.error
    description = f" — {err.error_description}" if err.error_description else ""
    if code == "authorization_pending":
        return AuthorizationPendingError(f"authorization_pending{description}")
    if code == "slow_down":
        return SlowDownError(f"slow_down{description}")
    if code == "access_denied":
        return AccessDeniedError(f"access_denied{description}")
    if code == "expired_token":
        return ExpiredTokenError(f"expired_token{description}")
    return OidcProtocolError(f"{code}{description}")


def _request_token(
    endpoints: OidcEndpoints,
    data: dict[str, str],
    *,
    client: httpx.Client,
) -> TokenResponse:
    """POST to the token endpoint and parse as :class:`TokenResponse`."""
    try:
        response = client.post(endpoints.token_endpoint, data=data)
    except httpx.HTTPError as exc:
        raise OidcProtocolError(
            f"Token request to {endpoints.token_endpoint} failed: {exc}"
        ) from exc

    if response.status_code >= 400:
        err = _parse_error_response(response)
        if err is not None:
            raise _classify_token_error(err)
        raise OidcProtocolError(f"Token request failed with HTTP {response.status_code}")

    try:
        return TokenResponse.model_validate_json(response.content)
    except ValidationError as exc:
        raise OidcProtocolError(
            f"Token response from {endpoints.token_endpoint} is malformed: {exc}"
        ) from exc


def poll_for_token(
    endpoints: OidcEndpoints,
    client_id: str,
    device_code: str,
    initial_interval: int,
    expires_in: int,
    *,
    client: httpx.Client,
    clock: Callable[[], int] = lambda: int(time.time()),
    sleep: Callable[[float], None] = time.sleep,
) -> TokenResponse:
    """Poll the token endpoint until success, denial, or budget exhaustion.

    Honors RFC 8628 ``slow_down`` (widen interval by 5s, per spec recommendation)
    and ``authorization_pending`` (continue polling). Terminal errors are
    surfaced as typed exceptions.

    Args:
        endpoints: Resolved :class:`OidcEndpoints`.
        client_id: OIDC client identifier.
        device_code: ``device_code`` from the device-auth response.
        initial_interval: Polling interval in seconds (from the device-auth
            response's ``interval`` field).
        expires_in: Flow-level expiry budget from the device-auth response.
        client: Injected HTTP client (test seam).
        clock: Returns current epoch seconds (test seam).
        sleep: Blocks the given number of seconds (test seam — tests pass a
            mock that records calls).

    Returns:
        A :class:`TokenResponse` on success.

    Raises:
        AccessDeniedError: Server returned ``access_denied``.
        ExpiredTokenError: Server returned ``expired_token`` OR the local
            budget (``expires_in``) was exhausted.
        OidcProtocolError: Any other protocol failure.
    """
    deadline = clock() + expires_in
    interval = initial_interval
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": client_id,
    }

    while True:
        if clock() >= deadline:
            raise ExpiredTokenError(
                "Device-code flow timed out before the user completed authorization"
            )
        sleep(interval)
        try:
            return _request_token(endpoints, data, client=client)
        except AuthorizationPendingError:
            continue
        except SlowDownError:
            interval += 5  # RFC 8628 recommendation
            continue


__all__ = [
    "AccessDeniedError",
    "AuthorizationPendingError",
    "DeviceAuthorizationResponse",
    "ExpiredTokenError",
    "OidcDiscoveryError",
    "OidcEndpoints",
    "OidcErrorResponse",
    "OidcProtocolError",
    "SlowDownError",
    "TokenResponse",
    "discover_endpoints",
    "initiate_device_flow",
    "poll_for_token",
]
