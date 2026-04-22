"""Tests for KnowledgeGraphTool ToolCard.

Covers:
- BaseToolParam subclass defaults and channel exposure (Task 1)
- KnowledgeGraphTool config, read_only, get_graph=False (Task 2)
- Observer wiring, singleton actor proxy (Task 2)
- Factory closures: get_graph, update_graph, search (Task 2)
- Story 10-9: depends_on + vector_store field, observer no longer creates
  VectorStoreActor, vector_store propagates into KnowledgeGraphConfig.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.orchestrator import Orchestrator

from akgentic.tool.core import COMMAND, SYSTEM_PROMPT, TOOL_CALL, BaseToolParam, _resolve
from akgentic.tool.knowledge_graph.kg_actor import (
    KG_ACTOR_NAME,
    KG_ACTOR_ROLE,
    KnowledgeGraphActor,
    KnowledgeGraphConfig,
)
from akgentic.tool.knowledge_graph.kg_tool import (
    GetGraph,
    KnowledgeGraphTool,
    SearchGraph,
    UpdateGraph,
)
from akgentic.tool.knowledge_graph.models import (
    EntityCreate,
    GraphView,
    ManageGraph,
    RelationCreate,
    SearchQuery,
)
from akgentic.tool.vector_store.actor import VectorStoreActor
from akgentic.tool.vector_store.protocol import CollectionConfig

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockActorAddress(ActorAddress):
    """Mock ActorAddress for ToolCard tests."""

    def __init__(self, name: str = "test-agent", role: str = "test-role") -> None:
        self._name = name
        self._role = role
        self._agent_id = uuid.uuid4()

    @property
    def agent_id(self) -> uuid.UUID:
        return self._agent_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    @property
    def team_id(self) -> uuid.UUID | None:
        return None

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: Any, message: Any) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def stop(self) -> None:
        pass

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> dict[str, Any]:
        return {"name": self._name, "role": self._role, "agent_id": str(self._agent_id)}

    def __repr__(self) -> str:
        return f"MockActorAddress(name={self._name})"


class MockActorToolObserver:
    """Mock implementing ActorToolObserver protocol for ToolCard tests."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self._address = MockActorAddress("test-agent")
        self._orchestrator = MockActorAddress("orchestrator")
        self._kg_actor: KnowledgeGraphActor | None = None
        self._orchestrator_proxy = MagicMock(spec=Orchestrator)
        self._vs_addr = MockActorAddress("#VectorStore", "ToolActor")

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        return self._address

    @property
    def orchestrator(self) -> ActorAddress:
        return self._orchestrator

    def notify_event(self, event: object) -> None:
        self.events.append(event)

    def proxy_ask(
        self,
        actor: ActorAddress,
        actor_type: type[AkgentType] | None = None,
        timeout: int | None = None,
    ) -> Any:
        if actor == self._orchestrator:
            return self._orchestrator_proxy
        # Return mock VectorStoreActor proxy for VS address
        if actor == self._vs_addr:
            return MagicMock()
        # Return the real KG actor for KG actor address
        if self._kg_actor is not None:
            return self._kg_actor
        return MagicMock()

    def setup_kg_actor(self) -> KnowledgeGraphActor:
        """Create a real KG actor and wire it for proxy_ask."""
        from akgentic.tool.knowledge_graph.models import KnowledgeGraphState

        actor = KnowledgeGraphActor()
        # Set typed config so search methods can access search_score_threshold.
        actor.config = KnowledgeGraphConfig(name=KG_ACTOR_NAME, role=KG_ACTOR_ROLE)
        # Manually init without orchestrator dependency
        actor.state = KnowledgeGraphState()
        actor.state.observer(actor)
        actor._vs_proxy = None
        actor._state_event_seq = 0
        self._kg_actor = actor
        kg_addr = MockActorAddress(KG_ACTOR_NAME, KG_ACTOR_ROLE)

        # getChildrenOrCreate returns VS addr for #VectorStore, KG addr for #KnowledgeGraphTool
        def _get_children_or_create(
            actor_class: type, config: object = None,
        ) -> MockActorAddress:
            from akgentic.tool.vector_store.actor import VectorStoreActor as _VSActor

            if actor_class is _VSActor:
                return self._vs_addr
            return kg_addr

        self._orchestrator_proxy.getChildrenOrCreate.side_effect = _get_children_or_create
        return actor


