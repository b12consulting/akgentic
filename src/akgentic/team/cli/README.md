# ak-team CLI

Command-line interface for managing akgentic-team lifecycle instances.

## Installation

The CLI requires the `[cli]` optional extra:

```bash
uv sync --extra cli
```

This installs [Typer](https://typer.tiangolo.com/) and
[Rich](https://rich.readthedocs.io/) for the terminal interface.

## Global Options

All commands accept these options:

| Option | Default | Description |
|---|---|---|
| `--data-dir` | `./data/` | Root directory for team data files |
| `--format` | `table` | Output format: `table`, `json`, or `yaml` |
| `--backend` | `yaml` | Storage backend: `yaml` or `mongodb` |
| `--mongo-uri` | — | MongoDB connection URI (required for `mongodb` backend) |
| `--mongo-db` | — | MongoDB database name (required for `mongodb` backend) |

`--mongo-uri` and `--mongo-db` can also be set via `MONGO_URI` and `MONGO_DB`
environment variables.

## Commands

### `ak-team list`

List all team instances.

```bash
# List all teams
ak-team list

# Filter by status
ak-team list --status running
ak-team list --status stopped
ak-team list --status deleted

# Output as JSON
ak-team list --format json
```

**Options:**

| Option | Description |
|---|---|
| `--status` | Filter by team status: `running`, `stopped`, or `deleted` |

### `ak-team inspect`

Display detailed information about a team instance.

```bash
ak-team inspect 550e8400-e29b-41d4-a716-446655440000
ak-team inspect 550e8400-e29b-41d4-a716-446655440000 --format json
```

Shows: team ID, name, status, user info, timestamps, event count, and agent
state count.

**Arguments:**

| Argument | Description |
|---|---|
| `TEAM_ID` | Team UUID to inspect |

### `ak-team create`

Create a new team from a TeamCard YAML file and run it interactively.

```bash
ak-team create team-card.yaml
ak-team create team-card.yaml --user-id alice
```

The command creates the team, starts it, and blocks until you press **Ctrl+C**.
On SIGINT, the team is gracefully stopped (actors torn down, state persisted
as STOPPED).

**Arguments:**

| Argument | Description |
|---|---|
| `TEAM_CARD_FILE` | Path to a YAML file containing a serialized TeamCard |

**Options:**

| Option | Default | Description |
|---|---|---|
| `--user-id` | `cli` | User identifier for the team creator |

**TeamCard YAML format:**

```yaml
name: my-team
description: A simple team
entry_point:
  card:
    role: Echo
    description: Echoes messages
    skills: [echo]
    agent_class: mymodule.EchoAgent
    config:
      name: "@Echo"
      role: Echo
members:
  - card:
      role: Echo
      description: Echoes messages
      skills: [echo]
      agent_class: mymodule.EchoAgent
      config:
        name: "@Echo"
        role: Echo
```

### `ak-team resume`

Resume a stopped team and run it interactively.

```bash
ak-team resume 550e8400-e29b-41d4-a716-446655440000
```

Restores the team from persisted events and agent state snapshots using the
3-phase restore protocol. The team resumes with full state (including LLM
conversation context reconstructed from event replay).

Like `create`, blocks until **Ctrl+C** for graceful shutdown.

**Arguments:**

| Argument | Description |
|---|---|
| `TEAM_ID` | Team UUID to resume |

### `ak-team delete`

Permanently delete a stopped team and purge all persisted data.

```bash
ak-team delete 550e8400-e29b-41d4-a716-446655440000
```

This removes all data: Process metadata, events, and agent state snapshots.
The team must be in STOPPED state — running teams must be stopped first.

**Arguments:**

| Argument | Description |
|---|---|
| `TEAM_ID` | Team UUID to delete |

## Backend Configuration

### YAML (default)

Teams are stored as YAML files in a per-team directory layout:

```bash
ak-team --data-dir ./my-data list
```

```
my-data/{team-uuid}/
  team.yaml              # Process metadata
  events.yaml            # Append-only event log
  states/{agent-id}.yaml # Agent state snapshots
```

### MongoDB

Requires the `[mongo]` optional extra (`pymongo`):

```bash
ak-team --backend mongodb \
        --mongo-uri mongodb://localhost:27017 \
        --mongo-db akgentic \
        list
```

Or via environment variables:

```bash
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=akgentic
ak-team --backend mongodb list
```

## Examples

```bash
# Full lifecycle from the CLI
ak-team create team-card.yaml          # creates and runs (Ctrl+C to stop)
ak-team list                           # see the stopped team
ak-team inspect <team-id>              # view details
ak-team resume <team-id>               # resume (Ctrl+C to stop again)
ak-team delete <team-id>               # purge all data
```
