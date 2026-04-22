"""Tests for NoAuth adapter."""

from __future__ import annotations

import inspect

from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.protocols.auth import AuthStrategy


class TestNoAuthProtocolCompliance:
    """AC2: NoAuth implements AuthStrategy protocol."""

    def test_satisfies_auth_strategy_protocol(self) -> None:
        """NoAuth structurally satisfies AuthStrategy."""
        adapter = NoAuth()
        assert isinstance(adapter, AuthStrategy)

    def test_has_authenticate_method(self) -> None:
        """NoAuth exposes authenticate with correct signature."""
        adapter = NoAuth()
        assert callable(adapter.authenticate)

    def test_authenticate_signature_matches_protocol(self) -> None:
        """authenticate has request parameter matching AuthStrategy."""
        sig = inspect.signature(NoAuth.authenticate)
        assert "request" in sig.parameters


class TestNoAuthBehavior:
    """AC2: NoAuth passes all requests through."""

    def test_authenticate_returns_anonymous(self) -> None:
        """authenticate always returns 'anonymous'."""
        adapter = NoAuth()
        assert adapter.authenticate(None) == "anonymous"

    def test_authenticate_returns_string(self) -> None:
        """authenticate returns a str, not None."""
        adapter = NoAuth()
        result = adapter.authenticate({})
        assert isinstance(result, str)

    def test_authenticate_ignores_request_content(self) -> None:
        """authenticate returns same value regardless of input."""
        adapter = NoAuth()
        assert adapter.authenticate("anything") == "anonymous"
        assert adapter.authenticate(42) == "anonymous"
        assert adapter.authenticate({"auth": "token"}) == "anonymous"
