"""CLI auth package.

Public API:

* :class:`TokenProvider` — Protocol boundary consumed by
  :func:`akgentic.infra.cli.http.build_http_client` (delivered in Story 21.2).
* :class:`OidcTokenProvider` — concrete RFC 8628 device-code + refresh-token
  provider (Story 21.3).
* :class:`TokenCacheEntry` — on-disk cache schema.
* :class:`ReAuthRequiredError`, :class:`OidcProtocolError` and its subclasses
  — typed error surface; callers import these to distinguish
  "re-auth needed" from "server refused".
* :func:`load_token_cache`, :func:`save_token_cache`,
  :func:`delete_token_cache` — cache I/O (used by Story 21.4's
  ``akgentic logout`` command).

Callers should import from this module, not from the private submodules
(``.oidc``, ``.cache``, ``.token_provider``) — the submodule layout is an
implementation detail.
"""

from akgentic.infra.cli.auth.cache import (
    TokenCacheCorruptError,
    TokenCacheEntry,
    delete_token_cache,
    load_token_cache,
    save_token_cache,
)
from akgentic.infra.cli.auth.oidc import (
    AccessDeniedError,
    AuthorizationPendingError,
    DeviceAuthorizationResponse,
    ExpiredTokenError,
    OidcDiscoveryError,
    OidcEndpoints,
    OidcErrorResponse,
    OidcProtocolError,
    SlowDownError,
    TokenResponse,
    discover_endpoints,
    initiate_device_flow,
    poll_for_token,
)
from akgentic.infra.cli.auth.token_provider import (
    OidcTokenProvider,
    ReAuthRequiredError,
    TokenProvider,
)

__all__ = [
    "AccessDeniedError",
    "AuthorizationPendingError",
    "DeviceAuthorizationResponse",
    "ExpiredTokenError",
    "OidcDiscoveryError",
    "OidcEndpoints",
    "OidcErrorResponse",
    "OidcProtocolError",
    "OidcTokenProvider",
    "ReAuthRequiredError",
    "SlowDownError",
    "TokenCacheCorruptError",
    "TokenCacheEntry",
    "TokenProvider",
    "TokenResponse",
    "delete_token_cache",
    "discover_endpoints",
    "initiate_device_flow",
    "load_token_cache",
    "poll_for_token",
    "save_token_cache",
]
