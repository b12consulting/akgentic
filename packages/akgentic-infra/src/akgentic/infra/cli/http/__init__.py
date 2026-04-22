"""CLI HTTP client factory and typed error surfaces.

Public API for :mod:`akgentic.infra.cli` HTTP access. See ADR-021 for the
profile-driven auth model (the factory here is the first consumer of the
``profile.auth is None`` signal delivered by :mod:`akgentic.infra.cli.config`).

This package intentionally stays thin: a single factory, a typed error
hierarchy, and nothing else. OIDC lives in Story 21.3's ``OidcTokenProvider``;
inline retry-once auto-auth lands in Story 21.4.
"""

from akgentic.infra.cli.http.auto_auth import build_http_client_with_auto_auth
from akgentic.infra.cli.http.client import build_http_client
from akgentic.infra.cli.http.errors import (
    AuthenticationError,
    HttpClientError,
    InvalidClientConfigurationError,
    ServerConfigurationError,
)

__all__ = [
    "AuthenticationError",
    "HttpClientError",
    "InvalidClientConfigurationError",
    "ServerConfigurationError",
    "build_http_client",
    "build_http_client_with_auto_auth",
]
