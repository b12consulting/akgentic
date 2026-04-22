"""Tests for TeamCatalog service with cross-validation and downstream wiring."""

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError
from akgentic.catalog.models.queries import TeamQuery
from akgentic.catalog.models.team import TeamMemberSpec
from akgentic.catalog.services.agent_catalog import AgentCatalog
from akgentic.catalog.services.team_catalog import TeamCatalog
from akgentic.catalog.services.template_catalog import TemplateCatalog
from akgentic.catalog.services.tool_catalog import ToolCatalog
from tests.conftest import make_agent, make_team, make_template, make_tool
from tests.services.conftest import InMemoryAgentCatalogRepository, InMemoryTeamCatalogRepository

# --- Fixtures ---


@pytest.fixture
def agent_catalog(
    agent_repo: InMemoryAgentCatalogRepository,
    template_catalog: TemplateCatalog,
    tool_catalog: ToolCatalog,
) -> AgentCatalog:
    cat = AgentCatalog(
        repository=agent_repo,
        template_catalog=template_catalog,
        tool_catalog=tool_catalog,
    )
    # Pre-populate with known agents for team validation
    cat.create(make_agent("agent-1", name="agent-one"))
    cat.create(make_agent("agent-2", name="agent-two"))
    cat.create(make_agent("agent-3", name="agent-three"))
    return cat


@pytest.fixture
def catalog(
    team_repo: InMemoryTeamCatalogRepository,
    agent_catalog: AgentCatalog,
) -> TeamCatalog:
    return TeamCatalog(repository=team_repo, agent_catalog=agent_catalog)


# --- Tests: validate_create ---


class TestValidateCreateDuplicateId:
    """AC5: Duplicate ID rejection."""

    def test_returns_error_for_duplicate_id(self, catalog: TeamCatalog) -> None:
        team = make_team("team-1")
        catalog.create(team)
        errors = catalog.validate_create(make_team("team-1"))
        assert len(errors) == 1
        assert "already exists" in errors[0]

    def test_returns_empty_for_unique_id(self, catalog: TeamCatalog) -> None:
        errors = catalog.validate_create(make_team("team-1"))
        assert errors == []


class TestValidateCreateValidTeam:
    """AC1: Valid team creation with all agents existing."""

    def test_valid_team_no_errors(self, catalog: TeamCatalog) -> None:
        team = make_team("team-1", members=[TeamMemberSpec(agent_id="agent-1")])
        errors = catalog.validate_create(team)
        assert errors == []

    def test_valid_team_multiple_members(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            members=[TeamMemberSpec(agent_id="agent-1"), TeamMemberSpec(agent_id="agent-2")],
        )
        errors = catalog.validate_create(team)
        assert errors == []


class TestValidateCreateEntryPoint:
    """AC2: Entry point validation."""

    def test_entry_point_not_in_members(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="agent-999",
            members=[TeamMemberSpec(agent_id="agent-1")],
        )
        errors = catalog.validate_create(team)
        assert any("Entry point 'agent-999' not found in members tree" in e for e in errors)

    def test_entry_point_found_in_nested_members(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="agent-2",
            members=[
                TeamMemberSpec(
                    agent_id="agent-1",
                    members=[TeamMemberSpec(agent_id="agent-2")],
                )
            ],
        )
        errors = catalog.validate_create(team)
        assert not any("Entry point" in e for e in errors)

    def test_entry_point_agent_not_in_catalog(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="nonexistent",
            members=[TeamMemberSpec(agent_id="nonexistent")],
        )
        errors = catalog.validate_create(team)
        assert any("Agent 'nonexistent' not found in AgentCatalog" in e for e in errors)


