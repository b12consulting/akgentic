# Run from source

For **changing framework code** rather than using it. The same checkout runs
against the submodules under `packages/`, installed editable, instead of the
PyPI wheels.

If you only want to run the stack, use [Run locally](run-local.md) or
[Run with Docker Compose](run-docker.md) — neither needs submodules.

## Switch the checkout into source mode

**Initialise the submodules first** — uv reports a confusing error if a
workspace member directory is missing:

```bash
# 1. Fetch the sources, pinned at the release tags this version pins
git submodule update --init

# 2. Uncomment the two blocks under "SOURCE MODE" in pyproject.toml

# 3. Re-sync; akgentic-* now resolve to the local sources, editable
uv sync
```

The submodules are pinned at the exact commits their release tags point to, so
what you get is the code this release was built from:

```bash
uv run python scripts/verify_submodules.py
```

Because every package's own CI resolves its dependencies from PyPI, this is the
only place unreleased cross-package changes are exercised together.

Two things to expect:

- `uv.lock` is rewritten when you switch modes. That diff is expected; don't
  commit it — `git checkout uv.lock` when you're done.
- The `==` pins still apply to the local sources. Bump a submodule's version
  and `uv sync` fails until you regenerate the pins with
  `scripts/sync_versions.py`. That's deliberate: the pin table *is* the
  declared release set.

To check how the published metadata resolves without re-commenting anything,
use `uv sync --no-sources`.

## Run the server

Identical to the published path — the imports now resolve to your local
sources:

```bash
source .venv/bin/activate
export OPENAI_API_KEY="..."
python src/infra_server.py
```

Confirm which code is actually loaded when in doubt:

```bash
python -c "import akgentic.infra; print(akgentic.infra.__file__)"
# .../packages/akgentic-infra/src/akgentic/infra/__init__.py   <- local
# .../.venv/lib/python3.12/site-packages/...                   <- wheels
```

## Run the web UI

From source the UI is built by Angular, with live reload:

```bash
git submodule update --init packages/akgentic-frontend
cd packages/akgentic-frontend
npm install
npm start
```

<http://localhost:4200>, rebuilding on save. `npm start` serves against
`src/environments/environment.ts`, whose `api` already points at
`http://localhost:8000`.

## In containers

The Docker stack builds from source too, without leaving the compose workflow:

```bash
BUILD_SOURCE=source FRONTEND_SOURCE=source docker compose up -d --build
```

See [Run with Docker Compose](run-docker.md#building-from-local-sources) for
the caveats — chiefly that external dependencies still come from this repo's
`uv.lock`, and that a source build is a copy rather than a live mount.

## Working on a package

Each submodule is a normal checkout of its own repository, so branch and commit
in it as usual — and open the PR **against that repository**, not this one.
Run its tests and checks from its directory, under its own configuration:

```bash
cd packages/akgentic-core
uv run pytest tests/
uv run mypy src/ --config-file pyproject.toml
uv run ruff check src/
```

> `mypy` must be given the package's own config. Run from the workspace root it
> picks up the root `pyproject.toml`, which describes no package — that both
> invents errors and hides real ones.

More on the release workflow in [Development](development.md).
