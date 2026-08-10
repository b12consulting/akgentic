#!/usr/bin/env python3
"""Verify each submodule is pinned at the release tag the umbrella names.

Source mode promises that `git submodule update --init` gives you the sources
release X was built from. Nothing enforces that on its own: a submodule can sit
several commits past its release tag while its pyproject still reports the same
version, and you would be editing code that is not what shipped — silently.

So the pin is checked two ways:

  * the gitlink SHA equals the commit `vX.Y.Z` resolves to, not merely that such
    a tag exists (tags are resolved through `^{commit}`, since some are
    annotated and some lightweight); and
  * that version is the one the umbrella pins for that package, so the submodule
    tree and the published release set cannot disagree.

Usage:
    verify_submodules.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Source-only, published to npm rather than PyPI, so the umbrella pins no
# version for it and there is nothing to cross-check it against.
UNPINNED = {"akgentic-frontend"}

PIN = re.compile(r"^(akgentic-[a-z-]+)(?:\[[^\]]*\])?==([^\s,;]+)$")


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def umbrella_pins() -> dict[str, str]:
    """Every akgentic-*==version the umbrella pins."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data.get("project", {})
    requirements = list(project.get("dependencies", []))
    for extra_reqs in project.get("optional-dependencies", {}).values():
        requirements.extend(extra_reqs)

    pins: dict[str, str] = {}
    for requirement in requirements:
        match = PIN.match(requirement.strip())
        if match:
            pins[match.group(1)] = match.group(2)
    return pins


def submodule_paths() -> list[Path]:
    listing = git("config", "--file", ".gitmodules", "--get-regexp", r"\.path$", cwd=ROOT)
    return [ROOT / line.split(" ", 1)[1] for line in listing.splitlines() if line]


def gitlink_sha(path: Path) -> str:
    entry = git("ls-files", "-s", str(path.relative_to(ROOT)), cwd=ROOT)
    return entry.split()[1]


def check(path: Path, pins: dict[str, str]) -> str | None:
    """Return a failure message, or None when the submodule is correctly pinned."""
    name = path.name
    if not (path / ".git").exists():
        return f"{name}: not initialised — run `git submodule update --init`"

    version = pins.get(name)
    if version is None:
        if name in UNPINNED:
            sha = gitlink_sha(path)
            print(f"  ok   {name} @ {sha[:9]} (source-only, no umbrella pin)")
            return None
        return f"{name}: no matching pin in the umbrella's pyproject.toml"

    try:
        tag_commit = git("rev-parse", f"v{version}^{{commit}}", cwd=path)
    except subprocess.CalledProcessError:
        return f"{name}: tag v{version} does not exist in that repository"

    link = gitlink_sha(path)
    if link != tag_commit:
        return (
            f"{name}: pinned at {link[:9]} but v{version} is {tag_commit[:9]} — "
            "the submodule is not the released code"
        )

    print(f"  ok   {name} @ v{version} ({link[:9]})")
    return None


def main() -> None:
    pins = umbrella_pins()
    failures = [message for path in submodule_paths() if (message := check(path, pins))]

    if failures:
        print("::error::Submodules do not match the umbrella's release set:", file=sys.stderr)
        for message in failures:
            print(f"  {message}", file=sys.stderr)
        raise SystemExit(1)

    print("\nEvery submodule is pinned at the release tag the umbrella names.")


if __name__ == "__main__":
    main()