class TestValidateCreateMemberAgents:
    """AC15: Member agent_id doesn't exist in AgentCatalog."""

    def test_missing_member_agent(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="agent-1",
            members=[TeamMemberSpec(agent_id="agent-1"), TeamMemberSpec(agent_id="nonexistent")],
        )
        errors = catalog.validate_create(team)
        assert any("Agent 'nonexistent' not found in AgentCatalog" in e for e in errors)

    def test_nested_missing_member_agent(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="agent-1",
            members=[
                TeamMemberSpec(
                    agent_id="agent-1",
                    members=[TeamMemberSpec(agent_id="nonexistent")],
                )
            ],
        )
        errors = catalog.validate_create(team)
        assert any("Agent 'nonexistent' not found in AgentCatalog" in e for e in errors)


class TestValidateCreateProfiles:
    """AC3: Profile agent validation."""

    def test_missing_profile_agent(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            members=[TeamMemberSpec(agent_id="agent-1")],
            agent_profiles=["nonexistent"],
        )
        errors = catalog.validate_create(team)
        assert any("Profile agent 'nonexistent' not found in AgentCatalog" in e for e in errors)

    def test_valid_profile_agent(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            members=[TeamMemberSpec(agent_id="agent-1")],
            agent_profiles=["agent-2"],
        )
        errors = catalog.validate_create(team)
        assert errors == []


class TestValidateCreateMessageTypes:
    """AC4: Message type resolution."""

    def test_unresolvable_message_type(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            message_types=["nonexistent.module.FakeClass"],
        )
        errors = catalog.validate_create(team)
        assert any("Cannot resolve message_type" in e for e in errors)

    def test_valid_message_type(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            message_types=["akgentic.core.messages.UserMessage"],
        )
        errors = catalog.validate_create(team)
        assert errors == []


class TestValidateCreateMultipleErrors:
    """AC6: Multiple error collection in single call."""

    def test_multiple_errors_collected(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="missing-ep",
            members=[TeamMemberSpec(agent_id="nonexistent-member")],
            agent_profiles=["nonexistent-profile"],
            message_types=["bad.module.FakeClass"],
        )
        errors = catalog.validate_create(team)
        # Should have at least: entry_point not in members, member not found,
        # profile not found, message_type unresolvable
        assert len(errors) >= 4
        assert any("Entry point" in e for e in errors)
        assert any("Agent 'nonexistent-member'" in e for e in errors)
        assert any("Profile agent 'nonexistent-profile'" in e for e in errors)
        assert any("Cannot resolve message_type" in e for e in errors)


# --- Tests: create ---


class TestCreate:
    """AC6: create delegates to validate_create and raises."""

    def test_create_success(self, catalog: TeamCatalog) -> None:
        team = make_team("team-1")
        result = catalog.create(team)
        assert result == "team-1"
        assert catalog.get("team-1") is not None

    def test_create_raises_on_validation_errors(self, catalog: TeamCatalog) -> None:
        team = make_team(
            "team-1",
            entry_point="nonexistent",
            members=[TeamMemberSpec(agent_id="nonexistent")],
        )
        with pytest.raises(CatalogValidationError):
            catalog.create(team)


# --- Tests: CRUD delegation ---


