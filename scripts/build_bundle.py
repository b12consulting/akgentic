#!/usr/bin/env python3
"""Build release artifacts for akgentic-framework.

Two modes, one source of truth for versions and pins:

  build_bundle.py DIR
      Default. Builds one wheel per workspace subpackage (core, llm, tool,
      agent, catalog, team, infra), the framework meta-wheel, and the
      deprecated alias wheels, then packages them all into a single tarball:
      akgentic-framework-<version>-bundle.tar.gz, with an install README
      alongside the wheels inside it.

  build_bundle.py DIR --pypi
      Builds the publishable set only, flat into DIR, as sdist + wheel for
      each: the 7 subpackages plus akgentic-framework. No tarball, no README.

      The deprecated aliases are deliberately NOT published. They exist to
      keep pins off a *previous bundle* resolving; nothing was ever published
      to PyPI under those names, so there are no public pins to preserve and
      claiming two public names for deprecated aliases is not undoable.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import tomllib
from pathlib import Path

SUBPACKAGES = ["core", "llm", "tool", "agent", "catalog", "team", "infra"]
ROOT = Path(__file__).resolve().parent.parent

# The publishable meta-distribution is `akgentic-framework`: a metadata-only
# wheel whose extras let a consumer install a coherent release set with one
# requirement, e.g. `pip install "akgentic-framework[all]"`.
#
# Why generated here rather than hand-written in the workspace root's
# pyproject.toml: the root is the uv *workspace* root (its akgentic-* deps
# resolve to editable members via [tool.uv.sources]), so it cannot also carry
# the exact `==` pins a release set needs. Generating from the subpackages'
# actual versions makes drift impossible — there is one source of truth.
#
# The pins are the point. Subpackages declare each other loosely
# (>=X.Y.0,<major+1) so they stay independently releasable; only this
# meta-wheel says "these exact versions were built and tested together".
# Without it, `akgentic-framework==2.8.0` on a public index would resolve each
# dependency to whatever is newest — the coherence guarantee that
# `--find-links` used to provide by restricting the candidate set.
#
# Hidden from users entirely (test-only, not relevant to a release bundle).
HIDDEN_EXTRAS = {"dev", "loadtest"}
# Reachable on its own as the `postgres` extra, but NOT pulled in by
# `all-extras`: it is mutually exclusive with `mongo`, which `all-extras` does
# pull in. Composing `[all,postgres]` is what selects the other backend.
META_EXCLUDED_EXTRAS = {"postgres"}

# Superseded by `akgentic-framework[all]` / `[all-extras]`. Still built, as
# thin alias wheels, so consumers pinning the old names off a previous bundle
# keep resolving. Drop once no consumer references them.
DEPRECATED_ALIASES = {
    "akgentic-all": "all",
    "akgentic-all-extras": "all-extras",
}

META_CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Topic :: Software Development :: Libraries :: Python Modules",
]


def discover_extras(subpackage: str, *, exclude: set[str]) -> list[str]:
    """Return the optional-dependencies keys for `akgentic-<subpackage>`, filtered."""
    pyproject = ROOT / "packages" / f"akgentic-{subpackage}" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    extras = data.get("project", {}).get("optional-dependencies", {})
    return sorted(name for name in extras if name not in exclude)


def read_subpackage_version(subpackage: str) -> str:
    pyproject = ROOT / "packages" / f"akgentic-{subpackage}" / "pyproject.toml"
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def pin(subpackage: str, *, extras: list[str] | None = None) -> str:
    """Render an exact requirement for `akgentic-<subpackage>` at its current version."""
    spec = f"akgentic-{subpackage}"
    if extras:
        spec += f"[{','.join(extras)}]"
    return f"{spec}=={read_subpackage_version(subpackage)}"


def akgentic_dependencies(subpackage: str) -> set[str]:
    """Short names of the akgentic-* packages that `akgentic-<subpackage>` requires."""
    pyproject = ROOT / "packages" / f"akgentic-{subpackage}" / "pyproject.toml"
    deps = tomllib.loads(pyproject.read_text())["project"].get("dependencies", [])
    names = (re.split(r"[<>=!~;\[\s]", dep, maxsplit=1)[0].strip() for dep in deps)
    return {name.removeprefix("akgentic-") for name in names if name.startswith("akgentic-")}


def akgentic_closure(roots: list[str]) -> list[str]:
    """`roots` plus every akgentic-* package they depend on, transitively.

    Derived from the subpackages' own metadata, so the umbrella can never claim
    a set the packages themselves disagree with. Returned in SUBPACKAGES order.
    """
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(akgentic_dependencies(current) - seen)
    return [sub for sub in SUBPACKAGES if sub in seen]


def pin_closure(roots: list[str], extras_by_sub: dict[str, list[str]] | None = None) -> list[str]:
    """Exact pins for the whole closure of `roots`, applying per-package extras."""
    per_sub = extras_by_sub or {}
    return [pin(sub, extras=per_sub.get(sub)) for sub in akgentic_closure(roots)]


def postgres_providers() -> list[str]:
    """Subpackages that publish a `postgres` extra."""
    return [sub for sub in SUBPACKAGES if "postgres" in discover_extras(sub, exclude=set())]


def build_framework_extras() -> dict[str, list[str]]:
    """Build the `akgentic-framework` optional-dependencies table, all pins exact.

    Every extra pins its entire akgentic closure, not just the package it names.
    Pinning only the named package would leave its akgentic dependencies to
    resolve by their own bounds, so `[agent]` could pull an akgentic-llm that
    was never tested against this release set — the one thing this
    meta-distribution exists to prevent.
    """
    table = {sub: pin_closure([sub]) for sub in SUBPACKAGES}
    # Every subpackage, no optional extras (lean install).
    table["all"] = pin_closure(SUBPACKAGES)
    # Every subpackage plus its own optional extras, discovered from source.
    table["all-extras"] = pin_closure(
        SUBPACKAGES,
        {
            sub: discover_extras(sub, exclude=HIDDEN_EXTRAS | META_EXCLUDED_EXTRAS)
            for sub in SUBPACKAGES
        },
    )
    # The backend `all-extras` cannot carry; compose it as `[all,postgres]`.
    providers = postgres_providers()
    table["postgres"] = pin_closure(providers, {sub: ["postgres"] for sub in providers})
    return table


def read_framework_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def render_extras_table() -> str:
    """Render a markdown table of subpackages that publish optional extras."""
    rows = []
    for sub in SUBPACKAGES:
        extras = discover_extras(sub, exclude=HIDDEN_EXTRAS)
        if not extras:
            continue
        formatted = ", ".join(f"`{e}`" for e in extras)
        rows.append(f"| `akgentic-{sub}` | {formatted} |")
    if not rows:
        return ""
    return "| Subpackage | Available extras |\n|---|---|\n" + "\n".join(rows)


# Where relative README paths have to point once the README is rendered on PyPI,
# which serves none of the repository's files.
GITHUB_REPO = "https://github.com/b12consulting/akgentic-framework"
GITHUB_RAW = "https://raw.githubusercontent.com/b12consulting/akgentic-framework/master"
# Tracked with the README rather than pinned to a tag: the publish workflow runs
# after the release tag on a normal release, but not necessarily on a re-run, and
# a broken image is worse than one that follows master.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg")


def absolutize_readme_links(markdown: str) -> str:
    """Rewrite the README's repo-relative links and images to absolute URLs.

    PyPI renders the long description outside the repository, so `akgents.png`
    or `packages/akgentic-core/README.md` would 404. Images need raw.github,
    everything else needs the blob/tree view.
    """

    def repo_url(path: str) -> str:
        clean = path.split("#", 1)[0]
        if clean.endswith(IMAGE_SUFFIXES):
            return f"{GITHUB_RAW}/{path}"
        # A trailing slash means a directory listing, which is `tree`, not `blob`.
        view = "tree" if clean.endswith("/") else "blob"
        return f"{GITHUB_REPO}/{view}/master/{path}"

    def sub_markdown(match: re.Match[str]) -> str:
        text, target = match.group(1), match.group(2)
        if re.match(r"^(https?:|mailto:|#)", target):
            return match.group(0)
        return f"[{text}]({repo_url(target)})"

    def sub_html_src(match: re.Match[str]) -> str:
        target = match.group(1)
        if re.match(r"^(https?:|data:)", target):
            return match.group(0)
        return f'src="{repo_url(target)}"'

    # `[text](target)` — the text may itself contain brackets (nested images in
    # badge links), so match it non-greedily rather than excluding `]`.
    out = re.sub(r"\[(.*?)\]\(([^)\s]+)\)", sub_markdown, markdown)
    return re.sub(r'src="([^"]+)"', sub_html_src, out)


def check_readme_documents_extras(markdown: str, extras: dict[str, list[str]]) -> None:
    """Fail the build if README.md's extras table has drifted from the real extras.

    The table used to be generated, which made drift impossible. It now lives in
    the README so that PyPI and the repository show the same page — this check is
    what keeps the old guarantee.
    """
    expected = {name for name in extras if name not in {"all", "all-extras"}}
    documented = set(re.findall(r"^\| `([a-z-]+)` \| `akgentic-", markdown, re.MULTILINE))
    if documented != expected:
        missing = ", ".join(sorted(expected - documented)) or "—"
        extra = ", ".join(sorted(documented - expected)) or "—"
        raise SystemExit(
            "::error::README.md's extras table is out of sync with the real extras.\n"
            f"  missing from README: {missing}\n"
            f"  in README but not real: {extra}\n"
            "  Update the 'À la carte' table in README.md."
        )


def render_framework_readme(version: str, extras: dict[str, list[str]]) -> str:
    """Long description for `akgentic-framework` (shown on PyPI): the repo README."""
    markdown = (ROOT / "README.md").read_text()
    check_readme_documents_extras(markdown, extras)
    return absolutize_readme_links(markdown)


def write_readme(bundle_dir: Path, version: str) -> None:
    bundle_name = bundle_dir.name
    extras_table = render_extras_table()
    readme = f"""# akgentic {version} — release bundle

