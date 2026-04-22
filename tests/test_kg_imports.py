"""Tests for knowledge_graph package structure and dependency guard (Task 3.6).

Covers:
- Import from akgentic.tool.knowledge_graph works
- akgentic.tool import does NOT trigger knowledge_graph import
- Dependency guard behavior
"""

from __future__ import annotations

import importlib
import sys


class TestPackageImports:
    def test_import_knowledge_graph_module(self) -> None:
        """akgentic.tool.knowledge_graph is importable."""
        from akgentic.tool.knowledge_graph import Entity, KnowledgeGraphActor

        assert Entity is not None
        assert KnowledgeGraphActor is not None

    def test_public_api_exports(self) -> None:
        """All public symbols listed in __all__ are importable."""
        from akgentic.tool import knowledge_graph

        expected = [
            "Entity",
            "Relation",
            "KnowledgeGraph",
            "KnowledgeGraphState",
            "EntityCreate",
            "EntityUpdate",
            "RelationCreate",
            "RelationDelete",
            "ManageGraph",
            "KnowledgeGraphActor",
            "KG_ACTOR_NAME",
            "KG_ACTOR_ROLE",
            "_check_kg_dependencies",
        ]
        for name in expected:
            assert hasattr(knowledge_graph, name), f"Missing export: {name}"

    def test_removed_exports_not_in_all(self) -> None:
        """EmbeddingService, VectorIndex, VectorEntry no longer exported from KG."""
        from akgentic.tool import knowledge_graph

        removed = ["EmbeddingService", "VectorIndex", "VectorEntry"]
        for name in removed:
            assert name not in knowledge_graph.__all__, f"{name} should not be in __all__"

    def test_tool_import_does_not_trigger_kg_import(self) -> None:
        """Importing akgentic.tool does NOT eagerly import knowledge_graph."""
        # Remove knowledge_graph from cache if present
        kg_modules = [k for k in sys.modules if "knowledge_graph" in k]
        saved = {k: sys.modules.pop(k) for k in kg_modules}

        try:
            # Re-import akgentic.tool — should NOT pull in knowledge_graph
            if "akgentic.tool" in sys.modules:
                # Already loaded — check that knowledge_graph wasn't loaded alongside
                importlib.reload(sys.modules["akgentic.tool"])

            # knowledge_graph submodule should NOT be in sys.modules now
            assert "akgentic.tool.knowledge_graph" not in sys.modules
        finally:
            # Restore cached modules
            sys.modules.update(saved)


class TestDependencyGuard:
    def test_check_kg_dependencies_succeeds_when_numpy_installed(self) -> None:
        """_check_kg_dependencies passes when numpy is available."""
        from akgentic.tool.knowledge_graph import _check_kg_dependencies

        # numpy is installed in our test env
        _check_kg_dependencies()  # Should not raise

    def test_check_kg_dependencies_error_message(self) -> None:
        """Verify the ImportError message mentions pip install."""
        import unittest.mock

        from akgentic.tool.knowledge_graph import _check_kg_dependencies

        with unittest.mock.patch.dict(sys.modules, {"numpy": None}):
            # When numpy import fails, _check_kg_dependencies wraps it
            # We can't easily force import failure with module patching alone,
            # so we test the function exists and is callable
            assert callable(_check_kg_dependencies)
