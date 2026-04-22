"""Tests for PlanActor error handling and update operations."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from akgentic.tool.errors import RetriableError
from akgentic.tool.planning.planning_actor import (
    PlanActor,
    TaskCreate,
    TaskUpdate,
    UpdatePlan,
)
from tests.conftest import MockActorAddress


def test_update_item_success() -> None:
    """Test successful item update returns None."""
    actor = PlanActor()
    actor.on_start()

    # Create an item
    create_req = TaskCreate(
        id=1, status="pending", description="Task 1", owner="Alice", dependencies=[]
    )
    actor_addr = MockActorAddress("test-agent")
    actor._create_task(create_req, actor_addr)

    # Update the item
    update_req = TaskUpdate(id=1, status="started", description="Updated Task 1")
    result = actor._update_task(update_req)

    # Should return None on success
    assert result is None

    # Verify the update was applied
    items = actor.get_planning()
    assert len(items) == 1
    assert items[0].status == "started"
    assert items[0].description == "Updated Task 1"


def test_update_item_not_found() -> None:
    """Test updating non-existent item returns error message."""
    actor = PlanActor()
    actor.on_start()

    # Try to update non-existent item
    update_req = TaskUpdate(id=999, status="started")
    result = actor._update_task(update_req)

    # Should return error message
    assert result is not None
    assert "Update error" in result
    assert "999" in result
    assert "found" in result.lower()


def test_update_planning_with_update_errors() -> None:
    """Test update_planning collects errors from failed updates."""
    actor = PlanActor()
    actor.on_start()
    actor_addr = MockActorAddress("test-agent")

    # Create one item
    create_req = TaskCreate(
        id=1, status="pending", description="Task 1", owner="Alice", dependencies=[]
    )
    actor._create_task(create_req, actor_addr)

    # Try to update both existing and non-existent items
    update_plan = UpdatePlan(
        create_tasks=[],
        update_tasks=[
            TaskUpdate(id=1, status="started"),  # Should succeed
            TaskUpdate(id=999, status="completed"),  # Should fail
        ],
        delete_tasks=[],
    )

    with pytest.raises(RetriableError, match="Update error.*999"):
        actor.update_planning(update_plan, actor_addr)


def test_update_planning_delete_not_found() -> None:
    """Test deleting non-existent item raises RetriableError."""
    actor = PlanActor()
    actor.on_start()
    actor_addr = MockActorAddress("test-agent")

    # Try to delete non-existent item
    update_plan = UpdatePlan(
        create_tasks=[],
        update_tasks=[],
        delete_tasks=[999],
    )

    with pytest.raises(RetriableError, match="Delete error.*999"):
        actor.update_planning(update_plan, actor_addr)


def test_update_planning_delete_success() -> None:
    """Test successful deletion."""
    actor = PlanActor()
    actor.on_start()
    actor_addr = MockActorAddress("test-agent")

    # Create an item
    create_req = TaskCreate(
        id=1, status="pending", description="Task 1", owner="Alice", dependencies=[]
    )
    actor._create_task(create_req, actor_addr)

    # Delete the item
    update_plan = UpdatePlan(
        create_tasks=[],
        update_tasks=[],
        delete_tasks=[1],
    )

    result = actor.update_planning(update_plan, actor_addr)

    # Should succeed without errors
    assert result == "Done"
    assert len(actor.get_planning()) == 0


def test_update_planning_mixed_operations_with_errors() -> None:
    """Test mixed create/update/delete operations with some errors."""
    actor = PlanActor()
    actor.on_start()
    actor_addr = MockActorAddress("test-agent")

    # Create initial item
    create_req = TaskCreate(
        id=1, status="pending", description="Task 1", owner="Alice", dependencies=[]
    )
    actor._create_task(create_req, actor_addr)

    # Mixed operations with some failures
    update_plan = UpdatePlan(
        create_tasks=[
            TaskCreate(id=2, status="pending", description="Task 2", owner="Bob", dependencies=[])
        ],
        update_tasks=[
            TaskUpdate(id=1, status="completed"),  # Should succeed
            TaskUpdate(id=999, status="started"),  # Should fail
        ],
        delete_tasks=[1, 888],  # First succeeds, second fails
    )

    with pytest.raises(RetriableError) as exc_info:
        actor.update_planning(update_plan, actor_addr)

    # Should report multiple errors
    error_msg = str(exc_info.value)
    assert "Update error" in error_msg or "Delete error" in error_msg

    # Verify operations that should succeed (committed before the raise)
    items = actor.get_planning()
    assert len(items) == 1  # Item 2 created, item 1 deleted
    assert items[0].id == 2
    assert items[0].description == "Task 2"


def test_update_planning_all_success() -> None:
    """Test update_planning returns 'Done' when no errors."""
    actor = PlanActor()
    actor.on_start()
    actor_addr = MockActorAddress("test-agent")

    # Create initial item
    create_req = TaskCreate(
        id=1, status="pending", description="Task 1", owner="Alice", dependencies=[]
    )
    actor._create_task(create_req, actor_addr)

    # All operations should succeed
    update_plan = UpdatePlan(
        create_tasks=[
            TaskCreate(id=2, status="pending", description="Task 2", owner="Bob", dependencies=[])
        ],
        update_tasks=[TaskUpdate(id=1, status="completed")],
        delete_tasks=[],
    )

    result = actor.update_planning(update_plan, actor_addr)

    # Should return clean "Done"
    assert result == "Done"

    # Verify final state
    items = actor.get_planning()
    assert len(items) == 2
    assert items[0].status == "completed"
    assert items[1].id == 2


# ---------------------------------------------------------------------------
# Field length constraints
# ---------------------------------------------------------------------------


class TestFieldLengthConstraints:
    """Tests for max_length constraints on TaskCreate and TaskUpdate."""

    def test_task_create_description_at_limit_accepted(self) -> None:
        """description of exactly 300 chars is valid."""
        t = TaskCreate(id=1, status="pending", description="x" * 300, owner="Alice")
        assert len(t.description) == 300

    def test_task_create_description_over_limit_raises(self) -> None:
        """description > 300 chars raises ValidationError."""
        with pytest.raises(ValidationError, match="300"):
            TaskCreate(id=1, status="pending", description="x" * 301, owner="Alice")

    def test_task_update_description_at_limit_accepted(self) -> None:
        """TaskUpdate description of exactly 300 chars is valid."""
        t = TaskUpdate(id=1, description="y" * 300)
        assert t.description is not None
        assert len(t.description) == 300

    def test_task_update_description_over_limit_raises(self) -> None:
        """TaskUpdate description > 300 chars raises ValidationError."""
        with pytest.raises(ValidationError, match="300"):
            TaskUpdate(id=1, description="y" * 301)

    def test_task_update_output_at_limit_accepted(self) -> None:
        """output of exactly 150 chars is stored as-is."""
        t = TaskUpdate(id=1, output="z" * 150)
        assert t.output == "z" * 150

    def test_task_update_output_truncated_at_151(self) -> None:
        """output of 151 chars is truncated to 150 chars (147 + '...')."""
        t = TaskUpdate(id=1, output="a" * 151)
        assert t.output is not None
        assert len(t.output) == 150
        assert t.output.endswith("...")

    def test_task_update_output_long_string_truncated(self) -> None:
        """output of 500 chars is truncated to 150 chars ending with '...'."""
        t = TaskUpdate(id=1, output="b" * 500)
        assert t.output is not None
        assert len(t.output) == 150
        assert t.output.endswith("...")
        assert t.output.startswith("b" * 147)

    def test_task_update_output_none_unchanged(self) -> None:
        """None output passes through the validator unchanged."""
        t = TaskUpdate(id=1, output=None)
        assert t.output is None

    def test_task_update_output_short_string_unchanged(self) -> None:
        """output shorter than 150 chars is not modified."""
        t = TaskUpdate(id=1, output="short result")
        assert t.output == "short result"

    def test_field_descriptions_mention_max_length(self) -> None:
        """Field descriptions must state the char limit so LLMs see it in the schema."""
        schema = TaskCreate.model_json_schema()
        desc_field = schema["properties"]["description"]
        assert "300" in desc_field.get("description", "")

        update_schema = TaskUpdate.model_json_schema()
        assert "300" in update_schema["properties"]["description"].get("description", "")
        assert "150" in update_schema["properties"]["output"].get("description", "")


# ---------------------------------------------------------------------------
# Story 10-9 — PlanConfig.vector_store + PlanActor._acquire_vs_proxy
# ---------------------------------------------------------------------------


class TestPlanConfigVectorStoreField:
    """AC-3: PlanConfig carries a fully-serialisable vector_store field."""

    def test_vector_store_default_true(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig

        cfg = PlanConfig(name="#PlanningTool", role="ToolActor")
        assert cfg.vector_store is True

    def test_vector_store_accepts_false(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig

        cfg = PlanConfig(name="#PlanningTool", role="ToolActor", vector_store=False)
        assert cfg.vector_store is False

    def test_vector_store_accepts_string(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig

        cfg = PlanConfig(
            name="#PlanningTool", role="ToolActor", vector_store="#VectorStore-RAG"
        )
        assert cfg.vector_store == "#VectorStore-RAG"

    def test_vector_store_roundtrip(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig

        for value in (True, False, "#VectorStore-RAG"):
            cfg = PlanConfig(
                name="#PlanningTool", role="ToolActor", vector_store=value
            )
            reloaded = PlanConfig.model_validate(cfg.model_dump())
            assert reloaded.vector_store == value


def _plan_actor_with_orchestrator(
    vector_store_value: object = True,
) -> tuple[PlanActor, MagicMock, MockActorAddress]:
    """Build a PlanActor with a stubbed orchestrator + proxy_ask recorder.

    Does NOT call on_start (which would run _acquire_vs_proxy). Caller
    invokes ``_acquire_vs_proxy`` directly.
    """
    from akgentic.tool.planning.planning_actor import PlanConfig, PlanManagerState

    actor = PlanActor()
    actor.config = PlanConfig(
        name="#PlanningTool",
        role="ToolActor",
        vector_store=vector_store_value,  # type: ignore[arg-type]
    )
    actor.state = PlanManagerState()
    actor.state.observer(actor)
    actor._vs_proxy = None

    orch_addr = MockActorAddress("orchestrator", "Orchestrator")
    actor._orchestrator = orch_addr  # type: ignore[assignment]

    orch_proxy = MagicMock()

    def _proxy_ask(target: object, actor_type: type | None = None,
                   timeout: int | None = None) -> object:
        if target is orch_addr:
            return orch_proxy
        return MagicMock()

    actor.proxy_ask = _proxy_ask  # type: ignore[method-assign,assignment]
    return actor, orch_proxy, orch_addr


class TestPlanActorAcquireVsProxy:
    """AC-8: PlanActor._acquire_vs_proxy mirrors the KG actor behaviour."""

    def test_uses_get_team_member_not_get_children_or_create(self) -> None:
        from akgentic.tool.vector_store.actor import VS_ACTOR_NAME

        actor, orch_proxy, _ = _plan_actor_with_orchestrator(vector_store_value=True)
        orch_proxy.get_team_member.return_value = MockActorAddress(VS_ACTOR_NAME, "ToolActor")

        actor._acquire_vs_proxy()

        orch_proxy.get_team_member.assert_called_once_with(VS_ACTOR_NAME)
        orch_proxy.getChildrenOrCreate.assert_not_called()
        assert actor._vs_proxy is not None

    def test_named_instance(self) -> None:
        named = "#VectorStore-RAG"
        actor, orch_proxy, _ = _plan_actor_with_orchestrator(vector_store_value=named)
        orch_proxy.get_team_member.return_value = MockActorAddress(named, "ToolActor")

        actor._acquire_vs_proxy()

        orch_proxy.get_team_member.assert_called_once_with(named)
        assert actor._vs_proxy is not None

    def test_false_skips_all_wiring(self) -> None:
        actor, orch_proxy, _ = _plan_actor_with_orchestrator(vector_store_value=False)

        actor._acquire_vs_proxy()

        assert actor._vs_proxy is None
        orch_proxy.get_team_member.assert_not_called()
        orch_proxy.getChildrenOrCreate.assert_not_called()

    def test_missing_raises_runtime_error(self) -> None:
        from akgentic.tool.vector_store.actor import VS_ACTOR_NAME

        actor, orch_proxy, _ = _plan_actor_with_orchestrator(vector_store_value=True)
        orch_proxy.get_team_member.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            actor._acquire_vs_proxy()

        msg = str(exc_info.value)
        assert "#PlanningTool" in msg
        assert VS_ACTOR_NAME in msg
        assert "VectorStoreTool" in msg
        assert actor._vs_proxy is None

    def test_no_orchestrator_degraded_mode(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig, PlanManagerState

        actor = PlanActor()
        actor.config = PlanConfig(
            name="#PlanningTool", role="ToolActor", vector_store=True
        )
        actor.state = PlanManagerState()
        actor.state.observer(actor)
        actor._vs_proxy = None
        actor._orchestrator = None  # type: ignore[assignment]

        actor._acquire_vs_proxy()

        assert actor._vs_proxy is None

    def test_vector_store_false_short_circuits_acquire(self) -> None:
        """vector_store=False short-circuits _acquire_vs_proxy in on_start.

        When ``vector_store=False`` the ``on_start`` guard must prevent
        ``_acquire_vs_proxy`` from running, so no orchestrator lookup occurs.
        """
        from akgentic.tool.planning.planning_actor import PlanConfig

        actor = PlanActor()
        actor.config = PlanConfig(
            name="#PlanningTool",
            role="ToolActor",
            vector_store=False,
        )
        actor.on_start()
        # _acquire_vs_proxy not invoked → _vs_proxy stays None, no RuntimeError
        assert actor._vs_proxy is None


# ---------------------------------------------------------------------------
# Story 10-10 — PlanConfig.collection + PlanActor._acquire_vs_proxy identity
# ---------------------------------------------------------------------------


class TestPlanConfigCollectionField:
    """AC-3: PlanConfig carries a fully-serialisable collection field."""

    def test_collection_default_is_default_collection_config(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig
        from akgentic.tool.vector_store.protocol import CollectionConfig

        cfg = PlanConfig(name="#PlanningTool", role="ToolActor")
        assert cfg.collection == CollectionConfig()
        # Structural defaults — AC-11 backward-compat guard.
        assert cfg.collection.dimension == 1536
        assert cfg.collection.backend == "inmemory"
        assert cfg.collection.persistence == "actor_state"
        assert cfg.collection.workspace_path is None
        assert cfg.collection.tenant is None

    def test_collection_accepts_custom_value(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig
        from akgentic.tool.vector_store.protocol import CollectionConfig

        cfg = PlanConfig(
            name="#PlanningTool",
            role="ToolActor",
            collection=CollectionConfig(
                backend="inmemory",
                persistence="workspace",
                workspace_path="/tmp/plan",
            ),
        )
        assert cfg.collection.persistence == "workspace"
        assert cfg.collection.workspace_path == "/tmp/plan"

    def test_collection_roundtrip_default(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig
        from akgentic.tool.vector_store.protocol import CollectionConfig

        cfg = PlanConfig(name="#PlanningTool", role="ToolActor")
        reloaded = PlanConfig.model_validate(cfg.model_dump())
        assert reloaded.collection == CollectionConfig()

    def test_collection_roundtrip_custom(self) -> None:
        from akgentic.tool.planning.planning_actor import PlanConfig
        from akgentic.tool.vector_store.protocol import CollectionConfig

        cfg = PlanConfig(
            name="#PlanningTool",
            role="ToolActor",
            collection=CollectionConfig(
                backend="inmemory",
                persistence="workspace",
                workspace_path="/tmp/plan",
            ),
        )
        reloaded = PlanConfig.model_validate(cfg.model_dump())
        assert reloaded.collection.backend == "inmemory"
        assert reloaded.collection.persistence == "workspace"
        assert reloaded.collection.workspace_path == "/tmp/plan"
        assert reloaded.collection.dimension == 1536  # default preserved

    def test_base_config_coercion_yields_default_collection(self) -> None:
        """AC-8: BaseConfig → PlanConfig coercion keeps default collection + vector_store."""
        from akgentic.core.agent_config import BaseConfig

        from akgentic.tool.planning.planning_actor import (
            PlanActor,
            PlanConfig,
            PlanManagerState,
        )
        from akgentic.tool.vector_store.protocol import CollectionConfig

        actor = PlanActor()
        actor.config = BaseConfig(  # type: ignore[assignment]
            name="#PlanningTool", role="ToolActor"
        )
        actor.state = PlanManagerState()
        actor.state.observer(actor)
        actor._vs_proxy = None
        actor._orchestrator = None  # type: ignore[assignment]

        # Exercise the coercion block from on_start.
        if not isinstance(actor.config, PlanConfig):
            actor.config = PlanConfig(
                name=actor.config.name,
                role=actor.config.role,
            )
        assert isinstance(actor.config, PlanConfig)
        assert actor.config.collection == CollectionConfig()
        assert actor.config.vector_store is True  # 10-9 invariant


def _plan_actor_with_vs_proxy(
    collection: object,
    vector_store_value: object = True,
) -> tuple[PlanActor, MagicMock]:
    """Build a PlanActor wired so _acquire_vs_proxy can reach create_collection.

    Returns (actor, vs_proxy_mock). Does NOT call on_start.
    """
    from akgentic.tool.planning.planning_actor import PlanConfig, PlanManagerState
    from akgentic.tool.vector_store.actor import VS_ACTOR_NAME

    actor = PlanActor()
    actor.config = PlanConfig(
        name="#PlanningTool",
        role="ToolActor",
        vector_store=vector_store_value,  # type: ignore[arg-type]
        collection=collection,  # type: ignore[arg-type]
    )
    actor.state = PlanManagerState()
    actor.state.observer(actor)
    actor._vs_proxy = None

    orch_addr = MockActorAddress("orchestrator", "Orchestrator")
    actor._orchestrator = orch_addr  # type: ignore[assignment]

    orch_proxy = MagicMock()
    orch_proxy.get_team_member.return_value = MockActorAddress(
        VS_ACTOR_NAME, "ToolActor"
    )

    vs_proxy = MagicMock()
    vs_proxy.create_collection.return_value = None

    def _proxy_ask(target: object, actor_type: type | None = None,
                   timeout: int | None = None) -> object:
        if target is orch_addr:
            return orch_proxy
        return vs_proxy

    actor.proxy_ask = _proxy_ask  # type: ignore[method-assign,assignment]
    return actor, vs_proxy


class TestPlanActorAcquireVsProxyCollectionPropagation:
    """AC-7 / AC-11: _acquire_vs_proxy forwards config.collection to create_collection."""

    def test_create_collection_receives_same_instance_as_config_collection(self) -> None:
        """AC-7: the CollectionConfig passed to create_collection is the config's instance."""
        from akgentic.tool.planning.planning_actor import PLAN_COLLECTION
        from akgentic.tool.vector_store.protocol import CollectionConfig

        custom = CollectionConfig(
            backend="inmemory", persistence="workspace", workspace_path="/tmp/plan"
        )
        actor, vs_proxy = _plan_actor_with_vs_proxy(collection=custom)

        actor._acquire_vs_proxy()

        vs_proxy.create_collection.assert_called_once()
        args, _ = vs_proxy.create_collection.call_args
        assert args[0] == PLAN_COLLECTION
        # Identity assertion — proves no fresh CollectionConfig is constructed.
        assert args[1] is actor.config.collection
        assert args[1] is custom

    def test_default_config_collection_is_structurally_default(self) -> None:
        """AC-11 regression guard: default config gets a default ``CollectionConfig()``."""
        from akgentic.tool.vector_store.protocol import CollectionConfig

        default_collection = CollectionConfig()
        actor, vs_proxy = _plan_actor_with_vs_proxy(collection=default_collection)

        actor._acquire_vs_proxy()

        args, _ = vs_proxy.create_collection.call_args
        assert args[1] == CollectionConfig()
        assert args[1].dimension == 1536
        assert args[1].backend == "inmemory"
        assert args[1].persistence == "actor_state"
        assert args[1].workspace_path is None
        assert args[1].tenant is None

    def test_vector_store_false_short_circuits_before_collection_examined(self) -> None:
        """vector_store=False → _acquire_vs_proxy never runs, even with custom coll."""
        from akgentic.tool.planning.planning_actor import PlanConfig
        from akgentic.tool.vector_store.protocol import CollectionConfig

        # Non-default collection, orchestrator absent (would otherwise warn+return).
        # vector_store=False must short-circuit in on_start before we touch it.
        actor = PlanActor()
        actor.config = PlanConfig(
            name="#PlanningTool",
            role="ToolActor",
            vector_store=False,
            collection=CollectionConfig(backend="weaviate", tenant="t1"),
        )
        actor.on_start()
        assert actor._vs_proxy is None
