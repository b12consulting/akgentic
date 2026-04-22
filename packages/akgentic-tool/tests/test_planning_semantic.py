"""Tests for PlanActor.get_planning_task — integer ID lookup path.

Covers AC#1 (int found) and AC#2 (int not found).
Also covers AC#8–#9 (PlanningTool embedding field wiring).
Story 10-9: adds PlanningTool depends_on + vector_store field coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from akgentic.tool.planning.planning_actor import (
    PlanActor,
    PlanConfig,
    Task,
)
from tests.conftest import MockActorAddress


def _make_actor(vector_store: bool = False) -> PlanActor:
    """Construct a bare PlanActor with no Pykka runtime — calls on_start directly."""
    actor = PlanActor()
    actor.config = PlanConfig(
        name="test-plan",
        role="ToolActor",
        vector_store=vector_store,
    )
    actor.on_start()
    return actor


def _add_task(actor: PlanActor, task_id: int, description: str) -> Task:
    """Helper: add a task to actor state without triggering embedding.

    Directly appends to task_list so tests can control embedding lifecycle
    independently.
    """
    addr = MockActorAddress()
    actor.state.task_list.append(
        Task(
            id=task_id,
            status="pending",
            description=description,
            owner="Alice",
            creator=addr.name,
        )
    )
    return actor.state.task_list[-1]


# ---------------------------------------------------------------------------
# AC#1 — int lookup: task found (no embedding)
# ---------------------------------------------------------------------------


class TestGetPlanningTaskIntFound:
    """AC1: int task_id → exact match returned, no embedding call."""

    def test_returns_task_when_id_matches(self) -> None:
        actor = _make_actor()
        _add_task(actor, task_id=3, description="Auth module")

        result = actor.get_planning_task(3)

        assert isinstance(result, Task)
        assert result.id == 3
        assert result.description == "Auth module"


# ---------------------------------------------------------------------------
# AC#2 — int lookup: task not found
# ---------------------------------------------------------------------------


class TestGetPlanningTaskIntNotFound:
    """AC2: int task_id with no matching task → 'No task with that ID.' string."""

    def test_returns_not_found_string_when_id_missing(self) -> None:
        actor = _make_actor()
        _add_task(actor, task_id=1, description="Some task")

        result = actor.get_planning_task(99)

        assert result == "No task with that ID."


# ---------------------------------------------------------------------------
# Story 10-9 — PlanningTool.observer() no longer creates VectorStoreActor
# ---------------------------------------------------------------------------


class TestPlanningToolObserverWiring:
    """Story 10-9 AC-5: observer() creates ONLY PlanActor and propagates vector_store."""

    def test_observer_creates_only_plan_actor(self) -> None:
        """observer() no longer creates VectorStoreActor — VectorStoreTool owns that."""
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.actor import VectorStoreActor

        tool = PlanningTool()

        # Capture configs passed to getChildrenOrCreate calls
        captured_configs: list[object] = []
        captured_classes: list[type] = []

        mock_proxy_ask = MagicMock()

        def capture_get_children_or_create(
            actor_cls: type, config: object = None,
        ) -> MagicMock:
            captured_classes.append(actor_cls)
            captured_configs.append(config)
            return MagicMock()

        mock_proxy_ask.getChildrenOrCreate.side_effect = capture_get_children_or_create

        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy_ask

        tool.observer(mock_observer)

        # Only ONE actor created: PlanActor. VectorStoreTool owns VectorStoreActor.
        assert len(captured_configs) == 1
        assert VectorStoreActor not in captured_classes

        # PlanConfig carries the default vector_store=True
        plan_config = captured_configs[0]
        assert isinstance(plan_config, PlanConfig)
        assert plan_config.vector_store is True
        # No embedding fields leaked onto PlanConfig (centralised on VectorStoreConfig).
        assert "embedding_model" not in PlanConfig.model_fields


# ---------------------------------------------------------------------------
# Story 10-9 AC-2 — PlanningTool depends_on + vector_store field
# ---------------------------------------------------------------------------


class TestPlanningToolDependsOn:
    """AC-2: PlanningTool declares depends_on and a vector_store field."""

    def test_depends_on_is_vector_store_tool(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert PlanningTool().depends_on == ["VectorStoreTool"]

    def test_depends_on_not_a_pydantic_field(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert "depends_on" not in PlanningTool.model_fields

    def test_depends_on_not_in_model_dump(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert "depends_on" not in PlanningTool().model_dump()

    def test_vector_store_field_default_true(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        assert tool.vector_store is True
        assert "vector_store" in PlanningTool.model_fields

    def test_vector_store_appears_in_model_dump(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        dump = PlanningTool().model_dump()
        assert "vector_store" in dump
        assert dump["vector_store"] is True

    def test_vector_store_roundtrip_true(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(vector_store=True)
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.vector_store is True

    def test_vector_store_roundtrip_false(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(vector_store=False)
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.vector_store is False

    def test_vector_store_roundtrip_string(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(vector_store="#VectorStore-RAG")
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.vector_store == "#VectorStore-RAG"


class TestPlanningToolObserverNoVsCreation:
    """AC-5: observer() does not create VectorStoreActor and propagates vector_store."""

    def _run_observer(self, vector_store_value: object) -> list[object]:
        """Run observer with the given vector_store value and return captured configs."""
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.actor import VectorStoreActor

        tool = PlanningTool(vector_store=vector_store_value)  # type: ignore[arg-type]
        captured_configs: list[object] = []
        captured_classes: list[type] = []

        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            captured_classes.append(actor_cls)
            captured_configs.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy

        tool.observer(mock_observer)

        # Never creates VectorStoreActor
        assert VectorStoreActor not in captured_classes
        return captured_configs

    def test_observer_propagates_vector_store_true(self) -> None:
        configs = self._run_observer(True)
        assert len(configs) == 1
        assert isinstance(configs[0], PlanConfig)
        assert configs[0].vector_store is True

    def test_observer_propagates_vector_store_false(self) -> None:
        configs = self._run_observer(False)
        assert len(configs) == 1
        assert isinstance(configs[0], PlanConfig)
        assert configs[0].vector_store is False

    def test_observer_propagates_vector_store_named_string(self) -> None:
        configs = self._run_observer("#VectorStore-RAG")
        assert len(configs) == 1
        assert isinstance(configs[0], PlanConfig)
        assert configs[0].vector_store == "#VectorStore-RAG"

    def test_observer_raises_when_no_orchestrator(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool()
        mock_observer = MagicMock()
        mock_observer.orchestrator = None
        with pytest.raises(ValueError, match="orchestrator"):
            tool.observer(mock_observer)


# ---------------------------------------------------------------------------
# Story 10-10 — PlanningTool.collection field + observer propagation
# ---------------------------------------------------------------------------


class TestPlanningToolCollectionField:
    """AC-2: PlanningTool.collection is a CollectionConfig field."""

    def test_default_collection_is_default_collection_config(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        tool = PlanningTool()
        assert isinstance(tool.collection, CollectionConfig)
        assert tool.collection == CollectionConfig()
        assert tool.collection.dimension == 1536
        assert tool.collection.backend == "inmemory"
        assert tool.collection.persistence == "actor_state"
        assert tool.collection.workspace_path is None
        assert tool.collection.tenant is None

    def test_collection_field_present_in_model_fields(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert "collection" in PlanningTool.model_fields

    def test_collection_appears_in_model_dump(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        dump = PlanningTool().model_dump()
        assert "collection" in dump

    def test_custom_collection_stored_on_instance(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        custom = CollectionConfig(
            backend="inmemory", persistence="workspace", workspace_path="/tmp/plan"
        )
        tool = PlanningTool(collection=custom)
        assert tool.collection is custom
        assert tool.collection.persistence == "workspace"
        assert tool.collection.workspace_path == "/tmp/plan"

    def test_collection_roundtrip_default(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        tool = PlanningTool()
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.collection == CollectionConfig()

    def test_collection_roundtrip_custom(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        tool = PlanningTool(
            collection=CollectionConfig(
                backend="inmemory",
                persistence="workspace",
                workspace_path="/tmp/plan",
            )
        )
        reloaded = PlanningTool.model_validate(tool.model_dump())
        assert reloaded.collection.backend == "inmemory"
        assert reloaded.collection.persistence == "workspace"
        assert reloaded.collection.workspace_path == "/tmp/plan"
        assert reloaded.collection.dimension == 1536  # default preserved
        assert reloaded.collection.tenant is None  # default preserved

    def test_independent_tools_do_not_alias_collection(self) -> None:
        """`default_factory=CollectionConfig` gives each instance a fresh object."""
        from akgentic.tool.planning.planning import PlanningTool

        a = PlanningTool()
        b = PlanningTool()
        assert a.collection is not b.collection


class TestPlanningToolObserverCollection:
    """AC-5: observer() propagates ``collection`` identity into ``PlanConfig``."""

    def _run_observer(self, tool: object) -> list[PlanConfig]:
        captured: list[PlanConfig] = []
        mock_proxy = MagicMock()

        def capture(actor_cls: type, config: object = None) -> MagicMock:
            assert isinstance(config, PlanConfig)
            captured.append(config)
            return MagicMock()

        mock_proxy.getChildrenOrCreate.side_effect = capture
        mock_observer = MagicMock()
        mock_observer.orchestrator = MagicMock()
        mock_observer.proxy_ask.return_value = mock_proxy
        tool.observer(mock_observer)  # type: ignore[attr-defined]
        return captured

    def test_observer_propagates_custom_collection_identity(self) -> None:
        """The exact CollectionConfig object on the ToolCard reaches the config."""
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        custom = CollectionConfig(
            backend="inmemory", persistence="workspace", workspace_path="/tmp/plan"
        )
        tool = PlanningTool(collection=custom)

        captured = self._run_observer(tool)

        assert len(captured) == 1
        assert captured[0].collection is custom
        # 10-9 invariant preserved.
        assert captured[0].vector_store is True

    def test_observer_propagates_default_collection_structurally_equal(self) -> None:
        """AC-11 backward-compat: default tool → config.collection == CollectionConfig()."""
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        tool = PlanningTool()

        captured = self._run_observer(tool)

        assert len(captured) == 1
        assert captured[0].collection == CollectionConfig()

    def test_observer_does_not_mutate_tool_collection(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool
        from akgentic.tool.vector_store.protocol import CollectionConfig

        custom = CollectionConfig(
            backend="inmemory", persistence="workspace", workspace_path="/tmp/x"
        )
        tool = PlanningTool(collection=custom)
        before_dump = tool.collection.model_dump()

        self._run_observer(tool)

        assert tool.collection.model_dump() == before_dump


# ---------------------------------------------------------------------------
# Story 10-11 — conditional depends_on property
# ---------------------------------------------------------------------------


class TestPlanningToolDependsOnProperty:
    """AC-4, AC-8: depends_on is a conditional @property, not serialised."""

    def test_default_depends_on_vector_store_tool(self) -> None:
        """Default (vector_store=True) depends on VectorStoreTool."""
        from akgentic.tool.planning.planning import PlanningTool

        assert PlanningTool().depends_on == ["VectorStoreTool"]

    def test_vector_store_true_depends_on_vector_store_tool(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert PlanningTool(vector_store=True).depends_on == ["VectorStoreTool"]

    def test_vector_store_str_depends_on_vector_store_tool(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert PlanningTool(vector_store="#VectorStore-RAG").depends_on == ["VectorStoreTool"]

    def test_vector_store_false_no_dependency(self) -> None:
        from akgentic.tool.planning.planning import PlanningTool

        assert PlanningTool(vector_store=False).depends_on == []

    def test_depends_on_not_in_model_fields(self) -> None:
        """depends_on is a @property, not a Pydantic field."""
        from akgentic.tool.planning.planning import PlanningTool

        assert "depends_on" not in PlanningTool.model_fields

    def test_depends_on_not_in_model_dump(self) -> None:
        """depends_on never appears in serialised output."""
        from akgentic.tool.planning.planning import PlanningTool

        tool_false = PlanningTool(vector_store=False)
        tool_true = PlanningTool(vector_store=True)
        assert "depends_on" not in tool_false.model_dump()
        assert "depends_on" not in tool_true.model_dump()
        assert "depends_on" not in tool_false.model_dump(mode="json")
        assert "depends_on" not in tool_true.model_dump(mode="json")

    def test_round_trip_preserves_depends_on_semantics(self) -> None:
        """Round-trip via model_validate reconstructs conditional depends_on."""
        from akgentic.tool.planning.planning import PlanningTool

        tool = PlanningTool(vector_store=False)
        dump = tool.model_dump()
        reconstructed = PlanningTool.model_validate(dump)
        assert reconstructed.depends_on == []
        assert reconstructed.vector_store is False

        tool_true = PlanningTool(vector_store=True)
        dump_true = tool_true.model_dump()
        reconstructed_true = PlanningTool.model_validate(dump_true)
        assert reconstructed_true.depends_on == ["VectorStoreTool"]


