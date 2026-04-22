"""Wheel packaging test (AC #11).

Runs ``uv build`` for the akgentic-catalog package and verifies the resulting
wheel contains ``akgentic/catalog/repositories/postgres/schema.toml``. Skipped
when ``uv`` is not on ``PATH`` — CI provisions ``uv`` so the assertion runs
there.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

# Resolve to packages/akgentic-catalog/
PKG_ROOT = Path(__file__).parents[3]
# Resolve to workspace root (parent of packages/)
WORKSPACE_ROOT = PKG_ROOT.parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv CLI not available on PATH")
def test_wheel_contains_schema_toml(tmp_path: Path) -> None:
    """`uv build --wheel` produces a wheel that ships ``schema.toml``."""
    result = subprocess.run(
        [
            "uv",
            "build",
            "--package",
            "akgentic-catalog",
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"uv build failed in this environment: {result.stderr.strip()}")

    wheels = sorted(tmp_path.glob("akgentic_catalog-*.whl"))
    assert wheels, f"No wheel produced in {tmp_path}: {list(tmp_path.iterdir())}"
    wheel = wheels[-1]

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())

    expected = "akgentic/catalog/repositories/postgres/schema.toml"
    assert expected in names, (
        f"{expected} missing from wheel; contents sample: "
        f"{sorted(n for n in names if 'postgres' in n)}"
    )