# ===========================================================================
# Task 1: BaseToolParam subclasses — channel exposure defaults
# ===========================================================================


class TestGetGraphParam:
    """GetGraph exposes on SYSTEM_PROMPT + COMMAND by default."""

    def test_default_channels(self) -> None:
        param = GetGraph()
        assert param.expose == {SYSTEM_PROMPT, COMMAND}

    def test_is_base_tool_param(self) -> None:
        assert issubclass(GetGraph, BaseToolParam)


class TestUpdateGraphParam:
    """UpdateGraph exposes on TOOL_CALL by default."""

    def test_default_channels(self) -> None:
        param = UpdateGraph()
        assert param.expose == {TOOL_CALL}

    def test_is_base_tool_param(self) -> None:
        assert issubclass(UpdateGraph, BaseToolParam)


class TestSearchGraphParam:
    """SearchGraph exposes on TOOL_CALL + COMMAND by default."""

    def test_default_channels(self) -> None:
        param = SearchGraph()
        assert param.expose == {TOOL_CALL, COMMAND}

    def test_is_base_tool_param(self) -> None:
        assert issubclass(SearchGraph, BaseToolParam)


class TestParamResolve:
    """_resolve() works with KG param types."""

    def test_resolve_true_returns_default_instance(self) -> None:
        result = _resolve(True, GetGraph)
        assert isinstance(result, GetGraph)
        assert result.expose == {SYSTEM_PROMPT, COMMAND}

    def test_resolve_false_returns_none(self) -> None:
        result = _resolve(False, GetGraph)
        assert result is None

    def test_resolve_instance_returns_same(self) -> None:
        custom = GetGraph(expose={COMMAND})
        result = _resolve(custom, GetGraph)
        assert result is custom
        assert result is not None
        assert result.expose == {COMMAND}


# ===========================================================================
# Task 2: KnowledgeGraphTool — default config, observer, factories
# ===========================================================================


