# Example 08 — Custom Types & FQCN Round-Trip

## Concepts Covered

### Custom ToolCard with All Four Methods

`ToolCard` is the abstract base class for tool configuration.  Subclassing it
lets you define domain-specific tools with callable implementations:

- **`get_tools()`** — the ONLY abstract method; returns callables that LLM
  agents invoke directly.
- **`get_system_prompts()`** — returns callables producing context strings
  injected into the LLM system prompt.  Default returns `[]`.
- **`get_commands()`** — maps `BaseToolParam` subclasses to handlers for
  programmatic invocation (inter-agent orchestration).  Default returns `{}`.
- **`get_toolsets()`** — returns grouped tool collections for runtime
  composition.  Default returns `[]`.

### Custom AgentConfig

`AgentConfig` (from `akgentic.agent.config`) extends `BaseConfig` with
prompt, model configuration, runtime settings, usage limits, and tools.
Subclassing `AgentConfig` adds domain-specific fields while inheriting the
full agent configuration surface.

### Custom Message Types

Pydantic `BaseModel` subclasses serve as typed message contracts between
agents.  When registered in `TeamEntry.message_types` as FQCNs, the catalog
validates they resolve to real Python classes.

### receiveMsg\_ Convention

Agents handle messages by defining `receiveMsg_<TypeName>` methods.  The
`Akgent` base class dispatches incoming messages by walking the message
class MRO and matching handler method names.

### FQCN Round-Trip

The catalog stores class references as fully-qualified class name (FQCN)
strings — e.g., `"__main__.WebSearchToolCard"`.  At validation time,
`import_class()` resolves each FQCN back to the real Python class.  This
enables YAML persistence of abstract types without knowing the concrete
class at parse time.

### ToolFactory Aggregation

`ToolFactory` takes a list of `ToolCard` instances and exposes aggregated
`get_tools()`, `get_system_prompts()`, `get_commands()`, and
`get_toolsets()` methods that merge results from all cards.

### \_extract\_config\_type() MRO Walk

A standalone function in `akgentic.catalog.models.agent` that walks the
agent class MRO to find the `ConfigType` from `Akgent[ConfigType, StateType]`
generic parameters.  Used during `AgentEntry` validation to ensure the
config dict is validated against the correct config subclass.

## Key API Patterns

### ToolCard Method Contracts

```python
class WebSearchToolCard(ToolCard):
    api_key: str
    max_results: int

    def get_tools(self) -> list[Callable]:
        def web_search(query: str) -> list[dict]:
            return [{"title": f"Result for '{query}'"}]
        return [web_search]

    def get_system_prompts(self) -> list[Callable]:
        def context() -> str:
            return f"Search engine available (max {self.max_results} results)"
        return [context]

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def handler(params: SearchCommand) -> list[dict]:
            return [{"title": f"Result for '{params.query}'"}]
        return {SearchCommand: handler}
```

### AgentConfig Extension

```python
class ResearchConfig(AgentConfig):
    research_domain: str = "general"
    max_sources: int = 10
    include_citations: bool = True
```

This inherits `prompt`, `model_cfg`, `runtime_cfg`, `usage_limits`, and
`tools` from `AgentConfig`, while adding three domain-specific fields.

### FQCN Registration and Resolution Flow

```python
# Register with __main__ FQCN
tool_entry = ToolEntry(
    id="web-search",
    tool_class="__main__.WebSearchToolCard",
    tool=WebSearchToolCard(name="Search", description="Web search"),
)
tool_catalog.create(tool_entry)

# Retrieve — catalog resolves FQCN back to the real class
retrieved = tool_catalog.get("web-search")
assert isinstance(retrieved.tool, WebSearchToolCard)  # True!
```

### ToolFactory Aggregation

```python
factory = ToolFactory(tool_cards=[card_a, card_b])
all_tools = factory.get_tools()      # merged from both cards
all_prompts = factory.get_system_prompts()  # merged from both cards
```

## Common Pitfalls

- **`__main__` FQCN prefix.**  Types defined in the script's top-level
  module get the FQCN prefix `__main__` (e.g., `"__main__.WebSearchToolCard"`).
  This works for in-process resolution but will fail if the class is imported
  from a different module.  For production use, define custom types in proper
  packages with stable FQCNs.

- **ToolCard requires `get_tools()` only.**  `get_tools()` is the ONLY
  abstract method.  The other three (`get_system_prompts()`,
  `get_commands()`, `get_toolsets()`) have default implementations returning
  empty collections.  You only need to override them if your tool provides
  those capabilities.

- **`_extract_config_type` MRO walk.**  This function walks `__orig_bases__`
  on the MRO to find `Akgent[ConfigType, StateType]`.  If your agent
  subclass does not parameterize `Akgent` with concrete types (e.g.,
  `class MyAgent(Akgent):` without generics), it raises `ValueError`.
  Always specify both type parameters: `class MyAgent(Akgent[MyConfig, BaseState])`.

- **AgentConfig import hazard.**  `AgentConfig` exists in TWO packages:
  - `akgentic.agent.config.AgentConfig` — the one to use (has prompt,
    model\_cfg, runtime\_cfg, usage\_limits, tools)
  - `akgentic.core.BaseConfig` — the minimal base class (name, role,
    squad\_id only)

  Always import from `akgentic.agent.config`, never subclass `BaseConfig`
  directly for agent configuration.

- **`tools` field is stripped during AgentEntry validation.**  The
  `resolve_config` validator on `AgentEntry` pops the `tools` key from
  config data because tools are referenced via `tool_ids` (catalog
  references), not embedded in the config.  This is by design.

## Related Examples

- [Example 07 — Python-First Workflows](07-python-first.md):
  Build and register entries entirely in Python using Pydantic constructors
- [Example 06 — Compound Queries & Cross-Catalog Search](06-search-and-query.md):
  Query models, match semantics, and cross-catalog search chaining
- [Example 01 — Template & Tool Entry Basics](01-catalog-entries.md):
  First contact with `TemplateEntry` and `ToolEntry` — FQCN resolution basics
