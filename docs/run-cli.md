# Run from the terminal

Two different things run from a terminal, and they are worth keeping apart:

| | Needs a server? | Use it to |
|---|---|---|
| [The scripted team example](#a-team-in-a-single-script) | No | See the actor model and `@mention` routing, in one file |
| [`ak-infra`](#ak-infra--drive-a-running-server) | Yes | Drive teams on a running server |
| [`ak-catalog`](#ak-catalog--validate-and-resolve-teams) | No | Validate and resolve catalog YAML |

## A team in a single script

[`src/agent_team/main.py`](../src/agent_team/main.py) builds a complete
multi-agent team in one Python file — no server, no catalog, no HTTP.

```bash
source .venv/bin/activate
export OPENAI_API_KEY="..."
export TAVILY_API_KEY="..."     # optional

python src/agent_team/main.py
```

**Team:** a Manager that coordinates and can hire on demand, an Assistant, an
Expert, and a `HumanProxy` routing your input to the Manager.

Messages go to the Manager by default; prefix with `@AgentName` to route
directly. `/help` lists the slash commands (`/team`, `/roles`, `/planning`,
`/hire <role>`, `/fire <name>`).

```
Type your message (start the message with @{agent_name} to route to specific agent, 'exit' to quit or '/help' for help):
----------------------------------------------------------------------------------------------------
Ask @Expert what is his role in the team

----------------------------------------------------------------------------------------------------
[@Manager] -> AgentMessage(request) [@Expert]:
Could you please describe your role and main responsibilities within the team?

----------------------------------------------------------------------------------------------------
[@Expert] -> AgentMessage(response) [@Manager]:
As the Expert within the team, my primary role is to provide deep, specialized
knowledge and technical guidance...

----------------------------------------------------------------------------------------------------
[@Manager] -> AgentMessage(response) [@Human]:
@Human, here is @Expert's response regarding his role: ...
```

What it demonstrates: `AgentCard` role definitions with `routes_to`
restrictions, `BaseAgent` and the typed `AgentMessage` protocol,
`register_agent_profiles()`, `EventSubscriber.on_message()` for live message
flow, and dynamic hiring at runtime. See the
[akgentic-agent README](https://github.com/b12consulting/akgentic-agent/blob/master/README.md).

## `ak-infra` — drive a running server

Installed with `akgentic-infra`. Start a server first
([locally](run-local.md) or [in Docker](run-docker.md)); the CLI talks to
`http://localhost:8000` unless told otherwise.

```bash
ak-infra --help
ak-infra team list
ak-infra team create agent-team          # from a catalog namespace
ak-infra chat <team_id>                  # interactive REPL against the team
```

| Command | Does |
|---|---|
| `team list` / `team get <id>` | Inspect teams |
| `team create <catalog-entry>` | Start a team from a catalog namespace |
| `team delete <id>` / `team restore <id>` | Lifecycle |
| `team events <id>` | Replay the event stream |
| `message <id> <text>` | Send one message |
| `reply` | Answer a pending human-input request |
| `chat <id>` | Interactive session over WebSocket |
| `workspace tree <id>` | List the files agents produced |
| `workspace read <id> <path>` | Print one file |
| `workspace upload <id> <local>` | Push a file into the workspace |
| `login` / `logout` | Device-code sign-in, when the server requires auth |
| `catalog` / `channel` | Inspect the server's catalog and channel registry |

Global options: `--server <url>`, `--api-key <key>`, and `--format
table|json|yaml` for scripting.

```bash
ak-infra --format json team list | jq '.[].team_id'
ak-infra --server http://box:8000 team list
```

## `ak-catalog` — validate and resolve teams

A team can be assembled entirely from YAML — prompt templates, tools, agents,
team structure — through **akgentic-catalog**. This repository ships that data
in two forms:

- [`data/catalog/`](../data/catalog/) — file-per-entry namespaces, the layout
  the server reads directly;
- [`data/catalog-import/`](../data/catalog-import/) — one bundle YAML per
  namespace, the import/export form for seeding a fresh deployment.

`ak-catalog` works against the bundled data with no server running:

```bash
# Strict-validate a namespace, resolve the team into a runnable TeamCard
ak-catalog --root data/catalog validate --namespace agent-team
ak-catalog --root data/catalog load-team --namespace agent-team

# Round-trip a namespace as a single bundle document
ak-catalog --root data/catalog export --namespace agent-team
ak-catalog --root data/catalog validate data/catalog-import/catalog.agent-team.yaml
```

See the
[akgentic-catalog README](https://github.com/b12consulting/akgentic-catalog/blob/master/README.md).

`ak-team` is also installed, for team-store inspection — see the
[akgentic-team README](https://github.com/b12consulting/akgentic-team/blob/master/README.md).
