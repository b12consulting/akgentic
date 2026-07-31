"""Plugin-installed closure guard for the community ``noauth`` auth path (Story 46.1).

The stronger sibling of ``TestImportClosure`` in ``tests/server/test_auth_loader.py``: that
guard proves the ``auth_strategy="noauth"`` wiring pulls in no ``akgentic.infra.auth`` module
*when nothing is registered* under the ``akgentic.infra.auth.strategies`` entry-point group.
This module proves the same invariant holds *even when a real, discoverable plugin IS
installed* under that group -- the OSS-clean default never scans-and-loads the licensed
plugin. That "plugin-installed" case is the delta this story adds; it is NOT a rewrite of the
existing seam / fail-loud guards.

The seam guard (Decision 5b, a fake entry point resolves via a non-``"noauth"`` selector to a
Protocol-satisfying strategy invoked as a zero-arg factory; a non-conforming stub raises
``UnknownAuthStrategyError``) and the fail-loud guard (Decision 5c, an unknown/uninstalled
selector raises, never a silent anonymous fallback) already live and pass in
``tests/server/test_auth_loader.py`` as ``TestEntryPointDiscovery`` and
``TestUnknownNameFailsLoud``. They are reused there, not duplicated here.

The guard has to be both non-vacuous and toothed, or it proves nothing:

* Non-vacuous -- the child first asserts the fake plugin *is* discoverable via
  ``importlib.metadata.entry_points(group=...)``. A fake nobody can discover would make
  "no ``akgentic.infra.auth`` in ``sys.modules``" trivially true and prove nothing.
* Toothed -- the fake's entry-point factory resolves to a module *under the
  ``akgentic.infra.auth`` namespace* (``akgentic.infra.auth._fake_strategy:make_strategy``), so
  a buggy loader that erroneously loaded it on the noauth path would populate
  ``sys.modules["akgentic.infra.auth"]`` and fail the closure assertion. A factory in an
  unrelated top-level module would let the assertion pass even against a broken loader.

``importlib.metadata`` parses the ``.dist-info`` ``entry_points.txt`` *without importing* the
target module -- only ``.load()`` imports it. So on the happy noauth path the plugin package
stays cold even though it is on ``sys.path`` and discoverable.

Runs in a fresh child interpreter (``subprocess`` + ``sys.executable``): ``akgentic.infra`` is
already cached in the parent's ``sys.modules`` by the time the suite runs, so the closure must
be exercised cold, not read off a warm module object (mirrors ``TestImportClosure`` and
``test_namespace_coexistence.py``). Every double is a test-owned stub -- no real
``akgentic-infra-auth`` distribution is imported. No ``ADR-NNN`` string assertions
(Golden Rule #8); the ``AuthStrategy`` stub is a real Protocol-shaped class, never a ``dict``
(Golden Rule #1).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

# Byte-identical single-line ``pkgutil.extend_path`` stub every non-infra akgentic distribution
# ships for ``akgentic/__init__.py`` and ``akgentic/infra/__init__.py`` (mirrors
# ``test_namespace_coexistence.py``). Keeps infra's heavy ``__init__`` winning the import race
# while the sibling's ``akgentic/infra/auth/`` directory is aggregated into ``__path__`` -- so a
# hypothetical ``.load()`` would land squarely under ``akgentic.infra.auth.*``.
_STUB = '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n'

# A Protocol-shaped strategy stub UNDER the ``akgentic.infra.auth`` namespace. Never imported on
# the noauth path -- it exists only to give the guard teeth: if a broken loader loaded it, its
# module would appear in ``sys.modules`` as ``akgentic.infra.auth._fake_strategy``. Mirrors
# ``_MinimalStrategy`` in ``test_auth_loader.py`` (a real class, not a ``dict`` -- Golden Rule #1).
_FAKE_STRATEGY_MODULE = '''\
"""Test-owned fake auth-strategy factory under the akgentic.infra.auth namespace."""


class _FakeStrategy:
    """Structurally satisfies the two-member AuthStrategy Protocol (never invoked here)."""

    async def resolve_request_user(self, connection):
        raise NotImplementedError

    def get_auth_routes(self):
        return []


def make_strategy():
    return _FakeStrategy()
'''

# A discoverable ``.dist-info`` registering the fake factory under the auth-strategies group.
_ENTRY_POINTS_TXT = (
    "[akgentic.infra.auth.strategies]\n"
    "oidc = akgentic.infra.auth._fake_strategy:make_strategy\n"
)
_METADATA = "Metadata-Version: 2.1\nName: fake-infra-auth\nVersion: 0.0.0\n"

_TIMEOUT_SECONDS = 180

# Cold-interpreter guard. With the fake plugin installed and discoverable under the group, the
# community ``auth_strategy="noauth"`` wiring completes and leaves the plugin cold: no
# ``akgentic.infra.auth[.*]`` module, no redis/dapr, no paid-tier module in ``sys.modules``.
_PLUGIN_INSTALLED_CLOSURE_CHILD = """
    import importlib
    import importlib.metadata
    import sys
    import tempfile
    from pathlib import Path

    sibling = sys.argv[1]
    sys.path.append(sibling)
    importlib.invalidate_caches()

    from akgentic.infra.server.auth_loader import AUTH_STRATEGY_GROUP

    # Non-vacuity: the fake plugin really IS discoverable under the group (else the closure
    # assertion below would be trivially true).
    discoverable = {ep.name for ep in importlib.metadata.entry_points(group=AUTH_STRATEGY_GROUP)}
    assert "oidc" in discoverable, "fake plugin not discoverable: " + repr(sorted(discoverable))

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

    # The discoverable plugin stays cold: no akgentic.infra.auth[.*] loaded on the noauth path.
    auth_lib = sorted(
        m for m in sys.modules
        if m == "akgentic.infra.auth" or m.startswith("akgentic.infra.auth.")
    )
    # Closure parity with TestImportClosure: no redis/dapr, no paid tiers leaked either.
    backends = sorted(m for m in ("redis", "dapr") if m in sys.modules)
    tiers = sorted(
        m for m in sys.modules
        if m.startswith("akgentic.infra.department") or m.startswith("akgentic.infra.enterprise")
    )
    services.actor_system.shutdown()

    problems = auth_lib + backends + tiers
    if problems:
        print("LEAKED:" + ",".join(problems))
        sys.exit(1)
    print("OK")
