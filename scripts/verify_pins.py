#!/usr/bin/env python3
"""Verify every akgentic pin in the umbrella is already published.

The umbrella is nothing but exact pins, so publishing it before the versions it
names are on the index produces a distribution that can never be installed —
and PyPI metadata is immutable, so that version number is burned permanently.

This used to be guaranteed structurally: one workflow published all eight
distributions in dependency order with `max-parallel: 1`. Now that each package
publishes itself from its own repository, nothing sequences the umbrella after
them. This check is what replaces that guarantee, so it must run before every
publish.

Usage:
    verify_pins.py [--index-url URL]
"""

from __future__ import annotations

import argparse
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# `akgentic-tool[docs,vector-search]==1.6.0` -> ("akgentic-tool", "1.6.0").
# Only exact pins are checked; anything looser is not a release-set claim.
PIN = re.compile(r"^(akgentic-[a-z-]+)(?:\[[^\]]*\])?==([^\s,;]+)$")


def collect_pins() -> dict[str, str]:
    """Every distinct akgentic-*==version pinned by the umbrella."""
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


def is_published(name: str, version: str, index_url: str) -> bool:
    """True if `name==version` exists on the index."""
    url = f"{index_url.rstrip('/')}/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return bool(response.status == 200)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-url",
        default="https://pypi.org/pypi",
        help="JSON API base (default: %(default)s)",
    )
    args = parser.parse_args()

    pins = collect_pins()
    if not pins:
        raise SystemExit("::error::No akgentic-*==version pins found in pyproject.toml.")

    missing: list[str] = []
    for name, version in sorted(pins.items()):
        published = is_published(name, version, args.index_url)
        print(f"  {'ok  ' if published else 'MISS'} {name}=={version}")
        if not published:
            missing.append(f"{name}=={version}")

    if missing:
        raise SystemExit(
            "::error::The umbrella pins versions that are not on the index:\n"
            + "".join(f"  {pin}\n" for pin in missing)
            + "  Publish those packages first, then re-run. Publishing the umbrella\n"
            "  now would create a version that can never be installed."
        )

    print(f"\nAll {len(pins)} pinned akgentic versions are published.")


if __name__ == "__main__":
    main()
