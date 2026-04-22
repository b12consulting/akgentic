"""Tests for TeamEntry.to_team_card() — catalog-to-runtime bridge."""

from __future__ import annotations

import pytest
from akgentic.team.models import TeamCard, TeamCardMember

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.models.team import TeamMemberSpec
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry
from tests.conftest import make_agent, make_team, make_template, make_tool


class StubAgentCatalog:
    """Minimal agent catalog stub satisfying _AgentCatalogProtocol."""

    def __init__(self, entries: dict[str, AgentEntry]) -> None:
        self._entries = entries

    def get(self, agent_id: str) -> AgentEntry | None:
        return self._entries.get(agent_id)


class StubToolCatalog:
    """Minimal tool catalog stub satisfying _ToolCatalogProtocol."""

    def __init__(self, entries: dict[str, ToolEntry]) -> None:
        self._entries = entries

    def get(self, tool_id: str) -> ToolEntry | None:
        return self._entries.get(tool_id)


class StubTemplateCatalog:
    """Minimal template catalog stub satisfying _TemplateCatalogProtocol."""

    def __init__(self, entries: dict[str, TemplateEntry]) -> None:
        self._entries = entries

    def get(self, template_id: str) -> TemplateEntry | None:
        return self._entries.get(template_id)


def _catalog_from(*agents: AgentEntry) -> StubAgentCatalog:
    """Build a StubAgentCatalog from a list of AgentEntry objects."""
    return StubAgentCatalog({a.id: a for a in agents})


# ---------------------------------------------------------------------------
# Happy path — flat members
# ---------------------------------------------------------------------------


