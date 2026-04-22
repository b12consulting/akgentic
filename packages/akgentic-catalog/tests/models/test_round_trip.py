"""Round-trip serialization tests for all catalog entry types.

Proves that custom subclass types survive model_dump() -> model_validate()
without silent field loss. Covers the fragile spots identified in ADR-02:
- BaseConfig type annotation on AgentCard.config
- SerializableBaseModel __model__ metadata injection
- tools pop side-effect in resolve_config
- ToolCard abstract base with runtime attributes
- TeamMemberSpec recursive nesting
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from akgentic.agent.config import AgentConfig
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.tool.core import ToolCard

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.team import TeamEntry, TeamMemberSpec
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry

if TYPE_CHECKING:
    from collections.abc import Callable


# --- Inline custom subclasses for FQCN stability ---


class BareConfigAgent(Akgent[BaseConfig, BaseState]):
    """Test agent using bare BaseConfig for round-trip testing."""


class ResearchConfig(AgentConfig):
    """Custom AgentConfig subclass with domain-specific fields."""

    research_domain: str = "general"
    max_sources: int = 5
    include_citations: bool = True


class ResearchAgent(Akgent[ResearchConfig, BaseState]):
    """Minimal agent for FQCN resolution in round-trip tests."""


class SearchToolCard(ToolCard):
    """Custom ToolCard subclass for round-trip testing."""

    api_key: str
    max_results: int = 10
    search_engine: str = "tavily"

    def get_tools(self) -> list[Callable[..., object]]:
        """Minimal implementation for testing -- not exercised in round-trip tests."""
        return []


# --- Round-trip test classes ---


class TestRoundTripAgentEntry:
    """AC1: AgentEntry with custom AgentConfig subclass round-trip."""

    def test_custom_config_fields_survive_round_trip(self) -> None:
        """Custom AgentConfig subclass fields survive model_dump -> model_validate."""
        original = AgentEntry(
            id="researcher",
            tool_ids=["search", "planning"],
            card={
                "role": "research-engineer",
                "description": "Research engineer specializing in biology",
                "skills": ["research", "analysis", "citation"],
                "agent_class": "tests.models.test_round_trip.ResearchAgent",
                "config": {
                    "name": "researcher",
                    "research_domain": "biology",
                    "max_sources": 20,
                    "include_citations": False,
                    "prompt": {
                        "template": "You are a {role}",
                        "params": {"role": "researcher"},
                    },
                    "model_cfg": {
                        "provider": "anthropic",
                        "model": "claude-sonnet-4-5-20250514",
                        "temperature": 0.3,
                    },
                    "runtime_cfg": {"retries": 5, "end_strategy": "early"},
                    "usage_limits": {
                        "request_limit": 100,
                        "total_tokens_limit": 50000,
                    },
                    "max_help_requests": 10,
                },
                "routes_to": ["reviewer", "writer"],
            },
        )
        dumped = original.model_dump()
        restored = AgentEntry.model_validate(dumped)

        # Type preserved
        assert isinstance(restored.card.config, ResearchConfig)

        # Custom fields (non-default values)
        assert restored.card.config.research_domain == "biology"
        assert restored.card.config.max_sources == 20
        assert restored.card.config.include_citations is False

        # Inherited AgentConfig fields
        assert restored.card.config.name == "researcher"
        assert restored.card.config.prompt.template == "You are a {role}"
        assert restored.card.config.prompt.params == {"role": "researcher"}
        assert restored.card.config.model_cfg.provider == "anthropic"
        assert restored.card.config.model_cfg.model == "claude-sonnet-4-5-20250514"
        assert restored.card.config.model_cfg.temperature == 0.3
        assert restored.card.config.runtime_cfg.retries == 5
        assert restored.card.config.runtime_cfg.end_strategy == "early"
        assert restored.card.config.usage_limits.request_limit == 100
        assert restored.card.config.usage_limits.total_tokens_limit == 50000
        assert restored.card.config.max_help_requests == 10

    def test_tool_ids_survive_round_trip(self) -> None:
        """tool_ids list preserved through round-trip."""
        original = AgentEntry(
            id="researcher",
            tool_ids=["search", "planning", "analysis"],
            card={
                "role": "engineer",
                "description": "Test agent",
                "skills": ["coding"],
                "agent_class": "tests.models.test_round_trip.ResearchAgent",
                "config": {"name": "test"},
                "routes_to": [],
            },
        )
        dumped = original.model_dump()
        restored = AgentEntry.model_validate(dumped)

        assert restored.tool_ids == ["search", "planning", "analysis"]
        assert restored.id == "researcher"

    def test_card_level_fields_survive_round_trip(self) -> None:
        """Card-level fields (role, description, skills, routes_to) preserved."""
        original = AgentEntry(
            id="researcher",
            tool_ids=["search"],
            card={
                "role": "research-engineer",
                "description": "Research engineer specializing in biology",
                "skills": ["research", "analysis", "citation"],
                "agent_class": "tests.models.test_round_trip.ResearchAgent",
                "config": {
                    "name": "researcher",
                    "research_domain": "biology",
                    "max_sources": 20,
                    "include_citations": False,
                },
                "routes_to": ["reviewer", "writer"],
                "metadata": {"source": "research-db", "version": 2},
            },
        )
        dumped = original.model_dump()
        restored = AgentEntry.model_validate(dumped)

        assert restored.card.role == "research-engineer"
        assert restored.card.description == "Research engineer specializing in biology"
        assert restored.card.skills == ["research", "analysis", "citation"]
        assert restored.card.routes_to == ["reviewer", "writer"]
        assert restored.card.agent_class == "tests.models.test_round_trip.ResearchAgent"
        assert restored.card.metadata == {"source": "research-db", "version": 2}
        assert restored.id == "researcher"


class TestRoundTripBaseConfigAgent:
    """Story 12-2: BaseConfig agent entry round-trip serialization."""

    def test_baseconfig_fields_survive_round_trip(self) -> None:
        """BaseConfig agent survives model_dump -> model_validate."""
        original = AgentEntry(
            id="human-proxy",
            tool_ids=[],
            card={
                "role": "Human",
                "description": "User-facing proxy",
                "skills": [],
                "agent_class": "tests.models.test_round_trip.BareConfigAgent",
                "config": {"name": "@Human", "role": "HumanProxy"},
                "routes_to": ["@Manager"],
            },
        )
        dumped = original.model_dump()
        restored = AgentEntry.model_validate(dumped)

        assert isinstance(restored.card.config, BaseConfig)
        assert restored.card.config.name == "@Human"
        assert restored.card.config.role == "HumanProxy"
        assert restored.tool_ids == []
        assert restored.card.role == "Human"
        assert restored.card.description == "User-facing proxy"
        assert restored.card.routes_to == ["@Manager"]
        assert restored.id == "human-proxy"


class TestRoundTripToolEntry:
    """AC2: ToolEntry with custom ToolCard subclass round-trip."""

    def test_custom_tool_card_fields_survive_round_trip(self) -> None:
        """Custom ToolCard subclass fields survive model_dump -> model_validate."""
        original = ToolEntry(
            id="web-search",
            tool_class="tests.models.test_round_trip.SearchToolCard",
            tool={
                "name": "Web Search",
                "description": "Searches the web for information",
                "api_key": "sk-test-key-12345",
                "max_results": 25,
                "search_engine": "google",
            },
        )
        dumped = original.model_dump()
        restored = ToolEntry.model_validate(dumped)

        # Type preserved
        assert isinstance(restored.tool, SearchToolCard)

        # Custom fields (non-default values)
        assert restored.tool.api_key == "sk-test-key-12345"
        assert restored.tool.max_results == 25
        assert restored.tool.search_engine == "google"

        # Base ToolCard fields
        assert restored.tool.name == "Web Search"
        assert restored.tool.description == "Searches the web for information"

        # Entry-level fields
        assert restored.id == "web-search"
        assert restored.tool_class == "tests.models.test_round_trip.SearchToolCard"

        # Guard: serializer includes all model fields
        assert set(dumped.keys()) == set(ToolEntry.model_fields.keys())


class TestRoundTripTeamEntry:
    """AC3: TeamEntry recursive tree and metadata round-trip."""

    def test_recursive_member_tree_survives_round_trip(self) -> None:
        """3+ level recursive TeamMemberSpec tree with non-default headcounts."""
        original = TeamEntry(
            id="research-team",
            name="Research Division",
            entry_point="lead",
            message_types=["builtins.str"],
            members=[
                TeamMemberSpec(
                    agent_id="lead",
                    headcount=1,
                    members=[
                        TeamMemberSpec(
                            agent_id="senior-researcher",
                            headcount=2,
                            members=[
                                TeamMemberSpec(
                                    agent_id="junior-researcher",
                                    headcount=3,
                                    members=[],
                                ),
                                TeamMemberSpec(
                                    agent_id="data-analyst",
                                    headcount=2,
                                    members=[],
                                ),
                            ],
                        ),
                        TeamMemberSpec(
                            agent_id="writer",
                            headcount=1,
                            members=[],
                        ),
                    ],
                ),
            ],
            description="A multi-level research team",
        )
        dumped = original.model_dump()
        restored = TeamEntry.model_validate(dumped)

        # Top level
        assert len(restored.members) == 1
        lead = restored.members[0]
        assert lead.agent_id == "lead"
        assert lead.headcount == 1

        # Level 2
        assert len(lead.members) == 2
        senior = lead.members[0]
        assert senior.agent_id == "senior-researcher"
        assert senior.headcount == 2
        writer = lead.members[1]
        assert writer.agent_id == "writer"
        assert writer.headcount == 1

        # Level 3
        assert len(senior.members) == 2
        junior = senior.members[0]
        assert junior.agent_id == "junior-researcher"
        assert junior.headcount == 3
        analyst = senior.members[1]
        assert analyst.agent_id == "data-analyst"
        assert analyst.headcount == 2

        # Entry-level fields
        assert restored.id == "research-team"
        assert restored.name == "Research Division"
        assert restored.entry_point == "lead"
        assert restored.description == "A multi-level research team"

    def test_agent_profiles_and_message_types_survive_round_trip(self) -> None:
        """agent_profiles list and message_types FQCN strings preserved."""
        original = TeamEntry(
            id="dev-team",
            name="Development Team",
            entry_point="manager",
            message_types=[
                "akgentic.core.agent_state.BaseState",
                "akgentic.core.agent_config.BaseConfig",
            ],
            members=[
                TeamMemberSpec(agent_id="manager", headcount=1, members=[]),
            ],
            agent_profiles=["designer", "tester", "devops"],
            description="A development team with hiring pool",
        )
        dumped = original.model_dump()
        restored = TeamEntry.model_validate(dumped)

        assert restored.message_types == [
            "akgentic.core.agent_state.BaseState",
            "akgentic.core.agent_config.BaseConfig",
        ]
        assert restored.agent_profiles == ["designer", "tester", "devops"]
        assert restored.id == "dev-team"
        assert restored.name == "Development Team"


class TestRoundTripTemplateEntry:
    """AC4: TemplateEntry computed placeholders round-trip."""

    def test_template_and_placeholders_survive_round_trip(self) -> None:
        """Template string preserved and placeholders computed field re-derived."""
        original = TemplateEntry(
            id="research-prompt",
            template="You are a {role} specializing in {domain}. Use {tool} for research.",
        )
        dumped = original.model_dump()
        restored = TemplateEntry.model_validate(dumped)

        # Template string preserved exactly
        assert (
            restored.template
            == "You are a {role} specializing in {domain}. Use {tool} for research."
        )

        # Computed placeholders re-derived correctly (sorted, deduplicated)
        assert restored.placeholders == ["domain", "role", "tool"]

        # Entry-level fields
        assert restored.id == "research-prompt"
