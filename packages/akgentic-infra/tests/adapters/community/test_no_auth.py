"""Tests for the NoAuth community strategy and the AuthStrategy contract."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from starlette.requests import HTTPConnection, Request
from starlette.routing import BaseRoute

from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.protocols import AuthStrategy
from akgentic.infra.server.auth import RequestUser, get_request_user

# Every tier strategy reachable from infra. A tier repo parametrizes this list
# with its own strategy against the same shared contract assertions.
STRATEGIES: list[AuthStrategy] = [NoAuth()]


def _http_connection(path: str = "/", state: dict[str, Any] | None = None) -> HTTPConnection:
    """Build a minimal HTTP-scope connection for resolver calls."""
    scope: dict[str, Any] = {"type": "http", "path": path, "headers": []}
    if state is not None:
        scope["state"] = state
    return HTTPConnection(scope)


class TestAuthStrategyContract:
    """The shared structural guard — a tier missing the resolver fails here (AC7)."""

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_is_instance_of_auth_strategy(self, strategy: AuthStrategy) -> None:
        """Each tier strategy structurally satisfies AuthStrategy."""
        assert isinstance(strategy, AuthStrategy)

    @pytest.mark.parametrize("strategy", STRATEGIES)
    async def test_resolve_request_user_is_awaitable_returning_request_user(
        self, strategy: AuthStrategy
    ) -> None:
        """resolve_request_user is awaitable and returns a RequestUser."""
        assert inspect.iscoroutinefunction(strategy.resolve_request_user)
        user = await strategy.resolve_request_user(_http_connection())
        assert isinstance(user, RequestUser)

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_get_auth_routes_returns_list_of_base_route(self, strategy: AuthStrategy) -> None:
        """get_auth_routes returns a list[BaseRoute] (empty for community)."""
        routes = strategy.get_auth_routes()
        assert isinstance(routes, list)
        assert all(isinstance(r, BaseRoute) for r in routes)


class TestNoAuth:
    """NoAuth is the trivial anonymous resolver (AC4)."""

    def test_noauth_has_no_authenticate_method(self) -> None:
        """The synchronous authenticate method is gone (collapsed onto the seam)."""
        assert not hasattr(NoAuth(), "authenticate")

    async def test_resolve_request_user_is_anonymous_and_never_raises(self) -> None:
        """NoAuth resolves the anonymous principal and never raises."""
        user = await NoAuth().resolve_request_user(_http_connection())
        assert user.user_id == "anonymous"

    def test_get_auth_routes_is_empty(self) -> None:
        """Community exposes no /auth/* routes."""
        assert NoAuth().get_auth_routes() == []


class TestCommunityIdentitySeam:
    """Community identity resolves to anonymous through get_request_user."""

    def test_get_request_user_default_is_anonymous(self) -> None:
        """With no middleware-populated stash, the seam returns the anonymous default."""
        request = Request({"type": "http", "path": "/", "headers": []})
        user = get_request_user(request)
        assert isinstance(user, RequestUser)
        assert user.user_id == "anonymous"

    def test_get_request_user_reads_the_stash(self) -> None:
        """When the middleware has stashed a principal, the seam returns it."""
        stashed = RequestUser(user_id="alice", roles=["admin"])
        request = Request(
            {"type": "http", "path": "/", "headers": [], "state": {"request_user": stashed}}
        )
        assert get_request_user(request) is stashed