class TestCrudDelegation:
    """AC8: get, list, search delegate directly to repository."""

    def test_get_existing(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        result = catalog.get("team-1")
        assert result is not None
        assert result.id == "team-1"

    def test_get_nonexistent(self, catalog: TeamCatalog) -> None:
        assert catalog.get("nonexistent") is None

    def test_list_delegates(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        team2 = make_team(
            "team-2",
            entry_point="agent-2",
            members=[TeamMemberSpec(agent_id="agent-2")],
        )
        catalog.create(team2)
        result = catalog.list()
        assert len(result) == 2

    def test_search_delegates(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        query = TeamQuery(id="team-1")
        result = catalog.search(query)
        assert len(result) >= 1


# --- Tests: update ---


class TestUpdate:
    """AC7: update with existence check and cross-validation."""

    def test_update_existing_success(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        updated = make_team("team-1", name="Updated Team")
        catalog.update("team-1", updated)
        result = catalog.get("team-1")
        assert result is not None
        assert result.name == "Updated Team"

    def test_update_nonexistent_raises(self, catalog: TeamCatalog) -> None:
        team = make_team("team-1")
        with pytest.raises(EntryNotFoundError, match="not found"):
            catalog.update("team-1", team)

    def test_update_id_mismatch_raises(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        mismatched = make_team(
            "team-2",
            entry_point="agent-2",
            members=[TeamMemberSpec(agent_id="agent-2")],
        )
        with pytest.raises(CatalogValidationError, match="does not match"):
            catalog.update("team-1", mismatched)

    def test_update_cross_validates(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        updated = make_team(
            "team-1",
            members=[TeamMemberSpec(agent_id="nonexistent")],
        )
        with pytest.raises(CatalogValidationError, match="not found in AgentCatalog"):
            catalog.update("team-1", updated)

    def test_update_skips_own_duplicate_check(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        updated = make_team("team-1", name="Renamed Team")
        catalog.update("team-1", updated)
        result = catalog.get("team-1")
        assert result is not None
        assert result.name == "Renamed Team"


# --- Tests: delete ---


class TestDelete:
    """AC14: Delete with no downstream refs (v1: teams are independent)."""

    def test_delete_success(self, catalog: TeamCatalog) -> None:
        catalog.create(make_team("team-1"))
        catalog.delete("team-1")
        assert catalog.get("team-1") is None

    def test_delete_not_found_raises(self, catalog: TeamCatalog) -> None:
        with pytest.raises(EntryNotFoundError, match="not found"):
            catalog.delete("nonexistent")


# --- Tests: downstream wiring integration ---


class TestDownstreamWiringTemplateDelete:
    """AC9: Template delete blocked by agent reference."""

    def test_template_delete_blocked_by_agent_ref(
        self,
        template_catalog: TemplateCatalog,
        agent_catalog: AgentCatalog,
    ) -> None:
        template_catalog.create(make_template("sys-prompt"))
        # Create agent that references template
        agent = make_agent(
            "ref-agent",
            name="ref-agent",
            template_ref="@sys-prompt",
            params={"role": "eng", "instructions": "code"},
        )
        agent_catalog.create(agent)
        # Wire downstream
        template_catalog.agent_catalog = agent_catalog
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            template_catalog.delete("sys-prompt")

    def test_template_delete_allowed_without_wiring(
        self,
        template_catalog: TemplateCatalog,
    ) -> None:
        template_catalog.create(make_template("sys-prompt"))
        assert template_catalog.agent_catalog is None
        template_catalog.delete("sys-prompt")
        assert template_catalog.get("sys-prompt") is None


class TestDownstreamWiringToolDelete:
    """AC10: Tool delete blocked by agent reference."""

    def test_tool_delete_blocked_by_agent_ref(
        self,
        tool_catalog: ToolCatalog,
        agent_catalog: AgentCatalog,
    ) -> None:
        tool_catalog.create(make_tool("search-1"))
        agent = make_agent("ref-agent", name="ref-agent", tool_ids=["search-1"])
        agent_catalog.create(agent)
        # Wire downstream
        tool_catalog.agent_catalog = agent_catalog
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            tool_catalog.delete("search-1")

    def test_tool_delete_allowed_without_wiring(
        self,
        tool_catalog: ToolCatalog,
    ) -> None:
        tool_catalog.create(make_tool("search-1"))
        assert tool_catalog.agent_catalog is None
        tool_catalog.delete("search-1")
        assert tool_catalog.get("search-1") is None


class TestDownstreamWiringAgentDeleteByTeamMember:
    """AC11: Agent delete blocked by team member reference."""

    def test_agent_delete_blocked_by_team_member(
        self,
        agent_catalog: AgentCatalog,
        catalog: TeamCatalog,
    ) -> None:
        team = make_team("team-1", members=[TeamMemberSpec(agent_id="agent-1")])
        catalog.create(team)
        agent_catalog.team_catalog = catalog
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            agent_catalog.delete("agent-1")


class TestDownstreamWiringAgentDeleteByTeamProfile:
    """AC11: Agent delete blocked by team profile reference."""

    def test_agent_delete_blocked_by_team_profile(
        self,
        agent_catalog: AgentCatalog,
        catalog: TeamCatalog,
    ) -> None:
        team = make_team(
            "team-1",
            members=[TeamMemberSpec(agent_id="agent-1")],
            agent_profiles=["agent-2"],
        )
        catalog.create(team)
        agent_catalog.team_catalog = catalog
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            agent_catalog.delete("agent-2")


class TestDownstreamWiringAgentDeleteByRouting:
    """AC12: Agent delete blocked by routing dependency."""

    def test_agent_delete_blocked_by_routing_dep(
        self,
        agent_catalog: AgentCatalog,
    ) -> None:
        # agent-1 already exists with name "agent-one"
        # Create agent that routes to "agent-one"
        router = make_agent("router", name="router-name", routes_to=["agent-one"])
        agent_catalog.create(router)
        with pytest.raises(CatalogValidationError, match="cannot delete"):
            agent_catalog.delete("agent-1")


class TestDownstreamWiringStandaloneUsage:
    """AC14: Standalone usage with None downstream refs."""

    def test_delete_with_none_downstream_skips_check(
        self,
        agent_catalog: AgentCatalog,
    ) -> None:
        assert agent_catalog.team_catalog is None
        agent_catalog.delete("agent-1")
        assert agent_catalog.get("agent-1") is None

    def test_template_delete_with_none_downstream(
        self,
        template_catalog: TemplateCatalog,
    ) -> None:
        template_catalog.create(make_template("sys-prompt"))
        assert template_catalog.agent_catalog is None
        template_catalog.delete("sys-prompt")
        assert template_catalog.get("sys-prompt") is None

    def test_tool_delete_with_none_downstream(
        self,
        tool_catalog: ToolCatalog,
    ) -> None:
        tool_catalog.create(make_tool("search-1"))
        assert tool_catalog.agent_catalog is None
        tool_catalog.delete("search-1")
        assert tool_catalog.get("search-1") is None


class TestFullFourCatalogWiring:
    """AC13: Full four-catalog wiring integration test."""

    def test_full_wiring_sequence(
        self,
        template_catalog: TemplateCatalog,
        tool_catalog: ToolCatalog,
        agent_catalog: AgentCatalog,
        catalog: TeamCatalog,
    ) -> None:
        # 1. Build in dependency order (already done by fixtures)
        # 2. Wire downstream refs for delete protection
        template_catalog.agent_catalog = agent_catalog
        tool_catalog.agent_catalog = agent_catalog
        agent_catalog.team_catalog = catalog

        # 3. Create a template and tool
        template_catalog.create(make_template("sys-prompt"))
        tool_catalog.create(make_tool("search-1"))

        # 4. Create an agent referencing both
        ref_agent = make_agent(
            "ref-agent",
            name="ref-agent",
            tool_ids=["search-1"],
            template_ref="@sys-prompt",
            params={"role": "eng", "instructions": "code"},
        )
        agent_catalog.create(ref_agent)

        # 5. Create a team referencing the agent
        team = make_team(
            "team-1",
            entry_point="ref-agent",
            members=[TeamMemberSpec(agent_id="ref-agent")],
        )
        catalog.create(team)

        # 6. Verify delete protection on all catalogs
        # Template delete blocked
        errors = template_catalog.validate_delete("sys-prompt")
        assert any("cannot delete" in e for e in errors)

        # Tool delete blocked
        errors = tool_catalog.validate_delete("search-1")
        assert any("cannot delete" in e for e in errors)

        # Agent delete blocked by team reference
        errors = agent_catalog.validate_delete("ref-agent")
        assert any("cannot delete" in e for e in errors)

        # Team delete allowed (no downstream refs for teams)
        errors = catalog.validate_delete("team-1")
        assert errors == []
