"""Behavioural tests for the config-driven auth-strategy loader (Story 39b.2).

Asserts behaviour only — produced object identity/type, exception type + message
substrings, ``isinstance`` against the runtime-checkable ``AuthStrategy`` Protocol,
and the import closure. No ``ADR-NNN`` string assertions (Golden Rule #8).
"""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import HTTPConnection
from starlette.routing import BaseRoute

from akgentic.infra.adapters.community.no_auth import NoAuth
from akgentic.infra.protocols import AuthStrategy
from akgentic.infra.server.auth import RequestUser
from akgentic.infra.server.auth_loader import (
    AUTH_STRATEGY_GROUP,
    NOAUTH,
    UnknownAuthStrategyError,
    load_auth_strategy,
)
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

# ---------------------------------------------------------------------------
# Fakes — a controllable stand-in for importlib.metadata entry-point discovery.
# Avoids installing a real dist; the test owns the factory signature so the loader
# is exercised independently of how Epic 40 finally registers its strategy.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeEntryPoint:
    """A fake entry point whose ``load()`` returns a pre-bound factory."""

    name: str
    factory: Callable[[], Any]

    def load(self) -> Callable[[], Any]:
        return self.factory


class _FakeEntryPoints(tuple):
    """Minimal stand-in for ``importlib.metadata.EntryPoints`` (tuple + ``select``)."""

    def select(self, *, name: str) -> _FakeEntryPoints:
        return _FakeEntryPoints(ep for ep in self if ep.name == name)


def _install_fake_entry_points(
    monkeypatch: pytest.MonkeyPatch, eps: Iterable[_FakeEntryPoint]
) -> None:
    """Patch ``importlib.metadata.entry_points`` where the loader looks it up."""
    collection = _FakeEntryPoints(eps)

    def _fake_entry_points(*, group: str) -> _FakeEntryPoints:
        assert group == AUTH_STRATEGY_GROUP
        return collection

    monkeypatch.setattr(importlib.metadata, "entry_points", _fake_entry_points)


class _MinimalStrategy:
    """A minimal object satisfying the two-member ``AuthStrategy`` Protocol."""

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        return RequestUser(user_id="plugin-user")

    def get_auth_routes(self) -> list[BaseRoute]:
        return []


class _InvalidStrategy:
    """Missing ``get_auth_routes`` — does NOT satisfy the Protocol."""

    async def resolve_request_user(self, connection: HTTPConnection) -> RequestUser:
        return RequestUser(user_id="x")


def _http_connection() -> HTTPConnection:
    return HTTPConnection({"type": "http", "path": "/", "headers": []})


# ---------------------------------------------------------------------------
# AC2 / AC4 — the default "noauth" path is byte-identical and never touches
# entry-point discovery.
# ---------------------------------------------------------------------------


