# Run locally, from published artifacts

Two processes on your machine — no Docker, and **no Angular build**. The
backend installs from PyPI; the UI is downloaded as a prebuilt bundle from its
GitHub release. Neither needs the submodules.

To run against framework code you are editing, see
[Run from source](run-from-source.md) instead.

## 1. The server

Install the release set and start the boot script
([`src/infra_server.py`](../src/infra_server.py)):

```bash
git clone https://github.com/b12consulting/akgentic-framework.git
cd akgentic-framework
uv sync
source .venv/bin/activate

export OPENAI_API_KEY="..."     # https://platform.openai.com/api-keys
export TAVILY_API_KEY="..."     # optional, enables web search

python src/infra_server.py      # add --logfire for structured logging
```

The API is on <http://localhost:8000>, Swagger on
<http://localhost:8000/docs>.

> **Why the clone?** The akgentic packages install cleanly from PyPI, but there
> is currently **no console script for the server** — the published entry
> points are the CLIs (`ak-infra`, `ak-catalog`, `ak-team`). Community-tier
> wiring lives in this repo's boot script. To run without a clone, install
> `akgentic-framework[all-extras]` and reproduce those ~20 lines:
>
> ```python
> import uvicorn
> from akgentic.infra.server.app import create_app
> from akgentic.infra.server.settings import CommunitySettings
> from akgentic.infra.wiring import wire_community
>
> settings = CommunitySettings()
> app = create_app(wire_community(settings), settings)
> uvicorn.run(app, host=settings.host, port=settings.port, ws="wsproto")
> ```

### Where state goes

Relative to the working directory, configurable through `CommunitySettings` or
`AKGENTIC_`-prefixed environment variables:

| Setting | Default |
|---|---|
| `AKGENTIC_CATALOG_PATH` | `./data/catalog/` |
| `AKGENTIC_EVENT_STORE_PATH` | `./data/event_store/` |
| `AKGENTIC_WORKSPACES_ROOT` | `./workspaces/` |
| `AKGENTIC_PORT` | `8000` |

## 2. The web UI

The frontend is an Angular app released from its own repository. Each release
attaches the **built web root** as a tarball, so serving it needs no Node
toolchain and no build:

```bash
VERSION=1.12.0     # https://github.com/b12consulting/akgentic-frontend/releases

curl -fsSL -o frontend.tar.gz \
  "https://github.com/b12consulting/akgentic-frontend/releases/download/v${VERSION}/akgentic-frontend-v${VERSION}.tar.gz"

mkdir -p web && tar -xzf frontend.tar.gz -C web
```

Point it at your server by writing `config.json` next to `index.html`. The app
fetches this at startup and merges it over its compiled-in defaults, so one
file repoints a prebuilt bundle:

```bash
echo '{"api": "http://localhost:8000"}' > web/config.json
```

Then serve `web/` with any static server:

```bash
npx serve -s web -l 4200
```

Open <http://localhost:4200>.

> **Use a server with SPA fallback.** `-s` is what gives `npx serve` that
> fallback, and it matters: the app is a single-page application, so every
> route must return `index.html`. `python3 -m http.server` has no fallback —
> the app loads at `/`, but a deep link like `/teams/<id>` (or a refresh on
> one) returns 404. Fine for a first look, wrong for real use.

## Ports at a glance

| | Default | Change with |
|---|---|---|
| Server | 8000 | `AKGENTIC_PORT` |
| Web UI | 4200 | your static server's flag |

If you change the server port, update `config.json` to match — that is the URL
the browser calls.

## Next

- [Run the CLI](run-cli.md) — drive this server from the terminal
- [Run with Docker Compose](run-docker.md) — the same two pieces, containerised