"""


def _run_child(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter with the same installed environment."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


def _build_fake_auth_distribution(root: Path) -> None:
    """Materialise a discoverable, test-owned fake auth plugin under *root*.

    Ships the ``extend_path`` namespace stubs plus an ``akgentic/infra/auth/`` package holding
    the factory, and a ``.dist-info`` registering it under the auth-strategies entry-point group
    -- so the plugin is discoverable by ``importlib.metadata`` and a hypothetical ``.load()``
    would import a module under ``akgentic.infra.auth.*``.
    """
    auth_pkg = root / "akgentic" / "infra" / "auth"
    auth_pkg.mkdir(parents=True)
    (root / "akgentic" / "__init__.py").write_text(_STUB)
    (root / "akgentic" / "infra" / "__init__.py").write_text(_STUB)
    (auth_pkg / "__init__.py").write_text(_STUB)
    (auth_pkg / "_fake_strategy.py").write_text(_FAKE_STRATEGY_MODULE)

    dist_info = root / "fake_infra_auth-0.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(_METADATA)
    (dist_info / "entry_points.txt").write_text(_ENTRY_POINTS_TXT)


class TestPluginInstalledClosure:
    """``noauth`` wiring leaves an installed, discoverable auth plugin cold (the story delta)."""

    def test_noauth_leaves_installed_plugin_cold(self, tmp_path: Path) -> None:
        """A discoverable fake auth plugin is registered under the entry-point group, yet the
        community ``auth_strategy="noauth"`` wiring imports no ``akgentic.infra.auth`` module (nor
        redis/dapr, nor the paid tiers). The child first asserts the plugin is genuinely
        discoverable (non-vacuity), and the plugin's factory lives under ``akgentic.infra.auth`` so
        a loader that erroneously scanned-and-loaded on the noauth path would be caught (teeth)."""
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        _build_fake_auth_distribution(sibling)

        result = _run_child(_PLUGIN_INSTALLED_CLOSURE_CHILD, str(sibling))

        assert result.returncode == 0, (
            f"child failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "OK" in result.stdout
