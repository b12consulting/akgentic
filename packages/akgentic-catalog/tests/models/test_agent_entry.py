"""Tests for AgentEntry model with config resolution."""

import pytest
from akgentic.agent.config import AgentConfig
from akgentic.core.agent import Akgent
from akgentic.core.agent_config import BaseConfig
from akgentic.core.agent_state import BaseState
from akgentic.llm.prompts import PromptTemplate
from akgentic.tool.search.search import SearchTool
from pydantic import ValidationError

from akgentic.catalog.models.agent import AgentEntry
from akgentic.catalog.models.errors import CatalogValidationError
from akgentic.catalog.models.template import TemplateEntry
from akgentic.catalog.models.tool import ToolEntry

# --- Test fixtures: custom agent classes for config resolution ---


class CustomConfig(AgentConfig):
    """Custom config extending AgentConfig for testing."""

    custom_field: str = "default"


class CustomAgent(Akgent[CustomConfig, BaseState]):
    """Test agent with custom config."""


class BareConfigAgent(Akgent[BaseConfig, BaseState]):
    """Test agent using bare BaseConfig (no prompt/model_cfg fields)."""


# --- Mock catalog helpers ---


class MockToolCatalog:
    """Simple catalog mock with .get() returning ToolEntry or None."""

    def __init__(self, entries: dict[str, ToolEntry]) -> None:
        self._entries = entries

    def get(self, tool_id: str) -> ToolEntry | None:
        return self._entries.get(tool_id)


class MockTemplateCatalog:
    """Simple catalog mock with .get() returning TemplateEntry or None."""

    def __init__(self, entries: dict[str, TemplateEntry]) -> None:
        self._entries = entries

    def get(self, template_id: str) -> TemplateEntry | None:
        return self._entries.get(template_id)


# --- Minimal valid card data helper ---


def _base_agent_card(**overrides: object) -> dict[str, object]:
    """Build minimal valid card data for BaseAgent.

    ``config.role`` is the single source of truth for ``AgentCard.role``
    (ADR-007); this helper injects ``role="engineer"`` into the config
    dict — including overridden configs that omit it — so every test
    builds a valid card by default.
    """
    role = overrides.pop("role", "engineer")
    config = overrides.pop("config", {"name": "test-agent"})
    if isinstance(config, dict) and "role" not in config:
        config = {**config, "role": role}
    card: dict[str, object] = {
        "description": "A test agent",
        "skills": ["coding"],
        "agent_class": "akgentic.agent.BaseAgent",
        "config": config,
    }
    card.update(overrides)
    return card


class TestAgentEntryValid:
    """Tests for valid AgentEntry creation and config resolution."""

    def test_base_agent_resolves_config_to_agent_config(self) -> None:
        """AC #1: config resolves to AgentConfig via MRO walk."""
        entry = AgentEntry(id="eng", card=_base_agent_card())
        assert isinstance(entry.card.config, AgentConfig)

    def test_resolved_config_has_agent_config_fields(self) -> None:
        """AC #1: resolved config has prompt, model_cfg, tools fields."""
        entry = AgentEntry(id="eng", card=_base_agent_card())
        config = entry.card.config
        assert isinstance(config, AgentConfig)
        assert hasattr(config, "prompt")
        assert hasattr(config, "model_cfg")
        assert hasattr(config, "tools")

    def test_tool_ids_stored_as_strings(self) -> None:
        """AC #2: tool_ids stored as list of string ids."""
        entry = AgentEntry(
            id="eng",
            tool_ids=["search", "planning"],
            card=_base_agent_card(),
        )
        assert entry.tool_ids == ["search", "planning"]

    def test_tools_key_in_config_silently_popped(self) -> None:
        """AC #4: tools key in config dict is silently removed."""
        card_data = _base_agent_card(
            config={"name": "test-agent", "tools": [{"name": "fake", "description": "x"}]}
        )
        entry = AgentEntry(id="eng", card=card_data)
        config = entry.card.config
        assert isinstance(config, AgentConfig)
        assert config.tools == []

    def test_custom_config_agent(self) -> None:
        """AC #1: custom config subclass resolved via MRO."""
        card_data = _base_agent_card(
            agent_class="tests.models.test_agent_entry.CustomAgent",
            config={"name": "custom", "custom_field": "hello"},
        )
        entry = AgentEntry(id="custom", card=card_data)
        assert isinstance(entry.card.config, CustomConfig)
        assert entry.card.config.custom_field == "hello"

    def test_bare_base_config_agent(self) -> None:
        """Bare BaseConfig agent resolves to BaseConfig."""
        card_data = _base_agent_card(
            agent_class="tests.models.test_agent_entry.BareConfigAgent",
            config={"name": "bare"},
        )
        entry = AgentEntry(id="bare", card=card_data)
        assert isinstance(entry.card.config, BaseConfig)
        assert not isinstance(entry.card.config, AgentConfig)

    def test_empty_tool_ids_default(self) -> None:
        entry = AgentEntry(id="eng", card=_base_agent_card())
        assert entry.tool_ids == []

    def test_card_with_routes_to(self) -> None:
        card_data = _base_agent_card(routes_to=["@manager"])
        entry = AgentEntry(id="eng", card=card_data)
        assert entry.card.routes_to == ["@manager"]


