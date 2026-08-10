#!/usr/bin/env python3
"""Build the publishable `akgentic-framework` distribution — and nothing else.

The umbrella is metadata only: `pyproject.toml` IS the artifact. Its version and
its pins are the source of truth, so this script generates no requirements. It
stages what is already there and builds that.

Subpackage wheels are deliberately not built here. Each package publishes itself
from its own repository; rebuilding them would produce a second, subtly
different artifact for the same version — which is exactly how the published
wheels came to disagree with the package repos' release tags.

Staging exists for two reasons:

  * PyPI renders the long description outside the repository, so README.md's
    repo-relative links and images have to be rewritten to absolute URLs.
    Building in place would publish broken links or mutate the tracked README.
  * The dev-only tables below the `--8<--` marker (uv workspace, dependency
    groups, shared tool settings) must not reach the staged copy: `uv build`
    fails on a [tool.uv.workspace] whose members do not exist there.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Everything from this marker to EOF configures the local checkout, not the
# distribution. Kept as a text marker rather than a table allow-list so that
# adding a dev-only table needs no change here — put it below the line.
DEV_ONLY_MARKER = "# --8<-- development-only below this line"

# Where relative README paths have to point once the README is rendered on PyPI,
# which serves none of the repository's files.
GITHUB_REPO = "https://github.com/b12consulting/akgentic-framework"
GITHUB_RAW = "https://raw.githubusercontent.com/b12consulting/akgentic-framework/master"
# Tracked with the README rather than pinned to a tag: the publish workflow runs
# after the release tag on a normal release, but not necessarily on a re-run, and
# a broken image is worse than one that follows master.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg")

STAGED_FILES = ("README.md", "LICENSE")


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


def read_extras() -> set[str]:
    """Extra names declared by the umbrella, minus the two aggregate ones."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = data.get("project", {}).get("optional-dependencies", {})
    return {name for name in declared if name not in {"all", "all-extras"}}


def check_readme_documents_extras(markdown: str) -> None:
    """Fail the build if README.md's à la carte table has drifted from the extras.

    The table is hand-written so that PyPI and the repository show the same page.
    This check is what stops it silently going stale — a reader who installs a
    documented extra that no longer exists gets a pip warning, not an error, and
    quietly ends up with less than they asked for.
    """
    expected = read_extras()
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


def publishable_pyproject() -> str:
    """The pyproject.toml text up to the dev-only marker."""
    text = (ROOT / "pyproject.toml").read_text()
    head, sep, _ = text.partition(DEV_ONLY_MARKER)
    if not sep:
        raise SystemExit(
            f"::error::pyproject.toml has no '{DEV_ONLY_MARKER}' marker.\n"
            "  Without it this script cannot tell publishable metadata from local\n"
            "  dev configuration, and uv build would fail on [tool.uv.workspace]."
        )
    return head


def stage(staging: Path) -> None:
    """Lay out the exact tree the distribution is built from."""
    readme = absolutize_readme_links((ROOT / "README.md").read_text())
    (staging / "pyproject.toml").write_text(publishable_pyproject())
    (staging / "README.md").write_text(readme)
    shutil.copyfile(ROOT / "LICENSE", staging / "LICENSE")


def read_version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default=str(ROOT / "dist"),
        help="output directory (default: ./dist)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in (*out_dir.glob("*.whl"), *out_dir.glob("*.tar.gz")):
        stale.unlink()

    # Checked against the tracked README, which is what a reader actually sees;
    # the staged copy differs only in link targets.
    check_readme_documents_extras((ROOT / "README.md").read_text())

    print("::group::Building akgentic-framework")
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        stage(staging)
        subprocess.run(
            ["uv", "build", "--project", str(staging), "--out-dir", str(out_dir)],
            check=True,
        )
    print("::endgroup::")

    built = sorted(p.name for p in out_dir.iterdir() if p.suffix in {".whl", ".gz"})
    print(f"\nBuilt akgentic-framework {read_version()}:")
    for name in built:
        print(f"  {name}")
    print(
        "\nSubpackage wheels are not built here — each package publishes itself "
        "from its own repository."
    )


if __name__ == "__main__":
    main()
