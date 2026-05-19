"""Example 03 — Team Composition, Member Trees & Profiles.

Purpose
-------
Demonstrate how ``TeamEntry`` captures hierarchical team structure and how
the catalog validates it.  This is the first example that exercises all four
catalog services (Template, Tool, Agent, **Team**) wired together.

What you'll learn
-----------------
* ``TeamEntry`` with nested ``TeamMemberSpec`` for multi-level hierarchies
* ``entry_point`` as HumanProxy — the user's proxy that sends the first message
* ``headcount`` for multi-instance agents (e.g. two researchers)
* ``members`` vs ``profiles`` — startup instantiation vs runtime hiring pool
* ``message_types`` FQCN validation at registration time
* Recursive ``TeamQuery`` search across nested member trees

Explanation
-----------
A ``TeamEntry`` defines how agents are composed into a working team.  The
``members`` tree starts with a **HumanProxy** as the ``entry_point`` — the
user's proxy that sends the first message to the team.  The HumanProxy
delegates to the manager, who further delegates to researchers, reviewers,
and analysts.

``profiles`` lists agents that the orchestrator can hire on-demand at
runtime — like an Expert who joins only when needed — but are **not** part
of the initial tree.

``message_types`` stores fully qualified class paths (e.g.
``akgentic.agent.AgentMessage``) so the team knows which message
types it handles.  The catalog validates each FQCN at ``create()`` time,
catching typos before runtime.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from akgentic.agent.config import AgentConfig
from akgentic.core import AgentCard, BaseConfig
from akgentic.llm.config import ModelConfig
from akgentic.llm.prompts import PromptTemplate

from akgentic.catalog import (
    AgentCatalog,
    AgentEntry,
    CatalogValidationError,
    TeamCatalog,
    TeamMemberSpec,
    TeamQuery,
    TeamEntry,
    TemplateCatalog,
    TemplateEntry,
    ToolCatalog,
    ToolEntry,
    YamlAgentCatalogRepository,
    YamlTeamCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)


def main() -> None:
    """Run example demonstrating TeamEntry with hierarchical member trees."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
        tempfile.TemporaryDirectory() as team_dir,
    ):
        # --- Wire all four catalogs ---
        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
        team_repo = YamlTeamCatalogRepository(Path(team_dir))

        template_catalog = TemplateCatalog(repository=template_repo)
        tool_catalog = ToolCatalog(repository=tool_repo)
        agent_catalog = AgentCatalog(
            repository=agent_repo,
            template_catalog=template_catalog,
            tool_catalog=tool_catalog,
        )
        team_catalog = TeamCatalog(
            repository=team_repo,
            agent_catalog=agent_catalog,
        )

        print("=== All four catalogs wired ===\n")

        # --- Prerequisite: TemplateEntry ---
        team_prompt = TemplateEntry(
            id="team-prompt",
            template="You are a {role}. {instructions}",
        )
        template_catalog.create(team_prompt)
        print(f"Created TemplateEntry: id={team_prompt.id!r}")

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
        print(f"Created ToolEntry: id={web_search_entry.id!r}")
        print()

        # --- Create 6 agents (leaf-first for routes_to validation) ---
        print("=== Creating 6 Agents (leaf-first order) ===\n")

        analyst_entry = AgentEntry(
            id="analyst",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Analyst",
                description="Analyzes research data and produces insights",
                skills=["analysis", "data-interpretation"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Analyst",
                    role="Analyst",
                    prompt=PromptTemplate(
                        template="@team-prompt",
                        params={
                            "role": "Data Analyst",
                            "instructions": "Analyze findings thoroughly.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.2
                    ),
                ),
                routes_to=[],
            ),
        )
        agent_catalog.create(analyst_entry)
        print(f"  Created: id={analyst_entry.id!r} (leaf)")

        reviewer_entry = AgentEntry(
            id="reviewer",
            card=AgentCard(
                role="Reviewer",
                description="Reviews research quality and accuracy",
                skills=["review", "fact-checking"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Reviewer",
                    role="Reviewer",
                    prompt=PromptTemplate(
                        template="@team-prompt",
                        params={
                            "role": "Quality Reviewer",
                            "instructions": "Check accuracy of findings.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.2
                    ),
                ),
                routes_to=[],
            ),
        )
        agent_catalog.create(reviewer_entry)
        print(f"  Created: id={reviewer_entry.id!r} (leaf)")

        specialist_entry = AgentEntry(
            id="specialist",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Specialist",
                description="Domain specialist available for on-demand hiring",
                skills=["domain-expertise", "deep-research"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Specialist",
                    role="Specialist",
                    prompt=PromptTemplate(
                        template="@team-prompt",
                        params={
                            "role": "Domain Specialist",
                            "instructions": "Provide expert analysis.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.3
                    ),
                ),
                routes_to=[],
            ),
        )
        agent_catalog.create(specialist_entry)
        print(f"  Created: id={specialist_entry.id!r} (profiles pool)")

        researcher_entry = AgentEntry(
            id="researcher",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Researcher",
                description="Performs web research and delegates to analyst",
                skills=["research", "web-search"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Researcher",
                    role="Researcher",
                    prompt=PromptTemplate(
                        template="@team-prompt",
                        params={
                            "role": "Research Specialist",
                            "instructions": "Research topics and delegate analysis.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.3
                    ),
                ),
                routes_to=["@Analyst"],
            ),
        )
        agent_catalog.create(researcher_entry)
        print(f"  Created: id={researcher_entry.id!r} (routes to @Analyst)")

        manager_entry = AgentEntry(
            id="manager",
            card=AgentCard(
                role="Manager",
                description="Coordinates research team work and delegates tasks",
                skills=["coordination", "delegation"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Manager",
                    role="Manager",
                    prompt=PromptTemplate(
                        template="@team-prompt",
                        params={
                            "role": "Team Manager",
                            "instructions": "Coordinate the team.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai", model="gpt-4.1", temperature=0.3
                    ),
                ),
                routes_to=["@Researcher", "@Reviewer"],
            ),
        )
        agent_catalog.create(manager_entry)
        print(f"  Created: id={manager_entry.id!r} (routes to @Researcher, @Reviewer)")

        human_proxy_entry = AgentEntry(
            id="human-proxy",
            card=AgentCard(
                role="Human",
                description="User-facing proxy that sends the first message",
                skills=[],
                agent_class="akgentic.agent.HumanProxy",
                config=BaseConfig(name="@Human"),
                routes_to=["@Manager"],
            ),
        )
        agent_catalog.create(human_proxy_entry)
        print(f"  Created: id={human_proxy_entry.id!r} (routes to @Manager)")
        print()

        # --- Create TeamEntry with nested member tree ---
        print("=== TeamEntry with Two-Level Nested Members ===\n")

        research_team = TeamEntry(
            id="research-team",
            name="Research Team",
            description="Multi-level research team with hierarchical delegation",
            entry_point="human-proxy",
            message_types=["akgentic.agent.AgentMessage"],
            members=[
                TeamMemberSpec(
                    agent_id="human-proxy",
                    headcount=1,
                    members=[
                        TeamMemberSpec(
                            agent_id="manager",
                            headcount=1,
                            members=[
                                TeamMemberSpec(
                                    agent_id="researcher",
                                    headcount=2,
                                    members=[
                                        TeamMemberSpec(agent_id="analyst"),
                                    ],
                                ),
                                TeamMemberSpec(agent_id="reviewer"),
                            ],
                        ),
                    ],
                ),
            ],
            profiles=["specialist"],
        )
        team_catalog.create(research_team)

        print(f"Created TeamEntry: id={research_team.id!r}")
        print(f"  name         : {research_team.name!r}")
        print(f"  entry_point  : {research_team.entry_point!r}")
        print(f"  message_types: {research_team.message_types}")
        print(f"  profiles     : {research_team.profiles}")
        print("  members tree :")
        print("    human-proxy (entry_point)")
        print("      └── manager")
        print("            ├── researcher (headcount=2)")
        print("            │     └── analyst")
        print("            └── reviewer")
        print()

        # --- get() and inspect ---
        print("=== get() and Inspect ===\n")

        retrieved = team_catalog.get("research-team")
        assert retrieved is not None, "expected 'research-team' to exist"
        print(f"team_catalog.get('research-team'): id={retrieved.id!r}")
        print(f"  name         : {retrieved.name!r}")
        print(f"  entry_point  : {retrieved.entry_point!r}")
        print(f"  message_types: {retrieved.message_types}")
        print(f"  profiles     : {retrieved.profiles}")
        print(f"  members count: {len(retrieved.members)} top-level")
        print()

        # --- list() ---
        all_teams = team_catalog.list()
        print(f"team_catalog.list() → {len(all_teams)} team(s)")
        for team in all_teams:
            print(f"  - {team.id} ({team.name!r})")
        print()

        # --- resolve_entry_point() ---
        print("=== resolve_entry_point() ===\n")

        entry_agent = retrieved.resolve_entry_point(agent_catalog)
        print(f"Resolved entry_point '{retrieved.entry_point}' (HumanProxy):")
        print(f"  role        : {entry_agent.card.role!r}")
        print(f"  agent_class : {entry_agent.card.agent_class!r}")
        print(f"  config.name : {entry_agent.card.config.name!r}")
        print()

        # --- resolve_message_types() ---
        print("=== resolve_message_types() ===\n")

        msg_types = retrieved.resolve_message_types()
        print(f"Resolved {len(msg_types)} message type(s):")
        for cls in msg_types:
            print(f"  - {cls.__name__}")
        print()

        # --- TeamQuery: recursive search by agent_id ---
        print("=== TeamQuery: Recursive Search (agent_id) ===\n")

        results = team_catalog.search(TeamQuery(agent_id="researcher"))
        print(f"search(TeamQuery(agent_id='researcher')) → {len(results)} result(s):")
        for team in results:
            print(f"  - {team.id} ({team.name!r})")
        print()

        # --- TeamQuery: substring search by name ---
        print("=== TeamQuery: Substring Search (name) ===\n")

        results = team_catalog.search(TeamQuery(name="research"))
        print(f"search(TeamQuery(name='research')) → {len(results)} result(s):")
        for team in results:
            print(f"  - {team.id} ({team.name!r})")
        print()

        # --- Error path: entry_point not in members tree ---
        print("=== Error Path: entry_point Not in Members Tree ===\n")

        try:
            team_catalog.create(
                TeamEntry(
                    id="bad-entry-point-team",
                    name="Bad Entry Point Team",
                    entry_point="specialist",
                    message_types=["akgentic.agent.AgentMessage"],
                    members=[
                        TeamMemberSpec(agent_id="manager"),
                    ],
                    profiles=["specialist"],
                ),
            )
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (entry_point not in members):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        # --- Error path: member agent_id not in AgentCatalog ---
        print("=== Error Path: Member agent_id Not in Catalog ===\n")

        try:
            team_catalog.create(
                TeamEntry(
                    id="bad-member-team",
                    name="Bad Member Team",
                    entry_point="nonexistent-agent",
                    message_types=["akgentic.agent.AgentMessage"],
                    members=[
                        TeamMemberSpec(agent_id="nonexistent-agent"),
                    ],
                ),
            )
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (member not in catalog):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        # --- Error path: invalid message_type FQCN ---
        print("=== Error Path: Invalid message_type FQCN ===\n")

        try:
            team_catalog.create(
                TeamEntry(
                    id="bad-msg-team",
                    name="Bad Message Type Team",
                    entry_point="manager",
                    message_types=["nonexistent.module.FakeMessage"],
                    members=[
                        TeamMemberSpec(agent_id="manager"),
                    ],
                ),
            )
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (invalid message_type):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        print("=== Example 03 complete ===")


if __name__ == "__main__":
    main()