class TestAgentEntryInvalid:
    """Tests for AgentEntry validation errors."""

    def test_unresolvable_agent_class_raises_validation_error(self) -> None:
        """AC #5: unresolvable agent_class raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AgentEntry(
                id="bad",
                card=_base_agent_card(agent_class="nonexistent.module.FakeAgent"),
            )
        errors = exc_info.value.errors()
        assert any("Cannot resolve agent_class" in str(e) for e in errors)

    def test_unresolvable_attribute_raises_validation_error(self) -> None:
        """AC #5: bad attribute raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            AgentEntry(
                id="bad",
                card=_base_agent_card(agent_class="akgentic.agent.NonExistentAgent"),
            )
        errors = exc_info.value.errors()
        assert any("Cannot resolve agent_class" in str(e) for e in errors)

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError):
            AgentEntry(id="", card=_base_agent_card())

    def test_non_dict_card_passes_to_pydantic(self) -> None:
        """Non-dict card input is handled by Pydantic validation."""
        with pytest.raises(ValidationError):
            AgentEntry.model_validate({"id": "x", "card": "not-a-dict"})

    def test_non_string_agent_class_skips_resolution(self) -> None:
        """Non-string agent_class skips resolution, Pydantic handles."""
        with pytest.raises((ValidationError, TypeError)):
            AgentEntry.model_validate(
                {
                    "id": "x",
                    "card": {
                        "role": "r",
                        "description": "d",
                        "skills": [],
                        "agent_class": 123,
                        "config": {},
                    },
                }
            )


class TestResolveTools:
    """Tests for AgentEntry.resolve_tools() method."""

    def test_resolve_tools_returns_tool_cards(self) -> None:
        """AC #2: resolve_tools returns list[ToolCard]."""
        search_tool = SearchTool(name="Web Search", description="Search the web")
        tool_entry = ToolEntry(
            id="search",
            tool_class="akgentic.tool.search.SearchTool",
            tool=search_tool,
        )
        catalog = MockToolCatalog({"search": tool_entry})

        entry = AgentEntry(
            id="eng",
            tool_ids=["search"],
            card=_base_agent_card(),
        )
        tools = entry.resolve_tools(catalog)
        assert len(tools) == 1
        assert tools[0] is search_tool

    def test_resolve_tools_multiple(self) -> None:
        search_tool = SearchTool(name="Search", description="s")
        plan_tool = SearchTool(name="Plan", description="p")
        catalog = MockToolCatalog(
            {
                "search": ToolEntry(
                    id="search",
                    tool_class="akgentic.tool.search.SearchTool",
                    tool=search_tool,
                ),
                "plan": ToolEntry(
                    id="plan",
                    tool_class="akgentic.tool.search.SearchTool",
                    tool=plan_tool,
                ),
            }
        )

        entry = AgentEntry(
            id="eng",
            tool_ids=["search", "plan"],
            card=_base_agent_card(),
        )
        tools = entry.resolve_tools(catalog)
        assert len(tools) == 2
        assert tools[0] is search_tool
        assert tools[1] is plan_tool

    def test_resolve_tools_missing_raises(self) -> None:
        """AC #2: missing tool_id raises CatalogValidationError."""
        catalog = MockToolCatalog({})
        entry = AgentEntry(
            id="eng",
            tool_ids=["missing"],
            card=_base_agent_card(),
        )
        with pytest.raises(CatalogValidationError, match="Tool 'missing' not found"):
            entry.resolve_tools(catalog)

    def test_resolve_tools_empty_tool_ids(self) -> None:
        catalog = MockToolCatalog({})
        entry = AgentEntry(id="eng", card=_base_agent_card())
        assert entry.resolve_tools(catalog) == []


