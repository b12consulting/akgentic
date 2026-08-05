#!/usr/bin/env python3
"""Regenerate the umbrella's pins and extras from the submodules.

The umbrella declares a release set: exact versions that were built and tested
together. That set is derived, not authored — every pin and every extra comes
from the submodules' own pyproject.toml files, so the umbrella can never claim a
combination the packages themselves disagree with.

What this does NOT touch is `[project].version`. There is no function from the
subpackage versions to the umbrella's: it is a release-set counter, bumped by a
human deciding that a new set is worth publishing.

Usage:
    sync_versions.py             rewrite pyproject.toml in place
    sync_versions.py --check     exit 1 if it would change anything (for CI)
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

# Canonical order — dependencies before dependents, so a rendered closure reads
# bottom-up and diffs stay stable across runs.
SUBPACKAGES = ["core", "llm", "tool", "agent", "catalog", "team", "infra"]

# Test-only; never part of a release install.
HIDDEN_EXTRAS = {"dev", "loadtest"}
# Reachable as its own extra, but excluded from all-extras: mutually exclusive
# with `mongo`, which all-extras does pull in.
META_EXCLUDED_EXTRAS = {"postgres"}

BASE = ["core"]

# The generated region, delimited by sentinels in pyproject.toml. Anchoring on
# neighbouring prose instead would silently swallow whatever moved in between.
BEGIN = "[project.optional-dependencies]"
END = "# END GENERATED"


def package_dir(subpackage: str) -> Path:
    return ROOT / "packages" / f"akgentic-{subpackage}"


def load(subpackage: str) -> dict[str, object]:
    pyproject = package_dir(subpackage) / "pyproject.toml"
    if not pyproject.exists():
        raise SystemExit(
            f"::error::{pyproject.relative_to(ROOT)} is missing.\n"
            "  The submodules are not initialised — run `git submodule update --init`."
        )
    data: dict[str, object] = tomllib.loads(pyproject.read_text())["project"]
    return data


def version(subpackage: str) -> str:
    return str(load(subpackage)["version"])


def extras_of(subpackage: str, *, exclude: set[str]) -> list[str]:
    declared = load(subpackage).get("optional-dependencies", {})
    assert isinstance(declared, dict)
    return sorted(name for name in declared if name not in exclude)


def akgentic_dependencies(subpackage: str) -> set[str]:
    """Short names of the akgentic-* packages `akgentic-<subpackage>` requires."""
    deps = load(subpackage).get("dependencies", [])
    assert isinstance(deps, list)
    names = (re.split(r"[<>=!~;\[\s]", str(dep), maxsplit=1)[0].strip() for dep in deps)
    return {name.removeprefix("akgentic-") for name in names if name.startswith("akgentic-")}


def closure(roots: list[str]) -> list[str]:
    """`roots` plus every akgentic-* package they depend on, transitively."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(akgentic_dependencies(current) - seen)
    return [sub for sub in SUBPACKAGES if sub in seen]


def normalise_extra(name: str) -> str:
    """Normalise an extra name the way PEP 685 requires.

    Subpackages declare extras however they like — akgentic-tool spells one
    `vector_search` — but a build backend normalises them, so the wheel says
    `vector-search`. Emitting the raw key would still work, but the file would
    disagree with what actually ships and --check would never settle.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def pin(subpackage: str, extras: list[str] | None = None) -> str:
    spec = f"akgentic-{subpackage}"
    if extras:
        spec += f"[{','.join(normalise_extra(name) for name in extras)}]"
    return f"{spec}=={version(subpackage)}"


def pin_closure(roots: list[str], extras_by_sub: dict[str, list[str]] | None = None) -> list[str]:
    per_sub = extras_by_sub or {}
    return [pin(sub, per_sub.get(sub)) for sub in closure(roots)]


def build_extras() -> dict[str, list[str]]:
    table: dict[str, list[str]] = {sub: pin_closure([sub]) for sub in SUBPACKAGES}
    table["all"] = pin_closure(SUBPACKAGES)
    table["all-extras"] = pin_closure(
        SUBPACKAGES,
        {
            sub: extras_of(sub, exclude=HIDDEN_EXTRAS | META_EXCLUDED_EXTRAS)
            for sub in SUBPACKAGES
        },
    )
    providers = [sub for sub in SUBPACKAGES if "postgres" in extras_of(sub, exclude=set())]
    table["postgres"] = pin_closure(providers, {sub: ["postgres"] for sub in providers})
    return table


# Emitted with the entries they describe. Per-entry comments cannot be preserved
# across a regeneration, so the generator has to own them or they are lost.
NOTES = {
    "all": "# Every subpackage, no optional extras (lean install).",
    "all-extras": (
        "# Every subpackage plus its own optional extras. `postgres` is\n"
        "# deliberately absent: it is mutually exclusive with `mongo`, which\n"
        "# this pulls in."
    ),
    "postgres": "# The backend all-extras cannot carry; compose it as [all,postgres].",
}


def render(table: dict[str, list[str]]) -> str:
    lines = [BEGIN]
    for name, requirements in table.items():
        if note := NOTES.get(name):
            lines.append(note)
        if len(requirements) == 1:
            lines.append(f'{name} = ["{requirements[0]}"]')
            continue
        lines.append(f"{name} = [")
        lines.extend(f'    "{requirement}",' for requirement in requirements)
        lines.append("]")
    return "\n".join(lines) + "\n"


def render_base() -> str:
    rendered = "".join(f'\n    "{pin(sub)}",' for sub in BASE)
    return f"dependencies = [{rendered}\n]\n"


def regenerate(current: str, table: dict[str, list[str]]) -> str:
    """Replace the generated base-dependency and extras regions."""
    updated = re.sub(
        r"^dependencies = \[.*?^\]\n", render_base(), current, count=1, flags=re.M | re.S
    )
    start = updated.index(BEGIN)
    end = updated.index(END, start)
    return updated[:start] + render(table) + updated[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if pyproject.toml is out of date, without writing",
    )
    args = parser.parse_args()

    current = PYPROJECT.read_text()
    updated = regenerate(current, build_extras())

    if current == updated:
        print("pyproject.toml is up to date with the submodules.")
        return

    if args.check:
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="pyproject.toml (committed)",
            tofile="pyproject.toml (from submodules)",
        )
        sys.stderr.writelines(diff)
        raise SystemExit(
            "::error::pyproject.toml's pins are out of date with the submodules.\n"
            "  Run scripts/sync_versions.py and commit the result."
        )

    PYPROJECT.write_text(updated)
    print("Rewrote pyproject.toml pins from the submodules.")
    print("`version` is untouched — bump it by hand when cutting a release set.")


if __name__ == "__main__":
    main()
