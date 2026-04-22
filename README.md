![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/jltournay/d0b84268aa19a7460fe186dbf23e500d/raw/coverage.json)

# akgentic-infra

**Status:** Beta — Community tier complete, department/enterprise tiers planned.

## What is akgentic-infra?

Infrastructure backend for the Akgentic platform. It provides protocol abstractions that decouple the server and CLI from any specific deployment model, plus a complete set of community-tier implementations for single-process deployment. Implement the protocols to build department (Docker Compose) or enterprise (Kubernetes/Dapr) tiers.

## Three-Tier Architecture

| Capability        | Community              | Department                    | Enterprise                         |
|-------------------|------------------------|-------------------------------|------------------------------------|
| Auth              | `NoAuth`               | OAuth2 + API key              | OAuth2 + API key + SSO + RBAC      |
| Placement         | `LocalPlacement`       | `LeastTeamsPlacement`         | LabelMatch / Weighted / ZoneAware  |
| Worker lifecycle  | `LocalWorkerHandle`    | `RemoteWorkerHandle` (HTTP)   | `RemoteWorkerHandle` (Dapr)        |
| Team interaction  | `LocalTeamHandle`      | Remote (HTTP proxy)           | Remote (Dapr service invocation)   |
| Runtime cache     | `LocalRuntimeCache`    | Redis-backed                  | Dapr State Store                   |
| Persistence       | `YamlEventStore`       | MongoDB                       | MongoDB + Dapr State               |
| Health monitoring | None (single process)  | `RedisHealthMonitor`          | `DaprHealthMonitor`                |
| Recovery          | None (single process)  | `MarkStoppedRecovery`         | `AutoRestoreRecovery`              |
| Channels          | `YamlChannelRegistry` / `NullChannelRegistry` | Redis-backed       | Dapr pub/sub                       |
| Worker discovery  | N/A (same process)     | HTTP via Redis-registered URLs| Dapr service invocation            |
| Observability     | Logfire (direct)       | Logfire (direct)              | Logfire + OTel Collector           |
| Workspace storage | Local filesystem       | Docker named volume           | NFS / EFS                          |

### Community (single process)

```mermaid
graph TB
    subgraph "Single Process"
        API[FastAPI Server]
        SVC[TeamService]
        NA[NoAuth]
        CAT[Catalog API<br/>YAML backend]
        LP[LocalPlacement]
        LWH[LocalWorkerHandle]
        LRC[LocalRuntimeCache]
        TM[TeamManager]
        AS[ActorSystem]
        YE[YamlEventStore]
        PS[PersistenceSubscriber]
        TS[TelemetrySubscriber]
        ICD[InteractionChannelDispatcher]
        LI[LocalIngestion]
        YCR[YamlChannelRegistry]
    end

    FE[Angular Frontend<br/>browser]
    CLI[ak-infra CLI]

    CLI -->|REST + WS| API
    FE -->|REST + WS| API
    API --> NA
    API --> SVC
    API --> CAT
    API --> LI
    SVC --> LP
    SVC --> LWH
    SVC --> LRC
    LP --> TM
    LWH --> TM
    TM --> AS
    TM --> YE
    PS --> YE
    LI --> SVC
    LI --> YCR

    style FE fill:#4CAF50,color:white
    style API fill:#2196F3,color:white
    style SVC fill:#FF9800,color:white
    style TM fill:#FF9800,color:white
```

### Department (Docker Compose)