class TestKnowledgeGraphToolDefaults:
    """KnowledgeGraphTool field defaults (2.1)."""

    def test_default_name(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.name == "KnowledgeGraph"

    def test_default_description(self) -> None:
        tool = KnowledgeGraphTool()
        assert "knowledge graph" in tool.description.lower()

    def test_default_get_graph_is_true(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.get_graph is True

    def test_default_update_graph_is_true(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.update_graph is True

    def test_default_search_is_true(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.search is True

    def test_default_read_only_is_false(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.read_only is False


class TestKnowledgeGraphToolObserver:
    """observer() wiring — singleton actor creation (2.2, story 10-9)."""

    def test_observer_requires_orchestrator(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer._orchestrator = None  # type: ignore[assignment]
        with pytest.raises(ValueError, match="orchestrator"):
            tool.observer(observer)

    def test_observer_creates_only_kg_actor_via_get_children_or_create(self) -> None:
        """Story 10-9: observer() creates ONLY KnowledgeGraphActor, not VectorStoreActor."""
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        addr = MockActorAddress(KG_ACTOR_NAME, KG_ACTOR_ROLE)
        observer._orchestrator_proxy.getChildrenOrCreate.return_value = addr

        tool.observer(observer)

        # Only KnowledgeGraphActor is created here; VectorStoreTool owns VectorStoreActor.
        assert observer._orchestrator_proxy.getChildrenOrCreate.call_count == 1

    def test_observer_does_not_create_vector_store_actor(self) -> None:
        """Story 10-9 AC-4: VectorStoreActor is never the class arg to getChildrenOrCreate."""
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        kg_addr = MockActorAddress(KG_ACTOR_NAME, KG_ACTOR_ROLE)
        observer._orchestrator_proxy.getChildrenOrCreate.return_value = kg_addr

        tool.observer(observer)

        for call in observer._orchestrator_proxy.getChildrenOrCreate.call_args_list:
            actor_cls = call.args[0] if call.args else call.kwargs.get("actor_class")
            assert actor_cls is not VectorStoreActor, (
                "KnowledgeGraphTool.observer() must not create VectorStoreActor"
            )
        # And the only remaining call is for KnowledgeGraphActor.
        last_cls = observer._orchestrator_proxy.getChildrenOrCreate.call_args.args[0]
        assert last_cls is KnowledgeGraphActor

    def test_observer_passes_kg_config_with_default_vector_store(self) -> None:
        """AC-4: vector_store=True (default) is propagated into KnowledgeGraphConfig."""
        tool = KnowledgeGraphTool()  # default vector_store=True
        captured: list[KnowledgeGraphConfig] = []

        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, KnowledgeGraphConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy

        tool.observer(mock_observer)

        assert len(captured) == 1
        assert captured[0].vector_store is True

    def test_observer_propagates_vector_store_false(self) -> None:
        """AC-4: vector_store=False flows into KnowledgeGraphConfig."""
        tool = KnowledgeGraphTool(vector_store=False)
        captured: list[KnowledgeGraphConfig] = []

        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, KnowledgeGraphConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy

        tool.observer(mock_observer)

        assert len(captured) == 1
        assert captured[0].vector_store is False

    def test_observer_propagates_vector_store_named_string(self) -> None:
        """AC-4 / AC-10: vector_store="<name>" flows into KnowledgeGraphConfig."""
        tool = KnowledgeGraphTool(vector_store="#VectorStore-RAG")
        captured: list[KnowledgeGraphConfig] = []

        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, KnowledgeGraphConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy

        tool.observer(mock_observer)

        assert len(captured) == 1
        assert captured[0].vector_store == "#VectorStore-RAG"


class TestKnowledgeGraphToolDependsOn:
    """Story 10-9 AC-1: depends_on declaration + vector_store field."""

    def test_depends_on_is_vector_store_tool(self) -> None:
        assert KnowledgeGraphTool().depends_on == ["VectorStoreTool"]

    def test_depends_on_not_a_pydantic_field(self) -> None:
        assert "depends_on" not in KnowledgeGraphTool.model_fields

    def test_depends_on_not_in_model_dump(self) -> None:
        dump = KnowledgeGraphTool().model_dump()
        assert "depends_on" not in dump

    def test_vector_store_field_default_true(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.vector_store is True
        assert "vector_store" in KnowledgeGraphTool.model_fields

    def test_vector_store_appears_in_model_dump(self) -> None:
        dump = KnowledgeGraphTool().model_dump()
        assert "vector_store" in dump
        assert dump["vector_store"] is True

    def test_vector_store_roundtrip_true(self) -> None:
        tool = KnowledgeGraphTool(vector_store=True)
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.vector_store is True

    def test_vector_store_roundtrip_false(self) -> None:
        tool = KnowledgeGraphTool(vector_store=False)
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.vector_store is False

    def test_vector_store_roundtrip_string(self) -> None:
        tool = KnowledgeGraphTool(vector_store="#VectorStore-RAG")
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.vector_store == "#VectorStore-RAG"


class TestKnowledgeGraphToolReadOnly:
    """read_only=True disables update_graph (2.9)."""

    def test_read_only_disables_update_in_get_tools(self) -> None:
        tool = KnowledgeGraphTool(read_only=True)
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "update_graph" not in tool_names

    def test_read_only_still_exposes_search(self) -> None:
        tool = KnowledgeGraphTool(read_only=True)
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "search_graph" in tool_names


class TestKnowledgeGraphToolGetGraphFalse:
    """get_graph=False removes all get_graph exposure (2.10)."""

    def test_get_graph_false_no_system_prompts(self) -> None:
        tool = KnowledgeGraphTool(get_graph=False)
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        prompts = tool.get_system_prompts()
        assert len(prompts) == 0

    def test_get_graph_false_no_commands_for_get_graph(self) -> None:
        tool = KnowledgeGraphTool(get_graph=False)
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        commands = tool.get_commands()
        assert GetGraph not in commands


class TestGetSystemPrompts:
    """get_system_prompts() returns graph prompt callable (2.3)."""

    def test_returns_prompt_when_get_graph_enabled(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        prompts = tool.get_system_prompts()
        assert len(prompts) == 1
        assert callable(prompts[0])

    def test_prompt_returns_empty_graph_message(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        prompts = tool.get_system_prompts()
        result = prompts[0]()
        assert result == "Knowledge graph is empty."

    def test_prompt_contains_compact_summary(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(
                        name="Alice",
                        entity_type="Person",
                        description="Engineer",
                        is_root=True,
                    ),
                ]
            )
        )
        tool.observer(observer)

        prompts = tool.get_system_prompts()
        result = prompts[0]()
        assert "Knowledge Graph Summary:" in result
        assert "Entities: 1" in result
        assert "Entity types: Person" in result
        assert "Root entities:" in result
        assert "Alice (Person): Engineer" in result
        assert "Use the get_graph tool" in result


class TestGetTools:
    """get_tools() returns correct factories based on channel config (2.4)."""

    def test_default_config_returns_update_and_search(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        # update_graph has TOOL_CALL, search has TOOL_CALL
        assert "update_graph" in tool_names
        assert "search_graph" in tool_names

    def test_default_config_no_get_graph_in_tools(self) -> None:
        """get_graph defaults to SYSTEM_PROMPT + COMMAND, NOT TOOL_CALL."""
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "get_graph" not in tool_names

    def test_get_graph_with_tool_call_channel(self) -> None:
        """When get_graph includes TOOL_CALL, it appears in get_tools()."""
        tool = KnowledgeGraphTool(get_graph=GetGraph(expose={TOOL_CALL, COMMAND}))
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        tool_names = [t.__name__ for t in tools]
        assert "get_graph" in tool_names


class TestGetCommands:
    """get_commands() returns correct command mappings (2.5)."""

    def test_default_config_has_get_graph_and_search_commands(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        commands = tool.get_commands()
        assert GetGraph in commands
        assert SearchGraph in commands

    def test_no_update_graph_command_by_default(self) -> None:
        """update_graph defaults to TOOL_CALL only, not COMMAND."""
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        observer.setup_kg_actor()
        tool.observer(observer)

        commands = tool.get_commands()
        assert UpdateGraph not in commands


class TestGetGraphFactory:
    """_get_graph_factory closure behavior (2.6)."""

    def test_get_graph_returns_formatted_string(self) -> None:
        tool = KnowledgeGraphTool(get_graph=GetGraph(expose={TOOL_CALL}))
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                    EntityCreate(name="Bob", entity_type="Person", description="Designer"),
                ],
                create_relations=[
                    RelationCreate(from_entity="Alice", to_entity="Bob", relation_type="KNOWS"),
                ],
            )
        )
        tool.observer(observer)

        tools = tool.get_tools()
        get_graph_fn = [t for t in tools if t.__name__ == "get_graph"][0]
        result = get_graph_fn()
        assert "Alice" in result
        assert "Bob" in result
        assert "KNOWS" in result


class TestUpdateGraphFactory:
    """_update_graph_factory closure behavior (2.7)."""

    def test_update_graph_calls_actor(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        tool.observer(observer)

        tools = tool.get_tools()
        update_fn = [t for t in tools if t.__name__ == "update_graph"][0]
        result = update_fn(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                ]
            )
        )
        assert result == "Done"
        assert len(actor.state.knowledge_graph.entities) == 1


class TestSearchFactory:
    """_search_factory closure behavior (2.8)."""

    def test_search_returns_formatted_string(self) -> None:
        tool = KnowledgeGraphTool()
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(name="Alice", entity_type="Person", description="Engineer"),
                ]
            )
        )
        tool.observer(observer)

        tools = tool.get_tools()
        search_fn = [t for t in tools if t.__name__ == "search_graph"][0]
        result = search_fn(SearchQuery(query="Alice"))
        assert "Alice" in result



# ===========================================================================
# Story 1.4 — _format_graph_summary and prompt config tests
# ===========================================================================


def _build_summary_view() -> GraphView:
    """Build a GraphView with varied types and root entities for summary tests."""
    from akgentic.tool.knowledge_graph.models import Entity, Relation

    entities = [
        Entity(
            name="Product", entity_type="Component", description="Main product platform",
            is_root=True,
        ),
        Entity(
            name="AuthService", entity_type="Service",
            description="Central authentication service", is_root=True,
        ),
        Entity(
            name="UserDB", entity_type="Database",
            description="Primary user data store", is_root=True,
        ),
        Entity(name="Cache", entity_type="Component", description="Redis cache layer"),
        Entity(name="Logger", entity_type="Service", description="Logging service"),
    ]
    relations = [
        Relation(
            from_entity="Product", to_entity="AuthService", relation_type="DEPENDS_ON",
        ),
        Relation(
            from_entity="AuthService", to_entity="UserDB", relation_type="STORES_IN",
        ),
        Relation(
            from_entity="Product", to_entity="Cache", relation_type="CONNECTS_TO",
        ),
    ]
    return GraphView(entities=entities, relations=relations)


class TestFormatGraphSummary:
    """Story 1.4 Task 4.2: _format_graph_summary static method."""

    def test_both_schema_and_roots_enabled(self) -> None:
        view = _build_summary_view()
        result = KnowledgeGraphTool._format_graph_summary(view)
        assert "Knowledge Graph Summary:" in result
        assert "Entities: 5 | Relations: 3" in result
        assert "Entity types:" in result
        assert "Component" in result
        assert "Service" in result
        assert "Database" in result
        assert "Relation types:" in result
        assert "DEPENDS_ON" in result
        assert "Root entities:" in result
        assert "Product (Component): Main product platform" in result
        assert "AuthService (Service): Central authentication service" in result
        assert "UserDB (Database): Primary user data store" in result
        assert "Use the get_graph tool" in result

    def test_schema_disabled(self) -> None:
        view = _build_summary_view()
        result = KnowledgeGraphTool._format_graph_summary(
            view, include_schema=False
        )
        assert "Entity types:" not in result
        assert "Relation types:" not in result
        # Counts and roots still present
        assert "Entities: 5" in result
        assert "Root entities:" in result

    def test_roots_disabled(self) -> None:
        view = _build_summary_view()
        result = KnowledgeGraphTool._format_graph_summary(
            view, include_roots=False
        )
        assert "Root entities:" not in result
        assert "Product (Component)" not in result
        # Counts and schema still present
        assert "Entities: 5" in result
        assert "Entity types:" in result

    def test_both_disabled_counts_and_footer_only(self) -> None:
        view = _build_summary_view()
        result = KnowledgeGraphTool._format_graph_summary(
            view, include_schema=False, include_roots=False
        )
        assert "Entities: 5 | Relations: 3" in result
        assert "Entity types:" not in result
        assert "Root entities:" not in result
        assert "Use the get_graph tool" in result

    def test_empty_graph(self) -> None:
        result = KnowledgeGraphTool._format_graph_summary(GraphView())
        assert result == "Knowledge graph is empty."

    def test_scales_by_types_not_entities(self) -> None:
        """AC-12: Summary length depends on distinct types + roots, not total count."""
        from akgentic.tool.knowledge_graph.models import Entity, Relation

        # Build graph with many entities but few types
        entities = [
            Entity(
                name=f"E{i}", entity_type="TypeA" if i % 2 == 0 else "TypeB",
                description=f"Entity {i}",
            )
            for i in range(50)
        ]
        entities[0].is_root = True
        relations = [
            Relation(
                from_entity=f"E{i}", to_entity=f"E{i+1}", relation_type="REL",
            )
            for i in range(49)
        ]
        view = GraphView(entities=entities, relations=relations)
        result = KnowledgeGraphTool._format_graph_summary(view)
        # Summary should have lines for: header, counts, 2 entity types,
        # 1 relation type, root header + 1 root, blank, footer = ~8 lines
        lines = result.strip().split("\n")
        assert len(lines) < 15  # NOT 50+ lines

    def test_format_graph_view_still_returns_full_details(self) -> None:
        """Task 4.4: _format_graph_view unchanged — still returns full entity/relation details."""
        view = _build_summary_view()
        result = KnowledgeGraphTool._format_graph_view(view)
        assert "Knowledge Graph:" in result
        assert "Entities:" in result
        assert "Relations:" in result
        # Full details — every entity listed
        assert "Product" in result
        assert "Cache" in result
        assert "Logger" in result


class TestSystemPromptConfig:
    """Story 1.4 Task 4.3/4.5: prompt config passed through to summary."""

    def test_prompt_schema_disabled(self) -> None:
        tool = KnowledgeGraphTool(
            get_graph=GetGraph(prompt_include_schema=False)
        )
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(
                        name="X", entity_type="T", description="d", is_root=True
                    ),
                ]
            )
        )
        tool.observer(observer)

        result = tool.get_system_prompts()[0]()
        assert "Entity types:" not in result
        assert "Entities: 1" in result

    def test_prompt_roots_disabled(self) -> None:
        tool = KnowledgeGraphTool(
            get_graph=GetGraph(prompt_include_roots=False)
        )
        observer = MockActorToolObserver()
        actor = observer.setup_kg_actor()
        actor.update_graph(
            ManageGraph(
                create_entities=[
                    EntityCreate(
                        name="X", entity_type="T", description="d", is_root=True
                    ),
                ]
            )
        )
        tool.observer(observer)

        result = tool.get_system_prompts()[0]()
        assert "Root entities:" not in result
        assert "Entity types:" in result


