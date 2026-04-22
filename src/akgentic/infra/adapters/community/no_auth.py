"""NoAuth — community-tier authentication that bypasses all auth checks."""

from __future__ import annotations

from typing import Any


class NoAuth:
    """Passes all requests through without authentication.

    Satisfies the AuthStrategy protocol via structural subtyping.
    Always returns a default anonymous user identifier.
    """

    def authenticate(self, request: Any) -> str | None:
        """Authenticate a request — always returns anonymous user.

        Args:
            request: The incoming HTTP request (ignored)

        Returns:
            The string "anonymous" for all requests
        """
        return "anonymous"