class TestFlatMembers:
    """to_team_card with a single level of members."""

    def test_returns_team_card(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(ep, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert isinstance(result, TeamCard)

    def test_entry_point_is_correct_member(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(ep, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert isinstance(result.entry_point, TeamCardMember)
        assert result.entry_point.card.config.name == "proxy-agent"

    def test_remaining_members_exclude_entry_point(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(ep, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert len(result.members) == 1
        assert result.members[0].card.config.name == "worker-agent"


# ---------------------------------------------------------------------------
# Happy path — nested members (3 levels)
# ---------------------------------------------------------------------------


class TestNestedMembers:
    """to_team_card with 3-level member hierarchy."""

    def test_recursive_resolution(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        mgr = make_agent(id="manager", name="manager-agent")
        lead = make_agent(id="lead", name="lead-agent")
        dev = make_agent(id="dev", name="dev-agent")
        catalog = _catalog_from(ep, mgr, lead, dev)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(
                    agent_id="manager",
                    members=[
                        TeamMemberSpec(
                            agent_id="lead",
                            members=[TeamMemberSpec(agent_id="dev")],
                        ),
                    ],
                ),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        # manager is in members (entry_point is proxy)
        assert len(result.members) == 1
        manager_member = result.members[0]
        assert manager_member.card.config.name == "manager-agent"

        # lead is nested under manager
        assert len(manager_member.members) == 1
        lead_member = manager_member.members[0]
        assert lead_member.card.config.name == "lead-agent"

        # dev is nested under lead
        assert len(lead_member.members) == 1
        dev_member = lead_member.members[0]
        assert dev_member.card.config.name == "dev-agent"
        assert dev_member.members == []


# ---------------------------------------------------------------------------
# Headcount preservation
# ---------------------------------------------------------------------------


class TestHeadcount:
    """to_team_card preserves headcount from TeamMemberSpec."""

    def test_headcount_preserved(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(ep, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker", headcount=5),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert result.members[0].headcount == 5

    def test_default_headcount_is_one(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(ep, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert result.entry_point.headcount == 1
        assert result.members[0].headcount == 1


# ---------------------------------------------------------------------------
# Name and description passthrough
# ---------------------------------------------------------------------------


class TestPassthrough:
    """to_team_card passes name and description through."""

    def test_name_and_description(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            id="my-team",
            name="My Great Team",
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            message_types=["pydantic.BaseModel"],
        )
        team.description = "A team that does great things"

        result = team.to_team_card(catalog)

        assert result.name == "My Great Team"
        assert result.description == "A team that does great things"


# ---------------------------------------------------------------------------
# Error collection — missing agents
# ---------------------------------------------------------------------------


class TestMissingAgents:
    """to_team_card collects ALL missing agent errors."""

    def test_single_missing_agent(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="missing-agent"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert len(exc_info.value.errors) == 1
        assert "missing-agent" in exc_info.value.errors[0]

    def test_multiple_missing_agents(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="ghost-1"),
                TeamMemberSpec(agent_id="ghost-2"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert len(exc_info.value.errors) == 2
        error_text = " ".join(exc_info.value.errors)
        assert "ghost-1" in error_text
        assert "ghost-2" in error_text


# ---------------------------------------------------------------------------
# Error collection — unresolvable message types
# ---------------------------------------------------------------------------


class TestBadMessageTypes:
    """to_team_card collects ALL message type resolution errors."""

    def test_unresolvable_message_type(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            message_types=["no.such.module.FakeClass"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert len(exc_info.value.errors) == 1
        assert "no.such.module.FakeClass" in exc_info.value.errors[0]


# ---------------------------------------------------------------------------
# Combined errors — missing agents + bad message types
# ---------------------------------------------------------------------------


class TestCombinedErrors:
    """to_team_card collects errors from both members and message types."""

    def test_combined_errors(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="missing-1"),
            ],
            message_types=["no.such.module.BadClass"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        errors = exc_info.value.errors
        assert len(errors) == 2
        error_text = " ".join(errors)
        assert "missing-1" in error_text
        assert "no.such.module.BadClass" in error_text


# ---------------------------------------------------------------------------
# Message types resolution — happy path
# ---------------------------------------------------------------------------


class TestMessageTypesResolution:
    """to_team_card resolves FQCN strings to Python classes."""

    def test_message_types_resolved(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        from pydantic import BaseModel

        assert result.message_types == [BaseModel]


# ---------------------------------------------------------------------------
# Edge case — entry_point not in top-level members
# ---------------------------------------------------------------------------


class TestEntryPointNotInMembers:
    """to_team_card raises when entry_point is not among top-level resolved members."""

    def test_entry_point_not_in_members_raises(self) -> None:
        proxy = make_agent(id="proxy", name="proxy-agent")
        worker = make_agent(id="worker", name="worker-agent")
        catalog = _catalog_from(proxy, worker)

        team = make_team(
            entry_point="proxy",
            members=[
                # proxy is only a child of worker, NOT at the top level
                TeamMemberSpec(
                    agent_id="worker",
                    members=[TeamMemberSpec(agent_id="proxy")],
                ),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert len(exc_info.value.errors) == 1
        assert "Entry point" in exc_info.value.errors[0]
        assert "proxy" in exc_info.value.errors[0]


# ---------------------------------------------------------------------------
# Edge case — missing agent in nested (child) members
# ---------------------------------------------------------------------------


class TestNestedMissingAgent:
    """to_team_card collects errors from nested missing agents."""

    def test_missing_nested_child_agent(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        manager = make_agent(id="manager", name="manager-agent")
        catalog = _catalog_from(ep, manager)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(
                    agent_id="manager",
                    members=[TeamMemberSpec(agent_id="ghost-nested")],
                ),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert len(exc_info.value.errors) == 1
        assert "ghost-nested" in exc_info.value.errors[0]

    def test_missing_agents_at_multiple_levels(self) -> None:
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(
                    agent_id="ghost-top",
                    members=[TeamMemberSpec(agent_id="ghost-child")],
                ),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        errors = exc_info.value.errors
        assert len(errors) == 2
        error_text = " ".join(errors)
        assert "ghost-top" in error_text
        assert "ghost-child" in error_text


# ---------------------------------------------------------------------------
# Tool and template resolution via to_team_card
# ---------------------------------------------------------------------------


class TestToolResolution:
    """to_team_card resolves tools when tool_catalog and template_catalog are provided."""

    def test_tools_resolved_when_catalogs_provided(self) -> None:
        agent = make_agent(id="worker", name="worker-agent", tool_ids=["search-1"])
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        tool = make_tool(id="search-1")
        tool_catalog = StubToolCatalog({"search-1": tool})
        template_catalog = StubTemplateCatalog({})

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        worker_card = result.members[0].card
        tools = getattr(worker_card.config, "tools", [])
        assert len(tools) == 1
        assert tools[0].name == "search"

    def test_tools_empty_without_catalogs(self) -> None:
        agent = make_agent(id="worker", name="worker-agent", tool_ids=["search-1"])
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        # Without tool/template catalogs, falls back to raw card (empty tools)
        result = team.to_team_card(agent_catalog)

        worker_card = result.members[0].card
        tools = getattr(worker_card.config, "tools", [])
        assert tools == []


class TestTemplateResolution:
    """to_team_card resolves prompt templates when catalogs are provided."""

    def test_template_resolved_when_catalogs_provided(self) -> None:
        agent = make_agent(
            id="worker",
            name="worker-agent",
            template_ref="@sys-prompt",
            params={"role": "expert", "instructions": "Be helpful."},
        )
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        template = make_template(id="sys-prompt", template="You are {role}. {instructions}")
        tool_catalog = StubToolCatalog({})
        template_catalog = StubTemplateCatalog({"sys-prompt": template})

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        worker_card = result.members[0].card
        prompt = getattr(worker_card.config, "prompt", None)
        assert prompt is not None
        assert prompt.template == "You are {role}. {instructions}"
        assert "@" not in prompt.template

    def test_template_unresolved_without_catalogs(self) -> None:
        agent = make_agent(
            id="worker",
            name="worker-agent",
            template_ref="@sys-prompt",
            params={"role": "expert", "instructions": "Be helpful."},
        )
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        # Without catalogs, template stays as @-reference
        result = team.to_team_card(agent_catalog)

        worker_card = result.members[0].card
        prompt = getattr(worker_card.config, "prompt", None)
        assert prompt is not None
        assert prompt.template == "@sys-prompt"


# ---------------------------------------------------------------------------
# Agent profiles resolution (ADR-005)
# ---------------------------------------------------------------------------


class TestAgentProfilesResolution:
    """to_team_card resolves agent_profiles to a flat list of AgentCard."""

    def test_raw_resolution_happy_path(self) -> None:
        """AC3: Without tool/template catalogs, profiles resolve to raw entry.card."""
        ep = make_agent(id="proxy", name="proxy-agent")
        expert = make_agent(id="expert", name="expert-agent")
        assistant = make_agent(id="assistant", name="assistant-agent")
        catalog = _catalog_from(ep, expert, assistant)

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            agent_profiles=["expert", "assistant"],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert len(result.agent_profiles) == 2
        assert result.agent_profiles[0].config.name == "expert-agent"
        assert result.agent_profiles[1].config.name == "assistant-agent"

    def test_fully_resolved_with_tool_and_template_catalogs(self) -> None:
        """AC4: With both catalogs, profiles use entry.to_agent_card (tools populated)."""
        ep = make_agent(id="proxy", name="proxy-agent")
        expert = make_agent(id="expert", name="expert-agent", tool_ids=["search-1"])
        agent_catalog = _catalog_from(ep, expert)

        tool = make_tool(id="search-1")
        tool_catalog = StubToolCatalog({"search-1": tool})
        template_catalog = StubTemplateCatalog({})

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            agent_profiles=["expert"],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        assert len(result.agent_profiles) == 1
        expert_card = result.agent_profiles[0]
        tools = getattr(expert_card.config, "tools", [])
        assert len(tools) == 1
        assert tools[0].name == "search"

    def test_missing_profile_agent_collected_as_error(self) -> None:
        """AC5: Missing profile agent → exact error string collected."""
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            agent_profiles=["missing-agent"],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        assert "Profile agent 'missing-agent' not found in catalog" in exc_info.value.errors

    def test_combined_member_and_profile_errors(self) -> None:
        """AC6: Both member and profile errors collected in a single CatalogValidationError."""
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="missing-member"),
            ],
            agent_profiles=["missing-profile"],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(catalog)

        errors = exc_info.value.errors
        assert any("missing-member" in e for e in errors)
        assert any("Profile agent 'missing-profile' not found in catalog" in e for e in errors)

    def test_empty_agent_profiles_passthrough(self) -> None:
        """AC7: Empty agent_profiles → empty list, no error, no lookup."""
        ep = make_agent(id="proxy", name="proxy-agent")
        catalog = _catalog_from(ep)

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            message_types=["pydantic.BaseModel"],
        )

        result = team.to_team_card(catalog)

        assert result.agent_profiles == []

    def test_fully_resolved_path_collects_to_agent_card_errors(self) -> None:
        """AC8: When entry.to_agent_card raises, the error is collected, not raised."""
        ep = make_agent(id="proxy", name="proxy-agent")
        bad_agent = make_agent(id="bad-agent", name="bad-agent", tool_ids=["nonexistent-tool"])
        agent_catalog = _catalog_from(ep, bad_agent)

        tool_catalog = StubToolCatalog({})  # empty — tool lookup fails
        template_catalog = StubTemplateCatalog({})

        team = make_team(
            entry_point="proxy",
            members=[TeamMemberSpec(agent_id="proxy")],
            agent_profiles=["bad-agent"],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        assert any("nonexistent-tool" in e for e in exc_info.value.errors)


class TestResolutionErrorCollection:
    """to_team_card collects tool/template resolution errors without failing fast."""

    def test_missing_tool_collected_as_error(self) -> None:
        agent = make_agent(id="worker", name="worker-agent", tool_ids=["missing-tool"])
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        tool_catalog = StubToolCatalog({})  # empty — tool not found
        template_catalog = StubTemplateCatalog({})

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        assert any("missing-tool" in e for e in exc_info.value.errors)

    def test_missing_template_collected_as_error(self) -> None:
        agent = make_agent(
            id="worker",
            name="worker-agent",
            template_ref="@nonexistent",
            params={"role": "test"},
        )
        ep = make_agent(id="proxy", name="proxy-agent")
        agent_catalog = _catalog_from(ep, agent)

        tool_catalog = StubToolCatalog({})
        template_catalog = StubTemplateCatalog({})  # empty — template not found

        team = make_team(
            entry_point="proxy",
            members=[
                TeamMemberSpec(agent_id="proxy"),
                TeamMemberSpec(agent_id="worker"),
            ],
            message_types=["pydantic.BaseModel"],
        )

        with pytest.raises(CatalogValidationError) as exc_info:
            team.to_team_card(agent_catalog, tool_catalog, template_catalog)

        assert any("nonexistent" in e for e in exc_info.value.errors)
