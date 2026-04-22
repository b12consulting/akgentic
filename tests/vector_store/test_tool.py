"""Tests for VectorStoreTool — declarative configuration ToolCard.

Covers story 10.7:
- AC-1: observer() calls getChildrenOrCreate with the correct config + raises
  ValueError when orchestrator is missing.
- AC-2: configuration-only surface returns empty tools / prompts / commands /
  toolsets.
- AC-3: named VectorStore instances trigger separate getChildrenOrCreate calls.
- AC-4: ToolCard surface matches ADR-019 addendum §1 (no weaviate_* fields).
- AC-5: Pydantic round-trip preserves every field.
- AC-6: VectorStoreTool is re-exported from akgentic.tool.vector_store.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import AkgentType
from akgentic.core.orchestrator import Orchestrator

from akgentic.tool.core import ToolCard
from akgentic.tool.vector_store.actor import VS_ACTOR_NAME, VS_ACTOR_ROLE, VectorStoreActor
from akgentic.tool.vector_store.protocol import VectorStoreConfig
from akgentic.tool.vector_store.tool import VectorStoreTool

# ---------------------------------------------------------------------------
# Test doubles (mirrors tests/test_kg_tool.py conventions)
# ---------------------------------------------------------------------------


class _MockActorAddress(ActorAddress):
    """Minimal ActorAddress stand-in for ToolCard tests."""

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
        return f"_MockActorAddress(name={self._name})"


class _MockActorToolObserver:
    """Stubbed ActorToolObserver that captures proxy_ask + getChildrenOrCreate calls."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self._address = _MockActorAddress("test-agent")
        self._orchestrator: ActorAddress | None = _MockActorAddress("orchestrator")
        self._orchestrator_proxy = MagicMock(spec=Orchestrator)
        # Default: return a fresh address whenever getChildrenOrCreate is called
        self._orchestrator_proxy.getChildrenOrCreate.side_effect = (
            lambda actor_cls, config=None: _MockActorAddress(
                getattr(config, "name", "unknown"),
                getattr(config, "role", VS_ACTOR_ROLE),
            )
        )

    @property
    def myAddress(self) -> ActorAddress:  # noqa: N802
        return self._address

    @property
    def orchestrator(self) -> ActorAddress | None:
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
        return MagicMock()


# ===========================================================================
# AC-4: field defaults and type surface
# ===========================================================================


class TestVectorStoreToolDefaults:
    """VectorStoreTool default fields match ADR-019 addendum §1."""

    def test_is_tool_card(self) -> None:
        assert issubclass(VectorStoreTool, ToolCard)

    def test_default_name(self) -> None:
        tool = VectorStoreTool()
        assert tool.name == "VectorStore"

    def test_default_description(self) -> None:
        tool = VectorStoreTool()
        assert tool.description == "Centralized vector storage and embedding service"

    def test_default_vector_store_name_matches_singleton_constant(self) -> None:
        tool = VectorStoreTool()
        assert tool.vector_store_name == VS_ACTOR_NAME

    def test_default_embedding_model(self) -> None:
        tool = VectorStoreTool()
        assert tool.embedding_model == "text-embedding-3-small"

    def test_default_embedding_provider(self) -> None:
        tool = VectorStoreTool()
        assert tool.embedding_provider == "openai"

    def test_embedding_provider_is_literal_openai_or_azure(self) -> None:
        """embedding_provider accepts only 'openai' or 'azure' (AC-4)."""
        VectorStoreTool(embedding_provider="openai")
        VectorStoreTool(embedding_provider="azure")
        with pytest.raises(ValueError):
            VectorStoreTool(embedding_provider="anthropic")  # type: ignore[arg-type]

    def test_does_not_declare_weaviate_url_field(self) -> None:
        """AC-4: infra-level Weaviate connection fields must not leak into the ToolCard."""
        assert "weaviate_url" not in VectorStoreTool.model_fields

    def test_does_not_declare_weaviate_api_key_field(self) -> None:
        """AC-4: infra-level Weaviate connection fields must not leak into the ToolCard."""
        assert "weaviate_api_key" not in VectorStoreTool.model_fields

    def test_every_field_uses_serialisable_type(self) -> None:
        """AC-5 / Golden Rule 1b: fields must be primitives, Literals, or BaseModels.

        Non-serialisable runtime state (actor proxies, file handles, open
        connections) must live on ``PrivateAttr``, not on a Pydantic field.
        """
        from typing import get_args, get_origin

        allowed_primitive_types = {str, int, float, bool, bytes, type(None)}

        for field_name, field_info in VectorStoreTool.model_fields.items():
            annotation = field_info.annotation
            assert annotation is not None, (
                f"VectorStoreTool.{field_name} has no annotation — "
                f"serialisation cannot be guaranteed"
            )

            # Unwrap Literal[...] / Union / generic origins into their args
            origin = get_origin(annotation)
            candidates = get_args(annotation) if origin is not None else (annotation,)

            for candidate in candidates:
                # Literals contain primitive values — acceptable
                if not isinstance(candidate, type):
                    continue
                assert candidate in allowed_primitive_types, (
                    f"VectorStoreTool.{field_name} uses non-serialisable type "
                    f"{candidate} — move runtime state to PrivateAttr"
                )


