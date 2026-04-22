"""Example 08 — Custom Types & FQCN Round-Trip.

Purpose
-------
Demonstrate how to build real custom types — a ToolCard with callable tools,
an Akgent with message handlers, a custom AgentConfig subclass, and a custom
message type — and prove the catalog preserves full type fidelity through
fully-qualified class name (FQCN) resolution.

What you'll learn
-----------------
* **Custom ToolCard** — subclass ``ToolCard`` with all four method
  implementations: ``get_tools()``, ``get_system_prompts()``,
  ``get_commands()``, and ``get_toolsets()``.
* **Custom AgentConfig** — extend ``AgentConfig`` with domain-specific
  fields (``research_domain``, ``max_sources``, ``include_citations``).
* **Custom message type** — define a ``ResearchRequest`` Pydantic model
  for typed inter-agent communication.
* **Custom Agent** — subclass ``Akgent[ResearchConfig, BaseState]`` with
  a ``receiveMsg_ResearchRequest`` handler that accesses typed config.
* **FQCN round-trip** — register custom types with ``__main__`` FQCNs,
  retrieve them from the catalog, and verify the catalog resolves FQCNs
  back to the real Python classes.
* **ToolFactory aggregation** — aggregate tools, prompts, and commands
  across multiple ToolCard instances.
* **_extract_config_type()** — MRO walk extracts ``ConfigType`` from
  ``Akgent[ConfigType, StateType]`` generic parameters.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from akgentic.agent.config import AgentConfig
from akgentic.core import AgentCard, Akgent, BaseState
from akgentic.llm.config import ModelConfig
from akgentic.llm.prompts import PromptTemplate
from akgentic.tool import BaseToolParam, ToolCard, ToolFactory
from pydantic import BaseModel, Field

from akgentic.catalog import (
    AgentCatalog,
    AgentEntry,
    TeamCatalog,
    TeamMemberSpec,
    TeamEntry,
    TemplateCatalog,
    ToolCatalog,
    ToolEntry,
    YamlAgentCatalogRepository,
    YamlTeamCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)
from akgentic.catalog.models.agent import _extract_config_type

# =====================================================================
# Custom Types — defined inline (the point of this example)
# =====================================================================


class SearchCommand(BaseToolParam):
    """Command parameter for programmatic search invocation."""

    query: str = Field(description="Search query string")
    max_results: int = Field(default=5, description="Maximum results to return")


class WebSearchToolCard(ToolCard):
    """Custom ToolCard with real callable implementations.

    Demonstrates all four ToolCard method contracts:
    - get_tools() — ONLY abstract method; returns callables for LLM agents
    - get_system_prompts() — returns callables producing context strings
    - get_commands() — maps param classes to handlers
    - get_toolsets() — returns grouped tool collections
    """

    api_key: str = Field(default="simulated-key", description="API key for search service")
    max_results: int = Field(default=10, description="Default max results per search")
    search_engine: str = Field(default="simulated", description="Search engine backend")

    def get_tools(self) -> list[Callable]:
        """Return callable tool functions for LLM agents."""

        def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
            """Search the web for information (simulated)."""
            return [
                {"title": f"Result {i} for '{query}'", "url": f"https://example.com/{i}"}
                for i in range(1, min(max_results, self.max_results) + 1)
            ]

        def fetch_page(url: str) -> str:
            """Fetch a web page (simulated)."""
            return f"<html>Simulated content from {url}</html>"

        return [web_search, fetch_page]

    def get_system_prompts(self) -> list[Callable]:
        """Return system prompt callables injected into LLM context."""
        engine = self.search_engine

        def search_context() -> str:
            return (
                f"You have access to a web search tool powered by {engine}. "
                f"Use it to find current information. Max results per query: {self.max_results}."
            )

        return [search_context]

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        """Return callable commands for programmatic invocation."""

        def execute_search(params: SearchCommand) -> list[dict[str, str]]:
            """Execute a search command programmatically."""
            return [
                {"title": f"Result {i} for '{params.query}'", "url": f"https://example.com/{i}"}
                for i in range(1, min(params.max_results, self.max_results) + 1)
            ]

        return {SearchCommand: execute_search}

    def get_toolsets(self) -> list[Any]:
        """Return runtime toolset objects."""
        return [{"name": "web-search-toolset", "engine": self.search_engine}]


class ResearchConfig(AgentConfig):
    """Custom AgentConfig with domain-specific fields.

    Extends AgentConfig (NOT BaseConfig) to inherit prompt, model_cfg,
    runtime_cfg, usage_limits, and tools — then adds research-specific
    configuration.
    """

    research_domain: str = Field(
        default="general", description="Primary research domain"
    )
    max_sources: int = Field(
        default=10, description="Maximum sources to consult per request"
    )
    include_citations: bool = Field(
        default=True, description="Whether to include citations in output"
    )


class ResearchRequest(BaseModel):
    """Custom message type for typed inter-agent communication."""

    topic: str = Field(description="Research topic")
    depth: str = Field(default="standard", description="Research depth: quick, standard, deep")
    requester: str = Field(default="user", description="Who requested the research")


class ResearchAgent(Akgent[ResearchConfig, BaseState]):
    """Custom agent with typed config and message handler.

    Demonstrates:
    - Generic parameters: ConfigType=ResearchConfig, StateType=BaseState
    - receiveMsg_ convention for handling custom message types
    - Typed config access via self.config
    """

    def receiveMsg_ResearchRequest(  # noqa: N802
        self, message: ResearchRequest, sender: Any = None,
    ) -> dict[str, Any]:
        """Handle a ResearchRequest message using typed config fields."""
        config: ResearchConfig = self.config  # type: ignore[assignment]
        return {
            "topic": message.topic,
            "depth": message.depth,
            "domain": config.research_domain,
            "max_sources": config.max_sources,
            "include_citations": config.include_citations,
            "status": "completed",
        }


# --- Helpers ---


def _wire_catalogs(
    template_dir: str,
    tool_dir: str,
    agent_dir: str,
    team_dir: str,
) -> tuple[TemplateCatalog, ToolCatalog, AgentCatalog, TeamCatalog]:
    """Wire four catalogs with upstream dependencies using temp directories."""
    template_repo = YamlTemplateCatalogRepository(Path(template_dir))
    tool_repo = YamlToolCatalogRepository(Path(tool_dir))
    agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
    team_repo = YamlTeamCatalogRepository(Path(team_dir))

    template_catalog = TemplateCatalog(template_repo)
    tool_catalog = ToolCatalog(tool_repo)
    agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
    team_catalog = TeamCatalog(team_repo, agent_catalog)
    return template_catalog, tool_catalog, agent_catalog, team_catalog


def main() -> None:
    """Run example demonstrating custom types and FQCN round-trip."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
        tempfile.TemporaryDirectory() as team_dir,
    ):
        template_catalog, tool_catalog, agent_catalog, team_catalog = _wire_catalogs(
            template_dir, tool_dir, agent_dir, team_dir,
        )

        # =============================================================
        # Section 1: Define custom ToolCard with all four methods
        # =============================================================
        print("=== Section 1: Custom ToolCard — WebSearchToolCard ===\n")

        search_card = WebSearchToolCard(
            name="Web Search",
            description="Search the web for current information",
            api_key="demo-key-123",
            max_results=10,
            search_engine="simulated",
        )

        # Verify get_tools() returns callables
        tools = search_card.get_tools()
        assert len(tools) == 2, f"Expected 2 tools, got {len(tools)}"
        assert callable(tools[0]), "get_tools()[0] must be callable"
        assert callable(tools[1]), "get_tools()[1] must be callable"
        print(f"  get_tools() → {len(tools)} callables: {[t.__name__ for t in tools]}")

        # Invoke a tool callable — real results returned
        search_results = tools[0]("Python async patterns", max_results=3)
        assert isinstance(search_results, list), "Search results must be a list"
        assert len(search_results) == 3, f"Expected 3 results, got {len(search_results)}"
        assert "title" in search_results[0], "Each result must have a title"
        print(
            f"  web_search('Python async patterns', max_results=3)"
            f" → {len(search_results)} results"
        )
        print(f"    First result: {search_results[0]}")

        # Invoke fetch_page callable — verify it returns correct results
        page_content = tools[1]("https://example.com/test")
        assert isinstance(page_content, str), "fetch_page must return string"
        assert "example.com/test" in page_content, "fetch_page must reference the URL"
        print(f"  fetch_page('https://example.com/test') → {page_content!r}")

        # Verify get_system_prompts() returns callables
        prompts = search_card.get_system_prompts()
        assert len(prompts) == 1, f"Expected 1 prompt callable, got {len(prompts)}"
        prompt_text = prompts[0]()
        assert isinstance(prompt_text, str), "Prompt callable must return string"
        assert "simulated" in prompt_text, "Prompt should reference search engine"
        print(f"  get_system_prompts() → {len(prompts)} callable")
        print(f"    Prompt: {prompt_text!r}")

        # Verify get_commands() maps param class to handler
        commands = search_card.get_commands()
        assert SearchCommand in commands, "SearchCommand must be in commands"
        assert callable(commands[SearchCommand]), "Command handler must be callable"
        cmd_result = commands[SearchCommand](SearchCommand(query="test query", max_results=2))
        assert len(cmd_result) == 2, f"Expected 2 command results, got {len(cmd_result)}"
        print(f"  get_commands() → {len(commands)} command(s)")
        print(f"    SearchCommand('test query', max_results=2) → {len(cmd_result)} results")

        # Verify get_toolsets() returns toolset objects
        toolsets = search_card.get_toolsets()
        assert len(toolsets) == 1, f"Expected 1 toolset, got {len(toolsets)}"
        assert toolsets[0]["engine"] == "simulated", "Toolset engine mismatch"
        print(f"  get_toolsets() → {len(toolsets)} toolset(s)")
        print()

        # =============================================================
        # Section 2: Custom AgentConfig — ResearchConfig
        # =============================================================
        print("=== Section 2: Custom AgentConfig — ResearchConfig ===\n")

        research_config = ResearchConfig(
            name="@Researcher",
            role="Researcher",
            prompt=PromptTemplate(
                template="You are a research agent specializing in {domain}.",
                params={"domain": "AI safety"},
            ),
            model_cfg=ModelConfig(provider="openai", model="gpt-4.1", temperature=0.2),
            research_domain="artificial-intelligence",
            max_sources=15,
            include_citations=True,
        )

        # Verify inherited AgentConfig fields
        assert research_config.name == "@Researcher", "Inherited name field failed"
        assert research_config.role == "Researcher", "Inherited role field failed"
        assert research_config.prompt.template is not None, "Inherited prompt field failed"
        assert research_config.model_cfg.provider == "openai", "Inherited model_cfg field failed"
        print(f"  Inherited fields: name={research_config.name!r}, role={research_config.role!r}")
        print(f"  Inherited fields: model={research_config.model_cfg.model!r}")

        # Verify custom domain-specific fields
        assert research_config.research_domain == "artificial-intelligence", "Custom field failed"
        assert research_config.max_sources == 15, "Custom field failed"
        assert research_config.include_citations is True, "Custom field failed"
        print(f"  Custom fields: domain={research_config.research_domain!r}")
        print(f"  Custom fields: max_sources={research_config.max_sources}")
        print(f"  Custom fields: include_citations={research_config.include_citations}")
        print()

        # =============================================================
        # Section 3: Custom message type — ResearchRequest
        # =============================================================
        print("=== Section 3: Custom Message Type — ResearchRequest ===\n")

        request = ResearchRequest(
            topic="Large language model safety",
            depth="deep",
            requester="project-lead",
        )

        assert request.topic == "Large language model safety", "Topic field failed"
        assert request.depth == "deep", "Depth field failed"
        assert request.requester == "project-lead", "Requester field failed"
        print(f"  ResearchRequest: topic={request.topic!r}")
        print(f"  ResearchRequest: depth={request.depth!r}, requester={request.requester!r}")

        # Verify FQCN for __main__-defined type
        fqcn = f"{ResearchRequest.__module__}.{ResearchRequest.__qualname__}"
        assert fqcn == "__main__.ResearchRequest", f"Unexpected FQCN: {fqcn}"
        print(f"  FQCN: {fqcn!r}")
        print()

        # =============================================================
        # Section 4: Custom Agent — ResearchAgent with receiveMsg_ handler
        # =============================================================
        print("=== Section 4: Custom Agent — ResearchAgent ===\n")

        # Verify _extract_config_type() resolves ResearchConfig from MRO
        extracted_config_type = _extract_config_type(ResearchAgent)
        assert extracted_config_type is ResearchConfig, (
            f"Expected ResearchConfig, got {extracted_config_type}"
        )
        print(f"  _extract_config_type(ResearchAgent) → {extracted_config_type.__name__}")

        # Verify handler method exists
        assert hasattr(ResearchAgent, "receiveMsg_ResearchRequest"), (
            "ResearchAgent must have receiveMsg_ResearchRequest handler"
        )
        print("  receiveMsg_ResearchRequest handler: present")

        # Verify FQCN
        agent_fqcn = f"{ResearchAgent.__module__}.{ResearchAgent.__qualname__}"
        assert agent_fqcn == "__main__.ResearchAgent", f"Unexpected FQCN: {agent_fqcn}"
        print(f"  FQCN: {agent_fqcn!r}")
        print()

        # =============================================================
        # Section 5: Register ToolEntry with FQCN — verify round-trip
        # =============================================================
        print("=== Section 5: ToolEntry Registration & FQCN Resolution ===\n")

        tool_entry = ToolEntry(
            id="web-search",
            tool_class="__main__.WebSearchToolCard",
            tool=WebSearchToolCard(
                name="Web Search",
                description="Search the web for current information",
                api_key="catalog-key",
                max_results=10,
                search_engine="simulated",
            ),
        )
        tool_catalog.create(tool_entry)
        print(f"  ToolEntry created: id={tool_entry.id!r}, tool_class={tool_entry.tool_class!r}")

        # Retrieve and verify FQCN resolution
        retrieved_tool = tool_catalog.get("web-search")
        assert retrieved_tool is not None, "ToolEntry not found after create()"
        assert isinstance(retrieved_tool.tool, WebSearchToolCard), (
            f"Expected WebSearchToolCard, got {type(retrieved_tool.tool).__name__}"
        )
        assert retrieved_tool.tool.api_key == "catalog-key", "Custom field not preserved"
        assert retrieved_tool.tool.max_results == 10, "Custom field not preserved"
        print(f"  Retrieved: type={type(retrieved_tool.tool).__name__}")
        print(f"  Custom fields preserved: api_key={retrieved_tool.tool.api_key!r}")
        print()

        # =============================================================
        # Section 6: Register AgentEntry with FQCN — config type resolution
        # =============================================================
        print("=== Section 6: AgentEntry Registration & Config Resolution ===\n")

        agent_entry = AgentEntry(
            id="researcher",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Researcher",
                description="AI research specialist",
                skills=["research", "analysis", "synthesis"],
                agent_class="__main__.ResearchAgent",
                config=ResearchConfig(
                    name="@Researcher",
                    role="Researcher",
                    prompt=PromptTemplate(
                        template="You are a research agent specializing in {domain}.",
                        params={"domain": "AI safety"},
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.2,
                    ),
                    research_domain="artificial-intelligence",
                    max_sources=15,
                    include_citations=True,
                ),
                routes_to=[],
            ),
        )
        agent_catalog.create(agent_entry)
        print(f"  AgentEntry created: id={agent_entry.id!r}")
        print(f"  agent_class FQCN: {agent_entry.card.agent_class!r}")

        # Retrieve and verify config type resolution
        retrieved_agent = agent_catalog.get("researcher")
        assert retrieved_agent is not None, "AgentEntry not found after create()"
        config = retrieved_agent.card.config
        assert isinstance(config, ResearchConfig), (
            f"Expected ResearchConfig, got {type(config).__name__}"
        )
        assert config.research_domain == "artificial-intelligence", "Custom config field lost"
        assert config.max_sources == 15, "Custom config field lost"
        assert config.include_citations is True, "Custom config field lost"
        print(f"  Config type resolved: {type(config).__name__}")
        print(f"  Custom fields: domain={config.research_domain!r}, "
              f"max_sources={config.max_sources}, citations={config.include_citations}")
        print()

        # =============================================================
        # Section 7: Register TeamEntry with custom message_types FQCN
        # =============================================================
        print("=== Section 7: TeamEntry with Custom message_types ===\n")

        team = TeamEntry(
            id="research-team",
            name="Research Team",
            description="AI research team with custom message types",
            entry_point="researcher",
            message_types=["__main__.ResearchRequest"],
            members=[
                TeamMemberSpec(agent_id="researcher", headcount=1),
            ],
        )
        team_catalog.create(team)
        print(f"  TeamEntry created: id={team.id!r}, name={team.name!r}")
        print(f"  message_types: {team.message_types}")

        # Verify message type FQCN validation
        resolved_types = team.resolve_message_types()
        assert len(resolved_types) == 1, f"Expected 1 resolved type, got {len(resolved_types)}"
        assert resolved_types[0] is ResearchRequest, (
            f"Expected ResearchRequest, got {resolved_types[0]}"
        )
        print(f"  resolve_message_types() → [{resolved_types[0].__name__}]")

        # Verify the resolved type has correct fields
        resolved_cls = resolved_types[0]
        fields = set(resolved_cls.model_fields.keys())
        assert {"topic", "depth", "requester"} == fields, f"Unexpected fields: {fields}"
        print(f"  Resolved type fields: {sorted(fields)}")
        print()

        # =============================================================
        # Section 8: Tool resolution chain — get_tools() → invoke → results
        # =============================================================
        print("=== Section 8: Tool Resolution Chain ===\n")

        # Retrieve tool entry from catalog, invoke its tools
        tool_from_catalog = tool_catalog.get("web-search")
        assert tool_from_catalog is not None, "Tool not found"
        card = tool_from_catalog.tool

        # get_tools() → invoke callable → real results
        catalog_tools = card.get_tools()
        assert len(catalog_tools) == 2, f"Expected 2 tools, got {len(catalog_tools)}"
        results = catalog_tools[0]("catalog round-trip test", max_results=3)
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"
        print(f"  tool.get_tools() → {len(catalog_tools)} callables")
        print(f"  Invoked web_search('catalog round-trip test') → {len(results)} results")
        print(f"    {results[0]}")
        print()

        # =============================================================
        # Section 9: System prompt resolution — config-aware string
        # =============================================================
        print("=== Section 9: System Prompt Resolution ===\n")

        prompts_from_catalog = card.get_system_prompts()
        assert len(prompts_from_catalog) == 1, (
            f"Expected 1 prompt callable, got {len(prompts_from_catalog)}"
        )
        prompt_result = prompts_from_catalog[0]()
        assert isinstance(prompt_result, str), "Prompt must return string"
        assert "simulated" in prompt_result, "Prompt should reference search engine"
        print(f"  tool.get_system_prompts() → {len(prompts_from_catalog)} callable")
        print(f"  Invoked → {prompt_result!r}")
        print()

        # =============================================================
        # Section 10: Command resolution — SearchCommand param → invoke
        # =============================================================
        print("=== Section 10: Command Resolution ===\n")

        catalog_commands = card.get_commands()
        assert SearchCommand in catalog_commands, "SearchCommand must be in commands"
        cmd_handler = catalog_commands[SearchCommand]

        search_param = SearchCommand(query="FQCN resolution patterns", max_results=3)
        cmd_results = cmd_handler(search_param)
        assert len(cmd_results) == 3, f"Expected 3 results, got {len(cmd_results)}"
        print(f"  tool.get_commands() → {len(catalog_commands)} command(s)")
        print("  SearchCommand(query='FQCN resolution patterns', max_results=3)")
        print(f"  Invoked → {len(cmd_results)} results")
        print(f"    {cmd_results[0]}")
        print()

        # =============================================================
        # Section 11: ToolFactory aggregation across multiple ToolCards
        # =============================================================
        print("=== Section 11: ToolFactory Aggregation ===\n")

        # Create a second ToolCard to demonstrate aggregation
        second_card = WebSearchToolCard(
            name="Academic Search",
            description="Search academic papers",
            api_key="academic-key",
            max_results=5,
            search_engine="academic-sim",
        )

        factory = ToolFactory(tool_cards=[card, second_card])

        # Aggregated tools from both cards
        all_tools = factory.get_tools()
        assert len(all_tools) == 4, f"Expected 4 tools (2 per card), got {len(all_tools)}"
        print(f"  ToolFactory(2 cards) → get_tools() = {len(all_tools)} callables")

        # Aggregated prompts
        all_prompts = factory.get_system_prompts()
        assert len(all_prompts) == 2, f"Expected 2 prompts, got {len(all_prompts)}"
        prompt_texts = [p() for p in all_prompts]
        assert "simulated" in prompt_texts[0], "First prompt should reference simulated engine"
        assert "academic-sim" in prompt_texts[1], "Second prompt should reference academic engine"
        print(f"  get_system_prompts() = {len(all_prompts)} callables")

        # Aggregated commands (both cards map SearchCommand, second overwrites first)
        all_commands = factory.get_commands()
        assert SearchCommand in all_commands, "SearchCommand must be in aggregated commands"
        agg_param = SearchCommand(query="aggregated test", max_results=2)
        agg_result = all_commands[SearchCommand](agg_param)
        assert len(agg_result) == 2, f"Expected 2 aggregated results, got {len(agg_result)}"
        print(f"  get_commands() = {len(all_commands)} command(s)")
        print(f"  Invoked aggregated handler → {len(agg_result)} results")

        # Aggregated toolsets
        all_toolsets = factory.get_toolsets()
        assert len(all_toolsets) == 2, f"Expected 2 toolsets, got {len(all_toolsets)}"
        engines = {ts["engine"] for ts in all_toolsets}
        assert engines == {"simulated", "academic-sim"}, f"Unexpected engines: {engines}"
        print(f"  get_toolsets() = {len(all_toolsets)} toolset(s)")
        print()

        # =============================================================
        # Section 12: Config field access — typed as ResearchConfig
        # =============================================================
        print("=== Section 12: Config Field Access ===\n")

        agent_from_catalog = agent_catalog.get("researcher")
        assert agent_from_catalog is not None, "Agent not found"
        typed_config = agent_from_catalog.card.config
        assert isinstance(typed_config, ResearchConfig), (
            f"Config must be ResearchConfig, got {type(typed_config).__name__}"
        )

        # Access domain-specific fields with type safety
        assert typed_config.research_domain == "artificial-intelligence"
        assert typed_config.max_sources == 15
        assert typed_config.include_citations is True
        print(f"  card.config type: {type(typed_config).__name__}")
        print(f"  research_domain: {typed_config.research_domain!r}")
        print(f"  max_sources: {typed_config.max_sources}")
        print(f"  include_citations: {typed_config.include_citations}")

        # Verify inherited fields are also accessible
        assert typed_config.name == "@Researcher"
        assert typed_config.model_cfg.model == "gpt-4.1"
        print(f"  Inherited: name={typed_config.name!r}, model={typed_config.model_cfg.model!r}")
        print()

        # =============================================================
        # Section 13: Message type resolution — FQCN → ResearchRequest
        # =============================================================
        print("=== Section 13: Message Type Resolution ===\n")

        team_from_catalog = team_catalog.get("research-team")
        assert team_from_catalog is not None, "Team not found"
        resolved = team_from_catalog.resolve_message_types()

        assert len(resolved) == 1, f"Expected 1 type, got {len(resolved)}"
        assert resolved[0] is ResearchRequest, f"Expected ResearchRequest, got {resolved[0]}"

        # Verify the resolved class has typed fields
        msg = resolved[0](topic="FQCN validation", depth="deep", requester="catalog")
        assert isinstance(msg, ResearchRequest), "Must be ResearchRequest instance"
        assert msg.topic == "FQCN validation"
        print(f"  resolve_message_types() → [{resolved[0].__name__}]")
        print(f"  Instantiated: topic={msg.topic!r}, depth={msg.depth!r}")
        print()

        print("=== Example 08 complete ===")


if __name__ == "__main__":
    main()
