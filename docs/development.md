# Development

## Where the work happens

Each package is its own repository, with its own CI, lint rules and coverage
gate. Changes to a package are made, reviewed and released **there** — this
repository holds no subpackage code.

What it does hold is the release set, and the one place unreleased packages are
exercised together. Every package's CI resolves its dependencies from PyPI, so
no package's own pipeline ever sees an unreleased sibling. Source mode here is
where that combination gets tried — see
[Run from source](run-from-source.md) for the full setup:

```bash
git submodule update --init
# uncomment the two blocks under "SOURCE MODE" in pyproject.toml
uv sync
```

Each submodule is a normal checkout of its repository, so branch and commit in
it as usual — and open the PR against that repository, not this one. The
submodules are pinned at release tags, so you start from exactly the code this
release was built from:

```bash
uv run python scripts/verify_submodules.py
```

Run a package's own tests and checks from its directory, under its own
configuration:

```bash
cd packages/akgentic-core
uv run pytest tests/
uv run mypy src/ --config-file pyproject.toml
uv run ruff check src/
```

This repository's own gates cover `scripts/` and `src/` only — it has no test
suite, and deliberately does not collect the submodules'.

## Cutting a release

The umbrella's version is a release-set counter: it is bumped by hand when a
set of package versions is worth publishing together. The pins are not — they
are generated from the submodules.

```bash
# 1. Move the submodules to the release tags you want in the set
git submodule update --init
git -C packages/akgentic-core checkout v1.6.0

# 2. Regenerate the pins and extras from those submodules
uv run python scripts/sync_versions.py

# 3. Bump [project].version by hand, then open a PR with both changes
```

Once merged, and once every package version in the set is on PyPI, dispatch
**Release** (tags the commit, attaches the umbrella wheel and sdist to a GitHub
Release) and then **Publish to PyPI** from the Actions tab. Both refuse to run
if a pinned version is missing from the index, if a submodule is not sitting on
its release tag, or if the committed pins disagree with the submodules.

The PyPI project page shows the description of the *latest* release, baked into
that release's metadata. It cannot be edited in place — a README fix reaches
PyPI only on the next version bump.

## The web UI release

`akgentic-frontend` releases on its own cadence, from its own repository. Its
release workflow attaches the built web root as a tarball, which is what both
the Docker image and [Run locally](run-local.md) consume — there is no npm
package.

The version this stack serves is pinned in [`package.json`](../package.json)
(`dependencies.akgentic-frontend`). Bump it there when adopting a new UI
release.

## Next

- [Run from source](run-from-source.md) — set the checkout up for local work
- [Run with Docker Compose](run-docker.md) — exercise a change in the stack
