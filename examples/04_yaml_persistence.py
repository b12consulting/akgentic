"""Example 04 — YAML Repository Round-Trip.

Purpose
-------
Prove that the YAML persistence layer works correctly with full type fidelity.
Every catalog entry type (``TemplateEntry``, ``ToolEntry``, ``AgentEntry``,
``TeamEntry``) survives a write-to-disk / read-back cycle without data loss.

What you'll learn
-----------------
* YAML file layout convention — one ``{entry-id}.yaml`` file per entry
* Serialization and deserialization with type fidelity (FQCN resolution)
* Lazy caching and ``reload()`` — cache is not auto-invalidated on external
  changes
* Multi-entry YAML files — a single file can hold a list of entries
* Duplicate ID detection across files — caught at load time, not write time

Explanation
-----------
When ``repo.create(entry)`` is called the repository writes the entry to
``{catalog_dir}/{entry_id}.yaml`` using ``model_dump(mode="json")`` for
correct enum and nested-model serialization.  The read path (``_load_all()``)
scans every ``*.yaml`` file in the directory, validates each dict via
``model_validate()``, and tracks seen IDs to catch cross-file duplicates.

A lazy cache (``_entries``) avoids repeated disk scans: entries are loaded on
first access and remain cached until ``create()``, ``update()``, ``delete()``,
or ``reload()`` invalidates the cache.  External file edits (e.g. another
process modifying YAML on disk) are **not** detected — call ``reload()``
explicitly to pick up external changes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from akgentic.agent.config import AgentConfig
from akgentic.core import AgentCard
from akgentic.llm.config import ModelConfig
from akgentic.llm.prompts import PromptTemplate

from akgentic.catalog import (
    AgentEntry,
    CatalogValidationError,
    TeamMemberSpec,
    TeamEntry,
    TemplateEntry,
    ToolEntry,
    YamlAgentCatalogRepository,
    YamlTeamCatalogRepository,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)


def main() -> None:
    """Run example demonstrating YAML repository round-trip persistence."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
        tempfile.TemporaryDirectory() as agent_dir,
        tempfile.TemporaryDirectory() as team_dir,
    ):
        # --- Instantiate YAML repositories directly (not service-layer catalogs) ---
        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
        team_repo = YamlTeamCatalogRepository(Path(team_dir))

        print("=== Repositories Created (direct, no service wiring) ===\n")

        # ---------------------------------------------------------------
        # Section 1: Create entries via repo.create() for all four types
        # ---------------------------------------------------------------
        print("=== Creating Entries via repo.create() ===\n")

        # TemplateEntry
        system_prompt = TemplateEntry(
            id="system-prompt",
            template="You are a {role}. {instructions}",
        )
        template_repo.create(system_prompt)
        print(f"Created TemplateEntry: id={system_prompt.id!r}")

        # ToolEntry with SearchTool config (FQCN resolution)
        web_search_entry = ToolEntry(
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
        tool_repo.create(web_search_entry)
        print(f"Created ToolEntry: id={web_search_entry.id!r}")

        # AgentEntry with full config chain
        researcher_entry = AgentEntry(
            id="researcher",
            tool_ids=["web-search"],
            card=AgentCard(
                role="Researcher",
                description="Research agent for web queries",
                skills=["research", "web-search"],
                agent_class="akgentic.agent.BaseAgent",
                config=AgentConfig(
                    name="@Researcher",
                    role="Researcher",
                    prompt=PromptTemplate(
                        template="You are a {role} agent. {instructions}",
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
                routes_to=[],
            ),
        )
        agent_repo.create(researcher_entry)
        print(f"Created AgentEntry: id={researcher_entry.id!r}")

        # TeamEntry
        research_team = TeamEntry(
            id="research-team",
            name="Research Team",
            description="A small research team",
            entry_point="researcher",
            message_types=["akgentic.agent.AgentMessage"],
            members=[TeamMemberSpec(agent_id="researcher", headcount=1)],
        )
        team_repo.create(research_team)
        print(f"Created TeamEntry: id={research_team.id!r}")
        print()

        # ---------------------------------------------------------------
        # Section 2: Inspect file layout on disk
        # ---------------------------------------------------------------
        print("=== File Layout on Disk ===\n")

        for label, directory in [
            ("templates", template_dir),
            ("tools", tool_dir),
            ("agents", agent_dir),
            ("teams", team_dir),
        ]:
            files = sorted(Path(directory).glob("*.yaml"))
            print(f"  {label}/")
            for f in files:
                print(f"    {f.name}")

        # Show raw YAML content for tool entry (demonstrates FQCN serialization)
        tool_file = Path(tool_dir) / "web-search.yaml"
        print(f"\nRaw YAML content of {tool_file.name}:")
        print(tool_file.read_text())

        # ---------------------------------------------------------------
        # Section 3: Round-trip verification with fresh repo instances
        # ---------------------------------------------------------------
        print("=== Round-Trip Verification (Fresh Repo Instances) ===\n")

        fresh_template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        fresh_tool_repo = YamlToolCatalogRepository(Path(tool_dir))
        fresh_agent_repo = YamlAgentCatalogRepository(Path(agent_dir))
        fresh_team_repo = YamlTeamCatalogRepository(Path(team_dir))

        # Verify templates
        fresh_templates = fresh_template_repo.list()
        assert len(fresh_templates) == 1
        assert fresh_templates[0].id == system_prompt.id
        assert fresh_templates[0].template == system_prompt.template
        print(f"  TemplateEntry round-trip OK: id={fresh_templates[0].id!r}")

        # Verify tools — including SearchTool config fields (type fidelity)
        fresh_tools = fresh_tool_repo.list()
        assert len(fresh_tools) == 1
        fresh_tool = fresh_tools[0]
        assert fresh_tool.id == web_search_entry.id
        assert fresh_tool.tool_class == web_search_entry.tool_class
        # Type fidelity: SearchTool config fields survive FQCN resolution
        assert fresh_tool.tool.web_search.max_results == 5
        assert fresh_tool.tool.web_crawl is True
        assert fresh_tool.tool.web_fetch is True
        print(
            f"  ToolEntry round-trip OK: id={fresh_tool.id!r}, "
            f"web_search.max_results={fresh_tool.tool.web_search.max_results}"
        )

        # Verify agents
        fresh_agents = fresh_agent_repo.list()
        assert len(fresh_agents) == 1
        fresh_agent = fresh_agents[0]
        assert fresh_agent.id == researcher_entry.id
        assert fresh_agent.card.role == researcher_entry.card.role
        assert fresh_agent.card.config.name == researcher_entry.card.config.name
        assert (
            fresh_agent.card.config.model_cfg.temperature
            == researcher_entry.card.config.model_cfg.temperature
        )
        print(f"  AgentEntry round-trip OK: id={fresh_agent.id!r}, role={fresh_agent.card.role!r}")

        # Verify teams
        fresh_teams = fresh_team_repo.list()
        assert len(fresh_teams) == 1
        fresh_team = fresh_teams[0]
        assert fresh_team.id == research_team.id
        assert fresh_team.entry_point == research_team.entry_point
        assert len(fresh_team.members) == len(research_team.members)
        print(
            f"  TeamEntry round-trip OK: id={fresh_team.id!r}, "
            f"entry_point={fresh_team.entry_point!r}"
        )
        print()

        # ---------------------------------------------------------------
        # Section 4: Cache behavior and reload()
        # ---------------------------------------------------------------
        print("=== Cache Behavior and reload() ===\n")

        # Step 1: Create a new entry
        cache_entry = TemplateEntry(
            id="cached-test",
            template="Original text: {topic}",
        )
        template_repo.create(cache_entry)

        # Step 2: Force cache population via list()
        templates = template_repo.list()
        original_text = [t for t in templates if t.id == "cached-test"][0].template
        print(f"  After create + list(): {original_text!r}")

        # Step 3: External modification (simulating another process)
        cache_file = Path(template_dir) / "cached-test.yaml"
        modified_data = yaml.safe_load(cache_file.read_text())
        modified_data[0]["template"] = "Modified text: {topic}"
        cache_file.write_text(yaml.dump(modified_data, default_flow_style=False))

        # Step 4: Stale read — cache not invalidated by external edit
        stale = template_repo.list()
        stale_text = [t for t in stale if t.id == "cached-test"][0].template
        print(f"  After external edit (stale cache): {stale_text!r}")
        assert stale_text == "Original text: {topic}", "Cache should be stale"

        # Step 5: reload() invalidates cache
        template_repo.reload()

        # Step 6: Fresh read after reload
        fresh = template_repo.list()
        fresh_text = [t for t in fresh if t.id == "cached-test"][0].template
        print(f"  After reload(): {fresh_text!r}")
        assert fresh_text == "Modified text: {topic}", "Should reflect disk change"
        print()

        # ---------------------------------------------------------------
        # Section 5: Multi-entry YAML file
        # ---------------------------------------------------------------
        print("=== Multi-Entry YAML File ===\n")

        entries_data = [
            {"id": "prompt-a", "template": "You are {role}"},
            {"id": "prompt-b", "template": "Research {topic}"},
        ]
        multi_file = Path(template_dir) / "multi.yaml"
        multi_file.write_text(yaml.dump(entries_data, default_flow_style=False))
        template_repo.reload()

        all_templates = template_repo.list()
        multi_ids = sorted(t.id for t in all_templates)
        print(f"  Template IDs after multi-entry file: {multi_ids}")
        assert len(all_templates) == 4, "Expected 4 templates total"
        assert "prompt-a" in multi_ids, "prompt-a should be loaded"
        assert "prompt-b" in multi_ids, "prompt-b should be loaded"
        print(
            f"  Total templates: {len(all_templates)} "
            "(system-prompt + cached-test + 2 from multi-entry file)"
        )
        print()

        # ---------------------------------------------------------------
        # Section 6: Duplicate ID detection across files
        # ---------------------------------------------------------------
        print("=== Duplicate ID Detection Across Files ===\n")

        with tempfile.TemporaryDirectory() as dup_dir:
            dup_path = Path(dup_dir)
            file1 = dup_path / "first.yaml"
            file2 = dup_path / "second.yaml"
            file1.write_text(
                yaml.dump(
                    [{"id": "dupe", "template": "Version A"}],
                    default_flow_style=False,
                )
            )
            file2.write_text(
                yaml.dump(
                    [{"id": "dupe", "template": "Version B"}],
                    default_flow_style=False,
                )
            )

            dup_repo = YamlTemplateCatalogRepository(dup_path)
            try:
                dup_repo.list()  # Triggers _load_all()
                raise AssertionError("Expected CatalogValidationError for duplicate IDs")
            except CatalogValidationError as e:
                print("  Caught CatalogValidationError (duplicate IDs):")
                for error in e.errors:
                    print(f"    {error}")
        print()

        print("=== Example 04 complete ===")


if __name__ == "__main__":
    main()
