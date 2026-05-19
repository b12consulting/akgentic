"""Example 01 — Template & Tool Entry Basics.

Purpose
-------
First contact with the catalog API.  Learn the two simplest entity types —
``TemplateEntry`` and ``ToolEntry`` — before encountering cross-catalog
dependencies introduced in later examples.

What you'll learn
-----------------
* Creating catalogs backed by YAML file repositories
* Registering, retrieving, and listing catalog entries
* ``TemplateEntry`` placeholder auto-parsing (``{role}``, ``{topic}``)
* ``ToolEntry`` FQCN class resolution (``SearchTool``, ``PlanningTool``)
* Duplicate-ID error handling via ``CatalogValidationError``

Explanation
-----------
The service layer (``TemplateCatalog``, ``ToolCatalog``) is the correct entry
point — **not** the raw repositories.  Services enforce business rules that
repositories do not: duplicate-ID rejection, FQCN resolution, and (in later
examples) cross-catalog reference checks.

``TemplateEntry`` exposes a read-only ``placeholders`` computed field that uses
``string.Formatter().parse()`` to extract ``{placeholder}`` names from the
template string.

``ToolEntry`` carries a ``tool_class`` FQCN string and a ``tool`` dict.  A
``model_validator(mode="before")`` dynamically imports the class at
``tool_class``, then validates the raw dict against that class's Pydantic
schema — exactly the path taken during YAML deserialization.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from akgentic.catalog import (
    CatalogValidationError,
    TemplateCatalog,
    TemplateEntry,
    ToolCatalog,
    ToolEntry,
    YamlTemplateCatalogRepository,
    YamlToolCatalogRepository,
)


def main() -> None:
    """Run example demonstrating TemplateEntry and ToolEntry catalog basics."""
    with (
        tempfile.TemporaryDirectory() as template_dir,
        tempfile.TemporaryDirectory() as tool_dir,
    ):
        # --- Create catalogs backed by YAML repositories ---
        template_repo = YamlTemplateCatalogRepository(Path(template_dir))
        tool_repo = YamlToolCatalogRepository(Path(tool_dir))

        template_catalog = TemplateCatalog(repository=template_repo)
        tool_catalog = ToolCatalog(repository=tool_repo)

        print("=== TemplateCatalog & ToolCatalog created ===\n")

        # --- TemplateEntry with auto-parsed placeholders ---
        research_prompt = TemplateEntry(
            id="research-prompt",
            template="You are a {role} researching {topic}",
        )
        template_catalog.create(research_prompt)

        print(f"Created TemplateEntry: id={research_prompt.id!r}")
        print(f"  template : {research_prompt.template!r}")
        print(f"  placeholders (auto-parsed): {research_prompt.placeholders}")
        print()

        # --- ToolEntry: SearchTool via FQCN ---
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
        print(f"  tool_class: {web_search_entry.tool_class!r}")
        print(f"  tool.name : {web_search_entry.tool.name!r}")
        print()

        # --- ToolEntry: PlanningTool via FQCN ---
        planning_entry = ToolEntry(
            id="planning",
            tool_class="akgentic.tool.planning.PlanningTool",
            tool={
                "name": "Planning",
                "description": "Planning tool to manage team plans and tasks",
                "get_planning": True,
                "get_planning_task": True,
                "update_planning": True,
            },
        )
        tool_catalog.create(planning_entry)

        print(f"Created ToolEntry: id={planning_entry.id!r}")
        print(f"  tool_class: {planning_entry.tool_class!r}")
        print(f"  tool.name : {planning_entry.tool.name!r}")
        print()

        # --- Get and list operations ---
        print("=== Get & List Operations ===\n")

        retrieved_template = template_catalog.get("research-prompt")
        assert retrieved_template is not None, "expected 'research-prompt' to exist"
        print(f"get('research-prompt'): id={retrieved_template.id!r}")

        retrieved_tool = tool_catalog.get("web-search")
        assert retrieved_tool is not None, "expected 'web-search' to exist"
        print(f"get('web-search')    : id={retrieved_tool.id!r}")
        print()

        all_templates = template_catalog.list()
        print(f"template_catalog.list() → {len(all_templates)} entries:")
        for tmpl in all_templates:
            print(f"  - {tmpl.id}")

        all_tools = tool_catalog.list()
        print(f"tool_catalog.list()     → {len(all_tools)} entries:")
        for tool in all_tools:
            print(f"  - {tool.id} ({tool.tool_class})")
        print()

        # --- Duplicate ID error handling ---
        print("=== Duplicate ID Error Handling ===\n")

        duplicate_entry = ToolEntry(
            id="web-search",
            tool_class="akgentic.tool.search.SearchTool",
            tool={
                "name": "Duplicate Search",
                "description": "This should fail",
                "web_search": True,
            },
        )

        try:
            tool_catalog.create(duplicate_entry)
        except CatalogValidationError as e:
            print("Caught CatalogValidationError (as expected):")
            for error in e.errors:
                print(f"  Validation error: {error}")
        print()

        print("=== Example 01 complete ===")


if __name__ == "__main__":
    main()
