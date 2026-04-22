"""Inline retry-once auto-auth HTTP client factory.

Implements ADR-021 §Decision 2 — composes :func:`build_http_client` with
:class:`OidcTokenProvider` plus a strict per-request retry budget of one.

Design decisions (documented here per Story 21.4 "Decisions the Dev agent
must make"):

* **Shape B — transport-layer retry.** The retry-once layer is an
  :class:`httpx.BaseTransport` that wraps the underlying transport. On a
  401 response from the underlying transport, the layer runs the
  interactive device-code flow, refreshes the Authorization header, and
  replays the request **exactly once**. A second 401 surfaces unchanged —
  the client's response hook from :func:`build_http_client` then translates
  it to :class:`AuthenticationError` (Story 21.2's frozen surface). The
  retry-layer does not see that translation because it returns the second
  401 to the client layer, where hooks run.

  Chosen over Shape A (a custom ``httpx.Auth.auth_flow``) because the
  existing :func:`build_http_client` installs a response event hook that
  raises :class:`AuthenticationError` on 401 — event hooks run per response
  inside the auth flow, which would convert our retryable 401 into an
  exception before ``auth_flow`` could yield the retried request. A
  transport wrapper is outside the event-hook boundary, so the retry can
  complete before the client layer inspects the final response.

* **Pre-flight ``ReAuthRequiredError``.** At factory-time we call
  :meth:`OidcTokenProvider.get_access_token` once inside a ``try``/``except``
  block. If it raises :class:`ReAuthRequiredError` (missing cache, expired
  refresh token, ``invalid_grant``, etc.) we run
  :meth:`OidcTokenProvider.run_device_code_flow` synchronously, persist
  the cache, and then return the client. The transport-layer retry-once
  then only handles the "cached token was still valid but the server
  rejected it" case (e.g. server-side revocation between cache write and
  request) — keeping the two concerns cleanly separated.

* **No modification of :func:`build_http_client`.** The frozen Story 21.2
  surface is composed, not extended. We wrap its ``transport`` argument
  from the outside; its 401 response hook still runs and still translates
  "true" 401s (after our retry budget is exhausted) to typed errors.

* **No-auth profiles never reach the retry layer.** When
  ``profile.auth is None`` we delegate straight to :func:`build_http_client`
  with ``token_provider=None`` — no :class:`OidcTokenProvider` is
  constructed, no discovery call is made, no device-code endpoint is
  contacted. Auto-auth MUST NOT engage for OSS profiles (AC #4).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from akgentic.infra.cli.auth import (
    DeviceAuthorizationResponse,
    OidcTokenProvider,
    ReAuthRequiredError,
)
from akgentic.infra.cli.config.profile import ProfileConfig
from akgentic.infra.cli.http.client import build_http_client


class _RetryOnceTransport(httpx.BaseTransport):
    """Transport wrapper that retries a 401 exactly once after re-auth.

    On a 401 response from the inner transport:

    1. Close the 401 response (free underlying socket / body).
    2. Run :meth:`OidcTokenProvider.run_device_code_flow` to refresh
       credentials interactively.
    3. Fetch a fresh access token via :meth:`OidcTokenProvider.get_access_token`
       and rewrite the request's ``Authorization`` header.
    4. Replay the request ONCE. Whatever status that replay returns
       (200, 401, 5xx, …) flows back to the client layer unchanged — no
       second retry is ever attempted.

    Per-request state: each call to :meth:`handle_request` runs independent
    retry logic. There is no shared counter across requests — a fresh
    client, a fresh request, and a fresh retry budget of one.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport,
        provider: OidcTokenProvider,
        *,
        on_user_code: Callable[[DeviceAuthorizationResponse], None] | None,
    ) -> None:
        self._inner = inner
        self._provider = provider
        self._on_user_code = on_user_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._inner.handle_request(request)
        if response.status_code != 401:
            return response

        # Drain and close the 401 body before re-using the request.
        try:
            response.read()
        finally:
            response.close()

        # Re-auth interactively. Any failure here (user denial, network
        # issues, etc.) surfaces as an OidcProtocolError to the caller —
        # we do NOT swallow auth-flow errors.
        self._provider.run_device_code_flow(on_user_code=self._on_user_code)

        # Rewrite Authorization with the freshly cached token and replay.
        new_token = self._provider.get_access_token()
        request.headers["Authorization"] = f"Bearer {new_token}"
        return self._inner.handle_request(request)

    def close(self) -> None:  # pragma: no cover - simple delegation
        self._inner.close()


