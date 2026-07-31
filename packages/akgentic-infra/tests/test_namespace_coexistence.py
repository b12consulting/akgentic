"""Coexistence guard: a sibling distribution that adds ``akgentic/infra/auth/`` to the
shared ``akgentic`` namespace must NOT shadow infra's heavy ``akgentic/infra/__init__.py``
eager re-exports (``NoAuth`` and the rest of ``akgentic.infra.__all__``).

Proven stub-packaging pattern for Epic 40's ``akgentic-infra-auth`` wheel (mirrors the
enterprise sibling):

* ship a byte-identical ``pkgutil.extend_path`` stub for ``akgentic/__init__.py`` **and**
  for ``akgentic/infra/__init__.py`` (each is the single ``extend_path`` namespace line;
  see ``packages/akgentic-infra-enterprise/src/akgentic/__init__.py`` and
  ``.../akgentic/infra/__init__.py``), and
* put ALL real content under ``akgentic/infra/auth/``.

With that shape, whenever infra's distribution precedes the sibling on ``sys.path`` (the
supported install order), infra's heavy ``akgentic/infra/__init__.py`` wins the import
race, binds every re-export on the ``akgentic.infra`` module object, and its own
``extend_path`` call appends the sibling's ``akgentic/infra/`` directory to
``akgentic.infra.__path__`` -- so ``akgentic.infra.auth`` resolves lazily as a submodule
while ``akgentic.infra.NoAuth`` (et al.) stay bound. Epic 40 ships stubs + subpackage only
and runs the byte-identity check on its own side; the coexistence guarantee and any fix to
infra's own ``__init__`` live HERE, in ``akgentic-infra`` (never inside the library epic).

Both guards run in a fresh child interpreter (``subprocess`` + ``sys.executable``) because
``akgentic.infra`` is already cached in the parent's ``sys.modules`` by the time the suite
runs -- the namespace race must be re-exercised cold, not read off a warm module object
(mirrors Story 39b.2's import-closure guard in ``tests/server/test_auth_loader.py``).

Assertions are behavioural only: attribute resolution, ``NoAuth`` identity, successful
``import``, ``__path__`` aggregation of the sibling, a sibling-only marker, and child
``returncode == 0``. No ``ADR-NNN`` string assertions and no "a comment/docstring is
present" assertions anywhere (Golden Rule #8).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# The single-line ``extend_path`` namespace stub every non-infra akgentic distribution
# ships for ``akgentic/__init__.py`` and ``akgentic/infra/__init__.py`` (the enterprise
# sibling is the proven reference; Epic 40's wheel uses exactly this byte shape).
_STUB = '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n'

# The real akgentic-infra-auth sibling is a workspace package locally, but it is NOT a
# dependency of infra -- infra's own standalone CI checkout does not install it. The
# real-layout guard skips there; the self-contained simulated guard always runs.
_REAL_SIBLING_INSTALLED = importlib.util.find_spec("akgentic.infra.auth") is not None

_TIMEOUT_SECONDS = 180


def _run_child(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a fresh interpreter with the same installed environment."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )


# The child asserts the two coexistence invariants together in one cold interpreter:
# infra's eager re-exports all still resolve, and the sibling's ``akgentic/infra/auth``
# directory is reachable as a submodule (aggregated into ``__path__`` by extend_path).
_SIMULATED_CHILD = """
    import os
    import sys

    sibling, expected_auth_dir = sys.argv[1], sys.argv[2]
    # Supported install order: infra precedes the sibling, so its heavy __init__ wins.
    sys.path.append(sibling)

    import akgentic.infra
    from akgentic.infra.adapters.community.no_auth import NoAuth as InfraNoAuth

    assert hasattr(akgentic.infra, "NoAuth"), "NoAuth re-export lost under coexistence"
    assert akgentic.infra.NoAuth is InfraNoAuth, "akgentic.infra.NoAuth is not infra's NoAuth"

    missing = [n for n in akgentic.infra.__all__ if not hasattr(akgentic.infra, n)]
    assert not missing, "re-exports dropped under coexistence: " + repr(missing)

    import akgentic.infra.auth

    aggregated = {os.path.realpath(p) for p in akgentic.infra.auth.__path__}
    assert os.path.realpath(expected_auth_dir) in aggregated, (
        "simulated sibling akgentic/infra/auth not aggregated into __path__: "
        + repr(sorted(aggregated))
    )
    print("OK")
"""

_REAL_CHILD = """
    import akgentic.infra
    from akgentic.infra.adapters.community.no_auth import NoAuth as InfraNoAuth

    assert hasattr(akgentic.infra, "NoAuth"), "NoAuth re-export lost under coexistence"
    assert akgentic.infra.NoAuth is InfraNoAuth, "akgentic.infra.NoAuth is not infra's NoAuth"

    missing = [n for n in akgentic.infra.__all__ if not hasattr(akgentic.infra, n)]
    assert not missing, "re-exports dropped under coexistence: " + repr(missing)

    import akgentic.infra.auth

    assert hasattr(akgentic.infra.auth, "__version__"), (
        "akgentic.infra.auth did not reach the sibling akgentic-infra-auth subpackage"
    )
    print("OK")
"""


def test_simulated_sibling_stub_does_not_shadow_infra_reexports(tmp_path: Path) -> None:
    """A simulated second distribution appends ``akgentic/infra/auth/`` (byte-identical
    ``extend_path`` stubs) in the supported order: infra's heavy ``__init__`` still wins,
    every ``akgentic.infra.__all__`` name resolves, and the simulated ``akgentic/infra/auth``
    directory is aggregated into ``akgentic.infra.auth.__path__``. Self-contained -- runs
    even when the real sibling is not installed (isolated under ``tmp_path``, child-only
    ``sys.path`` mutation)."""
    sibling = tmp_path / "sibling"
    auth_dir = sibling / "akgentic" / "infra" / "auth"
    auth_dir.mkdir(parents=True)
    (sibling / "akgentic" / "__init__.py").write_text(_STUB)
    (sibling / "akgentic" / "infra" / "__init__.py").write_text(_STUB)
    # Real content under akgentic/infra/auth/ (the only non-stub file), mirroring the
    # shape Epic 40's wheel ships. The marker proves this file could load in isolation.
    (auth_dir / "__init__.py").write_text("COEXISTENCE_MARKER = True\n" + _STUB)

    result = _run_child(_SIMULATED_CHILD, str(sibling), str(auth_dir))

    assert result.returncode == 0, (
        f"child failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout


@pytest.mark.skipif(
    not _REAL_SIBLING_INSTALLED,
    reason="real akgentic-infra-auth sibling not installed (infra standalone CI); "
    "the simulated-sibling guard covers the pattern",
)
def test_real_sibling_auth_wheel_does_not_shadow_infra_reexports() -> None:
    """The REAL installed akgentic-infra-auth layout: in one fresh interpreter,
    ``akgentic.infra.NoAuth`` (and every ``akgentic.infra.__all__`` name) still resolves AND
    ``import akgentic.infra.auth`` reaches the sibling subpackage (its ``__version__`` anchor,
    defined only in the sibling). Both invariants hold together, proving the heavy
    ``__init__`` is not shadowed in the actual installed layout."""
    result = _run_child(_REAL_CHILD)

    assert result.returncode == 0, (
        f"child failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "OK" in result.stdout
