# 06 — Agent Cards & Profile Discovery

**Demonstrates**: How agents discover team capabilities through a profile catalog.

This example shows how to use `AgentCard` to create a capability catalog that agents can query to understand what other agent types exist and what they can do.

## Key Concepts

### AgentCard — Profile Definitions

An `AgentCard` describes an agent profile/role available in the team:

```python
card = AgentCard(
    role="ResearchAgent",
    description="Performs web research and data gathering",
    skills=["web_search", "pdf_extraction"],
    agent_class="examples.research.ResearchAgent",
    configuration=BaseConfig(name="researcher", role="ResearchAgent"),
    routes_to=["WriterAgent", "AnalystAgent"],  # Routing constraints
    metadata={"version": "1.0"}
)
```

**Important**: AgentCards are *profiles*, not instances. They describe what kinds of agents exist, not which instances are running.

### Routing Constraints

The `routes_to` field controls which roles an agent can proactively send requests to:

```python
card = AgentCard(
    role="ResearchAgent",
    routes_to=["WriterAgent", "AnalystAgent"],  # Can only send to these
    # ...
)

# Check routing permissions
card.can_route_to("WriterAgent")   # True
card.can_route_to("UnknownAgent")  # False
```

**Key rules**:
- **Empty `routes_to` = no restrictions** (can route to any role)
- **Non-empty `routes_to` = restricted** (can only send to listed roles)
- **Responses are always allowed** regardless of `routes_to`
- Use this to model workflow phases, security boundaries, or team structure

### Orchestrator Catalog Management

The Orchestrator maintains a catalog of agent profiles:

```python
# Register profiles
orch_proxy.register_agent_profile(card)

# Query catalog
catalog = orch_proxy.get_agent_catalog()
profile = orch_proxy.get_agent_profile("ResearchAgent")
agents_with_skill = orch_proxy.get_profiles_by_skill("writing")
all_roles = orch_proxy.get_available_roles()
all_skills = orch_proxy.get_available_skills()
```

### Agent Discovery Methods

Agents can discover profiles through the orchestrator:

```python
class CoordinatorAgent(Akgent):
    def discover_capabilities(self):
        # Browse all profiles
        catalog = self.discover_catalog()
        
        # Find specific profile
        research_profile = self.discover_profile("ResearchAgent")
        
        # Find by skill
        writers = self.find_agents_with_skill("writing")
```

## Architecture

```
┌─────────────────────────────────────────┐
│          Orchestrator                   │
│  ┌───────────────────────────────────┐  │
│  │     Agent Profile Catalog         │  │
│  │  ┌─────────────────────────────┐  │  │
│  │  │ ResearchAgent               │  │  │
│  │  │ - skills: [web_search, ...] │  │  │
│  │  │ - config: BaseConfig(...)   │  │  │
│  │  └─────────────────────────────┘  │  │
│  │  ┌─────────────────────────────┐  │  │
│  │  │ WriterAgent                 │  │  │
│  │  │ - skills: [writing, ...]    │  │  │
│  │  └─────────────────────────────┘  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
           ↑ discover_catalog()
           │
    ┌──────────────┐
    │ Coordinator  │
    │    Agent     │
    └──────────────┘
```

## Profile vs Instance

**AgentCard catalog** = "What profiles exist?" (static capability directory)  
**get_team()** = "What instances are running?" (dynamic runtime roster)

This separation allows:
- Agents to know what *can* be created without hardcoding dependencies
- Dynamic team formation based on required capabilities
- Configuration templates for creating new instances

## Configuration in Cards

Each AgentCard can store default configuration:

```python
card = AgentCard(
    role="ResearchAgent",
    configuration=BaseConfig(name="researcher", role="ResearchAgent"),
    # or as dict:
    # configuration={"name": "researcher", "role": "ResearchAgent"}
)

# Retrieve config later
config = card.get_config()  # Returns BaseConfig instance
```

This enables:
- Consistent agent creation patterns
- Default settings for each profile type
- Easy extension with custom BaseConfig subclasses

## Running the Example

```bash
python examples/06_agent_cards.py
```

Output shows:
1. Profile registration in the catalog
2. Available roles and skills
3. Coordinator discovering capabilities
4. Finding profiles by role and skill

## Use Cases

- **Dynamic team assembly**: Discover what agents exist before creating instances
- **Capability negotiation**: Find agents with specific skills
- **Service directory**: Agents as discoverable services
- **Configuration templates**: Store default settings per agent type
- **Access control**: Define routing constraints via `routes_to` field
- **Workflow phases**: Model sequential workflows (e.g., Research → Writer → Analyst)
- **Security boundaries**: Restrict agent communication patterns