```mermaid
graph TB
    subgraph "Caddy"
        PROXY[Reverse Proxy<br/>TLS]
    end

    subgraph "Server Container"
        FE[Angular Frontend]
        SRV[FastAPI Server<br/>stateless]
        SVC_S[TeamService]
        AUTH[OAuth2 + API Key]
        CAT[Catalog API<br/>MongoDB backend]
        PS_SRV[LeastTeamsPlacement]
        RWH[RemoteWorkerHandle<br/>HTTP]
        HM[RedisHealthMonitor]
        RP[MarkStoppedRecovery]
    end

    subgraph "Worker 1"
        W1_API[FastAPI Worker]
        W1_TM[TeamManager]
        W1_AS[ActorSystem]
        W1_HB[Heartbeat Loop]
        W1_PS[PersistenceSubscriber]
        W1_RSS[RedisStreamSubscriber]
        W1_TS[TelemetrySubscriber]
        W1_ICD[InteractionChannelDispatcher]
    end

    subgraph "Worker 2"
        W2_API[FastAPI Worker]
        W2_TM[TeamManager]
    end

    subgraph "Infrastructure"
        MONGO[(MongoDB)]
        REDIS[(Redis)]
    end

    PROXY --> FE
    PROXY -->|/api/*| SRV
    SRV --> AUTH
    SRV --> SVC_S
    SRV --> CAT
    SVC_S --> PS_SRV
    SVC_S --> RWH
    PS_SRV -->|find worker| REDIS
    RWH -->|HTTP proxy| W1_API
    RWH -->|HTTP proxy| W2_API
    HM -->|check heartbeat| REDIS
    HM -->|expired workers| RP
    W1_API --> W1_TM
    W1_TM --> W1_AS
    W1_HB -->|heartbeat TTL| REDIS
    W1_PS --> MONGO
    W1_RSS --> REDIS
    W1_AS --> W1_PS
    W1_AS --> W1_RSS
    W1_AS --> W1_TS
    W1_AS --> W1_ICD
    CAT --> MONGO

    style PROXY fill:#9C27B0,color:white
    style SRV fill:#2196F3,color:white
    style SVC_S fill:#FF9800,color:white
    style W1_API fill:#FF9800,color:white
    style W2_API fill:#FF9800,color:white
    style MONGO fill:#4CAF50,color:white
    style REDIS fill:#F44336,color:white
```

### Enterprise (Kubernetes / Dapr)

```mermaid
graph TB
    subgraph "Ingress"
        ING[Ingress Controller<br/>TLS]
    end

    subgraph "Server Pod"
        SRV[FastAPI Server<br/>stateless]
        SVC_E[TeamService]
        AUTH[OAuth2 + API Key<br/>+ SSO + RBAC]
        CAT[Catalog API<br/>MongoDB backend]
        PS_SRV[PlacementStrategy<br/>LabelMatch / Weighted / ZoneAware]
        RWH_E[RemoteWorkerHandle<br/>Dapr]
        DSR[DaprStateServiceRegistry]
        HM_E[DaprHealthMonitor]
        RP_E[AutoRestoreRecovery]
        SRV_DAPR[Dapr Sidecar]
    end

    subgraph "Worker Pod 1"
        W1_API[FastAPI Worker]
        W1_TM[TeamManager]
        W1_AS[ActorSystem]
        W1_PS[PersistenceSubscriber]
        W1_DSS[DaprStreamSubscriber]
        W1_TS[TelemetrySubscriber]
        W1_ICD[InteractionChannelDispatcher]
        W1_DAPR[Dapr Sidecar]
    end

    subgraph "Worker Pod N"
        WN_API[FastAPI Worker]
        WN_DAPR[Dapr Sidecar]
    end

    subgraph "Infrastructure"
        MONGO[(MongoDB)]
        OTEL[OTel Collector]
    end

    subgraph "Dapr Components"
        PUBSUB[Pub/Sub<br/>Redis / NATS / Kafka]
        STATE[State Store<br/>Redis / PostgreSQL / Cosmos DB]
    end

    ING --> SRV
    SRV --> AUTH
    SRV --> SVC_E
    SRV --> CAT
    SVC_E --> PS_SRV
    SVC_E --> RWH_E
    PS_SRV --> DSR
    DSR --> SRV_DAPR
    RWH_E --> SRV_DAPR
    SRV_DAPR -->|service invocation| W1_DAPR
    SRV_DAPR -->|service invocation| WN_DAPR
    SRV_DAPR --> STATE
    HM_E -->|check health| SRV_DAPR
    HM_E -->|expired workers| RP_E
    W1_DAPR --> W1_API
    WN_DAPR --> WN_API
    W1_API --> W1_TM
    W1_TM --> W1_AS
    W1_AS --> W1_PS
    W1_AS --> W1_DSS
    W1_AS --> W1_TS
    W1_AS --> W1_ICD
    W1_PS --> MONGO
    W1_DSS --> W1_DAPR
    W1_DAPR --> PUBSUB
    W1_TS --> OTEL
    CAT --> MONGO

    style ING fill:#9C27B0,color:white
    style SRV fill:#2196F3,color:white
    style SVC_E fill:#FF9800,color:white
    style W1_API fill:#FF9800,color:white
    style WN_API fill:#FF9800,color:white
    style MONGO fill:#4CAF50,color:white
    style PUBSUB fill:#F44336,color:white
    style STATE fill:#F44336,color:white
    style OTEL fill:#607D8B,color:white
    style SRV_DAPR fill:#E91E63,color:white
    style W1_DAPR fill:#E91E63,color:white
    style WN_DAPR fill:#E91E63,color:white
```