# ===========================================================================
# Story 10-10 — KnowledgeGraphTool.collection field + observer propagation
# ===========================================================================


class TestKnowledgeGraphToolCollectionField:
    """AC-1: KnowledgeGraphTool.collection is a CollectionConfig field."""

    def test_default_collection_is_default_collection_config(self) -> None:
        """Default ``collection`` matches a freshly-constructed ``CollectionConfig()``."""
        tool = KnowledgeGraphTool()
        assert isinstance(tool.collection, CollectionConfig)
        assert tool.collection == CollectionConfig()
        # Default values explicitly (guards against AC-11 regressions).
        assert tool.collection.dimension == 1536
        assert tool.collection.backend == "inmemory"
        assert tool.collection.persistence == "actor_state"
        assert tool.collection.workspace_path is None
        assert tool.collection.tenant is None

    def test_collection_field_present_in_model_fields(self) -> None:
        assert "collection" in KnowledgeGraphTool.model_fields

    def test_collection_appears_in_model_dump(self) -> None:
        dump = KnowledgeGraphTool().model_dump()
        assert "collection" in dump

    def test_custom_collection_stored_on_instance(self) -> None:
        custom = CollectionConfig(backend="weaviate", tenant="team-42")
        tool = KnowledgeGraphTool(collection=custom)
        assert tool.collection is custom
        assert tool.collection.backend == "weaviate"
        assert tool.collection.tenant == "team-42"

    def test_collection_roundtrip_default(self) -> None:
        tool = KnowledgeGraphTool()
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.collection == CollectionConfig()

    def test_collection_roundtrip_custom(self) -> None:
        tool = KnowledgeGraphTool(
            collection=CollectionConfig(backend="weaviate", tenant="team-123")
        )
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.collection.backend == "weaviate"
        assert reloaded.collection.tenant == "team-123"
        # Non-touched fields preserved at CollectionConfig defaults.
        assert reloaded.collection.dimension == 1536
        assert reloaded.collection.persistence == "actor_state"

    def test_independent_tools_do_not_alias_collection(self) -> None:
        """`default_factory=CollectionConfig` gives each instance a fresh object."""
        a = KnowledgeGraphTool()
        b = KnowledgeGraphTool()
        assert a.collection is not b.collection


