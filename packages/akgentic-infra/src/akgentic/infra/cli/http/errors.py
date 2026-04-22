"""Typed error hierarchy raised by clients built with :func:`build_http_client`.

Implements ADR-021 §Decision 1 — a 401 from a profile with no ``auth`` block is
a **server configuration error** (the operator pointed the CLI at a protected
endpoint with an OSS profile), NOT a login prompt. A 401 from a profile that
does declare ``auth`` is an :class:`AuthenticationError`; Story 21.4 will wrap
it with a single-shot retry. Story 21.2 only raises the type.
"""

from __future__ import annotations


class HttpClientError(Exception):
    """Base class for errors raised by clients returned from ``build_http_client``."""


class ServerConfigurationError(HttpClientError):
    """Raised on 401 when the active profile has ``auth is None``.

    The message MUST point the operator at **profile configuration** — never at
    ``akgentic login``. A 401 here means the server is enforcing auth but the
    caller's profile is OSS-shaped; that's a configuration mismatch, not a
    missing credential.
    """

    def __init__(
        self,
        *,
        profile_name: str,
        url: str,
        cause: Exception | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.url = url
        message = (
            f"The server at {url} returned 401 but profile '{profile_name}' has no auth "
            "configured. Check the profile's `auth:` block in ~/.akgentic/config.yaml "
            "(profile configuration error)."
        )
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class AuthenticationError(HttpClientError):
    """Raised on 401 when the active profile has an ``auth`` block.

    Story 21.2 only defines the type and ensures it is raised. Story 21.4 adds
    the retry-once auto-auth wrapper that catches this, re-authenticates, and
    replays the request.
    """

    def __init__(
        self,
        *,
        profile_name: str,
        url: str,
        cause: Exception | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.url = url
        message = (
            f"Authentication failed for profile '{profile_name}' at {url} (HTTP 401). "
            "The access token was rejected by the server."
        )
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class InvalidClientConfigurationError(HttpClientError):
    """Raised at factory-build time when profile/token_provider combination is incoherent.

    See :func:`build_http_client` AC #7: mismatches between ``profile.auth`` and
    ``token_provider`` are rejected loudly at construction rather than silently
    papered over.
    """