## Source Layout

```
src/akgentic/infra/
  protocols/          Protocol definitions (the contracts)
    auth.py             AuthStrategy
    placement.py        PlacementStrategy
    worker_handle.py    WorkerHandle
    team_handle.py      TeamHandle
    runtime_cache.py    RuntimeCache
    channels.py         ChannelAdapter, Ingestion, Parser, Registry
    health.py           HealthMonitor
    recovery.py         RecoveryPolicy
  adapters/           Protocol implementations
    community/          Single-process adapters (NoAuth, LocalPlacement, etc.)
    shared/             Tier-agnostic adapters (Telegram, telemetry, WebSocket)
  server/             FastAPI application
    routes/             REST, WebSocket, webhook, and frontend adapter routes
    services/           TeamService (tier-agnostic orchestrator)
    settings.py         Pydantic-settings configuration classes
    app.py              Application factory (create_app)
  cli/                Typer-based CLI (ak-infra)
  wiring.py           Dependency injection — wires adapters into services
  worker/             Worker module (planned for department/enterprise tiers)
```

## Quick Start

**1. Start the server** (from the `akgentic-framework` root):

```python
# src/infra_server.py
from pathlib import Path
import uvicorn
from akgentic.infra.server.app import create_app
from akgentic.infra.server.settings import CommunitySettings
from akgentic.infra.wiring import wire_community

settings = CommunitySettings(catalog_path=Path("./src/catalog"))
services = wire_community(settings)
app = create_app(services, settings)

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port, timeout_graceful_shutdown=1)
```

```bash
python src/infra_server.py
```

**2. Connect with the CLI** (in a second terminal):

```bash
# Create a team from the catalog and open the chat TUI
ak-infra chat --create agent-team
```

## Protocols

These are the contracts that department/enterprise tiers must implement. All use structural subtyping (`typing.Protocol`) — no inheritance required.

| Protocol                       | File                | Abstracts                                     |
|--------------------------------|---------------------|-----------------------------------------------|
| `PlacementStrategy`            | `placement.py`      | Worker selection and team creation             |
| `WorkerHandle`                 | `worker_handle.py`  | Team stop / delete / resume / get             |
| `TeamHandle`                   | `team_handle.py`    | Send messages, route human input, subscribe   |
| `RuntimeCache`                 | `runtime_cache.py`  | Map team IDs to live TeamHandle instances      |
| `AuthStrategy`                 | `auth.py`           | Request authentication and user extraction     |
| `InteractionChannelAdapter`    | `channels.py`       | Outbound message delivery to external channels |
| `InteractionChannelIngestion`  | `channels.py`       | Inbound webhook routing to teams               |
| `ChannelParser`                | `channels.py`       | Parse channel-specific webhook payloads        |
| `ChannelRegistry`              | `channels.py`       | Map external channel users to active teams     |
| `EventStream`                  | `event_stream.py`   | Tier-agnostic event streaming with replay and fan-out (ADR-010) |
| `StreamReader`                 | `event_stream.py`   | Cursor-based blocking reader for a team's event stream |
| `HealthMonitor`                | `health.py`         | Worker liveness detection                      |
| `RecoveryPolicy`               | `recovery.py`       | Recovery behavior on worker failure            |