class TestKnowledgeGraphToolObserverCollection:
    """AC-4: observer() propagates the exact ``collection`` into ``KnowledgeGraphConfig``."""

    def _run_observer(
        self, tool: KnowledgeGraphTool
    ) -> list[KnowledgeGraphConfig]:
        captured: list[KnowledgeGraphConfig] = []
        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, KnowledgeGraphConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy
        tool.observer(mock_observer)
        return captured

    def test_observer_propagates_custom_collection_identity(self) -> None:
        """The exact CollectionConfig object on the ToolCard reaches the config."""
        custom = CollectionConfig(backend="weaviate", tenant="t1")
        tool = KnowledgeGraphTool(collection=custom)

        captured = self._run_observer(tool)

        assert len(captured) == 1
        # Identity match — same object, no copy/reconstruction.
        assert captured[0].collection is custom
        # And vector_store (10-9 invariant) still propagates.
        assert captured[0].vector_store is True

    def test_observer_propagates_default_collection_structurally_equal(self) -> None:
        """Default ``KnowledgeGraphTool()`` propagates a CollectionConfig() to the config.

        Verifies the AC-11 backward-compatibility guarantee: the value reaching
        ``_acquire_vs_proxy`` via ``self.config.collection`` is structurally
        identical to the historical hardcoded ``CollectionConfig()``.
        """
        tool = KnowledgeGraphTool()  # default collection

        captured = self._run_observer(tool)

        assert len(captured) == 1
        assert captured[0].collection == CollectionConfig()

    def test_observer_does_not_mutate_tool_collection(self) -> None:
        """observer() passes collection through without mutating the ToolCard."""
        custom = CollectionConfig(backend="weaviate", tenant="zz")
        tool = KnowledgeGraphTool(collection=custom)
        before_dump = tool.collection.model_dump()

        self._run_observer(tool)

        assert tool.collection.model_dump() == before_dump


