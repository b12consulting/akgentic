#!/usr/bin/env python3
"""Build a release bundle for akgentic-framework.

Builds one wheel per workspace subpackage (core, llm, tool, agent, catalog,
team, infra) plus the framework meta-wheel, and packages them all into a
single tarball: akgentic-framework-<version>-bundle.tar.gz.

A README.md with install instructions is emitted alongside the wheels inside
the tarball.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
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
HIDDEN_EXTRAS = {"dev"}
# Documented in the README table but NOT pulled in by the `all-extras` extra
# (mutually exclusive with another extra that IS pulled in).
META_EXCLUDED_EXTRAS = {"postgres"}

# `akgentic-framework` extras → the subpackages each one installs. Transitive
# akgentic deps are NOT repeated here; they come from the subpackage's own
# metadata (so [agent] pulls llm+tool, and [infra] pulls all six). Repeating
# them would be a second, drift-prone source of truth.
FRAMEWORK_EXTRAS = {
    "llm": ["llm"],
    "tool": ["tool"],
    "agent": ["agent"],
    "team": ["team"],
    "catalog": ["catalog"],
    "infra": ["infra"],
}

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


def build_framework_extras() -> dict[str, list[str]]:
    """Build the `akgentic-framework` optional-dependencies table, all pins exact."""
    table = {name: [pin(sub) for sub in subs] for name, subs in FRAMEWORK_EXTRAS.items()}
    # Every subpackage, no optional extras (lean install).
    table["all"] = [pin(sub) for sub in SUBPACKAGES]
    # Every subpackage plus its own optional extras, discovered from source.
    table["all-extras"] = [
        pin(sub, extras=discover_extras(sub, exclude=HIDDEN_EXTRAS | META_EXCLUDED_EXTRAS))
        for sub in SUBPACKAGES
    ]
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


def render_framework_readme(version: str, extras: dict[str, list[str]]) -> str:
    """Long description for the `akgentic-framework` meta-distribution (shown on PyPI)."""
    rows = "\n".join(
        f"| `{name}` | " + ", ".join(f"`{d}`" for d in deps) + " |"
        for name, deps in extras.items()
        if name not in {"all", "all-extras"}
    )
    return f"""# akgentic-framework {version}

Meta-distribution for the akgentic framework. It contains no code — only a
pinned, coherent set of requirements. Installing an extra pulls the exact
subpackage versions that were built and tested together for release {version}.

## Install

Everything:

```bash
pip install "akgentic-framework[all]"
```

Everything, including the optional backends and heavier tool extras
(Mongo persistence, vector search, document parsing, …):

```bash
pip install "akgentic-framework[all-extras]"
```

Just the actor framework (`akgentic.core`):

```bash
pip install akgentic-framework
```

## À la carte

Extras compose, and each one pulls its own transitive akgentic dependencies —
`[agent]` brings `akgentic-llm` and `akgentic-tool` with it, and `[infra]`
brings the whole set.

| Extra | Installs |
|---|---|
{rows}

```bash
pip install "akgentic-framework[agent,catalog]"
```

## Notes

- Pins are exact by design. To move a single subpackage independently, depend
  on it directly instead of through this meta-distribution.
- `akgentic-all` and `akgentic-all-extras` are deprecated aliases for
  `akgentic-framework[all]` and `akgentic-framework[all-extras]`.
- Licensed under AGPL-3.0-only.
"""


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


def build_wheel(project_dir: Path, out_dir: Path) -> None:
    print(f"::group::Building {project_dir.name}")
    subprocess.run(
        ["uv", "build", "--wheel", "--project", str(project_dir), "--out-dir", str(out_dir)],
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
    """)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "pyproject.toml").write_text(pyproject)
        (tmp_path / "README.md").write_text(readme_body or f"# {name}\n\n{description}\n")
        shutil.copyfile(ROOT / "LICENSE", tmp_path / "LICENSE")
        subprocess.run(
            ["uv", "build", "--wheel", "--project", str(tmp_path), "--out-dir", str(out_dir)],
            check=True,
        )
    print("::endgroup::")


def main() -> None:
    out_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.whl"):
        stale.unlink()

    version = read_framework_version()
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