## Server Architecture

The server is built around a tier-agnostic `TeamService` that delegates all infrastructure concerns to protocol implementations. The `create_app()` factory wires everything together.

### REST API

| Method   | Path                            | Description                          |
|----------|---------------------------------|--------------------------------------|
| `POST`   | `/teams/`                       | Create a team from a catalog entry   |
| `GET`    | `/teams/`                       | List all teams                       |
| `GET`    | `/teams/{team_id}`              | Get team metadata                    |
| `DELETE` | `/teams/{team_id}`              | Stop and delete a team               |
| `POST`   | `/teams/{team_id}/message`      | Send a message to a running team     |
| `POST`   | `/teams/{team_id}/human-input`  | Provide human input to an agent      |
| `POST`   | `/teams/{team_id}/stop`         | Stop a team (preserve data)          |
| `POST`   | `/teams/{team_id}/restore`      | Restore a stopped team               |
| `GET`    | `/teams/{team_id}/events`       | Get persisted events                 |
| `GET`    | `/workspace/{team_id}/tree`     | List workspace files                 |
| `GET`    | `/workspace/{team_id}/file`     | Read a workspace file                |
| `POST`   | `/workspace/{team_id}/file`     | Upload a file to workspace           |
| `WS`     | `/ws/{team_id}`                 | Real-time event stream               |
| `POST`   | `/webhook/{channel}`            | Inbound channel webhook              |

Catalog endpoints are mounted under `/catalog/` and provided by `akgentic-catalog`.

### Frontend Adapter Plugin

An optional plugin system for translating API responses to legacy frontend formats. Configured via `AKGENTIC_FRONTEND_ADAPTER` (FQDN of the adapter class). When absent, the server serves the native V2 API only.

### Shared Adapters

Tier-agnostic adapters that work across community, department, and enterprise deployments:

| Adapter                      | Description                                                  |
|------------------------------|--------------------------------------------------------------|
| `InteractionChannelDispatcher` | Per-team outbound message dispatcher — routes `SentMessage` events to registered channel adapters |
| `TelegramChannelAdapter`     | Delivers outbound messages via the Telegram Bot API          |
| `TelegramParser`             | Parses inbound Telegram webhook payloads                     |
| `ChannelParserRegistry`      | Resolves and holds channel parsers/adapters from config      |
| `TelemetrySubscriber`        | Event subscriber that traces messages via Logfire            |

## CLI

The `ak-infra` command provides a terminal interface to the server.

### Team management

```bash
ak-infra team list                      # List all teams
ak-infra team get <team_id>             # Show team detail
ak-infra team create <catalog_entry>    # Create a team
ak-infra team delete <team_id>          # Delete a team
ak-infra team restore <team_id>         # Restore a stopped team
ak-infra team events <team_id>          # Show team events
```

### Messaging

```bash
ak-infra message <team_id> <content>                    # Send a message
ak-infra reply <team_id> <content> --message-id <id>    # Reply to agent request
ak-infra chat [TEAM_ID]                                 # Interactive REPL
ak-infra chat --create <catalog_entry>                   # Create + chat
```

### Workspace

```bash
ak-infra workspace tree <team_id>                  # List files
ak-infra workspace read <team_id> <path>            # Read a file
ak-infra workspace upload <team_id> <local_path>    # Upload a file
```

### REPL Commands

Inside `ak-infra chat`, use `/` for slash commands:

| Command             | Description                    |
|---------------------|--------------------------------|
| `/help`             | Show available commands        |
| `/status`           | Show team status               |
| `/agents`           | List team agents               |
| `/history [N]`      | Show recent messages           |
| `/files`            | Show workspace files           |
| `/read <path>`      | Read a workspace file          |
| `/upload <path>`    | Upload a file                  |
| `/stop`             | Stop the team                  |
| `/restore`          | Restore a stopped team         |
| `/switch <team_id>` | Switch to another team         |

### Global Options

```bash
ak-infra --server http://localhost:8000   # Server URL (default)
ak-infra --api-key <key>                  # API key for auth
ak-infra --format table|json              # Output format
```

## Configuration

All settings are loaded from environment variables prefixed with `AKGENTIC_`.

### Server Settings (all tiers)

| Variable                       | Default       | Description                      |
|--------------------------------|---------------|----------------------------------|
| `AKGENTIC_HOST`                | `0.0.0.0`    | Bind address                     |
| `AKGENTIC_PORT`                | `8000`        | Port number                      |
| `AKGENTIC_LOG_LEVEL`           | `INFO`        | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Invalid values fall back to `INFO`. |
| `AKGENTIC_CORS_ORIGINS`        | `["*"]`       | Allowed CORS origins (JSON list) |
| `AKGENTIC_FRONTEND_ADAPTER`    | `None`        | Frontend adapter plugin FQDN     |

### Community Settings (extends server)

| Variable                       | Default        | Description                        |
|--------------------------------|----------------|------------------------------------|
| `AKGENTIC_WORKSPACES_ROOT`     | `workspaces`   | Root directory for team workspace storage |
| `AKGENTIC_EVENT_STORE_PATH`    | `data/event_store` | Root directory for event store persistence |
| `AKGENTIC_CATALOG_PATH`        | `data/catalog` | Catalog directory for team/agent/tool/template definitions |
| `AKGENTIC_CHANNEL_REGISTRY_PATH` | `None`      | Path to channel registry YAML; disabled when unset |

## Installation

### Within Monorepo Workspace

```bash
# From workspace root
source .venv/bin/activate

# Package is already installed in editable mode via workspace
# No additional installation needed
```

### Standalone Package

```bash
cd packages/akgentic-infra

uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Development

```bash
# Run all tests
pytest packages/akgentic-infra/tests/

# Run integration tests (requires API keys in .env)
pytest packages/akgentic-infra/tests/integration/ -m integration

# Type checking (strict mode)
mypy packages/akgentic-infra/src/

# Lint
ruff check packages/akgentic-infra/src/

# Format
ruff format packages/akgentic-infra/src/
```

Coverage target: **90%** (higher than other packages at 80%).

### Test Markers

| Marker          | Description                                                    |
|-----------------|----------------------------------------------------------------|
| `integration`   | Full server flow tests requiring real LLM and API keys         |
| `llm`           | Tests requiring LLM API keys (auto-skipped when `OPENAI_API_KEY` is absent) |
| `smoke`         | End-to-end smoke tests using `TestModel` (no API key required) |
| `e2e`           | Real end-to-end tests requiring a running server and `OPENAI_API_KEY` |

By default, `integration` tests are excluded (`-m 'not integration'`). Run them explicitly:

```bash
pytest packages/akgentic-infra/tests/ -m integration
```

## Dependencies

### Akgentic packages

`akgentic-core`, `akgentic-team`, `akgentic-catalog`, `akgentic-agent`, `akgentic-llm`, `akgentic-tool`

### Third-party

| Package             | Purpose                                |
|---------------------|----------------------------------------|
| `fastapi`           | HTTP server framework                  |
| `pydantic-settings` | Environment-based configuration        |
| `typer`             | CLI framework                          |
| `rich`              | Terminal rendering                     |
| `httpx`             | HTTP client (CLI to server)            |
| `websockets`        | WebSocket client and server            |
| `pyyaml`            | YAML persistence (event store, catalog)|
| `logfire`           | Observability and logging              |