# ---------------------------------------------------------------------------
# Story 10-11 — conditional depends_on property
# ---------------------------------------------------------------------------


class TestKnowledgeGraphToolDependsOnProperty:
    """AC-3, AC-8: depends_on is a conditional @property, not serialised."""

    def test_default_depends_on_vector_store_tool(self) -> None:
        """Default (vector_store=True) depends on VectorStoreTool."""
        assert KnowledgeGraphTool().depends_on == ["VectorStoreTool"]

    def test_vector_store_true_depends_on_vector_store_tool(self) -> None:
        assert KnowledgeGraphTool(vector_store=True).depends_on == ["VectorStoreTool"]

    def test_vector_store_str_depends_on_vector_store_tool(self) -> None:
        assert KnowledgeGraphTool(vector_store="#VectorStore-RAG").depends_on == ["VectorStoreTool"]

    def test_vector_store_false_no_dependency(self) -> None:
        assert KnowledgeGraphTool(vector_store=False).depends_on == []

    def test_depends_on_not_in_model_fields(self) -> None:
        """depends_on is a @property, not a Pydantic field."""
        assert "depends_on" not in KnowledgeGraphTool.model_fields

    def test_depends_on_not_in_model_dump(self) -> None:
        """depends_on never appears in serialised output."""
        tool_false = KnowledgeGraphTool(vector_store=False)
        tool_true = KnowledgeGraphTool(vector_store=True)
        assert "depends_on" not in tool_false.model_dump()
        assert "depends_on" not in tool_true.model_dump()
        assert "depends_on" not in tool_false.model_dump(mode="json")
        assert "depends_on" not in tool_true.model_dump(mode="json")

    def test_round_trip_preserves_depends_on_semantics(self) -> None:
        """Round-trip via model_validate reconstructs conditional depends_on."""
        tool = KnowledgeGraphTool(vector_store=False)
        dump = tool.model_dump()
        reconstructed = KnowledgeGraphTool.model_validate(dump)
        assert reconstructed.depends_on == []
        assert reconstructed.vector_store is False

        tool_true = KnowledgeGraphTool(vector_store=True)
        dump_true = tool_true.model_dump()
        reconstructed_true = KnowledgeGraphTool.model_validate(dump_true)
        assert reconstructed_true.depends_on == ["VectorStoreTool"]