This archive contains wheels for the `akgentic.*` subpackages:

| Wheel | Namespace | Purpose |
|-------|-----------|---------|
| `akgentic-core`    | `akgentic.core`    | Actor framework — required by everything else (formerly published as `akgentic`) |
| `akgentic-llm`     | `akgentic.llm`     | LLM integration (pydantic-ai, multi-provider) |
| `akgentic-tool`    | `akgentic.tool`    | Tool abstractions, Tavily search, MCP, etc. |
| `akgentic-agent`   | `akgentic.agent`   | Collaborative agent patterns |
| `akgentic-catalog` | `akgentic.catalog` | Configuration registry (templates, tools, agents) |
| `akgentic-team`    | `akgentic.team`    | Team lifecycle management |
| `akgentic-infra`   | `akgentic.infra`   | Infrastructure server / TUI / CLIs |

External runtime dependencies (pydantic, fastapi, textual, …) are resolved
from PyPI at install time. Pick only the subpackages you need.

## Install (use a clean virtual environment)

```bash
python -m venv .venv
source .venv/bin/activate

tar -xzf {bundle_name}.tar.gz
```

### Common combinations

Minimal — just the actor framework:
```bash
pip install --find-links {bundle_name} akgentic-core
```

Build an agent with LLM + tools:
```bash
pip install --find-links {bundle_name} akgentic-agent akgentic-llm akgentic-tool
```