class TestResolveTemplate:
    """Tests for AgentEntry.resolve_template() method."""

    def test_resolve_template_with_catalog_ref(self) -> None:
        """AC #3: @-reference resolves to PromptTemplate."""
        card_data = _base_agent_card(
            config={
                "name": "test",
                "prompt": {"template": "@coordinator-v1", "params": {"role": "lead"}},
            }
        )
        entry = AgentEntry(id="eng", card=card_data)

        template_entry = TemplateEntry(
            id="coordinator-v1",
            template="You are a {role} coordinator",
        )
        catalog = MockTemplateCatalog({"coordinator-v1": template_entry})

        result = entry.resolve_template(catalog)
        assert result is not None
        assert result.template == "You are a {role} coordinator"
        assert result.params == {"role": "lead"}

    def test_resolve_template_inline_returns_existing(self) -> None:
        """AC #3: inline template returns existing PromptTemplate as-is."""
        card_data = _base_agent_card(
            config={
                "name": "test",
                "prompt": {"template": "You are a helpful assistant", "params": {}},
            }
        )
        entry = AgentEntry(id="eng", card=card_data)
        catalog = MockTemplateCatalog({})

        result = entry.resolve_template(catalog)
        assert result is not None
        assert isinstance(result, PromptTemplate)
        assert result.template == "You are a helpful assistant"

    def test_resolve_template_bare_config_returns_none(self) -> None:
        """AC #3: bare BaseConfig returns None."""
        card_data = _base_agent_card(
            agent_class="tests.models.test_agent_entry.BareConfigAgent",
            config={"name": "bare"},
        )
        entry = AgentEntry(id="bare", card=card_data)
        catalog = MockTemplateCatalog({})

        result = entry.resolve_template(catalog)
        assert result is None

    def test_resolve_template_missing_raises(self) -> None:
        """AC #3: missing template raises CatalogValidationError."""
        card_data = _base_agent_card(
            config={
                "name": "test",
                "prompt": {"template": "@missing-template"},
            }
        )
        entry = AgentEntry(id="eng", card=card_data)
        catalog = MockTemplateCatalog({})

        with pytest.raises(CatalogValidationError, match="Template '@missing-template' not found"):
            entry.resolve_template(catalog)

    def test_resolve_template_default_prompt(self) -> None:
        """Default prompt template is inline, returned as-is."""
        entry = AgentEntry(id="eng", card=_base_agent_card())
        catalog = MockTemplateCatalog({})
        result = entry.resolve_template(catalog)
        assert result is not None
        assert isinstance(result, PromptTemplate)
        assert result.template == "You are a useful assistant"


class TestBaseConfigAgent:
    """Tests for AgentEntry with BaseConfig agents (HumanProxy, UserProxy).

    Story 12-2: validates that duck-typed hasattr guards from story 12-1
    correctly handle agents parameterized with BaseConfig (no prompt/tools).
    """

    def test_humanproxy_entry_creation(self) -> None:
        """AC1: HumanProxy resolves to BaseConfig via MRO walk."""
        card_data = _base_agent_card(
            agent_class="akgentic.agent.HumanProxy",
            config={"name": "@Human"},
        )
        entry = AgentEntry(id="human-proxy", card=card_data)
        assert isinstance(entry.card.config, BaseConfig)
        assert not hasattr(entry.card.config, "prompt")
        assert not hasattr(entry.card.config, "tools")
        assert entry.card.config.name == "@Human"

    def test_to_agent_card_with_baseconfig(self) -> None:
        """AC2: to_agent_card returns AgentCard with BaseConfig as-is."""
        card_data = _base_agent_card(
            agent_class="tests.models.test_agent_entry.BareConfigAgent",
            config={"name": "bare-agent"},
        )
        entry = AgentEntry(id="bare", card=card_data)
        tool_catalog = MockToolCatalog({})
        template_catalog = MockTemplateCatalog({})

        card = entry.to_agent_card(tool_catalog, template_catalog)
        assert isinstance(card.config, BaseConfig)
        assert card.config.name == "bare-agent"
        assert card.role == "engineer"
        assert card.description == "A test agent"

    def test_to_agent_card_humanproxy_no_tools_no_prompt(self) -> None:
        """AC2+AC4: HumanProxy card has no tools or prompt mutation."""
        card_data = _base_agent_card(
            agent_class="akgentic.agent.HumanProxy",
            config={"name": "@Human"},
            routes_to=["@Manager"],
        )
        entry = AgentEntry(id="human-proxy", tool_ids=[], card=card_data)
        tool_catalog = MockToolCatalog({})
        template_catalog = MockTemplateCatalog({})

        card = entry.to_agent_card(tool_catalog, template_catalog)
        assert isinstance(card.config, BaseConfig)
        assert not hasattr(card.config, "prompt")
        assert not hasattr(card.config, "tools")
        assert card.routes_to == ["@Manager"]

    def test_resolve_template_humanproxy_returns_none(self) -> None:
        """AC3: resolve_template returns None for HumanProxy."""
        card_data = _base_agent_card(
            agent_class="akgentic.agent.HumanProxy",
            config={"name": "@Human"},
        )
        entry = AgentEntry(id="human-proxy", card=card_data)
        catalog = MockTemplateCatalog({})
        assert entry.resolve_template(catalog) is None


