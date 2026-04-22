# ak-catalog CLI Usage Guide

The `ak-catalog` command manages catalog entries for templates, tools,
agents, and teams. It supports YAML and MongoDB backends, three output
formats, and bulk import/validation commands.

## Table of Contents

- [Installation](#installation)
- [Global Options](#global-options)
- [Backend Selection](#backend-selection)
- [Output Formats](#output-formats)
- [Template Commands](#template-commands)
- [Tool Commands](#tool-commands)
- [Agent Commands](#agent-commands)
- [Team Commands](#team-commands)
- [Import Command](#import-command)
- [Validate Command](#validate-command)
- [YAML File Format](#yaml-file-format)
- [Error Handling](#error-handling)

## Installation

Install the CLI extra:

```bash
# From workspace root
uv sync --extra cli

# Or with all extras
uv sync --all-extras
```

Verify the installation:

```bash
ak-catalog --help
```

## Global Options

Every command inherits these options:

| Option | Default | Description |
|---|---|---|
| `--catalog-dir PATH` | `catalog/` | Root directory for YAML catalog files |
| `--format FORMAT` | `table` | Output format: `table`, `json`, or `yaml` |
| `--backend BACKEND` | `yaml` | Storage backend: `yaml` or `mongodb` |
| `--mongo-uri URI` | `$MONGO_URI` | MongoDB connection string (required for mongodb backend) |
| `--mongo-db NAME` | `$MONGO_DB` | MongoDB database name (required for mongodb backend) |

Global options go **before** the subcommand:

```bash
ak-catalog --catalog-dir /path/to/catalog --format json template list
```

## Backend Selection

### YAML Backend (Default)

The YAML backend stores one file per entry in type-specific subdirectories.
Subdirectories are created automatically on first use:

```bash
ak-catalog --catalog-dir ./my-catalog template list
```

This creates and reads from:

```
my-catalog/
  templates/     # TemplateEntry YAML files
  tools/         # ToolEntry YAML files
  agents/        # AgentEntry YAML files
  teams/         # TeamEntry YAML files
```

### MongoDB Backend

Switch to MongoDB with `--backend mongodb` and provide connection details
via flags or environment variables:

```bash
# Via flags
ak-catalog --backend mongodb \
  --mongo-uri mongodb://localhost:27017 \
  --mongo-db akgentic \
  agent list

# Via environment variables
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=akgentic
ak-catalog --backend mongodb agent list
```

MongoDB uses four collections: `template_entries`, `tool_entries`,
`agent_entries`, and `team_entries`.

**Validation rules:**

- `--mongo-uri` must start with `mongodb://` or `mongodb+srv://`
- Both `--mongo-uri` and `--mongo-db` are required when using the mongodb
  backend (flags override environment variables)

## Output Formats

Control output with `--format`:

### Table (Default)

Rich-formatted table with columns tailored to each entry type:

```bash
ak-catalog template list
```

```
 id                 template                                              placeholders
 researcher-prompt  You are a {role} researching {topic}.                 role, topic
 system-prompt      You are a helpful assistant named {name}.             name
```

**Table columns by type:**

| Entry Type | Columns |
|---|---|
| Template | `id`, `template` (truncated to 60 chars), `placeholders` |
| Tool | `id`, `tool_class`, `tool.name` |
| Agent | `id`, `card.role`, `card.description` |
| Team | `id`, `name`, `entry_point` |

### JSON

Pretty-printed JSON with full entry details:

```bash
ak-catalog --format json template get researcher-prompt
```

```json
{
  "id": "researcher-prompt",
  "template": "You are a {role} researching {topic}."
}
```

### YAML

YAML-formatted output:

```bash
ak-catalog --format yaml tool get search
```

```yaml
id: search
tool_class: akgentic.tool.search.SearchTool
tool:
  name: Web Search
  description: Search the web
```

## Template Commands

### List All Templates

```bash
ak-catalog template list
```

### Get a Template

```bash
ak-catalog template get researcher-prompt
```

### Create a Template

```bash
ak-catalog template create prompt.yaml
```

Where `prompt.yaml` contains:

```yaml
id: researcher-prompt
template: "You are a {role} researching {topic}."
```

### Update a Template

```bash
ak-catalog template update researcher-prompt updated-prompt.yaml
```

### Delete a Template

```bash
ak-catalog template delete researcher-prompt
```

Delete protection: fails if any agent references this template.

### Search Templates

```bash
# Find templates containing a specific placeholder
ak-catalog template search --placeholder role
```

| Option | Description |
|---|---|
| `--placeholder NAME` | Filter templates containing this placeholder |

## Tool Commands

### List All Tools

```bash
ak-catalog tool list
```

### Get a Tool

```bash
ak-catalog tool get search
```

### Create a Tool

```bash
ak-catalog tool create search-tool.yaml
```

Where `search-tool.yaml` contains:

```yaml
id: search
tool_class: akgentic.tool.search.SearchTool
```

### Update a Tool

```bash
ak-catalog tool update search updated-search.yaml
```

### Delete a Tool

```bash
ak-catalog tool delete search
```

Delete protection: fails if any agent lists this tool in `tool_ids`.

### Search Tools

```bash
# By class name (substring match)
ak-catalog tool search --class SearchTool

# By tool name (substring match)
ak-catalog tool search --name "Web Search"

# Combined filters (AND logic)
ak-catalog tool search --class Search --name Web
```

| Option | Description |
|---|---|
| `--class FQCN` | Filter by tool class name (substring) |
| `--name NAME` | Filter by tool display name (substring) |

## Agent Commands

### List All Agents

```bash
ak-catalog agent list
```

### Get an Agent

```bash
ak-catalog agent get researcher
```

### Create an Agent

```bash
ak-catalog agent create researcher.yaml
```

Cross-validation at creation:

- Every `tool_ids` entry must exist in the tool catalog
- `@template-id` references must resolve to existing templates with
  matching placeholders
- `routes_to` agent names must exist in the agent catalog

### Update an Agent

```bash
ak-catalog agent update researcher updated-researcher.yaml
```

Re-runs the full cross-validation pipeline.

### Delete an Agent

```bash
ak-catalog agent delete researcher
```

Delete protection: fails if referenced by a team member, team profile,
or another agent's `routes_to`.

### Search Agents

```bash
# By role (exact match)
ak-catalog agent search --role Researcher

# By skill (set overlap — matches agents with this skill)
ak-catalog agent search --skill research

# By description (substring match)
ak-catalog agent search --description "finds information"

# Combined filters (AND logic)
ak-catalog agent search --role Coder --skill python
```

| Option | Description |
|---|---|
| `--role ROLE` | Filter by agent role (exact match) |
| `--skill SKILL` | Filter by skill (matches if agent has this skill) |
| `--description DESC` | Filter by description (substring match) |

## Team Commands

### List All Teams

```bash
ak-catalog team list
```

### Get a Team

```bash
ak-catalog team get research-team
```

### Create a Team

```bash
ak-catalog team create research-team.yaml
```

Cross-validation at creation:

- `entry_point` must appear in the members tree
- Every member `agent_id` must exist in the agent catalog (recursive check)
- Every profile `agent_id` must exist in the agent catalog
- Every `message_types` FQCN must be importable

### Update a Team

```bash
ak-catalog team update research-team updated-team.yaml
```

### Delete a Team

```bash
ak-catalog team delete research-team
```

### Search Teams

```bash
# By name (substring match)
ak-catalog team search --name Research

# By description (substring match)
ak-catalog team search --description "AI research"

# By member agent (recursive tree walk)
ak-catalog team search --agent-id researcher

# Combined filters (AND logic)
ak-catalog team search --name Dev --agent-id coder
```

| Option | Description |
|---|---|
| `--name NAME` | Filter by team name (substring match) |
| `--description DESC` | Filter by description (substring match) |
| `--agent-id ID` | Filter by member agent ID (recursive tree walk) |

## Import Command

Bulk-import catalog entries from a Python file. The file must define an
`entries` list containing Pydantic model instances:

```bash
ak-catalog import entries.py
```

### Python Entry File Format

```python
from akgentic.catalog import TemplateEntry, ToolEntry, AgentEntry, TeamEntry

entries = [
    TemplateEntry(id="greeting", template="Hello {name}!"),
    ToolEntry(id="search", tool_class="akgentic.tool.search.SearchTool"),
    AgentEntry(id="researcher", tool_ids=["search"], card=...),
    TeamEntry(id="team", name="My Team", entry_point="researcher", members=[...]),
]
```

### Resolution Order

Entries are classified by type and imported in dependency order:

1. **Templates** (no dependencies)
2. **Tools** (no dependencies)
3. **Agents** (validated against templates and tools)
4. **Teams** (validated against agents)

### Create-or-Update Logic

For each entry, the import command checks if the ID already exists:

- **New entry** -> `create()` (shown in green)
- **Existing entry** -> `update()` (shown in blue)

### Dry Run

Preview what would happen without persisting:

```bash
ak-catalog import entries.py --dry-run
```

Reports which entries would be created, updated, or fail validation.
Exit code 1 if any validation errors are found.

### Example Output

```
Created template: greeting
Created tool: search
Updated agent: researcher
Created team: research-team

Imported: 3 created, 1 updated, 0 errors.
```

## Validate Command

Check cross-reference consistency across catalogs:

```bash
# Validate everything
ak-catalog validate

# Validate only agents
ak-catalog validate --catalog agents

# Validate only teams
ak-catalog validate --catalog teams
```

### What Gets Validated

| Catalog | Checks |
|---|---|
| Templates | Entry count (no cross-refs to check) |
| Tools | Entry count (no cross-refs to check) |
| Agents | `tool_ids` exist, `@template` refs resolve with matching placeholders, `routes_to` targets exist |
| Teams | `entry_point` in members tree, all member/profile `agent_id`s exist, `message_types` FQCNs importable |

### Example Output

```
Validated: 5 templates, 3 tools, 4 agents, 2 teams. Found 0 errors.
```

With errors:

```
Agent 'broken-agent': Tool 'nonexistent' not found
Team 'bad-team': Member agent_id 'ghost' not found in agent catalog

Validated: 5 templates, 3 tools, 4 agents, 2 teams. Found 2 errors.
```

Exit code 1 if errors are found.

## YAML File Format

### TemplateEntry

```yaml
id: researcher-prompt
template: "You are a {role} researching {topic}."
```

### ToolEntry

```yaml
id: search
tool_class: akgentic.tool.search.SearchTool
```

Or with inline tool configuration:

```yaml
id: search
tool_class: akgentic.tool.search.SearchTool
tool:
  name: Web Search
  description: Search the web for information
  api_key: ${SEARCH_API_KEY}
```

### AgentEntry

```yaml
id: researcher
tool_ids:
  - search
card:
  role: Researcher
  description: Finds relevant information
  skills:
    - research
    - analysis
  agent_class: akgentic.agent.BaseAgent
  config:
    name: "@Researcher"
    role: Researcher
    prompt:
      template: "You are a research specialist in {domain}."
      params:
        domain: AI safety
    model_cfg:
      provider: openai
      model: gpt-4.1
      temperature: 0.2
  routes_to:
    - "@Reviewer"
```

### TeamEntry

```yaml
id: research-team
name: Research Team
description: AI research team
entry_point: researcher
message_types:
  - akgentic.agent.AgentMessage
members:
  - agent_id: researcher
    headcount: 1
    members:
      - agent_id: coder
        headcount: 2
profiles:
  - specialist
```

## Error Handling

The CLI catches all errors and displays formatted messages. No Python
tracebacks are shown.

### Common Errors

**Entry not found:**

```
Not found: Template 'nonexistent' not found
```

**Cross-validation failure (create/update):**

```
Validation error:
  - Tool 'nonexistent-tool' not found
  - Agent '@Ghost' not found in routes_to targets
```

**Delete protection:**

```
Validation error:
  - Cannot delete tool 'search': referenced by agent 'researcher' in tool_ids
```

**File errors:**

```
File not found: /path/to/missing.yaml
Error reading file: <YAML parse error details>
```

**Backend configuration:**

```
Error: MongoDB backend requires --mongo-uri (or MONGO_URI env var)
Error: Invalid MongoDB URI — must start with mongodb:// or mongodb+srv://
```

All errors exit with code 1. Successful commands exit with code 0.