Everything, no extras (lean install):
```bash
pip install --find-links {bundle_name} "akgentic-framework[all]"
```

Everything with all optional extras (mongo backend, vector search, doc parsing, etc.):
```bash
pip install --find-links {bundle_name} "akgentic-framework[all-extras]"
```

`akgentic-framework` is a metadata-only wheel that pins the exact subpackage
versions of this release. Its extras also work à la carte —
`"akgentic-framework[agent,catalog]"` — and each extra pulls its own transitive
akgentic dependencies.

> `akgentic-all` and `akgentic-all-extras` are **deprecated** aliases for
> `akgentic-framework[all]` and `akgentic-framework[all-extras]`. They are still
> in this bundle so existing pins keep resolving.

### Pick individual extras

Several subpackages publish optional extras for heavier backends. Request
them with the `[extra]` syntax on the package name:

{extras_table}

> `mongo` and `postgres` are mutually exclusive persistence backends — pick one.

Tool: vector search (openai + numpy) and doc parsing:
```bash
pip install --find-links {bundle_name} "akgentic-tool[vector_search,docs]"
```

Catalog: FastAPI server + CLI + Postgres backend:
```bash
pip install --find-links {bundle_name} "akgentic-catalog[api,cli,postgres]"
```

Team: Mongo backend + CLI:
```bash
pip install --find-links {bundle_name} "akgentic-team[mongo,cli]"
```

