"""HTTP client factory for CLI commands.

Implements ADR-021 §Decision 1 — the runtime signal for "attach auth" is
``profile.auth is None`` versus ``profile.auth is not None``; we never branch
on a side flag. OSS profiles are guaranteed to send NO ``Authorization``
header, and a 401 from one is translated to :class:`ServerConfigurationError`
(an operator-actionable mismatch, not a login prompt).

Testing seam
------------

:func:`build_http_client` accepts a keyword-only ``transport`` argument typed
as ``httpx.BaseTransport | None``. Production callers leave it ``None`` and
httpx constructs its default HTTPS transport. Tests pass an
``httpx.MockTransport`` to intercept requests and assert on outgoing headers /
inject 401 responses, with zero real network I/O. This seam is documented
behavior, not an implementation accident — it stays stable for Story 21.4's
retry wiring.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import httpx

from akgentic.infra.cli.auth.token_provider import TokenProvider
from akgentic.infra.cli.config.profile import ProfileConfig
from akgentic.infra.cli.http.errors import (
    AuthenticationError,
    InvalidClientConfigurationError,
    ServerConfigurationError,
)

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class _BearerAuth(httpx.Auth):
    """httpx Auth flow that fetches a fresh bearer token per request.

    The token is NOT cached at construction time — each request calls
    ``token_provider.get_access_token()``, so rotation handled by the provider
    is observed without rebuilding the client (AC #3).
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(self, token_provider: TokenProvider) -> None:
        self._token_provider = token_provider

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token = self._token_provider.get_access_token()
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def _enforce_misuse_guards(profile: ProfileConfig, token_provider: TokenProvider | None) -> None:
    """Reject incoherent ``(profile, token_provider)`` combinations loudly."""
    if profile.auth is None and token_provider is not None:
        raise InvalidClientConfigurationError(
            "Profile has no `auth` block (OSS profile) but a token_provider was "
            "supplied. OSS profiles must never attach an Authorization header; "
            "pass token_provider=None or configure an `auth:` block on the profile."
        )
    if profile.auth is not None and token_provider is None:
        raise InvalidClientConfigurationError(
            "Profile declares an `auth` block but no token_provider was supplied. "
            "Pass a TokenProvider (Story 21.3 provides OidcTokenProvider) so the "
            "factory can attach bearer tokens on outgoing requests."
        )


def _build_raise_on_401_hook(
    profile: ProfileConfig, profile_name: str
) -> Callable[[httpx.Response], None]:
    """Return a response event hook that translates 401s into typed errors.

    Non-401 responses pass through untouched; the factory's ONLY behavioral
    departure from a stock ``httpx.Client`` is this 401 translation.
    """

    def _on_response(response: httpx.Response) -> None:
        if response.status_code != 401:
            return
        url = str(response.request.url)
        if profile.auth is None:
            raise ServerConfigurationError(profile_name=profile_name, url=url)
        raise AuthenticationError(profile_name=profile_name, url=url)

    return _on_response


def build_http_client(
    profile: ProfileConfig,
    token_provider: TokenProvider | None = None,
    *,
    profile_name: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Construct an ``httpx.Client`` wired for the active CLI profile.

    The returned client:

    * Uses ``profile.endpoint`` as its ``base_url`` (stringified from Pydantic
      ``HttpUrl``).
    * Has ``follow_redirects=True`` and a sane default timeout.
    * Attaches ``Authorization: Bearer <token>`` on every request IFF
      ``profile.auth is not None`` — otherwise NEVER sends an Authorization
      header (ADR-021 OSS invariant, AC #2).
    * Raises :class:`ServerConfigurationError` on a 401 from an OSS profile
      and :class:`AuthenticationError` on a 401 from an auth-enabled profile.
      Non-401 responses (2xx, 4xx other than 401, 5xx, network errors) are
      NOT translated — callers see httpx's native surface.

    Args:
        profile: The active :class:`ProfileConfig` (from ``resolve_profile``).
        token_provider: Required when ``profile.auth is not None``; MUST be
            ``None`` when ``profile.auth is None``. Mismatches raise
            :class:`InvalidClientConfigurationError` at call time.
        profile_name: The resolved profile name — used in 401 error messages
            so operators know which profile to edit. Defaults to the string
            ``"<unknown>"`` if the caller did not pass one.
        transport: Test seam. Pass an ``httpx.MockTransport`` to intercept
            requests in tests. ``None`` in production.

    Returns:
        A ready-to-use ``httpx.Client`` bound to the profile's endpoint.

    Raises:
        InvalidClientConfigurationError: When ``profile`` and ``token_provider``
            are incoherent (AC #7).
    """
    _enforce_misuse_guards(profile, token_provider)

    resolved_name = profile_name if profile_name is not None else "<unknown>"

    auth: httpx.Auth | None
    if profile.auth is not None:
        assert token_provider is not None  # guaranteed by _enforce_misuse_guards
        auth = _BearerAuth(token_provider)
    else:
        auth = None

    client = httpx.Client(
        base_url=str(profile.endpoint),
        follow_redirects=True,
        timeout=_DEFAULT_TIMEOUT,
        auth=auth,
        transport=transport,
    )
    client.event_hooks["response"].append(_build_raise_on_401_hook(profile, resolved_name))
    return client
