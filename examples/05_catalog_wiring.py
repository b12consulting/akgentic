"""Example 05 — Full Catalog Wiring, Delete Protection & Env Vars.

Purpose
-------
Demonstrate the catalog's referential integrity mechanisms and runtime secret
handling.  This example operates at the **service layer** (not the repository
layer used in example 04), showing the production entry point for creating,
validating, updating, and deleting catalog entries with full cross-catalog
protection.

What you'll learn
-----------------
* **Bidirectional catalog wiring** — construction-time upstream refs for
  create/update validation, post-construction downstream back-refs for delete
  protection.
* **Delete protection across every boundary** — tool → agent, template → agent,
  agent → team (members), agent → agent (routes_to).
* **Correct deletion order** — reverse dependency graph: teams first, then
  agents, then tools and templates.
* **Update re-validation** — ``agent_catalog.update()`` re-runs full
  cross-catalog validation.
* **``resolve_env_vars()``** — replaces ``${VAR}`` patterns with environment
  variable values at runtime, not at load time.
* **``CatalogValidationError`` vs ``EntryNotFoundError``** — two distinct error
  types for business-rule violations vs simple lookup failures.

Explanation
-----------
**Forward references** (upstream wiring) enable create-time validation:
``AgentCatalog`` receives ``TemplateCatalog`` and ``ToolCatalog`` at
construction so it can verify that ``tool_ids`` and ``@template`` refs point
to existing entries.  ``TeamCatalog`` receives ``AgentCatalog`` for member
validation.

**Back-references** (downstream wiring) enable delete-time protection:
``template_catalog.agent_catalog = agent_catalog`` lets the template catalog
check whether any agent still uses a template before allowing deletion.
Without these back-refs, deletes proceed unchecked — by design for simpler
use cases like tests.

**Environment variables** are stored as ``${VAR}`` literals in YAML.
``resolve_env_vars()`` resolves them at instantiation time (when a tool is
actually used at runtime), keeping catalog entries portable and secret-free.
"""

from __future__ import annotations

import copy
import os
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
    EntryNotFoundError,
    TeamCatalog,
    TeamMemberSpec,
    TeamEntry,
    TemplateCatalog,
    TemplateEntry,
    ToolCatalog,
    ToolEntry,
    YamlAgentCatalogRepository,
    YamlTeamCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
    resolve_env_vars,
)


