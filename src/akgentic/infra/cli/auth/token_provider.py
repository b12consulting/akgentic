"""Token provider Protocol + concrete OIDC implementation.

Story 21.2 delivered the :class:`TokenProvider` Protocol — the boundary the
HTTP client factory depends on. Story 21.3 adds :class:`OidcTokenProvider`
*in the same module* (per Story 21.3 Dev Notes — do not move the Protocol).

Division of responsibility (intentional — see Story 21.3 Dev Notes):

* :meth:`OidcTokenProvider.get_access_token` is the Protocol surface. It is
  **non-interactive**: uses cache + refresh only. On missing or invalid cache
  it raises :class:`ReAuthRequiredError`; Story 21.4's auto-auth wiring
  catches that and re-runs the device-code flow.
* :meth:`OidcTokenProvider.run_device_code_flow` is **interactive**: it
  drives the device-code flow, writes the cache, and returns the entry.
  Story 21.4's ``akgentic login`` and inline retry wiring calls this method.

Decisions documented here (per Story 21.3 "Decisions the Dev agent must
make"):

* **Discovery caching.** The instance caches the resolved
  :class:`OidcEndpoints` for its lifetime; refresh never re-fetches
  ``.well-known``.
* **Orchestrator as method, not module function.** Keeps the injection
  seams (``http_client``, ``clock``, ``sleep``, ``credentials_dir``) in one
  place rather than threading them through a module function.
* **Strict ``refresh_token`` handling.** The token endpoint MUST return a
  ``refresh_token`` on both device-code and refresh responses. If it is
  missing we treat the cache as unusable and raise
  :class:`ReAuthRequiredError` after purging. Keycloak (ADR-021's target)
  always returns one; the strict path is safe for the ADR's scope and keeps
  ``TokenCacheEntry.refresh_token`` non-optional.

Test seams: ``http_client``, ``clock``, ``sleep``, ``credentials_dir``,
``on_user_code``.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from akgentic.infra.cli.auth.cache import (
    TokenCacheEntry,
    delete_token_cache,
    load_token_cache,
    save_token_cache,
)
from akgentic.infra.cli.auth.oidc import (
    DeviceAuthorizationResponse,
    OidcEndpoints,
    OidcProtocolError,
    TokenResponse,
    _request_token,
    discover_endpoints,
    initiate_device_flow,
    poll_for_token,
)
from akgentic.infra.cli.config.profile import ProfileConfig

_REFRESH_LEEWAY_SECONDS = 30


# ---------------------------------------------------------------------------
# Protocol (unchanged from Story 21.2)
# ---------------------------------------------------------------------------


@runtime_checkable
class TokenProvider(Protocol):
    """Source of bearer tokens for authenticated CLI requests.

    Implementations are expected to handle their own caching / refresh policy.
    :func:`akgentic.infra.cli.http.build_http_client` calls
    :meth:`get_access_token` on every outgoing request, so a provider that
    caches internally picks up rotation transparently without the client being
    rebuilt.
    """

    def get_access_token(self) -> str:
        """Return a valid bearer token as a string.

        Implementations MUST NOT prefix the return value with ``"Bearer "`` —
        the factory formats the ``Authorization`` header.
        """
        ...


# ---------------------------------------------------------------------------
# Typed error raised by the concrete provider
# ---------------------------------------------------------------------------


class ReAuthRequiredError(OidcProtocolError):
    """Raised when :meth:`OidcTokenProvider.get_access_token` cannot produce
    a valid token without user interaction.

    Story 21.4's auto-auth wiring catches this and re-runs
    :meth:`OidcTokenProvider.run_device_code_flow`. Story 21.3 never calls
    the interactive flow from inside ``get_access_token`` — that would break
    the "non-interactive Protocol surface" contract.
    """


# ---------------------------------------------------------------------------
# Concrete provider
# ---------------------------------------------------------------------------


def _default_on_user_code(auth: DeviceAuthorizationResponse) -> None:
    """Default prompt hook — prints verification URL and user code to stderr.

    Story 21.4 replaces this with a TUI-friendly hook; writing to stderr
    keeps Story 21.3 safe to use in scripts (stdout stays clean for any
    command output).
    """
    message = (
        f"To authenticate, visit: {auth.verification_uri}\nAnd enter the code: {auth.user_code}\n"
    )
    if auth.verification_uri_complete:
        message += f"Or open directly: {auth.verification_uri_complete}\n"
    sys.stderr.write(message)
    sys.stderr.flush()


class OidcTokenProvider:
    """OIDC device-code + refresh-token provider.

    Construct with the active :class:`ProfileConfig` and profile name. The
    provider assumes ``profile.auth is not None`` — passing an OSS profile
    is a programming error (the HTTP client factory's guard catches the
    mismatch if the caller wires one through).
    """

    def __init__(
        self,
        profile: ProfileConfig,
        profile_name: str,
        *,
        http_client: httpx.Client | None = None,
        clock: Callable[[], int] | None = None,
        sleep: Callable[[float], None] | None = None,
        credentials_dir: Path | None = None,
    ) -> None:
        if profile.auth is None:
            raise ValueError(
                f"OidcTokenProvider requires a profile with an `auth` block; "
                f"profile {profile_name!r} has none."
            )
        self._profile = profile
        self._profile_name = profile_name
        self._http_client = http_client if http_client is not None else httpx.Client()
        self._owns_client = http_client is None
        self._clock: Callable[[], int] = clock if clock is not None else (lambda: int(time.time()))
        self._sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep
        self._credentials_dir = credentials_dir
        self._endpoints: OidcEndpoints | None = None

    # -- Public API ---------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a valid access token from cache (refreshing if expired).

        Contract (per AC #5):
            1. Cache hit with ``expires_at > now + leeway`` → return.
            2. Expired → refresh via ``grant_type=refresh_token``; on success
               write new entry and return new token.
            3. Refresh failure → purge cache, raise
               :class:`ReAuthRequiredError`.
            4. Cache missing → raise :class:`ReAuthRequiredError` without
               touching the network.

        :meth:`run_device_code_flow` is **NOT** called from here — Story
        21.4 owns the retry decision.
        """
        entry = load_token_cache(self._profile_name, credentials_dir=self._credentials_dir)
        if entry is None:
            raise ReAuthRequiredError(
                f"No cached credentials for profile {self._profile_name!r}; "
                "run the device-code flow to authenticate."
            )

        now = self._clock()
        if entry.expires_at > now + _REFRESH_LEEWAY_SECONDS:
            return entry.access_token

        return self._refresh_and_return(entry, now)

    def run_device_code_flow(
        self,
        *,
        on_user_code: Callable[[DeviceAuthorizationResponse], None] | None = None,
    ) -> TokenCacheEntry:
        """Drive the interactive device-code flow and persist the cache.

        Args:
            on_user_code: Hook invoked with the :class:`DeviceAuthorizationResponse`
                so the caller can display the user code / verification URL in
                whatever UX it prefers. Defaults to a stderr writer (Story
                21.4 injects its own TUI-friendly hook).

        Returns:
            The freshly persisted :class:`TokenCacheEntry`.
        """
        endpoints = self._ensure_endpoints()
        assert self._profile.auth is not None  # constructor guarded this
        client_id = self._profile.auth.client_id

        device_auth = initiate_device_flow(
            endpoints,
            client_id,
            client=self._http_client,
        )

        prompt_hook = on_user_code if on_user_code is not None else _default_on_user_code
        prompt_hook(device_auth)

        token_response = poll_for_token(
            endpoints,
            client_id,
            device_auth.device_code,
            device_auth.interval,
            device_auth.expires_in,
            client=self._http_client,
            clock=self._clock,
            sleep=self._sleep,
        )

        entry = self._token_response_to_cache_entry(token_response)
        save_token_cache(self._profile_name, entry, credentials_dir=self._credentials_dir)
        return entry

    def close(self) -> None:
        """Close the underlying httpx.Client if we own it."""
        if self._owns_client:
            self._http_client.close()

    # -- Internal helpers ---------------------------------------------------

    def _ensure_endpoints(self) -> OidcEndpoints:
        """Resolve and cache the OIDC endpoints for this provider instance."""
        if self._endpoints is None:
            assert self._profile.auth is not None
            self._endpoints = discover_endpoints(
                str(self._profile.auth.issuer),
                client=self._http_client,
            )
        return self._endpoints

    def _refresh_and_return(self, entry: TokenCacheEntry, now: int) -> str:
        """Refresh the access token and return the new value.

        On any refresh failure the cache is purged and
        :class:`ReAuthRequiredError` is raised.
        """
        endpoints = self._ensure_endpoints()
        assert self._profile.auth is not None
        client_id = self._profile.auth.client_id

        try:
            token_response = _request_token(
                endpoints,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": entry.refresh_token,
                    "client_id": client_id,
                },
                client=self._http_client,
            )
        except OidcProtocolError as exc:
            # ANY refresh failure → purge + re-auth. This includes
            # invalid_grant, invalid_token, expired refresh token, etc.
            delete_token_cache(self._profile_name, credentials_dir=self._credentials_dir)
            raise ReAuthRequiredError(
                f"Refresh failed for profile {self._profile_name!r}: {exc}. "
                "Cache purged — re-run the device-code flow to authenticate."
            ) from exc

        new_entry = self._token_response_to_cache_entry(token_response, fallback_now=now)
        save_token_cache(self._profile_name, new_entry, credentials_dir=self._credentials_dir)
        return new_entry.access_token

    def _token_response_to_cache_entry(
        self,
        response: TokenResponse,
        *,
        fallback_now: int | None = None,
    ) -> TokenCacheEntry:
        """Convert a :class:`TokenResponse` to a :class:`TokenCacheEntry`.

        Enforces two invariants in wire-order before writing the cache:
        (a) ``refresh_token`` must be present (strict module-level decision);
        (b) ``token_type`` must be ``Bearer`` (case-insensitive) — ADR-021
        §Decision 2 pins Bearer enforcement to this single cache-write funnel.
        """
        if response.refresh_token is None:
            # Strict path: we require a refresh_token to maintain the cache
            # invariant (TokenCacheEntry.refresh_token is non-optional).
            delete_token_cache(self._profile_name, credentials_dir=self._credentials_dir)
            raise ReAuthRequiredError(
                f"Token endpoint did not return a refresh_token for profile "
                f"{self._profile_name!r}; cache purged."
            )
        if response.token_type.casefold() != "bearer":
            # The downstream HTTP layer unconditionally formats the Authorization
            # header as "Bearer <token>"; any other scheme would silently mis-wrap
            # into an opaque 401. Purge-first to match the refresh_token branch's
            # side-effect ordering.
            delete_token_cache(self._profile_name, credentials_dir=self._credentials_dir)
            raise OidcProtocolError(
                f"Token endpoint returned unsupported token_type={response.token_type!r}; "
                "only 'Bearer' is supported."
            )
        now = fallback_now if fallback_now is not None else self._clock()
        return TokenCacheEntry(
            access_token=response.access_token,
            refresh_token=response.refresh_token,
            expires_at=now + response.expires_in,
        )


__all__ = [
    "OidcTokenProvider",
    "ReAuthRequiredError",
    "TokenProvider",
]