class TestPublicApi:
    """Tests for module exports."""

    def test_agent_entry_in_models_init(self) -> None:
        from akgentic.catalog.models import AgentEntry as ModelsAgentEntry

        assert ModelsAgentEntry is AgentEntry

    def test_agent_entry_in_catalog_init(self) -> None:
        from akgentic.catalog import AgentEntry as CatalogAgentEntry

        assert CatalogAgentEntry is AgentEntry

    def test_extract_config_type_not_in_models_public_api(self) -> None:
        """Private _extract_config_type should not be in models __all__."""
        from akgentic.catalog.models import __all__ as models_all

        assert "_extract_config_type" not in models_all


class TestToAgentCard:
    """Tests for AgentEntry.to_agent_card() convenience method."""

    def test_returns_agent_card_with_resolved_tools(self) -> None:
        """to_agent_card resolves tool_ids into config.tools."""
        search_tool = SearchTool(name="Web Search", description="Search the web")
        tool_entry = ToolEntry(
            id="search",
            tool_class="akgentic.tool.search.SearchTool",
            tool=search_tool,
        )
        tool_catalog = MockToolCatalog({"search": tool_entry})
        template_catalog = MockTemplateCatalog({})

        entry = AgentEntry(
            id="eng",
            tool_ids=["search"],
            card=_base_agent_card(),
        )
        card = entry.to_agent_card(tool_catalog, template_catalog)
        config = card.config
        assert isinstance(config, AgentConfig)
        assert len(config.tools) == 1
        assert config.tools[0] is search_tool

    def test_returns_agent_card_with_resolved_template(self) -> None:
        """to_agent_card resolves @-reference templates."""
        template_entry = TemplateEntry(
            id="team-prompt",
            template="You are a {role}. {instructions}",
        )
        tool_catalog = MockToolCatalog({})
        template_catalog = MockTemplateCatalog({"team-prompt": template_entry})

        entry = AgentEntry(
            id="eng",
            card=_base_agent_card(
                config={
                    "name": "test",
                    "prompt": {
                        "template": "@team-prompt",
                        "params": {"role": "engineer", "instructions": "Build things."},
                    },
                }
            ),
        )
        card = entry.to_agent_card(tool_catalog, template_catalog)
        config = card.config
        assert isinstance(config, AgentConfig)
        assert config.prompt.template == "You are a {role}. {instructions}"
        assert config.prompt.params == {"role": "engineer", "instructions": "Build things."}

    def test_preserves_card_metadata(self) -> None:
        """to_agent_card preserves role, description, skills, routes_to."""
        tool_catalog = MockToolCatalog({})
        template_catalog = MockTemplateCatalog({})

        entry = AgentEntry(
            id="eng",
            card=_base_agent_card(routes_to=["@Manager"]),
        )
        card = entry.to_agent_card(tool_catalog, template_catalog)
        assert card.role == "engineer"
        assert card.description == "A test agent"
        assert card.skills == ["coding"]
        assert card.routes_to == ["@Manager"]

    def test_does_not_mutate_original_card(self) -> None:
        """to_agent_card returns a new card without mutating the entry."""
        tool_catalog = MockToolCatalog({})
        template_catalog = MockTemplateCatalog({})

        entry = AgentEntry(id="eng", card=_base_agent_card())
        original_config = entry.card.config
        card = entry.to_agent_card(tool_catalog, template_catalog)
        assert card.config is not original_config