Extras compose with each other and with other subpackages on the same
`pip install` line:
```bash
pip install --find-links {bundle_name} \\
    akgentic-agent akgentic-llm \\
    "akgentic-tool[vector_search,docs,vision]" \\
    "akgentic-catalog[api,cli,postgres]"
```

## Troubleshooting

- **"Requirement already satisfied: akgentic …"** — pip is reusing an older
  install from your system / conda environment instead of the bundled wheel.
  Install into a clean venv, or add `--force-reinstall --no-deps` for the
  `akgentic*` packages.
- **"No matching distribution found for <pkg>"** with `--no-index` — the
  bundle is missing a transitive dep. Either drop `--no-index`, or rebuild
  the bundle with `pip download` as shown above.
"""
    (bundle_dir / "README.md").write_text(readme)


def _build_flags(sdist: bool) -> list[str]:
    """uv build flags: wheel only for the bundle, sdist + wheel for PyPI."""
    return [] if sdist else ["--wheel"]


def build_wheel(project_dir: Path, out_dir: Path, *, sdist: bool = False) -> None:
    print(f"::group::Building {project_dir.name}")
    subprocess.run(
        [
            "uv",
            "build",
            *_build_flags(sdist),
            "--project",
            str(project_dir),
            "--out-dir",
            str(out_dir),
        ],
        check=True,
    )
    print("::endgroup::")


def _render_deps(deps: list[str]) -> str:
    return "".join(f'\n    "{d}",' for d in deps)


def build_meta_wheel(
    name: str,
    description: str,
    dependencies: list[str],
    version: str,
    out_dir: Path,
    *,
    optional_dependencies: dict[str, list[str]] | None = None,
    readme_body: str = "",
    sdist: bool = False,
) -> None:
    """Generate a metadata-only wheel (no modules) with the given requirements."""
    print(f"::group::Building {name} (meta)")
    optional_block = ""
    if optional_dependencies:
        rendered = "\n".join(
            f"{extra} = [{_render_deps(deps)}\n]" for extra, deps in optional_dependencies.items()
        )
        optional_block = f"\n[project.optional-dependencies]\n{rendered}\n"
    classifiers_block = "".join(f'\n    "{c}",' for c in META_CLASSIFIERS)
    pyproject = textwrap.dedent(f"""\
        [project]
        name = "{name}"
        version = "{version}"
        description = "{description}"
        readme = "README.md"
        requires-python = ">=3.12"
        license = "AGPL-3.0-only"
        license-files = ["LICENSE"]
        classifiers = [{classifiers_block}
        ]
        dependencies = [{_render_deps(dependencies)}
        ]
        {optional_block}
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        # Metadata-only distribution: there is no module to package, so skip
        # hatchling's file selection entirely. This is what makes the wheel a
        # pure set of requirements.
        bypass-selection = true

        [tool.hatch.build.targets.sdist]
        # Same reason, but sdist has no bypass-selection: name the files
        # explicitly, or hatchling errors out looking for a package to include.
        only-include = ["README.md", "LICENSE"]
    """)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "pyproject.toml").write_text(pyproject)
        (tmp_path / "README.md").write_text(readme_body or f"# {name}\n\n{description}\n")
        shutil.copyfile(ROOT / "LICENSE", tmp_path / "LICENSE")
        subprocess.run(
            [
                "uv",
                "build",
                *_build_flags(sdist),
                "--project",
                str(tmp_path),
                "--out-dir",
                str(out_dir),
            ],
            check=True,
        )
    print("::endgroup::")


def build_pypi_dists(out_dir: Path, version: str) -> None:
    """Build the publishable set (sdist + wheel) flat into out_dir."""
    for sub in SUBPACKAGES:
        build_wheel(ROOT / "packages" / f"akgentic-{sub}", out_dir, sdist=True)

    extras = build_framework_extras()
    build_meta_wheel(
        name="akgentic-framework",
        description="Meta-distribution pinning a coherent akgentic release set.",
        dependencies=[pin("core")],
        optional_dependencies=extras,
        version=version,
        out_dir=out_dir,
        readme_body=render_framework_readme(version, extras),
        sdist=True,
    )

    dists = sorted(p.name for p in out_dir.iterdir() if p.suffix in {".whl", ".gz"})
    print(f"\nBuilt {len(dists)} distribution file(s) for PyPI:")
    for d in dists:
        print(f"  {d}")
    print(
        "\nDeprecated aliases (akgentic-all, akgentic-all-extras) are intentionally "
        "excluded — bundle-only."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default=str(ROOT / "dist"),
        help="output directory (default: ./dist)",
    )
    parser.add_argument(
        "--pypi",
        action="store_true",
        help="build the publishable set (sdist + wheel, no aliases, no tarball)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in (*out_dir.glob("*.whl"), *out_dir.glob("*.tar.gz")):
        stale.unlink()

    version = read_framework_version()

    if args.pypi:
        build_pypi_dists(out_dir, version)
        return

    bundle_name = f"akgentic-framework-{version}-bundle"
    bundle_dir = out_dir / bundle_name
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir()

    for sub in SUBPACKAGES:
        build_wheel(ROOT / "packages" / f"akgentic-{sub}", bundle_dir)

    extras = build_framework_extras()
    build_meta_wheel(
        name="akgentic-framework",
        description="Meta-distribution pinning a coherent akgentic release set.",
        # Base install is the actor framework alone; everything else is opt-in
        # through an extra, so `akgentic-framework` stays a usable minimal floor
        # instead of dragging in fastapi/textual/pydantic-ai for every consumer.
        dependencies=[pin("core")],
        optional_dependencies=extras,
        version=version,
        out_dir=bundle_dir,
        readme_body=render_framework_readme(version, extras),
    )
    for alias, extra in DEPRECATED_ALIASES.items():
        build_meta_wheel(
            name=alias,
            description=f"Deprecated alias for akgentic-framework[{extra}].",
            dependencies=[f"akgentic-framework[{extra}]=={version}"],
            version=version,
            out_dir=bundle_dir,
            readme_body=(
                f"# {alias}\n\nDeprecated. Use `akgentic-framework[{extra}]=={version}` "
                f"instead; this distribution only forwards to it.\n"
            ),
        )

    write_readme(bundle_dir, version)

    tarball = out_dir / f"{bundle_name}.tar.gz"
    if tarball.exists():
        tarball.unlink()
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(bundle_dir, arcname=bundle_name)

    wheels = sorted(bundle_dir.glob("*.whl"))
    print(f"\nBundled {len(wheels)} wheel(s) into {tarball.name}:")
    for w in wheels:
        print(f"  {w.name} ({w.stat().st_size:,} bytes)")
    print(f"\nTarball: {tarball} ({tarball.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