# ===========================================================================
# Story 10-13 — search_top_k, search_score_threshold fields + propagation
# ===========================================================================


class TestKnowledgeGraphToolSearchFields:
    """AC-1: KnowledgeGraphTool has search_top_k and search_score_threshold fields."""

    def test_default_search_top_k(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.search_top_k == 10

    def test_default_search_score_threshold(self) -> None:
        tool = KnowledgeGraphTool()
        assert tool.search_score_threshold == 0.3

    def test_search_top_k_field_in_model_fields(self) -> None:
        assert "search_top_k" in KnowledgeGraphTool.model_fields

    def test_search_score_threshold_field_in_model_fields(self) -> None:
        assert "search_score_threshold" in KnowledgeGraphTool.model_fields

    def test_custom_search_top_k(self) -> None:
        tool = KnowledgeGraphTool(search_top_k=15)
        assert tool.search_top_k == 15

    def test_custom_search_score_threshold(self) -> None:
        tool = KnowledgeGraphTool(search_score_threshold=0.4)
        assert tool.search_score_threshold == 0.4

    def test_roundtrip_with_defaults(self) -> None:
        """AC-6: default values round-trip cleanly."""
        tool = KnowledgeGraphTool()
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.search_top_k == 10
        assert reloaded.search_score_threshold == 0.3

    def test_roundtrip_with_custom_values(self) -> None:
        """AC-5: catalog YAML config values round-trip cleanly."""
        tool = KnowledgeGraphTool(search_top_k=15, search_score_threshold=0.4)
        reloaded = KnowledgeGraphTool.model_validate(tool.model_dump())
        assert reloaded.search_top_k == 15
        assert reloaded.search_score_threshold == 0.4

    def test_appears_in_model_dump(self) -> None:
        dump = KnowledgeGraphTool().model_dump()
        assert "search_top_k" in dump
        assert "search_score_threshold" in dump
        assert dump["search_top_k"] == 10
        assert dump["search_score_threshold"] == 0.3


class TestKnowledgeGraphToolObserverSearchFields:
    """AC-2: observer() propagates search_top_k and search_score_threshold."""

    def _run_observer(
        self, tool: KnowledgeGraphTool,
    ) -> list[KnowledgeGraphConfig]:
        from unittest.mock import MagicMock

        captured: list[KnowledgeGraphConfig] = []
        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, KnowledgeGraphConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy
        tool.observer(mock_observer)
        return captured

    def test_observer_propagates_default_search_fields(self) -> None:
        tool = KnowledgeGraphTool()
        captured = self._run_observer(tool)
        assert len(captured) == 1
        assert captured[0].search_top_k == 10
        assert captured[0].search_score_threshold == 0.3

    def test_observer_propagates_custom_search_fields(self) -> None:
        tool = KnowledgeGraphTool(search_top_k=15, search_score_threshold=0.4)
        captured = self._run_observer(tool)
        assert len(captured) == 1
        assert captured[0].search_top_k == 15
        assert captured[0].search_score_threshold == 0.4