def _preflight_credentials(
    provider: OidcTokenProvider,
    *,
    on_user_code: Callable[[DeviceAuthorizationResponse], None] | None,
) -> None:
    """Ensure a usable access token exists in the cache before first use.

    Calls :meth:`OidcTokenProvider.get_access_token` once. If that raises
    :class:`ReAuthRequiredError` (missing cache, expired refresh, etc.) we
    run the device-code flow synchronously to populate the cache.

    Returns None; side effect is that the on-disk cache is valid when this
    function returns. Any non-:class:`ReAuthRequiredError` exception from
    :meth:`get_access_token` propagates unchanged.
    """
    try:
        provider.get_access_token()
    except ReAuthRequiredError:
        provider.run_device_code_flow(on_user_code=on_user_code)


def build_http_client_with_auto_auth(
    profile: ProfileConfig,
    *,
    profile_name: str,
    credentials_dir: Path | None = None,
    transport: httpx.BaseTransport | None = None,
    http_client_for_auth: httpx.Client | None = None,
    on_user_code: Callable[[DeviceAuthorizationResponse], None] | None = None,
) -> httpx.Client:
    """Build an HTTP client with inline retry-once auto-auth for the given profile.

    Behaves as :func:`build_http_client` augmented with:

    * Pre-flight of the token cache — if no valid access token exists, the
      device-code flow runs once before the client is returned.
    * Transport-level retry-once on 401 — a single in-flight 401 triggers
      re-auth + one replay; a second 401 surfaces as
      :class:`AuthenticationError` via the existing 401 response hook.

    For no-auth profiles this function delegates straight to
    :func:`build_http_client` with ``token_provider=None`` — no
    :class:`OidcTokenProvider` is constructed, and the auto-auth machinery
    never engages.

    Args:
        profile: Active :class:`ProfileConfig` (from ``resolve_profile``).
        profile_name: Resolved profile name — used in 401 error messages
            and for cache-file naming.
        credentials_dir: Optional override for the per-profile token cache
            directory. ``None`` resolves to ``~/.akgentic/credentials``.
        transport: Test seam. Pass an ``httpx.MockTransport`` to intercept
            requests in tests. Threaded both into the business
            :class:`httpx.Client` and (when auth is enabled) into the
            :class:`OidcTokenProvider`'s discovery / token client so a
            single mock transport can stage the full conversation.
        http_client_for_auth: Optional dedicated :class:`httpx.Client` for
            OIDC traffic. When ``None`` and ``transport`` is provided, a
            client wrapping ``transport`` is constructed so discovery and
            token calls share the test's mock. When both are ``None`` the
            provider constructs its own default client.
        on_user_code: Optional hook invoked with the
            :class:`DeviceAuthorizationResponse` so the caller can display
            the user code / verification URL. Defaults to a stderr writer
            (see :class:`OidcTokenProvider`).

    Returns:
        A ready-to-use :class:`httpx.Client` bound to ``profile.endpoint``.

    Raises:
        InvalidClientConfigurationError: If ``profile`` and token-provider
            wiring are incoherent (bubbled from :func:`build_http_client`).
        OidcProtocolError: If pre-flight device-code flow fails (e.g. user
            denial, expired device code). Callers see the typed surface
            from :mod:`akgentic.infra.cli.auth`.
    """
    if profile.auth is None:
        # No-auth profile: never construct a provider, never install the
        # retry layer. 401s flow straight through to
        # ServerConfigurationError via build_http_client's response hook.
        return build_http_client(
            profile,
            token_provider=None,
            profile_name=profile_name,
            transport=transport,
        )

    # Auth-enabled profile: build the provider first, pre-flight, then
    # wrap the requested transport with the retry-once layer.
    auth_http_client = http_client_for_auth
    if auth_http_client is None and transport is not None:
        # Share the test's mock transport so discovery / device-code /
        # token calls all land on the same handler.
        auth_http_client = httpx.Client(transport=transport)

    provider = OidcTokenProvider(
        profile,
        profile_name,
        http_client=auth_http_client,
        credentials_dir=credentials_dir,
    )

    _preflight_credentials(provider, on_user_code=on_user_code)

    # Wrap the caller's transport (or httpx's default) with the retry-once
    # layer. The wrapped transport is passed into build_http_client, which
    # installs _BearerAuth and the 401 response hook on top — the retry
    # layer runs BEFORE those (transport layer sits below the client).
    inner_transport = transport if transport is not None else httpx.HTTPTransport()
    retry_transport = _RetryOnceTransport(
        inner_transport,
        provider,
        on_user_code=on_user_code,
    )

    return build_http_client(
        profile,
        token_provider=provider,
        profile_name=profile_name,
        transport=retry_transport,
    )


__all__ = ["build_http_client_with_auto_auth"]
