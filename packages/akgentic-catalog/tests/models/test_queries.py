"""Tests for query models."""

from akgentic.catalog.models.queries import (
    AgentQuery,
    TeamQuery,
    TemplateQuery,
    ToolQuery,
)


class TestTemplateQuery:
    """Tests for TemplateQuery model."""

    def test_default_all_none(self) -> None:
        query = TemplateQuery()
        assert query.id is None
        assert query.placeholder is None

    def test_single_field(self) -> None:
        query = TemplateQuery(id="foo")
        assert query.id == "foo"
        assert query.placeholder is None

    def test_multiple_fields(self) -> None:
        query = TemplateQuery(id="foo", placeholder="role")
        assert query.id == "foo"
        assert query.placeholder == "role"


class TestToolQuery:
    """Tests for ToolQuery model."""

    def test_default_all_none(self) -> None:
        query = ToolQuery()
        assert query.id is None
        assert query.tool_class is None
        assert query.name is None
        assert query.description is None

    def test_multiple_fields(self) -> None:
        query = ToolQuery(tool_class="akgentic.tool.SearchTool", name="search")
        assert query.tool_class == "akgentic.tool.SearchTool"
        assert query.name == "search"
        assert query.id is None
        assert query.description is None


class TestAgentQuery:
    """Tests for AgentQuery model."""

    def test_default_all_none(self) -> None:
        query = AgentQuery()
        assert query.id is None
        assert query.role is None
        assert query.skills is None
        assert query.description is None

    def test_list_field(self) -> None:
        query = AgentQuery(skills=["research", "writing"])
        assert query.skills == ["research", "writing"]
        assert query.id is None
        assert query.role is None
        assert query.description is None

    def test_multiple_fields_including_list(self) -> None:
        query = AgentQuery(role="Manager", skills=["coordination"])
        assert query.role == "Manager"
        assert query.skills == ["coordination"]
        assert query.id is None
        assert query.description is None


class TestTeamQuery:
    """Tests for TeamQuery model."""

    def test_default_all_none(self) -> None:
        query = TeamQuery()
        assert query.id is None
        assert query.name is None
        assert query.description is None
        assert query.agent_id is None

    def test_agent_id_filter(self) -> None:
        query = TeamQuery(agent_id="eng-manager")
        assert query.agent_id == "eng-manager"
        assert query.id is None
        assert query.name is None
        assert query.description is None

    def test_multiple_fields(self) -> None:
        query = TeamQuery(name="Engineering", agent_id="eng-manager")
        assert query.name == "Engineering"
        assert query.agent_id == "eng-manager"
        assert query.id is None
        assert query.description is None
