# Run with Docker Compose

The whole stack — backend server and web UI — in two commands. This is the
fastest way to a working system, and the only path that needs **neither a
Python environment nor Node** on your machine.

## Quick start

```bash
git clone https://github.com/b12consulting/akgentic-framework.git
cd akgentic-framework

cp .env.example .env      # then fill in OPENAI_API_KEY
docker compose up -d --build
```

| | |
|---|---|
| Web UI | <http://localhost:4200> |
| API docs (Swagger) | <http://localhost:8000/docs> |

By default **no submodules are needed**: the backend installs akgentic-* from
PyPI at the pinned release set, and the frontend is downloaded as a prebuilt
bundle from its GitHub release. A bare clone is enough.

```bash
docker compose logs -f          # follow both services
docker compose ps               # health status
docker compose down             # stop; data and workspaces survive
```

## Configuration

Everything lives in `.env` (copied from `.env.example`, which documents each
entry). The essentials:

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** |
| `TAVILY_API_KEY` | — | Optional; enables the web-search tool. |
| `FRONTEND_PORT` | `4200` | Published host port for the UI. |
| `SERVER_PORT` | `8000` | Published host port for the API. |
| `API_URL` | `http://localhost:8000` | **Must match `SERVER_PORT`.** |

`API_URL` is the one setting that is easy to get wrong. The UI is a browser
app, so the call to the backend leaves *your browser*, not the frontend
container — it must be a host-visible URL. `http://server:8000` would resolve
only inside the Compose network and the UI would fail to load any team.

The container ports are fixed (server 8000, nginx 4200); the variables above
remap only what is published on the host. They default to the same numbers,
which are also the ports [Run locally](run-local.md) uses — convenient, but it
means you cannot run both at once without remapping one.

## What is persisted

Two bind mounts, so state stays readable on the host after `docker compose
down`:

| Host | Container | Holds |
|---|---|---|
| `./data` | `/app/data` | Team catalog and event store |
| `./workspaces` | `/app/workspaces` | Files the agents produce |

## Building from local sources

Both images default to a published artifact and can be switched to local
sources. Same idea on each side, so the two selectors read alike:

| Variable | Default | Local sources |
|---|---|---|
| `BUILD_SOURCE` | `wheel` — akgentic-* from PyPI at the `uv.lock` pins | `source` — editable from `packages/akgentic-*` |
| `FRONTEND_SOURCE` | `release` — prebuilt bundle from the GitHub release | `source` — Angular build from `packages/akgentic-frontend` |

The `source` values require the submodules:

```bash
git submodule update --init

BUILD_SOURCE=source FRONTEND_SOURCE=source docker compose up -d --build
```

Set them in `.env` to make the choice stick. Two things to know about
`BUILD_SOURCE=source`:

- External (non-akgentic) dependencies still come from this repo's `uv.lock`.
  A submodule that adds a brand-new third-party dependency will not have it
  installed — re-lock, or add it explicitly.
- It is a build-time copy, not a live mount, so editing a submodule needs a
  rebuild. The dependency layer stays cached, so that is seconds, not minutes.

Which frontend release is fetched is pinned in [`package.json`](../package.json)
(`dependencies.akgentic-frontend`) — bump it there. `FRONTEND_VERSION` in
`.env` overrides it for a one-off test.

> `akgentic-frontend` is **not** on the npm registry, so `npm install` against
> that `package.json` will fail. It is a version pin the Docker build reads;
> the only published artifact is the web-root tarball on each GitHub release.

## Files

| File | Role |
|---|---|
| `docker-compose.yml` | The two services, ports, mounts, healthcheck |
| `docker/server.Dockerfile` | Backend image; `wheel` / `source` stages |
| `docker/frontend.Dockerfile` | UI image; `release` / `source` stages |
| `docker/nginx.conf` | SPA routing and cache headers |
| `.dockerignore` | Keeps the build context small for both images |

## Troubleshooting

**The UI loads but no teams appear.** Almost always `API_URL` disagreeing with
`SERVER_PORT`. Check what the browser is actually told to call:

```bash
curl -s http://localhost:4200/config.json
```

**The server never becomes healthy.** The healthcheck polls `/readiness`, the
one route exempt from auth:

```bash
docker compose ps                                    # health column
docker compose logs server
docker compose exec server curl -fsS http://localhost:8000/readiness
```

**A `source` build fails on a missing path.** The submodules are not
initialised — `git submodule update --init`.
