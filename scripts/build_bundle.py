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

# Two meta-wheels are generated:
#   - akgentic-all:         every subpackage, no optional extras (lean install)
#   - akgentic-all-extras:  every subpackage with all optional extras (mongo backend)
#
# `akgentic-all-extras` deps are derived from each subpackage's pyproject.toml
# (see `discover_extras`), filtered by EXCLUDED_EXTRAS to drop test-only and
# mutually-exclusive-backend extras.
META_ALL_DEPENDENCIES = [f"akgentic-{sub}" for sub in ["core", "llm", "tool", "agent", "catalog", "team", "infra"]]
# Hidden from users entirely (test-only, not relevant to a release bundle).
HIDDEN_EXTRAS = {"dev"}
# Documented in the README table but NOT pulled in by `akgentic-all-extras`
# (mutually exclusive with another extra that IS pulled in).
META_EXCLUDED_EXTRAS = {"postgres"}


def discover_extras(subpackage: str, *, exclude: set[str]) -> list[str]:
    """Return the optional-dependencies keys for `akgentic-<subpackage>`, filtered."""
    pyproject = ROOT / "packages" / f"akgentic-{subpackage}" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    extras = data.get("project", {}).get("optional-dependencies", {})
    return sorted(name for name in extras if name not in exclude)


def build_all_extras_dependencies() -> list[str]:
    """Build the `akgentic-all-extras` dep list by reading each subpackage's extras."""
    deps: list[str] = []
    for sub in SUBPACKAGES:
        extras = discover_extras(sub, exclude=HIDDEN_EXTRAS | META_EXCLUDED_EXTRAS)
        pkg = f"akgentic-{sub}"
        deps.append(f"{pkg}[{','.join(extras)}]" if extras else pkg)
    return deps


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
pip install --find-links {bundle_name} akgentic-all
```

Everything with all optional extras (mongo backend, vector search, doc parsing, etc.):
```bash
pip install --find-links {bundle_name} akgentic-all-extras
```

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


def build_meta_wheel(
    name: str, description: str, dependencies: list[str], version: str, out_dir: Path
) -> None:
    """Generate a meta-wheel with the given name and subpackage dependencies."""
    print(f"::group::Building {name} (meta)")
    deps_block = ",\n    ".join(f'"{d}"' for d in dependencies)
    pyproject = textwrap.dedent(f"""\
        [project]
        name = "{name}"
        version = "{version}"
        description = "{description}"
        requires-python = ">=3.12"
        dependencies = [
            {deps_block},
        ]

        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"

        [tool.hatch.build.targets.wheel]
        bypass-selection = true
    """)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "pyproject.toml").write_text(pyproject)
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

    build_meta_wheel(
        name="akgentic-all",
        description="Meta-package: installs every akgentic subpackage (no optional extras).",
        dependencies=META_ALL_DEPENDENCIES,
        version=version,
        out_dir=bundle_dir,
    )
    build_meta_wheel(
        name="akgentic-all-extras",
        description="Meta-package: installs every akgentic subpackage with all optional extras.",
        dependencies=build_all_extras_dependencies(),
        version=version,
        out_dir=bundle_dir,
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
