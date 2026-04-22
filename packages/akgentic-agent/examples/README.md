# akgentic-agent Examples

Examples demonstrating the akgentic-agent module capabilities.

## Running the Examples

From the **project root**:

```bash
uv run python packages/akgentic-agent/examples/simple_team.py
```

From the **akgentic-agent directory**:

```bash
cd packages/akgentic-agent
uv run python examples/simple_team.py
```

## Available Examples

### [simple_team.py](simple_team.py) — Interactive Agent Team

**What it demonstrates:**

- Defining a three-role team (Manager, Assistant, Expert) via `AgentCard`
- Registering role profiles with the `Orchestrator` for dynamic hiring
- Pre-hiring agents and exploring dynamic on-demand hiring via `TeamTool`
- Interactive chat with `@mention` routing and `/commands`
- Real-time message and tool-call visibility via `EventSubscriber`
- `HumanProxy` as the human-in-the-loop entry point

**Team structure:**

```
@Human (HumanProxy)
    └──→ @Manager (BaseAgent, routes_to=[Assistant, Expert])
              ├── @Assistant (BaseAgent)
              └── @Expert (BaseAgent)
```

**Key concepts:**

- `AgentCard` — Declarative role definition with skills, prompt, and `routes_to`
- `AgentCard.get_config_copy()` — Extract a fresh `AgentConfig` from a card
- `register_agent_profiles()` — Register role blueprints with the Orchestrator
- `EventSubscriber.on_message()` — Event-driven visibility into agent interactions
- `ActorAddress.send()` — Route human input into the actor system
- `proxy_ask(addr, T).cmd_*()` — Programmatic commands: roster, planning, hire/fire
- `ToolCallEvent` — Observe LLM tool calls as they happen

**Tools included:**

| Tool | Purpose | Requires |
|---|---|---|
| `SearchTool` | Web search, fetch, and crawl via Tavily | `TAVILY_API_KEY` |
| `PlanningTool` | Shared task tracking across the team | — |
| `WorkspaceTool` | File I/O anchored to a named workspace | — |

**Interactive commands:**

| Command | Description |
|---|---|
| `@AgentName message` | Route message to a specific agent |
| `/team` | Show current team roster |
| `/roles` | Show available roles for hiring |
| `/planning` | Show current team planning |
| `/task <id>` | Show a specific planning task |
| `/hire <role>` | Hire a new team member by role |
| `/fire <name>` | Fire a team member by name |
| `/help` | Show command help |
| `/exit` | Quit |

**Requirements:** `OPENAI_API_KEY`, `TAVILY_API_KEY` (for web search)

```bash
uv run python packages/akgentic-agent/examples/simple_team.py
```

**Example session:**

```
Team Roster:
  @Orchestrator (Orchestrator)
  @Human (Human)
  @Manager (Manager)
  @Assistant (Assistant)
  @Expert (Expert)

Type your message (use @AgentName to route to a specific agent, /help for commands, /exit to quit):
----------------------------------------------------------------------------------------------------
> Hello, I need help planning a Python refactoring project
----------------------------------------------------------------------------------------------------
[Manager] -> AgentMessage (@Human):
I'd be happy to help you plan your Python refactoring project...
----------------------------------------------------------------------------------------------------
> @Expert What are the best practices for large-scale Python refactoring?
----------------------------------------------------------------------------------------------------
[Expert] -> AgentMessage (@Human):
For large-scale Python refactoring, the key best practices are...
----------------------------------------------------------------------------------------------------
> /planning
No planning tasks yet.
> /exit
Exiting chat loop.
```

---

## Prerequisites

All examples require:

- Python 3.12+
- `akgentic` workspace packages installed
- A valid LLM API key

```bash
# From workspace root
uv sync --all-packages --all-extras
```

## Environment Variables

```bash
export OPENAI_API_KEY="your-key-here"
export TAVILY_API_KEY="your-key-here"   # for SearchTool
# or
export ANTHROPIC_API_KEY="your-key-here"
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'akgentic.agent'`**

Run from the workspace root so `uv` picks up the workspace configuration:

```bash
uv run python packages/akgentic-agent/examples/simple_team.py
```

**`API key not found`**

```bash
export OPENAI_API_KEY="your-key"
export TAVILY_API_KEY="your-key"
```

**Agents not responding**

Increase the `time.sleep()` delay after actor creation if running on a slow machine.
