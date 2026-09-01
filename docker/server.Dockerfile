# Backend image — the akgentic-infra community-tier server (src/infra_server.py).
#
# ONE Dockerfile, TWO sources for the akgentic-* packages, selected by ARG
# BUILD_SOURCE (fed from the BUILD_SOURCE env var by docker-compose.yml). Both
# stages leave a ready venv at /app/.venv, and the final stage builds on
# whichever was selected. BuildKit only builds the stage the final image needs,
# so the wheel path never touches packages/ and the source path never
# installs an akgentic wheel. Same mechanism as FRONTEND_SOURCE in
# docker/frontend.Dockerfile, and as BUILD_SOURCE in the department tier.
#
#   wheel  (default)  akgentic-* install from PyPI at the EXACT pins in this
#          repo's pyproject.toml / uv.lock. `uv sync --frozen` fails rather
#          than resolving something else, so the image carries the declared
#          release set and nothing else. Needs NO submodule checkout.
#
#   source            akgentic-* install EDITABLE from packages/akgentic-*, for
#          running the stack against modified framework sources. Requires
#          `git submodule update --init` first. The seven Python submodules are
#          copied by literal path below; a missing one fails at its COPY line.
#
# Build context is the repository ROOT for both — the root .dockerignore is what
# keeps the source path from copying submodule .venv directories into the
# image.
#
# NOTE on the source path: external (non-akgentic) dependencies still come
# from THIS repo's uv.lock. A local submodule that adds a brand-new third-party
# dependency will not have it installed — re-lock, or add it explicitly. The
# akgentic-* code is local; its dependency floor is not.

ARG BUILD_SOURCE=wheel

# ---- shared base -------------------------------------------------------------
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# curl is here for the docker-compose.yml healthcheck, and nothing else.
# python:3.12-slim ships neither curl nor wget, so without it the healthcheck
# has to be an inline `python -c "import urllib.request..."` one-liner — which
# works, but is unreadable inside a YAML string and awkward to run by hand when
# a container is failing. ~3 MB buys a healthcheck anyone can read and re-run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# ---- wheel: akgentic-* from PyPI at the locked pins --------------------------
FROM base AS wheel

# Manifests only: this layer is rebuilt when the release set changes, not on
# every edit to src/. LICENSE and README.md are required because pyproject.toml
# declares license-files and readme, and `uv sync` installs the root project.
COPY pyproject.toml uv.lock README.md LICENSE ./

# --no-group dev drops mypy/ruff (lint tooling, useless in a runtime image) but
# KEEPS the `demo` group, which is what pulls akgentic-framework[all-extras] —
# the actual server dependencies. Dropping both groups would install nothing.
RUN uv sync --frozen --no-group dev

# ---- source: akgentic-* editable from the local submodules ------------------
FROM base AS source

# Step 1 (cached) — EXTERNAL dependencies only, at this repo's locked versions.
# Rebuilt only when the lock or manifests change, NOT when submodule code is
# edited. The grep is what makes it "external only": the akgentic-* lines are
# dropped here so step 2 can supply them from local source instead.
# --all-extras matches the closure `uv sync` installs in the wheel stage, so
# the two paths carry the same third-party dependencies.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv venv --seed /app/.venv \
    && uv export --frozen --no-dev --no-hashes --no-emit-project --all-extras \
         --format requirements-txt \
       | grep -viE '^akgentic[-_]' > /app/ext-reqs.txt \
    && uv pip install -r /app/ext-reqs.txt

# Step 2 (cheap; re-runs on any submodule edit) — akgentic-* from local source.
# --no-deps is what keeps this honest: every dependency is already installed
# above, and without it pip would happily pull the PyPI-pinned akgentic wheels
# back in and shadow the local code this stage exists to use.
COPY packages/akgentic-core/    ./packages/akgentic-core/
COPY packages/akgentic-llm/     ./packages/akgentic-llm/
COPY packages/akgentic-tool/    ./packages/akgentic-tool/
COPY packages/akgentic-agent/   ./packages/akgentic-agent/
COPY packages/akgentic-team/    ./packages/akgentic-team/
COPY packages/akgentic-catalog/ ./packages/akgentic-catalog/
COPY packages/akgentic-infra/   ./packages/akgentic-infra/
RUN /app/.venv/bin/pip install --no-deps \
      -e ./packages/akgentic-core -e ./packages/akgentic-llm \
      -e ./packages/akgentic-tool -e ./packages/akgentic-agent \
      -e ./packages/akgentic-team -e ./packages/akgentic-catalog \
      -e ./packages/akgentic-infra

# ---- final: run the server on whichever venv was selected -------------------
FROM ${BUILD_SOURCE} AS final

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

# Last: src/ changes on every edit, and nothing above depends on it.
COPY src/ ./src/

# Team catalog, event store and workspaces live on bind mounts declared in
# docker-compose.yml. Creating them here means the container still starts if a
# mount is absent, instead of failing on a missing directory.
RUN mkdir -p /app/data/catalog /app/data/event_store /app/workspaces

EXPOSE 8000

CMD ["python", "src/infra_server.py"]
