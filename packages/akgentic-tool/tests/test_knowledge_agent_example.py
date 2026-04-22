"""Smoke tests for the knowledge_agent.py example.

Validates that the example runs end-to-end without errors and
produces the expected knowledge graph state.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


def _load_example_module() -> types.ModuleType:
    """Load knowledge_agent.py as a module via importlib.

    Uses a unique module name per call to avoid sys.modules caching
    between tests, ensuring each call gets a fresh module with clean state.

    Returns:
        The loaded knowledge_agent module.
    """
    examples_dir = Path(__file__).parent.parent / "examples"
    example_path = examples_dir / "knowledge_agent.py"
    # Use a unique key each load to prevent cross-test sys.modules caching
    module_key = f"knowledge_agent_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_key, example_path)
    assert spec is not None and spec.loader is not None, "Could not load example module spec"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]  # loader type narrowed by assert above
    return module


class TestKnowledgeAgentExample:
    """AC-1: Knowledge agent example runs end-to-end without errors."""

    def test_main_runs_without_error(self) -> None:
        """AC-1: Example main() runs without raising any exception."""
        # Ensure no accidental OpenAI API calls during test run
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            module = _load_example_module()
            # Should not raise
            module.main()

    def test_example_module_importable(self) -> None:
        """AC-4: Example has callable main() function (importable, not only __main__)."""
        module = _load_example_module()
        assert callable(module.main), "main() must be a callable function"

    def test_knowledge_graph_populated_after_main(self) -> None:
        """AC-1: After running, the knowledge graph contains expected entities."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            module = _load_example_module()

            # Create a fresh observer for introspection; wire tool to register the KG actor
            observer = module._ExampleObserver()
            module.KnowledgeGraphTool(
                get_graph=module.GetGraph(prompt_include_schema=True, prompt_include_roots=True),
                update_graph=True,
                search=True,
            ).observer(observer)

            # Build the graph via the helper function
            module.build_tech_stack_graph(observer._kg_actor)

            # Verify graph is populated
            from akgentic.tool.knowledge_graph.models import GetGraphQuery

            view = observer._kg_actor.get_graph(GetGraphQuery())
            assert len(view.entities) == 6, f"Expected 6 entities, got {len(view.entities)}"
            assert len(view.relations) == 5, f"Expected 5 relations, got {len(view.relations)}"

            entity_names = {e.name for e in view.entities}
            assert "FastAPI" in entity_names
            assert "PostgreSQL" in entity_names
            assert "Docker" in entity_names

    def test_keyword_search_works_without_api_key(self) -> None:
        """AC-3: Keyword search works without OPENAI_API_KEY set."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            module = _load_example_module()
            observer = module._ExampleObserver()
            tool = module.KnowledgeGraphTool(update_graph=True, search=True)
            tool.observer(observer)

            module.build_tech_stack_graph(observer._kg_actor)

            tools = tool.get_tools()
            search_fn = next((t for t in tools if t.__name__ == "search_graph"), None)
            assert search_fn is not None

            from akgentic.tool.knowledge_graph.models import SearchQuery

            result = search_fn(SearchQuery(query="database", mode="keyword"))
            assert "PostgreSQL" in result

    def test_root_entities_are_marked(self) -> None:
        """AC-4: Root entities are properly marked (FastAPI and Docker)."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
            module = _load_example_module()
            observer = module._ExampleObserver()

            module.build_tech_stack_graph(observer._kg_actor)

            from akgentic.tool.knowledge_graph.models import GetGraphQuery

            view = observer._kg_actor.get_graph(GetGraphQuery())
            root_names = {e.name for e in view.entities if e.is_root}
            assert "FastAPI" in root_names
            assert "Docker" in root_names
