"""Example 02 — Agent Entries & Cross-Validation.

Purpose
-------
Demonstrate how the catalog validates agent cross-references at registration
time.  ``AgentEntry`` is the first entity that depends on *other* catalogs:
its ``tool_ids`` reference ``ToolCatalog``, its ``@``-prefixed prompt template
references ``TemplateCatalog``, and its ``routes_to`` list references other
agents by name.

What you'll learn
-----------------
* Wiring ``AgentCatalog`` to ``ToolCatalog`` and ``TemplateCatalog`` at
  construction time
* Creating an ``AgentEntry`` with ``tool_ids``, ``@``-template reference,
  and ``routes_to``
* ``agent_class`` FQCN resolution (``BaseAgent``)
* Cross-catalog validation errors (missing tool, missing template, invalid
  route target)
* ``resolve_tools()`` and ``resolve_template()`` — bridging catalog-form to
  runtime objects

Explanation
-----------
``AgentCatalog`` requires ``TemplateCatalog`` and ``ToolCatalog`` as
constructor dependencies.  This *construction-time wiring* enables
cross-validation on every ``create()`` call: the service verifies that every
``tool_ids`` entry exists in the tool catalog, every ``@``-prefixed
``prompt.template`` exists in the template catalog, and every ``routes_to``
value matches a registered agent's ``config.name``.

The ``@``-reference convention applies **only** to ``prompt.template`` fields.
Agent names like ``"@Researcher"`` in ``config.name`` and ``routes_to`` are
*routing names* — a completely separate convention that happens to share the
``@`` prefix.

After registration, ``AgentEntry.resolve_tools()`` and
``AgentEntry.resolve_template()`` transform catalog-form IDs into concrete
runtime objects (``ToolCard`` list and ``PromptTemplate`` respectively),
bridging the gap between declarative configuration and executable agents.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from akgentic.agent.config import AgentConfig
from akgentic.core import AgentCard
from akgentic.llm.config import ModelConfig
from akgentic.llm.prompts import PromptTemplate

from akgentic.catalog import (
    AgentCatalog,
    AgentEntry,
    CatalogValidationError,
    TemplateCatalog,
    TemplateEntry,
    ToolCatalog,
    ToolEntry,
    YamlAgentCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)


def main() -> None:
    """Run example demonstrating AgentEntry cross-catalog validation."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
    ):
        # --- Create catalogs with cross-catalog wiring ---
        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        agent_repo = YamlAgentCatalogRepository(Path(agent_dir))

        template_catalog = TemplateCatalog(repository=template_repo)
        tool_catalog = ToolCatalog(repository=tool_repo)
        agent_catalog = AgentCatalog(
            repository=agent_repo,
            template_catalog=template_catalog,
            tool_catalog=tool_catalog,
        )

        print("=== AgentCatalog wired to TemplateCatalog & ToolCatalog ===\n")

        # --- Prerequisite: TemplateEntry ---
        research_prompt = TemplateEntry(
            id="research-prompt",
            template="You are a {role} researching {topic}. {instructions}",
        )
        template_catalog.create(research_prompt)

        print(f"Created prerequisite TemplateEntry: id={research_prompt.id!r}")
        print(f"  template     : {research_prompt.template!r}")
        print(f"  placeholders : {research_prompt.placeholders}")
        print()

        # --- Prerequisite: ToolEntry ---
        web_search_entry = ToolEntry(
            id="web-search",
            tool_class="akgentic.tool.search.SearchTool",
            tool={
                "name": "Web Search",
                "description": "Search the web for current information",
                "web_search": {"max_results": 5},
            },
        )
        tool_catalog.create(web_search_entry)

        print(f"Created prerequisite ToolEntry: id={web_search_entry.id!r}")
        print(f"  tool_class: {web_search_entry.tool_class!r}")
        print(f"  tool.name : {web_search_entry.tool.name!r}")
        print()

        # --- Route target agent (simple, no @-template) ---
        reviewer_config = AgentConfig(
            name="@Reviewer",
            role="Reviewer",
            prompt=PromptTemplate(
                template="You review research findings for accuracy.",
            ),
            model_cfg=ModelConfig(
                provider="openai",
                model="gpt-4.1",
                temperature=0.2,
            ),
        )
        reviewer_entry = AgentEntry(
            id="reviewer",
            card=AgentCard(
                role="Reviewer",
                description="Reviews research findings for accuracy",
                skills=["review", "fact-checking"],
                agent_class="akgentic.agent.BaseAgent",
                config=reviewer_config,
                routes_to=[],
            ),
        )
        agent_catalog.create(reviewer_entry)

        print(f"Created route-target AgentEntry: id={reviewer_entry.id!r}")
        print(f"  config.name: {reviewer_entry.card.config.name!r}")
        print()

        # --- Main agent with tool_ids, @-template, and routes_to ---
        print("=== AgentEntry with Cross-Catalog References ===\n")

        researcher_config = AgentConfig(
            name="@Researcher",
            role="Researcher",
            prompt=PromptTemplate(
                template="@research-prompt",
                params={
                    "role": "Research Specialist",
                    "topic": "technology trends",
                    "instructions": "Provide thorough analysis with sources.",
                },
            ),
            model_cfg=ModelConfig(
                provider="openai",
                model="gpt-4.1",
                temperature=0.3,
            ),
        )
        researcher_entry = AgentEntry(
            id="researcher",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Researcher",
                description="Performs web research and analysis",
                skills=["research", "analysis"],
                agent_class="akgentic.agent.BaseAgent",
                config=researcher_config,
                routes_to=["@Reviewer"],
            ),
        )
        agent_catalog.create(researcher_entry)

        print(f"Created AgentEntry: id={researcher_entry.id!r}")
        print(f"  tool_ids  : {researcher_entry.tool_ids}")
        print(f"  routes_to : {researcher_entry.card.routes_to}")
        print(f"  prompt.template: {researcher_entry.card.config.prompt.template!r}")
        print(f"  prompt.params  : {researcher_entry.card.config.prompt.params}")
        print()

        # --- List all registered agents ---
        all_agents = agent_catalog.list()
        print(f"agent_catalog.list() → {len(all_agents)} entries:")
        for agent in all_agents:
            print(f"  - {agent.id} (config.name={agent.card.config.name!r})")
        print()

        # --- Get and inspect ---
        retrieved = agent_catalog.get("researcher")
        assert retrieved is not None, "expected 'researcher' to exist"
        print(f"get('researcher'): id={retrieved.id!r}")
        print(f"  card.role       : {retrieved.card.role!r}")
        print(f"  card.agent_class: {retrieved.card.agent_class!r}")
        print(f"  card.config.name: {retrieved.card.config.name!r}")
        print()

        # --- resolve_tools(): catalog-form → runtime ToolCards ---
        print("=== resolve_tools() ===\n")

        tools = retrieved.resolve_tools(tool_catalog)
        print(f"Resolved {len(tools)} tool(s):")
        for tool_card in tools:
            print(f"  - type: {type(tool_card).__name__}")
            print(f"    name: {tool_card.name!r}")
            print(f"    description: {tool_card.description!r}")
        print()

        # --- resolve_template(): @-reference → PromptTemplate ---
        # Resolves the @-reference to the catalog template string,
        # combined with the agent's own params → a new PromptTemplate.
        print("=== resolve_template() ===\n")

        resolved_prompt = retrieved.resolve_template(template_catalog)
        assert resolved_prompt is not None, "expected resolved prompt"
        print("Resolved PromptTemplate:")
        print(f"  template: {resolved_prompt.template!r}")
        print(f"  params  : {resolved_prompt.params}")
        print()

        # --- Error path: missing tool_id ---
        # Key difference: tool_ids references a tool that doesn't exist in ToolCatalog
        print("=== Error Path: Missing tool_id ===\n")

        bad_tool_config = AgentConfig(
            name="@BadToolAgent",
            role="Test",
            prompt=PromptTemplate(template="Test prompt."),
            model_cfg=ModelConfig(
                provider="openai",
                model="gpt-4.1",
                temperature=0.5,
            ),
        )
        bad_tool_entry = AgentEntry(
            id="bad-tool-agent",
            tool_ids=["nonexistent-tool"],
            card=AgentCard(
                role="Test",
                description="Agent with missing tool reference",
                skills=["test"],
                agent_class="akgentic.agent.BaseAgent",
                config=bad_tool_config,
                routes_to=[],
            ),
        )

        try:
            agent_catalog.create(bad_tool_entry)
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (missing tool_id):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        # --- Error path: missing @template ---
        # Key difference: prompt.template uses @-reference to a nonexistent TemplateEntry
        print("=== Error Path: Missing @template ===\n")

        bad_template_config = AgentConfig(
            name="@BadTemplateAgent",
            role="Test",
            prompt=PromptTemplate(template="@nonexistent-template"),
            model_cfg=ModelConfig(
                provider="openai",
                model="gpt-4.1",
                temperature=0.5,
            ),
        )
        bad_template_entry = AgentEntry(
            id="bad-template-agent",
            card=AgentCard(
                role="Test",
                description="Agent with missing template reference",
                skills=["test"],
                agent_class="akgentic.agent.BaseAgent",
                config=bad_template_config,
                routes_to=[],
            ),
        )

        try:
            agent_catalog.create(bad_template_entry)
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (missing @template):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        # --- Error path: invalid routes_to ---
        # Key difference: routes_to references an agent name not registered in AgentCatalog
        print("=== Error Path: Invalid routes_to ===\n")

        bad_route_config = AgentConfig(
            name="@BadRouteAgent",
            role="Test",
            prompt=PromptTemplate(template="Test prompt."),
            model_cfg=ModelConfig(
                provider="openai",
                model="gpt-4.1",
                temperature=0.5,
            ),
        )
        bad_route_entry = AgentEntry(
            id="bad-route-agent",
            card=AgentCard(
                role="Test",
                description="Agent with invalid route target",
                skills=["test"],
                agent_class="akgentic.agent.BaseAgent",
                config=bad_route_config,
                routes_to=["@NonexistentAgent"],
            ),
        )

        try:
            agent_catalog.create(bad_route_entry)
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (invalid routes_to):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        print("=== Example 02 complete ===")


if __name__ == "__main__":
    main()
