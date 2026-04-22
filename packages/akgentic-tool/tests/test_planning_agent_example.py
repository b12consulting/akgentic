"""Smoke tests for the planning_agent.py example.

Validates that the example runs end-to-end without errors and
that exact-ID lookup returns a typed Task object.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

from akgentic.tool.planning.planning_actor import Task


def _load_example_module() -> types.ModuleType:
    """Load planning_agent.py as a module via importlib.

    Uses a unique module name per call to avoid sys.modules caching
    between tests, ensuring each call gets a fresh module with clean state.

    Returns:
        The loaded planning_agent module.
    """
    examples_dir = Path(__file__).parent.parent / "examples"
    example_path = examples_dir / "planning_agent.py"
    # Use a unique key each load to prevent cross-test sys.modules caching
    module_key = f"planning_agent_{id(object())}"
    spec = importlib.util.spec_from_file_location(module_key, example_path)
    assert spec is not None and spec.loader is not None, "Could not load example module spec"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]  # loader type narrowed by assert above
    except Exception:
        sys.modules.pop(module_key, None)
        raise
    return module


class TestPlanningAgentExample:
    """AC-7: Planning agent example smoke tests."""

    def test_main_runs_without_error(self) -> None:
        """AC-1, AC-3: Example runs end-to-end without errors (no API key)."""
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        with patch.dict("os.environ", env, clear=True):
            module = _load_example_module()
            module.main()

    def test_example_module_importable(self) -> None:
        """AC-4: Example has callable main() function."""
        module = _load_example_module()
        assert callable(module.main), "main() must be a callable function"

    def test_exact_id_lookup_returns_task(self) -> None:
        """AC-2: Exact-ID lookup returns a typed Task object."""
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        with patch.dict("os.environ", env, clear=True):
            module = _load_example_module()

            # Wire a fresh observer and plan actor
            observer = module._ExampleObserver()
            plan_actor = observer._plan_actor
            address = observer.myAddress

            # Create tasks
            module.create_sprint_tasks(plan_actor, address)

            # Exact-ID lookup — must return a Task instance
            result = plan_actor.get_planning_task(2)
            assert isinstance(result, Task), f"Expected Task, got {type(result)}"
            assert result.id == 2
            assert result.description == "Implement authentication middleware"

    def test_semantic_search_graceful_without_api_key(self) -> None:
        """AC-3: Semantic search degrades gracefully without API key.

        Without OPENAI_API_KEY, embeddings fail silently and search_planning
        falls back to keyword-only. "login security" has no substring overlap
        with any task description, so the result is an empty list — proving
        the semantic path is unavailable but the system does not crash.
        """
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        with patch.dict("os.environ", env, clear=True):
            module = _load_example_module()

            observer = module._ExampleObserver()
            plan_actor = observer._plan_actor
            address = observer.myAddress

            module.create_sprint_tasks(plan_actor, address)

            # "login security" has no substring match — without semantic search
            # this returns an empty list (graceful degradation, no crash)
            result = plan_actor.search_planning(query="login security")
            assert isinstance(result, list)
            assert len(result) == 0