# ===========================================================================
# AC-1: observer() wiring
# ===========================================================================


class TestVectorStoreToolObserver:
    """observer() ensures the VectorStoreActor singleton exists."""

    def test_observer_requires_orchestrator(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        observer._orchestrator = None
        with pytest.raises(ValueError, match="orchestrator"):
            tool.observer(observer)

    def test_observer_returns_none(self) -> None:
        """Observer override returns None (not Self) — no chaining."""
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        assert tool.observer(observer) is None

    def test_observer_calls_get_children_or_create_once(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert observer._orchestrator_proxy.getChildrenOrCreate.call_count == 1

    def test_observer_passes_vector_store_actor_class(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        call = observer._orchestrator_proxy.getChildrenOrCreate.call_args
        assert call.args[0] is VectorStoreActor

    def test_observer_passes_config_with_tool_card_fields(self) -> None:
        """AC-1: VectorStoreConfig carries vector_store_name / role / embedding_*.

        The default ToolCard uses the default embedding model + openai provider.
        """
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)

        call = observer._orchestrator_proxy.getChildrenOrCreate.call_args
        passed_config = call.kwargs["config"]
        assert isinstance(passed_config, VectorStoreConfig)
        assert passed_config.name == VS_ACTOR_NAME
        assert passed_config.role == VS_ACTOR_ROLE
        assert passed_config.embedding_model == "text-embedding-3-small"
        assert passed_config.embedding_provider == "openai"

    def test_observer_forwards_custom_embedding_config(self) -> None:
        tool = VectorStoreTool(
            embedding_model="text-embedding-3-large",
            embedding_provider="azure",
        )
        observer = _MockActorToolObserver()
        tool.observer(observer)

        call = observer._orchestrator_proxy.getChildrenOrCreate.call_args
        passed_config = call.kwargs["config"]
        assert passed_config.embedding_model == "text-embedding-3-large"
        assert passed_config.embedding_provider == "azure"

    def test_observer_stores_observer_on_private_attr(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert tool._observer is observer


# ===========================================================================
# AC-2: configuration-only surface
# ===========================================================================


class TestVectorStoreToolConfigurationOnly:
    """ToolCard factory methods return empty — this is a configuration-only tool."""

    def test_get_tools_returns_empty_list(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert tool.get_tools() == []

    def test_get_system_prompts_returns_empty_list(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert tool.get_system_prompts() == []

    def test_get_commands_returns_empty_dict(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert tool.get_commands() == {}

    def test_get_toolsets_returns_empty_list(self) -> None:
        tool = VectorStoreTool()
        observer = _MockActorToolObserver()
        tool.observer(observer)
        assert tool.get_toolsets() == []


# ===========================================================================
# AC-3: named VectorStore instances — multiple singletons per name
# ===========================================================================


class TestVectorStoreToolNamedInstances:
    """Two ToolCards with different vector_store_name trigger two singletons."""

    def test_two_different_names_trigger_two_get_children_or_create_calls(self) -> None:
        tool_default = VectorStoreTool(vector_store_name="#VectorStore")
        tool_rag = VectorStoreTool(vector_store_name="#VectorStore-RAG")

        observer = _MockActorToolObserver()

        tool_default.observer(observer)
        tool_rag.observer(observer)

        assert observer._orchestrator_proxy.getChildrenOrCreate.call_count == 2

    def test_per_call_config_names_match_tool_card_names(self) -> None:
        tool_default = VectorStoreTool(vector_store_name="#VectorStore")
        tool_rag = VectorStoreTool(vector_store_name="#VectorStore-RAG")

        observer = _MockActorToolObserver()
        tool_default.observer(observer)
        tool_rag.observer(observer)

        all_calls = observer._orchestrator_proxy.getChildrenOrCreate.call_args_list
        first_config = all_calls[0].kwargs["config"]
        second_config = all_calls[1].kwargs["config"]
        assert first_config.name == "#VectorStore"
        assert second_config.name == "#VectorStore-RAG"

    def test_same_name_twice_is_idempotent_by_contract(self) -> None:
        """getChildrenOrCreate guarantees one actor per name (ADR-025).

        The ToolCard always invokes the call — idempotency is enforced in the
        orchestrator, not in the ToolCard. Here we just verify that the
        ToolCard does not short-circuit duplicate invocations: both calls use
        the same name, so the orchestrator resolves them to the same actor.
        """
        tool_a = VectorStoreTool(vector_store_name="#VectorStore-RAG")
        tool_b = VectorStoreTool(vector_store_name="#VectorStore-RAG")

        observer = _MockActorToolObserver()
        tool_a.observer(observer)
        tool_b.observer(observer)

        all_calls = observer._orchestrator_proxy.getChildrenOrCreate.call_args_list
        assert len(all_calls) == 2
        assert all_calls[0].kwargs["config"].name == "#VectorStore-RAG"
        assert all_calls[1].kwargs["config"].name == "#VectorStore-RAG"


# ===========================================================================
# AC-5: Pydantic round-trip (serialisation)
# ===========================================================================


class TestVectorStoreToolSerialization:
    """model_dump → model_validate(dump) preserves every field."""

    def test_default_instance_round_trip(self) -> None:
        original = VectorStoreTool()
        dumped = original.model_dump()
        restored = VectorStoreTool.model_validate(dumped)
        assert restored == original

    def test_custom_values_round_trip(self) -> None:
        original = VectorStoreTool(
            embedding_model="text-embedding-3-large",
            embedding_provider="azure",
            vector_store_name="#VectorStore-RAG",
        )
        dumped = original.model_dump()
        restored = VectorStoreTool.model_validate(dumped)
        assert restored.embedding_model == "text-embedding-3-large"
        assert restored.embedding_provider == "azure"
        assert restored.vector_store_name == "#VectorStore-RAG"
        assert restored.name == original.name
        assert restored.description == original.description

    def test_dumped_payload_contains_all_fields(self) -> None:
        original = VectorStoreTool(
            embedding_model="text-embedding-3-large",
            embedding_provider="azure",
            vector_store_name="#VectorStore-RAG",
        )
        dumped = original.model_dump()
        assert dumped["name"] == "VectorStore"
        assert dumped["description"] == "Centralized vector storage and embedding service"
        assert dumped["vector_store_name"] == "#VectorStore-RAG"
        assert dumped["embedding_model"] == "text-embedding-3-large"
        assert dumped["embedding_provider"] == "azure"


# ===========================================================================
# AC-6: public API re-export
# ===========================================================================


class TestVectorStoreToolPublicApi:
    """VectorStoreTool is importable from akgentic.tool.vector_store."""

    def test_direct_import_succeeds(self) -> None:
        from akgentic.tool.vector_store import VectorStoreTool as Imported

        assert Imported is VectorStoreTool

    def test_is_in_package_all(self) -> None:
        import akgentic.tool.vector_store as vs

        assert "VectorStoreTool" in vs.__all__
