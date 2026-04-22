"""Tests for ToolEntry model."""

import pytest
from pydantic import ValidationError

from akgentic.catalog.models.tool import ToolEntry


class TestToolEntryValid:
    """Tests for valid ToolEntry creation."""

    def test_valid_search_tool(self) -> None:
        entry = ToolEntry(
            id="search",
            tool_class="akgentic.tool.search.SearchTool",
            tool={"name": "Web Search", "description": "Search the web"},
        )
        assert entry.id == "search"
        assert entry.tool_class == "akgentic.tool.search.SearchTool"
        assert entry.tool.name == "Web Search"

    def test_tool_validated_against_resolved_class(self) -> None:
        entry = ToolEntry(
            id="search",
            tool_class="akgentic.tool.search.SearchTool",
            tool={
                "name": "Custom Search",
                "description": "Custom",
                "web_search": False,
            },
        )
        from akgentic.tool.search.search import SearchTool

        assert isinstance(entry.tool, SearchTool)
        assert entry.tool.web_search is False

    def test_planning_tool(self) -> None:
        entry = ToolEntry(
            id="planner",
            tool_class="akgentic.tool.planning.PlanningTool",
            tool={"name": "Planning", "description": "Manage plans"},
        )
        from akgentic.tool.planning.planning import PlanningTool

        assert isinstance(entry.tool, PlanningTool)

    def test_tool_as_instance_skips_resolution(self) -> None:
        from akgentic.tool.search.search import SearchTool

        tool_instance = SearchTool(name="search", description="test")
        entry = ToolEntry(
            id="search", tool_class="akgentic.tool.search.SearchTool", tool=tool_instance
        )
        assert entry.tool is tool_instance


class TestToolEntryInvalid:
    """Tests for ToolEntry validation errors."""

    def test_unresolvable_class_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ToolEntry(
                id="bad",
                tool_class="nonexistent.module.FakeClass",
                tool={"name": "x", "description": "x"},
            )
        errors = exc_info.value.errors()
        assert any("Cannot resolve tool_class" in str(e) for e in errors)

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            ToolEntry(
                id="",
                tool_class="akgentic.tool.search.SearchTool",
                tool={"name": "x", "description": "x"},
            )

    def test_empty_tool_class_raises(self) -> None:
        with pytest.raises(ValidationError):
            ToolEntry(
                id="valid",
                tool_class="",
                tool={"name": "x", "description": "x"},
            )

    def test_non_dict_data_passes_through(self) -> None:
        from akgentic.tool.search.search import SearchTool

        tool_instance = SearchTool(name="search", description="test")
        entry = ToolEntry.model_validate(
            ToolEntry(
                id="s",
                tool_class="akgentic.tool.search.SearchTool",
                tool=tool_instance,
            )
        )
        assert entry.id == "s"

    def test_non_string_tool_class_skips_resolution(self) -> None:
        with pytest.raises((ValidationError, TypeError)):
            ToolEntry.model_validate(
                {"id": "x", "tool_class": 123, "tool": {"name": "x", "description": "x"}}
            )

    def test_unresolvable_attribute_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ToolEntry(
                id="bad",
                tool_class="akgentic.tool.NonExistentTool",
                tool={"name": "x", "description": "x"},
            )
        errors = exc_info.value.errors()
        assert any("Cannot resolve tool_class" in str(e) for e in errors)
