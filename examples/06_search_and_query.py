"""Example 06 — Compound Queries & Cross-Catalog Search.

Purpose
-------
Systematically explore the four query models and their field-specific match
semantics.  Each catalog type has its own query model with different matching
strategies per field — understanding these is essential for effective catalog
discovery.

What you'll learn
-----------------
* **Four query model types** — ``TemplateQuery``, ``ToolQuery``, ``AgentQuery``,
  ``TeamQuery``, each tailored to their entry's structure.
* **Field-specific match semantics** — exact match (``id``, ``role``,
  ``tool_class``), substring case-insensitive (``name``, ``description``),
  membership check (``placeholder``), set overlap (``skills``), and recursive
  tree walk (``agent_id`` in ``TeamQuery``).
* **AND composition** — all non-``None`` fields on a query must match for an
  entry to be included in results.
* **Cross-catalog search chaining** — combine results from one catalog's
  search as input to another (e.g. find agents by skill, then find teams
  containing those agents).
* **Empty result handling** — queries with no matches return empty lists, never
  raise exceptions.

Explanation
-----------
Each query model's fields use different matching strategies because the
underlying data types differ:

- **Exact match** is used for identifiers and class paths (``id``, ``role``,
  ``tool_class``) where partial matching would be misleading.
- **Substring match** (case-insensitive) is used for human-readable text
  (``name``, ``description``) where users want flexible discovery.
- **Membership check** is used for ``TemplateQuery.placeholder`` — tests
  whether the given placeholder name appears in the template's placeholder
  set.
- **Set overlap** is used for ``AgentQuery.skills`` — returns entries where
  *any* queried skill appears in the agent's skill list (union semantics,
  not exact set equality).
- **Recursive tree walk** is used for ``TeamQuery.agent_id`` — the search
  descends through the entire nested ``TeamMemberSpec`` hierarchy to find
  the agent at any depth.

When multiple fields are set on a single query, they are AND-composed: every
non-``None`` field must match for the entry to appear in results.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from akgentic.agent.config import AgentConfig
from akgentic.core import AgentCard
from akgentic.llm.config import ModelConfig
from akgentic.llm.prompts import PromptTemplate
from akgentic.tool.mcp.mcp import MCPHTTPConnectionConfig, MCPTool
from akgentic.tool.planning.planning import PlanningTool, UpdatePlanning
from akgentic.tool.search.search import SearchTool, WebSearch

from akgentic.catalog import (
    AgentCatalog,
    AgentEntry,
    AgentQuery,
    TeamCatalog,
    TeamMemberSpec,
    TeamQuery,
    TeamEntry,
    TemplateCatalog,
    TemplateEntry,
    TemplateQuery,
    ToolCatalog,
    ToolEntry,
    ToolQuery,
    YamlAgentCatalogRepository,
    YamlTeamCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)


def main() -> None:
    """Run example demonstrating query models, match semantics, and cross-catalog search."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
        tempfile.TemporaryDirectory() as team_dir,
    ):
        # =============================================================
        # Section 1: Catalog setup (upstream wiring only — no deletes)
        # =============================================================
        print("=== Catalog Setup ===\n")

        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
        team_repo = YamlTeamCatalogRepository(Path(team_dir))

        template_catalog = TemplateCatalog(template_repo)
        tool_catalog = ToolCatalog(tool_repo)
        agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
        team_catalog = TeamCatalog(team_repo, agent_catalog)

        print("  Catalogs wired (upstream only — no delete protection needed)")
        print()

        # =============================================================
        # Section 2: Populate diverse catalog entries
        # =============================================================
        print("=== Populating Catalogs ===\n")

        # --- Templates (3) ---
        templates = [
            TemplateEntry(
                id="research-prompt",
                template="You are a {role} researching {topic}. {instructions}",
            ),
            TemplateEntry(
                id="coordinator-prompt",
                template="You are a {role} for the {team} team. {instructions}",
            ),
            TemplateEntry(
                id="specialist-prompt",
                template="You are a {specialty} specialist. {instructions}",
            ),
        ]
        for t in templates:
            template_catalog.create(t)
            print(f"  Template: {t.id!r}  placeholders={t.placeholders}")

        # --- Tools (3) ---
        tools = [
            ToolEntry(
                id="web-search",
                tool_class="akgentic.tool.search.SearchTool",
                tool=SearchTool(
                    name="Web Search",
                    description="Search the web for current information",
                    web_search=WebSearch(max_results=5),
                    web_crawl=True,
                    web_fetch=True,
                ),
            ),
            ToolEntry(
                id="task-planner",
                tool_class="akgentic.tool.planning.PlanningTool",
                tool=PlanningTool(
                    name="Task Planner",
                    description="Task planning and tracking tool",
                    update_planning=UpdatePlanning(
                        instructions="CRITICAL: Always keep the plan updated.",
                    ),
                ),
            ),
            ToolEntry(
                id="chrome-browser",
                tool_class="akgentic.tool.mcp.MCPTool",
                tool=MCPTool(
                    name="Chrome MCP",
                    description="Chrome browser automation via MCP",
                    connection=MCPHTTPConnectionConfig(
                        url="http://127.0.0.1:12306/mcp",
                        transport="streamable-http",
                        tool_prefix="chrome",
                    ),
                ),
            ),
        ]
        for tool in tools:
            tool_catalog.create(tool)
            print(f"  Tool: {tool.id!r}  name={tool.tool.name!r}")

        # --- Agents (4) — create in dependency order ---
        def make_agent(
            agent_id: str,
            role: str,
            skills: list[str],
            description: str,
            tool_ids: list[str] | None = None,
            routes_to: list[str] | None = None,
        ) -> AgentEntry:
            return AgentEntry(
                id=agent_id,
                tool_ids=tool_ids or [],
                card=AgentCard(
                    role=role,
                    description=description,
                    skills=skills,
                    agent_class="akgentic.agent.BaseAgent",
                    config=AgentConfig(
                        name=f"@{role}",
                        role=role,
                        prompt=PromptTemplate(
                            template=f"You are a {role.lower()}.",
                            params={},
                        ),
                        model_cfg=ModelConfig(
                            provider="openai",
                            model="gpt-4.1",
                            temperature=0.3,
                        ),
                    ),
                    routes_to=routes_to or [],
                ),
            )

        # Order: analyst, expert first (no routes_to deps), then researcher, manager
        analyst = make_agent(
            "analyst",
            "Analyst",
            ["analysis", "data-processing"],
            "Analyzes research findings in depth",
        )
        expert = make_agent(
            "expert",
            "Expert",
            ["problem-solving", "deep-analysis"],
            "Provides deep specialized knowledge",
        )
        researcher = make_agent(
            "researcher",
            "Researcher",
            ["research", "analysis"],
            "Researches topics using web search",
            tool_ids=["web-search"],
            routes_to=["@Analyst"],
        )
        manager = make_agent(
            "manager",
            "Manager",
            ["coordination", "delegation"],
            "Coordinates team work and delegates tasks",
            tool_ids=["web-search", "task-planner"],
            routes_to=["@Researcher", "@Expert"],
        )

        for agent in [analyst, expert, researcher, manager]:
            agent_catalog.create(agent)
            print(f"  Agent: {agent.id!r}  role={agent.card.role!r}  skills={agent.card.skills}")

        # --- Teams (2) ---
        research_team = TeamEntry(
            id="research-team",
            name="Research Team",
            description="Research and analysis team",
            entry_point="researcher",
            message_types=["akgentic.agent.AgentMessage"],
            members=[
                TeamMemberSpec(
                    agent_id="researcher",
                    headcount=1,
                    members=[TeamMemberSpec(agent_id="analyst", headcount=1)],
                ),
            ],
        )
        engineering_team = TeamEntry(
            id="engineering-team",
            name="Engineering Team",
            description="Engineering coordination team",
            entry_point="manager",
            message_types=["akgentic.agent.AgentMessage"],
            members=[
                TeamMemberSpec(
                    agent_id="manager",
                    headcount=1,
                    members=[
                        TeamMemberSpec(
                            agent_id="researcher",
                            headcount=1,
                            members=[
                                TeamMemberSpec(agent_id="analyst", headcount=1),
                            ],
                        ),
                        TeamMemberSpec(agent_id="expert", headcount=1),
                    ],
                ),
            ],
        )

        for team in [research_team, engineering_team]:
            team_catalog.create(team)
            print(f"  Team: {team.id!r}  name={team.name!r}")
        print()

        # =============================================================
        # Section 3: TemplateQuery semantics
        # =============================================================
        print("=== TemplateQuery Semantics ===\n")

        # Exact ID match
        results = template_catalog.search(TemplateQuery(id="research-prompt"))
        assert len(results) == 1
        assert results[0].id == "research-prompt"
        print(f"  TemplateQuery(id='research-prompt') → {[r.id for r in results]}")

        # Placeholder membership check
        results = template_catalog.search(TemplateQuery(placeholder="role"))
        assert len(results) == 2  # research-prompt and coordinator-prompt
        result_ids = sorted([r.id for r in results])
        assert "research-prompt" in result_ids
        assert "coordinator-prompt" in result_ids
        print(f"  TemplateQuery(placeholder='role') → {result_ids}")

        # AND composition: id + placeholder
        results = template_catalog.search(TemplateQuery(id="research-prompt", placeholder="role"))
        assert len(results) == 1
        assert results[0].id == "research-prompt"
        print(
            f"  TemplateQuery(id='research-prompt', placeholder='role') → {[r.id for r in results]}"
        )
        print()

        # =============================================================
        # Section 4: ToolQuery semantics
        # =============================================================
        print("=== ToolQuery Semantics ===\n")

        # Name substring (case-insensitive)
        results = tool_catalog.search(ToolQuery(name="search"))
        assert len(results) == 1
        assert results[0].id == "web-search"
        print(f"  ToolQuery(name='search') → {[r.id for r in results]}")

        # Description substring (case-insensitive)
        results = tool_catalog.search(ToolQuery(description="web"))
        assert len(results) == 1
        assert results[0].id == "web-search"
        print(f"  ToolQuery(description='web') → {[r.id for r in results]}")

        # Exact tool_class match
        results = tool_catalog.search(
            ToolQuery(tool_class="akgentic.tool.search.SearchTool")
        )
        assert len(results) == 1
        assert results[0].id == "web-search"
        print(f"  ToolQuery(tool_class='...SearchTool') → {[r.id for r in results]}")

        # AND composition: name + description
        results = tool_catalog.search(ToolQuery(name="search", description="web"))
        assert len(results) == 1
        assert results[0].id == "web-search"
        print(f"  ToolQuery(name='search', description='web') → {[r.id for r in results]}")
        print()

        # =============================================================
        # Section 5: AgentQuery semantics
        # =============================================================
        print("=== AgentQuery Semantics ===\n")

        # Exact role match
        results = agent_catalog.search(AgentQuery(role="Manager"))
        assert len(results) == 1
        assert results[0].id == "manager"
        print(f"  AgentQuery(role='Manager') → {[r.id for r in results]}")

        # Skills set overlap
        results = agent_catalog.search(AgentQuery(skills=["research", "analysis"]))
        result_ids = sorted([r.id for r in results])
        assert len(results) == 2  # researcher has both, analyst has "analysis"
        assert "researcher" in result_ids
        assert "analyst" in result_ids
        print(f"  AgentQuery(skills=['research', 'analysis']) → {result_ids}")

        # Description substring (case-insensitive)
        results = agent_catalog.search(AgentQuery(description="coordinates"))
        result_ids = [r.id for r in results]
        assert len(results) == 1
        assert results[0].id == "manager"
        print(f"  AgentQuery(description='coordinates') → {result_ids}")

        # AND composition: role + skills
        results = agent_catalog.search(AgentQuery(role="Manager", skills=["coordination"]))
        assert len(results) == 1
        assert results[0].id == "manager"
        print(f"  AgentQuery(role='Manager', skills=['coordination']) → {[r.id for r in results]}")
        print()

        # =============================================================
        # Section 6: TeamQuery semantics
        # =============================================================
        print("=== TeamQuery Semantics ===\n")

        # Name substring (case-insensitive)
        results = team_catalog.search(TeamQuery(name="research"))
        assert len(results) == 1
        assert results[0].id == "research-team"
        print(f"  TeamQuery(name='research') → {[r.id for r in results]}")

        # agent_id recursive tree walk
        results = team_catalog.search(TeamQuery(agent_id="researcher"))
        result_ids = sorted([r.id for r in results])
        assert "research-team" in result_ids
        assert "engineering-team" in result_ids
        print(f"  TeamQuery(agent_id='researcher') → {result_ids}")

        # AND composition: name + agent_id
        results = team_catalog.search(TeamQuery(name="research", agent_id="analyst"))
        assert len(results) == 1
        assert results[0].id == "research-team"
        print(f"  TeamQuery(name='research', agent_id='analyst') → {[r.id for r in results]}")
        print()

        # =============================================================
        # Section 7: Cross-catalog search chaining
        # =============================================================
        print("=== Cross-Catalog Search Chaining ===\n")
        print("  Goal: Find all teams containing agents with 'research' skill\n")

        # Step 1: Find agents with "research" skill
        research_agents = agent_catalog.search(AgentQuery(skills=["research"]))
        assert len(research_agents) == 1
        assert research_agents[0].id == "researcher"
        agent_ids = [a.id for a in research_agents]
        print(f"  Step 1 — AgentQuery(skills=['research']) → {agent_ids}")

        # Step 2: For each research agent, find teams containing them
        for agent in research_agents:
            teams = team_catalog.search(TeamQuery(agent_id=agent.id))
            assert len(teams) == 2
            team_ids = sorted([t.id for t in teams])
            assert "research-team" in team_ids
            assert "engineering-team" in team_ids
            print(f"  Step 2 — TeamQuery(agent_id='{agent.id}') → {team_ids}")
        print()

        # =============================================================
        # Section 8: Empty results — no matches, no errors
        # =============================================================
        print("=== Empty Result Handling ===\n")

        results = agent_catalog.search(AgentQuery(role="Nonexistent"))
        assert results == []
        print(f"  AgentQuery(role='Nonexistent') → {results} (empty list, no error)")

        results = tool_catalog.search(ToolQuery(name="quantum-teleporter"))
        assert results == []
        print(f"  ToolQuery(name='quantum-teleporter') → {results} (empty list, no error)")
        print()

        print("=== Example 06 complete ===")


if __name__ == "__main__":
    main()
