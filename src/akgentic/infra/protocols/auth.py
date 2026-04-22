"""AuthStrategy protocol — authenticates and authorizes incoming server requests."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AuthStrategy(Protocol):
    """Authenticates and authorizes incoming server requests.

    Implementations:

    - **Community** (``NoAuth``): returns a fixed user ID, no validation.
    - **Department** (``OAuth2Auth``): validates JWT bearer tokens.
    - **Enterprise** (``SsoRbacAuth``): SSO + role-based access control.

    Note:
        ``request`` is typed as ``Any`` because the protocol must be
        framework-agnostic. In practice, community and department tiers
        receive a ``starlette.requests.Request``; enterprise tiers may
        receive a Dapr or gRPC request object. Implementations should
        document the concrete type they expect.
    """

    def authenticate(self, request: Any) -> str | None:
        """Authenticate a request and return user identifier.

        Args:
            request: The incoming HTTP request (concrete type depends on tier;
                see class docstring).

        Returns:
            User identifier string, or None if authentication fails.
        """
        ...