def main() -> None:
    """Run example demonstrating full catalog wiring and delete protection."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
        tempfile.TemporaryDirectory() as team_dir,
    ):
        # =============================================================
        # Section 1: Two-phase bidirectional catalog wiring
        # =============================================================
        print("=== Two-Phase Bidirectional Catalog Wiring ===\n")

        # Phase 1 — construction-time upstream refs (for create/update validation)
        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
        team_repo = YamlTeamCatalogRepository(Path(team_dir))

        template_catalog = TemplateCatalog(template_repo)
        tool_catalog = ToolCatalog(tool_repo)
        agent_catalog = AgentCatalog(agent_repo, template_catalog, tool_catalog)
        team_catalog = TeamCatalog(team_repo, agent_catalog)

        print("  Phase 1 (upstream): AgentCatalog ← TemplateCatalog, ToolCatalog")
        print("  Phase 1 (upstream): TeamCatalog ← AgentCatalog")

        # Phase 2 — post-construction downstream back-refs (for delete protection)
        template_catalog.agent_catalog = agent_catalog
        tool_catalog.agent_catalog = agent_catalog
        agent_catalog.team_catalog = team_catalog

        print("  Phase 2 (downstream): TemplateCatalog → AgentCatalog")
        print("  Phase 2 (downstream): ToolCatalog → AgentCatalog")
        print("  Phase 2 (downstream): AgentCatalog → TeamCatalog")
        print()

        # =============================================================
        # Section 2: Populate catalogs bottom-up
        # =============================================================
        print("=== Populating Catalogs Bottom-Up ===\n")

        # Templates
        research_prompt = TemplateEntry(
            id="research-prompt",
            template="You are a {role}. {instructions}",
        )
        template_catalog.create(research_prompt)
        print(f"  Created template: {research_prompt.id!r}")

        # Tools
        web_search_tool = ToolEntry(
            id="web-search",
            tool_class="akgentic.tool.search.SearchTool",
            tool={
                "name": "Web Search",
                "description": "Search the web for current information",
                "web_search": {"max_results": 5},
                "web_crawl": True,
                "web_fetch": True,
            },
        )
        tool_catalog.create(web_search_tool)
        print(f"  Created tool: {web_search_tool.id!r}")

        # Agents — create reviewer first (researcher routes_to reviewer)
        reviewer_entry = AgentEntry(
            id="reviewer",
            card=AgentCard(
                role="Reviewer",
                description="Reviews research findings",
                skills=["review", "editing"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Reviewer",
                    role="Reviewer",
                    prompt=PromptTemplate(
                        template="You are a {role}. {instructions}",
                        params={
                            "role": "Reviewer",
                            "instructions": "Review findings.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai",
                        model="gpt-4.1",
                        temperature=0.3,
                    ),
                ),
            ),
        )
        agent_catalog.create(reviewer_entry)
        print(f"  Created agent: {reviewer_entry.id!r} (name={reviewer_entry.card.config.name!r})")

        researcher_entry = AgentEntry(
            id="researcher",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Researcher",
                description="Researches topics using web search",
                skills=["research", "analysis"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Researcher",
                    role="Researcher",
                    prompt=PromptTemplate(
                        template="@research-prompt",
                        params={
                            "role": "Researcher",
                            "instructions": "Research topics.",
                        },
                    ),
                    model_cfg=ModelConfig(
                        provider="openai",
                        model="gpt-4.1",
                        temperature=0.3,
                    ),
                ),
                routes_to=["@Reviewer"],
            ),
        )
        agent_catalog.create(researcher_entry)
        print(
            f"  Created agent: {researcher_entry.id!r} "
            f"(name={researcher_entry.card.config.name!r}, "
            f"routes_to={researcher_entry.card.routes_to})"
        )

        human_proxy_entry = AgentEntry(
            id="human-proxy",
            card=AgentCard(
                role="Human",
                description="User-facing proxy that sends the first message",
                skills=[],
                agent_class="akgentic.agent.HumanProxy",
                config=BaseConfig(name="@Human"),
                routes_to=["@Researcher"],
            ),
        )
        agent_catalog.create(human_proxy_entry)
        print(f"  Created agent: {human_proxy_entry.id!r} (HumanProxy, routes_to=['@Researcher'])")

        # Team with nested member tree — human-proxy is entry_point
        research_team = TeamEntry(
            id="research-team",
            name="Research Team",
            description="A team for research tasks",
            entry_point="human-proxy",
            message_types=["akgentic.agent.AgentMessage"],
            members=[
                TeamMemberSpec(
                    agent_id="human-proxy",
                    headcount=1,
                    members=[
                        TeamMemberSpec(
                            agent_id="researcher",
                            headcount=1,
                            members=[TeamMemberSpec(agent_id="reviewer", headcount=1)],
                        ),
                    ],
                ),
            ],
        )
        team_catalog.create(research_team)
        print(f"  Created team: {research_team.id!r} (entry_point={research_team.entry_point!r})")
        print()

        # =============================================================
        # Section 3: Delete protection — tool referenced by agent
        # =============================================================
        print("=== Delete Protection: Tool Referenced by Agent ===\n")

        try:
            tool_catalog.delete("web-search")
            raise AssertionError("Expected CatalogValidationError")
        except CatalogValidationError as e:
            print("  Blocked! Cannot delete tool 'web-search':")
            for error in e.errors:
                print(f"    {error}")
        print()

        # =============================================================
        # Section 4: Delete protection — template referenced by agent
        # =============================================================
        print("=== Delete Protection: Template Referenced by Agent ===\n")

        try:
            template_catalog.delete("research-prompt")
            raise AssertionError("Expected CatalogValidationError")
        except CatalogValidationError as e:
            print("  Blocked! Cannot delete template 'research-prompt':")
            for error in e.errors:
                print(f"    {error}")
        print()

        # =============================================================
        # Section 5: Delete protection — agent referenced by team
        # =============================================================
        print("=== Delete Protection: Agent Referenced by Team (Members) ===\n")

        try:
            agent_catalog.delete("researcher")
            raise AssertionError("Expected CatalogValidationError")
        except CatalogValidationError as e:
            print("  Blocked! Cannot delete agent 'researcher':")
            for error in e.errors:
                print(f"    {error}")
        print()

        # =============================================================
        # Section 6: Delete protection — agent referenced by routes_to
        # (also triggers team-member protection since reviewer is in
        #  the team's member tree — multiple boundaries fire at once)
        # =============================================================
        print("=== Delete Protection: Agent Referenced by routes_to ===\n")

        try:
            agent_catalog.delete("reviewer")
            raise AssertionError("Expected CatalogValidationError")
        except CatalogValidationError as e:
            print("  Blocked! Cannot delete agent 'reviewer':")
            print("  (Both routing AND team-member protection fire simultaneously)")
            for error in e.errors:
                print(f"    {error}")
        print()

        # =============================================================
        # Section 7: Clean deletion in reverse dependency order
        # =============================================================
        print("=== Clean Deletion in Reverse Order ===\n")

        # Teams first
        team_catalog.delete("research-team")
        assert team_catalog.list() == []
        print("  Deleted team 'research-team' — team list is now empty")

        # Agents next — reverse dependency order: human-proxy → researcher → reviewer
        agent_catalog.delete("human-proxy")
        agent_catalog.delete("researcher")
        agent_catalog.delete("reviewer")
        assert agent_catalog.list() == []
        print("  Deleted agents 'human-proxy', 'researcher', 'reviewer' — agent list is now empty")

        # Tools and templates last
        tool_catalog.delete("web-search")
        assert tool_catalog.list() == []
        print("  Deleted tool 'web-search' — tool list is now empty")

        template_catalog.delete("research-prompt")
        assert template_catalog.list() == []
        print("  Deleted template 'research-prompt' — template list is now empty")
        print()

        # =============================================================
        # Section 8: Update re-validation
        # =============================================================
        print("=== Update Re-Validation ===\n")

        # Re-create entries for update demo
        template_catalog.create(research_prompt)
        tool_catalog.create(web_search_tool)
        agent_catalog.create(reviewer_entry)
        agent_catalog.create(researcher_entry)
        agent_catalog.create(human_proxy_entry)

        # Failed update: invalid tool reference
        bad_update = copy.deepcopy(researcher_entry)
        bad_update.tool_ids = ["nonexistent-tool"]
        try:
            agent_catalog.update("researcher", bad_update)
            raise AssertionError("Expected CatalogValidationError")
        except CatalogValidationError as e:
            print("  Update blocked (invalid tool ref):")
            for error in e.errors:
                print(f"    {error}")

        # Successful update: valid changes
        good_update = copy.deepcopy(researcher_entry)
        good_update.card = AgentCard(
            role="Senior Researcher",
            description="Senior researcher with web search",
            skills=["research", "analysis"],
            agent_class="akgentic.agent.BaseAgent",
            config=researcher_entry.card.config,
            routes_to=["@Reviewer"],
        )
        agent_catalog.update("researcher", good_update)
        updated = agent_catalog.get("researcher")
        assert updated is not None, "expected 'researcher' to exist after update"
        assert updated.card.role == "Senior Researcher"
        print(f"  Update succeeded: role changed to {updated.card.role!r}")
        print()

        # =============================================================
        # Section 9: EntryNotFoundError
        # =============================================================
        print("=== EntryNotFoundError ===\n")

        try:
            agent_catalog.delete("nonexistent-agent")
            raise AssertionError("Expected EntryNotFoundError")
        except EntryNotFoundError as e:
            print(f"  Delete non-existent: {e}")

        try:
            agent_catalog.update("nonexistent-agent", researcher_entry)
            raise AssertionError("Expected EntryNotFoundError")
        except EntryNotFoundError as e:
            print(f"  Update non-existent: {e}")
        print()

        # =============================================================
        # Section 10: Environment variable substitution
        # =============================================================
        print("=== Environment Variable Substitution ===\n")

        os.environ["SEARCH_API_KEY"] = "sk-test-secret-key"

        resolved = resolve_env_vars("Bearer ${SEARCH_API_KEY}")
        assert resolved == "Bearer sk-test-secret-key"
        print(f"  resolve_env_vars('Bearer ${{SEARCH_API_KEY}}') → {resolved!r}")

        try:
            resolve_env_vars("${MISSING_VAR}")
            raise AssertionError("Expected OSError")
        except OSError as e:
            print(f"  resolve_env_vars('${{MISSING_VAR}}') → Error: {e}")

        del os.environ["SEARCH_API_KEY"]
        print("  Cleaned up SEARCH_API_KEY env var")
        print()

        print("=== Example 05 complete ===")


if __name__ == "__main__":
    main()
