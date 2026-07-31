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

    def test_protocol_surface_is_exactly_two_members(self) -> None:
        """The contract stays exactly resolve_request_user + get_auth_routes.

        The additive RequestUser.scopes widening must not grow the AuthStrategy
        surface — the frozen two-member Protocol is unchanged.
        """
        members = {
            m
            for m in dir(AuthStrategy)
            if not m.startswith("_") and callable(getattr(AuthStrategy, m))
        }
        assert members == {"resolve_request_user", "get_auth_routes"}


class TestNoAuth:
    """NoAuth is the trivial anonymous resolver (AC4)."""

    def test_noauth_has_no_authenticate_method(self) -> None:
        """The synchronous authenticate method is gone (collapsed onto the seam)."""
        assert not hasattr(NoAuth(), "authenticate")

    async def test_resolve_request_user_is_anonymous_and_never_raises(self) -> None:
        """NoAuth resolves the anonymous principal and never raises."""
        user = await NoAuth().resolve_request_user(_http_connection())
        assert user.user_id == "anonymous"

    async def test_resolve_request_user_has_empty_scopes(self) -> None:
        """NoAuth's anonymous principal carries the default-empty scopes axis."""
        user = await NoAuth().resolve_request_user(_http_connection())
        assert user.roles == []
        assert user.scopes == []

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

    def test_get_request_user_default_has_empty_roles_and_scopes(self) -> None:
        """The anonymous fallback resolves with empty roles AND scopes, never raises."""
        request = Request({"type": "http", "path": "/", "headers": []})
        user = get_request_user(request)
        assert user.roles == []
        assert user.scopes == []

    def test_get_request_user_reads_the_stash(self) -> None:
        """When the middleware has stashed a principal, the seam returns it."""
        stashed = RequestUser(user_id="alice", roles=["admin"])
        request = Request(
            {"type": "http", "path": "/", "headers": [], "state": {"request_user": stashed}}
        )
        assert get_request_user(request) is stashed


class TestRequestUserScopes:
    """The additive, default-empty ``scopes`` inbound-authz axis on RequestUser."""

    def test_construction_without_scopes_still_validates(self) -> None:
        """user_id alone validates — no previously-optional field became required."""
        user = RequestUser(user_id="x")
        assert user.user_id == "x"
        assert user.roles == []
        assert user.scopes == []

    def test_default_scopes_not_shared_between_instances(self) -> None:
        """Each instance gets a fresh scopes list — no shared-mutable-default leak."""
        first = RequestUser(user_id="a")
        second = RequestUser(user_id="b")
        first.scopes.append("teams:read")
        assert first.scopes == ["teams:read"]
        assert second.scopes == []
        assert first.scopes is not second.scopes

    def test_default_instance_round_trips_via_model_dump(self) -> None:
        """A default instance dumps scopes == [] and round-trips equal to itself."""
        user = RequestUser(user_id="x")
        dumped = user.model_dump()
        assert dumped["scopes"] == []
        assert RequestUser.model_validate(dumped) == user

    def test_default_instance_round_trips_via_json(self) -> None:
        """A default instance round-trips equal through JSON, scopes default []."""
        user = RequestUser(user_id="x")
        restored = RequestUser.model_validate_json(user.model_dump_json())
        assert restored == user
        assert restored.scopes == []

    def test_validate_with_scopes_omitted_defaults_empty(self) -> None:
        """model_validate with scopes omitted validates and defaults to []."""
        user = RequestUser.model_validate({"user_id": "x"})
        assert user.scopes == []

    def test_non_empty_scopes_survive_round_trip(self) -> None:
        """A non-empty scopes value survives model_dump → model_validate intact."""
        user = RequestUser(user_id="x", scopes=["teams:read", "teams:write"])
        restored = RequestUser.model_validate(user.model_dump())
        assert restored.scopes == ["teams:read", "teams:write"]
        assert restored == user

    def test_non_empty_scopes_survive_json_round_trip(self) -> None:
        """A non-empty scopes value survives the JSON round-trip intact."""
        user = RequestUser(user_id="x", scopes=["teams:read"])
        restored = RequestUser.model_validate_json(user.model_dump_json())
        assert restored.scopes == ["teams:read"]
        assert restored == user