class TestLoadNoAuthDefault:
    """``load_auth_strategy("noauth")`` returns NoAuth without any discovery."""

    def test_noauth_returns_noauth_instance(self) -> None:
        strategy = load_auth_strategy(NOAUTH)
        assert isinstance(strategy, NoAuth)

    def test_noauth_satisfies_auth_strategy_protocol(self) -> None:
        assert isinstance(load_auth_strategy(NOAUTH), AuthStrategy)

    async def test_noauth_resolves_anonymous(self) -> None:
        strategy = load_auth_strategy(NOAUTH)
        user = await strategy.resolve_request_user(_http_connection())
        assert user.user_id == "anonymous"

    def test_noauth_never_consults_entry_points(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default path does NO entry-point lookup (keeps the import closure clean)."""

        def _boom(*, group: str) -> object:
            raise AssertionError("entry_points must NOT be consulted for the 'noauth' default")

        monkeypatch.setattr(importlib.metadata, "entry_points", _boom)
        assert isinstance(load_auth_strategy(NOAUTH), NoAuth)


# ---------------------------------------------------------------------------
# AC3 — an unknown / non-"noauth" name fails loud (never an anonymous fallback).
# ---------------------------------------------------------------------------


class TestUnknownNameFailsLoud:
    """A non-``"noauth"`` name with no matching entry point raises, never falls back."""

    def test_unknown_name_raises_dedicated_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_entry_points(
            monkeypatch, [_FakeEntryPoint("other", lambda: _MinimalStrategy())]
        )
        with pytest.raises(UnknownAuthStrategyError) as exc_info:
            load_auth_strategy("nope")
        message = str(exc_info.value)
        assert "nope" in message
        assert AUTH_STRATEGY_GROUP in message
        # SHOULD list the discoverable names to make the typo diagnosable.
        assert "other" in message

    def test_unknown_name_never_returns_noauth_or_anonymous(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No silent fallback: the loader raises rather than producing NoAuth."""
        _install_fake_entry_points(monkeypatch, [])
        result: AuthStrategy | None = None
        with pytest.raises(UnknownAuthStrategyError):
            result = load_auth_strategy("nope")
        assert result is None  # nothing was returned — the loader raised


# ---------------------------------------------------------------------------
# AC4 / AC5 — entry-point discovery and Protocol validation.
# ---------------------------------------------------------------------------


class TestEntryPointDiscovery:
    """A registered entry point resolves to its strategy and is Protocol-validated."""

    def test_registered_entry_point_strategy_is_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        produced = _MinimalStrategy()
        _install_fake_entry_points(monkeypatch, [_FakeEntryPoint("fake", lambda: produced)])
        strategy = load_auth_strategy("fake")
        assert strategy is produced
        assert isinstance(strategy, AuthStrategy)

    def test_factory_invoked_with_zero_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The resolved factory is called with NO arguments (zero-arg convention)."""
        calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def _factory(*args: Any, **kwargs: Any) -> _MinimalStrategy:
            calls.append((args, kwargs))
            return _MinimalStrategy()

        _install_fake_entry_points(monkeypatch, [_FakeEntryPoint("fake", _factory)])
        load_auth_strategy("fake")
        assert calls == [((), {})]

    def test_invalid_strategy_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A produced object failing the Protocol raises — never returned."""
        bad = _InvalidStrategy()
        assert not isinstance(bad, AuthStrategy)  # guard: the fake really is non-conforming
        _install_fake_entry_points(monkeypatch, [_FakeEntryPoint("bad", lambda: bad)])
        result: AuthStrategy | None = None
        with pytest.raises(UnknownAuthStrategyError) as exc_info:
            result = load_auth_strategy("bad")
        assert result is None
        assert "AuthStrategy" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AC1 / AC2 — wire_community drives the loader from settings.auth_strategy.
# ---------------------------------------------------------------------------


class TestWireCommunityUsesLoader:
    """``wire_community`` selects ``services.auth`` via the loader from config."""

    def _settings(self, tmp_path: Path, **overrides: Any) -> CommunitySettings:
        return CommunitySettings(
            workspaces_root=tmp_path / "workspaces",
            event_store_path=tmp_path / "event_store",
            catalog_path=tmp_path / "catalog",
            **overrides,
        )

    def test_default_settings_auth_is_noauth(self, tmp_path: Path) -> None:
        services = wire_community(self._settings(tmp_path))
        try:
            assert isinstance(services.auth, NoAuth)
        finally:
            services.actor_system.shutdown()

    async def test_default_settings_auth_resolves_anonymous(self, tmp_path: Path) -> None:
        services = wire_community(self._settings(tmp_path))
        try:
            user = await services.auth.resolve_request_user(_http_connection())
            assert user.user_id == "anonymous"
        finally:
            services.actor_system.shutdown()

    def test_selector_flows_from_settings_into_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The configured ``auth_strategy`` is the exact name handed to the loader."""
        captured: list[str] = []
        sentinel = NoAuth()

        def _spy(name: str) -> NoAuth:
            captured.append(name)
            return sentinel

        monkeypatch.setattr("akgentic.infra.wiring.load_auth_strategy", _spy)
        services = wire_community(self._settings(tmp_path, auth_strategy="some-plugin"))
        try:
            assert captured == ["some-plugin"]
            assert services.auth is sentinel
        finally:
            services.actor_system.shutdown()

    def test_community_app_mounts_no_auth_middleware(self, tmp_path: Path) -> None:
        """The community app factory mounts no RequireAuthMiddleware (unchanged)."""
        from akgentic.infra.server.app import create_app
        from akgentic.infra.server.middleware.require_auth import RequireAuthMiddleware

        services = wire_community(self._settings(tmp_path))
        try:
            app = create_app(services, self._settings(tmp_path))
            mounted = {m.cls for m in app.user_middleware}
            assert RequireAuthMiddleware not in mounted
        finally:
            services.actor_system.shutdown()


# ---------------------------------------------------------------------------
# AC4 / AC6 — import-closure guard: the default community wiring pulls in none of
# the auth machinery (the redis/dapr backends the paid strategies use, the auth
# library itself, or the paid tiers). A subprocess is the robust form — it is
# immune to other tests in the session having already imported these modules.
#
# NB: bare ``authlib`` is deliberately NOT asserted absent. Importing any
# ``akgentic.infra.*`` module runs ``akgentic/infra/__init__.py``, which eagerly
# imports community adapters that transitively reach ``akgentic-tool`` ->
# ``weaviate`` -> ``authlib`` (weaviate's httpx auth integration) — entirely
# unrelated to auth strategies and present in infra's closure regardless of this
# story. The bare module name therefore cannot distinguish auth usage; the
# achievable, intent-faithful guard is the absence of redis/dapr, the auth
# library (``akgentic.infra.auth*``), and the paid tiers. This story adds NO new
# dependency to infra's pyproject closure (stdlib ``importlib.metadata`` only).
# ---------------------------------------------------------------------------

_IMPORT_CLOSURE_SCRIPT = """
import sys
import tempfile
from pathlib import Path

from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

tmp = Path(tempfile.mkdtemp())
settings = CommunitySettings(
    workspaces_root=tmp / "ws",
    event_store_path=tmp / "es",
    catalog_path=tmp / "cat",
)
assert settings.auth_strategy == "noauth"
services = wire_community(settings)
backends = sorted(m for m in ("redis", "dapr") if m in sys.modules)
auth_lib = sorted(
    m for m in sys.modules
    if m == "akgentic.infra.auth" or m.startswith("akgentic.infra.auth.")
)
tiers = sorted(
    m for m in sys.modules
    if m.startswith("akgentic.infra.department") or m.startswith("akgentic.infra.enterprise")
)
services.actor_system.shutdown()
problems = backends + auth_lib + tiers
if problems:
    print("LEAKED:" + ",".join(problems))
    sys.exit(1)
print("OK")
"""


class TestImportClosure:
    """``strategy="noauth"`` wiring pulls in no auth machinery (redis/dapr/auth-lib/tiers)."""

    def test_noauth_wiring_imports_no_auth_machinery(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", _IMPORT_CLOSURE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, (
            f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "OK" in result.stdout
