"""Validate protocol definitions are structurally correct."""

from __future__ import annotations

import inspect
import uuid
from typing import Protocol, get_type_hints


def test_placement_strategy_is_protocol() -> None:
    """PlacementStrategy uses typing.Protocol base."""
    from akgentic.infra.protocols import PlacementStrategy

    assert Protocol in inspect.getmro(PlacementStrategy)


def test_placement_strategy_has_create_team() -> None:
    """PlacementStrategy defines create_team with team_card and user_id parameters."""
    from akgentic.infra.protocols import PlacementStrategy

    assert hasattr(PlacementStrategy, "create_team")
    sig = inspect.signature(PlacementStrategy.create_team)
    assert "team_card" in sig.parameters
    assert "user_id" in sig.parameters


def test_auth_strategy_is_protocol() -> None:
    """AuthStrategy uses typing.Protocol base."""
    from akgentic.infra.protocols import AuthStrategy

    assert Protocol in inspect.getmro(AuthStrategy)


def test_auth_strategy_has_authenticate() -> None:
    """AuthStrategy defines authenticate with request parameter."""
    from akgentic.infra.protocols import AuthStrategy

    assert hasattr(AuthStrategy, "authenticate")
    sig = inspect.signature(AuthStrategy.authenticate)
    assert "request" in sig.parameters


def test_recovery_policy_is_protocol() -> None:
    """RecoveryPolicy uses typing.Protocol base."""
    from akgentic.infra.protocols import RecoveryPolicy

    assert Protocol in inspect.getmro(RecoveryPolicy)


def test_recovery_policy_has_recover() -> None:
    """RecoveryPolicy defines recover with instance_id and team_ids parameters."""
    from akgentic.infra.protocols import RecoveryPolicy

    assert hasattr(RecoveryPolicy, "recover")
    sig = inspect.signature(RecoveryPolicy.recover)
    assert "instance_id" in sig.parameters
    assert "team_ids" in sig.parameters


def test_health_monitor_is_protocol() -> None:
    """HealthMonitor uses typing.Protocol base."""
    from akgentic.infra.protocols import HealthMonitor

    assert Protocol in inspect.getmro(HealthMonitor)


def test_health_monitor_has_check_health() -> None:
    """HealthMonitor defines check_health method."""
    from akgentic.infra.protocols import HealthMonitor

    assert hasattr(HealthMonitor, "check_health")
    sig = inspect.signature(HealthMonitor.check_health)
    # Only self parameter
    assert len(sig.parameters) == 1


# --- InteractionChannelAdapter ---


def test_interaction_channel_adapter_is_protocol() -> None:
    """InteractionChannelAdapter uses typing.Protocol base."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    assert Protocol in inspect.getmro(InteractionChannelAdapter)


def test_interaction_channel_adapter_has_matches() -> None:
    """InteractionChannelAdapter defines matches with msg parameter."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    assert hasattr(InteractionChannelAdapter, "matches")
    sig = inspect.signature(InteractionChannelAdapter.matches)
    assert "msg" in sig.parameters


def test_interaction_channel_adapter_has_deliver() -> None:
    """InteractionChannelAdapter defines deliver with msg parameter."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    assert hasattr(InteractionChannelAdapter, "deliver")
    sig = inspect.signature(InteractionChannelAdapter.deliver)
    assert "msg" in sig.parameters


def test_interaction_channel_adapter_has_on_stop() -> None:
    """InteractionChannelAdapter defines on_stop with team_id parameter."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    assert hasattr(InteractionChannelAdapter, "on_stop")
    sig = inspect.signature(InteractionChannelAdapter.on_stop)
    assert "team_id" in sig.parameters


def test_interaction_channel_adapter_matches_returns_bool() -> None:
    """InteractionChannelAdapter.matches has bool return annotation."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    sig = inspect.signature(InteractionChannelAdapter.matches)
    assert sig.return_annotation is bool or sig.return_annotation == "bool"


def test_interaction_channel_adapter_deliver_returns_none() -> None:
    """InteractionChannelAdapter.deliver has None return annotation."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    sig = inspect.signature(InteractionChannelAdapter.deliver)
    assert sig.return_annotation is None or sig.return_annotation == "None"


def test_interaction_channel_adapter_on_stop_returns_none() -> None:
    """InteractionChannelAdapter.on_stop returns None."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    hints = get_type_hints(InteractionChannelAdapter.on_stop)
    assert hints["return"] is type(None)


def test_interaction_channel_adapter_structural_subtyping() -> None:
    """A concrete class satisfying InteractionChannelAdapter is recognized."""
    from akgentic.infra.protocols import InteractionChannelAdapter

    class FakeAdapter:
        def matches(self, msg: object) -> bool:
            return True

        def deliver(self, msg: object) -> None:
            pass

        def on_stop(self, team_id: uuid.UUID) -> None:
            pass

    assert isinstance(FakeAdapter(), InteractionChannelAdapter)


# --- InteractionChannelIngestion ---


def test_interaction_channel_ingestion_is_protocol() -> None:
    """InteractionChannelIngestion uses typing.Protocol base."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    assert Protocol in inspect.getmro(InteractionChannelIngestion)


def test_interaction_channel_ingestion_has_route_reply() -> None:
    """InteractionChannelIngestion defines async route_reply."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    assert hasattr(InteractionChannelIngestion, "route_reply")
    sig = inspect.signature(InteractionChannelIngestion.route_reply)
    assert "team_id" in sig.parameters
    assert "content" in sig.parameters
    assert "original_message_id" in sig.parameters
    assert inspect.iscoroutinefunction(InteractionChannelIngestion.route_reply)


def test_interaction_channel_ingestion_has_initiate_team() -> None:
    """InteractionChannelIngestion defines async initiate_team."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    assert hasattr(InteractionChannelIngestion, "initiate_team")
    sig = inspect.signature(InteractionChannelIngestion.initiate_team)
    assert "content" in sig.parameters
    assert "channel_user_id" in sig.parameters
    assert "catalog_entry_id" in sig.parameters
    assert inspect.iscoroutinefunction(InteractionChannelIngestion.initiate_team)


def test_interaction_channel_ingestion_route_reply_returns_none() -> None:
    """InteractionChannelIngestion.route_reply returns None."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    hints = get_type_hints(InteractionChannelIngestion.route_reply)
    assert hints["return"] is type(None)


def test_interaction_channel_ingestion_initiate_team_returns_uuid() -> None:
    """InteractionChannelIngestion.initiate_team returns UUID."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    hints = get_type_hints(InteractionChannelIngestion.initiate_team)
    assert hints["return"] is uuid.UUID


def test_interaction_channel_ingestion_structural_subtyping() -> None:
    """A concrete class satisfying InteractionChannelIngestion is recognized."""
    from akgentic.infra.protocols import InteractionChannelIngestion

    class FakeIngestion:
        async def route_reply(
            self,
            team_id: uuid.UUID,
            content: str,
            original_message_id: str | None = None,
        ) -> None:
            pass

        async def initiate_team(
            self,
            content: str,
            channel_user_id: str,
            catalog_entry_id: str,
        ) -> uuid.UUID:
            return uuid.uuid4()

    assert isinstance(FakeIngestion(), InteractionChannelIngestion)


# --- ChannelParser ---


def test_channel_parser_is_protocol() -> None:
    """ChannelParser uses typing.Protocol base."""
    from akgentic.infra.protocols import ChannelParser

    assert Protocol in inspect.getmro(ChannelParser)


def test_channel_parser_has_parse() -> None:
    """ChannelParser defines async parse with payload parameter."""
    from akgentic.infra.protocols import ChannelParser

    assert hasattr(ChannelParser, "parse")
    sig = inspect.signature(ChannelParser.parse)
    assert "payload" in sig.parameters
    assert inspect.iscoroutinefunction(ChannelParser.parse)


def test_channel_parser_has_channel_name_property() -> None:
    """ChannelParser defines channel_name property."""
    from akgentic.infra.protocols import ChannelParser

    assert hasattr(ChannelParser, "channel_name")


def test_channel_parser_has_default_catalog_entry_property() -> None:
    """ChannelParser defines default_catalog_entry property."""
    from akgentic.infra.protocols import ChannelParser

    assert hasattr(ChannelParser, "default_catalog_entry")


def test_channel_parser_return_type() -> None:
    """ChannelParser.parse returns ChannelMessage."""
    from akgentic.infra.protocols import ChannelMessage, ChannelParser

    hints = get_type_hints(ChannelParser.parse)
    assert hints["return"] is ChannelMessage


def test_channel_parser_structural_subtyping() -> None:
    """A concrete class satisfying ChannelParser is recognized."""
    from akgentic.infra.protocols import ChannelMessage, ChannelParser

    class FakeParser:
        @property
        def channel_name(self) -> str:
            return "fake"

        @property
        def default_catalog_entry(self) -> str:
            return "default-fake"

        async def parse(self, payload: dict[str, object]) -> ChannelMessage:
            return ChannelMessage(content="", channel_user_id="")

    assert isinstance(FakeParser(), ChannelParser)


# --- ChannelRegistry ---


def test_channel_registry_is_protocol() -> None:
    """ChannelRegistry uses typing.Protocol base."""
    from akgentic.infra.protocols import ChannelRegistry

    assert Protocol in inspect.getmro(ChannelRegistry)


def test_channel_registry_has_register() -> None:
    """ChannelRegistry defines async register with channel, channel_user_id, team_id."""
    from akgentic.infra.protocols import ChannelRegistry

    assert hasattr(ChannelRegistry, "register")
    sig = inspect.signature(ChannelRegistry.register)
    assert "channel" in sig.parameters
    assert "channel_user_id" in sig.parameters
    assert "team_id" in sig.parameters
    assert inspect.iscoroutinefunction(ChannelRegistry.register)


def test_channel_registry_has_find_team() -> None:
    """ChannelRegistry defines async find_team with channel and channel_user_id."""
    from akgentic.infra.protocols import ChannelRegistry

    assert hasattr(ChannelRegistry, "find_team")
    sig = inspect.signature(ChannelRegistry.find_team)
    assert "channel" in sig.parameters
    assert "channel_user_id" in sig.parameters
    assert inspect.iscoroutinefunction(ChannelRegistry.find_team)


def test_channel_registry_has_deregister() -> None:
    """ChannelRegistry defines async deregister with channel and channel_user_id."""
    from akgentic.infra.protocols import ChannelRegistry

    assert hasattr(ChannelRegistry, "deregister")
    sig = inspect.signature(ChannelRegistry.deregister)
    assert "channel" in sig.parameters
    assert "channel_user_id" in sig.parameters
    assert inspect.iscoroutinefunction(ChannelRegistry.deregister)


def test_channel_registry_find_team_return_type() -> None:
    """ChannelRegistry.find_team returns uuid.UUID | None."""
    from akgentic.infra.protocols import ChannelRegistry

    hints = get_type_hints(ChannelRegistry.find_team)
    assert hints["return"] == uuid.UUID | None


def test_channel_registry_register_returns_none() -> None:
    """ChannelRegistry.register returns None."""
    from akgentic.infra.protocols import ChannelRegistry

    hints = get_type_hints(ChannelRegistry.register)
    assert hints["return"] is type(None)


def test_channel_registry_deregister_returns_none() -> None:
    """ChannelRegistry.deregister returns None."""
    from akgentic.infra.protocols import ChannelRegistry

    hints = get_type_hints(ChannelRegistry.deregister)
    assert hints["return"] is type(None)


def test_channel_registry_structural_subtyping() -> None:
    """A concrete class satisfying ChannelRegistry is recognized."""
    from akgentic.infra.protocols import ChannelRegistry

    class FakeRegistry:
        async def register(self, channel: str, channel_user_id: str, team_id: uuid.UUID) -> None:
            pass

        async def find_team(self, channel: str, channel_user_id: str) -> uuid.UUID | None:
            return None

        async def deregister(self, channel: str, channel_user_id: str) -> None:
            pass

    assert isinstance(FakeRegistry(), ChannelRegistry)


# --- ChannelMessage ---


def test_channel_message_is_pydantic_model() -> None:
    """ChannelMessage is a Pydantic BaseModel with correct fields."""
    from pydantic import BaseModel

    from akgentic.infra.protocols import ChannelMessage

    assert issubclass(ChannelMessage, BaseModel)
    fields = ChannelMessage.model_fields
    assert "content" in fields
    assert "channel_user_id" in fields
    assert "team_id" in fields
    assert "message_id" in fields


def test_channel_message_field_descriptions() -> None:
    """ChannelMessage fields have descriptions."""
    from akgentic.infra.protocols import ChannelMessage

    for name, field_info in ChannelMessage.model_fields.items():
        assert field_info.description is not None, f"Field {name} missing description"


def test_channel_message_optional_defaults() -> None:
    """ChannelMessage team_id and message_id default to None."""
    from akgentic.infra.protocols import ChannelMessage

    msg = ChannelMessage(content="hello", channel_user_id="u1")
    assert msg.team_id is None
    assert msg.message_id is None


def test_channel_message_with_all_fields() -> None:
    """ChannelMessage can be created with all fields."""
    from akgentic.infra.protocols import ChannelMessage

    tid = uuid.uuid4()
    msg = ChannelMessage(
        content="hello",
        channel_user_id="u1",
        team_id=tid,
        message_id="msg-123",
    )
    assert msg.content == "hello"
    assert msg.channel_user_id == "u1"
    assert msg.team_id == tid
    assert msg.message_id == "msg-123"


# --- TeamHandle ---


def test_team_handle_is_protocol() -> None:
    """TeamHandle uses typing.Protocol base."""
    from akgentic.infra.protocols import TeamHandle

    assert Protocol in inspect.getmro(TeamHandle)


def test_team_handle_has_team_id_property() -> None:
    """TeamHandle defines team_id property returning uuid.UUID."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "team_id")
    hints = get_type_hints(TeamHandle.team_id.fget)  # type: ignore[union-attr]
    assert hints["return"] is uuid.UUID


def test_team_handle_is_runtime_checkable() -> None:
    """TeamHandle has @runtime_checkable decorator."""
    from akgentic.infra.protocols import TeamHandle

    assert getattr(TeamHandle, "__protocol_attrs__", None) is not None or hasattr(
        TeamHandle, "_is_runtime_protocol"
    )

    # Verify isinstance works (runtime_checkable requirement)
    class FakeHandle:
        @property
        def team_id(self) -> object:
            import uuid

            return uuid.uuid4()

        def send(self, content: str) -> None:
            pass

        def send_to(self, agent_name: str, content: str) -> None:
            pass

        def send_from_to(self, sender_name: str, recipient_name: str, content: str) -> None:
            pass

        def process_human_input(self, content: str, message: object) -> None:
            pass

        def subscribe(self, subscriber: object) -> None:
            pass

        def unsubscribe(self, subscriber: object) -> None:
            pass

    assert isinstance(FakeHandle(), TeamHandle)


def test_team_handle_has_send() -> None:
    """TeamHandle defines send with content parameter."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "send")
    sig = inspect.signature(TeamHandle.send)
    assert "content" in sig.parameters


def test_team_handle_has_send_to() -> None:
    """TeamHandle defines send_to with agent_name and content parameters."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "send_to")
    sig = inspect.signature(TeamHandle.send_to)
    assert "agent_name" in sig.parameters
    assert "content" in sig.parameters


def test_team_handle_has_send_from_to() -> None:
    """TeamHandle defines send_from_to with sender_name, recipient_name, and content parameters."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "send_from_to")
    sig = inspect.signature(TeamHandle.send_from_to)
    assert "sender_name" in sig.parameters
    assert "recipient_name" in sig.parameters
    assert "content" in sig.parameters


def test_team_handle_send_from_to_returns_none() -> None:
    """TeamHandle.send_from_to returns None."""
    from akgentic.infra.protocols import TeamHandle

    hints = get_type_hints(TeamHandle.send_from_to)
    assert hints["return"] is type(None)


def test_team_handle_has_process_human_input() -> None:
    """TeamHandle defines process_human_input with content and message parameters."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "process_human_input")
    sig = inspect.signature(TeamHandle.process_human_input)
    assert "content" in sig.parameters
    assert "message" in sig.parameters


def test_team_handle_has_subscribe() -> None:
    """TeamHandle defines subscribe with subscriber parameter."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "subscribe")
    sig = inspect.signature(TeamHandle.subscribe)
    assert "subscriber" in sig.parameters


def test_team_handle_has_unsubscribe() -> None:
    """TeamHandle defines unsubscribe with subscriber parameter."""
    from akgentic.infra.protocols import TeamHandle

    assert hasattr(TeamHandle, "unsubscribe")
    sig = inspect.signature(TeamHandle.unsubscribe)
    assert "subscriber" in sig.parameters


def test_team_handle_method_count() -> None:
    """TeamHandle has exactly 6 public methods."""
    from akgentic.infra.protocols import TeamHandle

    public_methods = [
        m for m in dir(TeamHandle) if not m.startswith("_") and callable(getattr(TeamHandle, m))
    ]
    assert len(public_methods) == 6


def test_team_handle_send_returns_none() -> None:
    """TeamHandle.send returns None."""
    from akgentic.infra.protocols import TeamHandle

    hints = get_type_hints(TeamHandle.send)
    assert hints["return"] is type(None)


def test_team_handle_send_to_returns_none() -> None:
    """TeamHandle.send_to returns None."""
    from akgentic.infra.protocols import TeamHandle

    hints = get_type_hints(TeamHandle.send_to)
    assert hints["return"] is type(None)


# --- RuntimeCache ---


def test_runtime_cache_is_protocol() -> None:
    """RuntimeCache uses typing.Protocol base."""
    from akgentic.infra.protocols import RuntimeCache

    assert Protocol in inspect.getmro(RuntimeCache)


def test_runtime_cache_is_runtime_checkable() -> None:
    """RuntimeCache has @runtime_checkable decorator and isinstance works."""
    from akgentic.infra.protocols import RuntimeCache

    class FakeCache:
        def store(self, team_id: uuid.UUID, handle: object) -> None:
            pass

        def get(self, team_id: uuid.UUID) -> object:
            return None

        def remove(self, team_id: uuid.UUID) -> None:
            pass

    assert isinstance(FakeCache(), RuntimeCache)


def test_runtime_cache_has_store() -> None:
    """RuntimeCache defines store with team_id and handle parameters."""
    from akgentic.infra.protocols import RuntimeCache

    assert hasattr(RuntimeCache, "store")
    sig = inspect.signature(RuntimeCache.store)
    assert "team_id" in sig.parameters
    assert "handle" in sig.parameters


def test_runtime_cache_has_get() -> None:
    """RuntimeCache defines get with team_id parameter."""
    from akgentic.infra.protocols import RuntimeCache

    assert hasattr(RuntimeCache, "get")
    sig = inspect.signature(RuntimeCache.get)
    assert "team_id" in sig.parameters


def test_runtime_cache_has_remove() -> None:
    """RuntimeCache defines remove with team_id parameter."""
    from akgentic.infra.protocols import RuntimeCache

    assert hasattr(RuntimeCache, "remove")
    sig = inspect.signature(RuntimeCache.remove)
    assert "team_id" in sig.parameters


def test_runtime_cache_store_returns_none() -> None:
    """RuntimeCache.store returns None."""
    from akgentic.infra.protocols import RuntimeCache

    hints = get_type_hints(RuntimeCache.store)
    assert hints["return"] is type(None)


def test_runtime_cache_remove_returns_none() -> None:
    """RuntimeCache.remove returns None."""
    from akgentic.infra.protocols import RuntimeCache

    hints = get_type_hints(RuntimeCache.remove)
    assert hints["return"] is type(None)


def test_runtime_cache_method_count() -> None:
    """RuntimeCache has exactly 3 public methods."""
    from akgentic.infra.protocols import RuntimeCache

    public_methods = [
        m for m in dir(RuntimeCache) if not m.startswith("_") and callable(getattr(RuntimeCache, m))
    ]
    assert len(public_methods) == 3


# --- Non-channel protocols (unchanged) ---


def test_placement_strategy_return_type() -> None:
    """PlacementStrategy.create_team returns TeamHandle."""
    from akgentic.team.models import TeamCard

    from akgentic.infra.protocols import PlacementStrategy, TeamHandle

    hints = get_type_hints(
        PlacementStrategy.create_team,
        localns={"TeamCard": TeamCard, "TeamHandle": TeamHandle},
    )
    assert hints["return"] is TeamHandle


def test_auth_strategy_return_type() -> None:
    """AuthStrategy.authenticate returns str | None."""
    from akgentic.infra.protocols import AuthStrategy

    hints = get_type_hints(AuthStrategy.authenticate)
    assert hints["return"] == str | None


def test_recovery_policy_return_type() -> None:
    """RecoveryPolicy.recover returns None."""
    from akgentic.infra.protocols import RecoveryPolicy

    hints = get_type_hints(RecoveryPolicy.recover)
    assert hints["return"] is type(None)


def test_health_monitor_return_type() -> None:
    """HealthMonitor.check_health returns list[uuid.UUID]."""
    from akgentic.infra.protocols import HealthMonitor

    hints = get_type_hints(HealthMonitor.check_health)
    assert hints["return"] == list[uuid.UUID]


# --- WorkerHandle ---


def test_worker_handle_is_protocol() -> None:
    """WorkerHandle uses typing.Protocol base."""
    from akgentic.infra.protocols import WorkerHandle

    assert Protocol in inspect.getmro(WorkerHandle)


def test_worker_handle_is_runtime_checkable() -> None:
    """WorkerHandle has @runtime_checkable decorator and isinstance works."""
    from akgentic.infra.protocols import WorkerHandle

    class FakeWorkerHandle:
        def stop_team(self, team_id: uuid.UUID) -> None:
            pass

        def delete_team(self, team_id: uuid.UUID) -> None:
            pass

        def resume_team(self, team_id: uuid.UUID) -> object:
            return None

        def get_team(self, team_id: uuid.UUID) -> object:
            return None

        def stop_all(self) -> None:
            pass

    assert isinstance(FakeWorkerHandle(), WorkerHandle)


def test_worker_handle_has_stop_team() -> None:
    """WorkerHandle defines stop_team with team_id parameter."""
    from akgentic.infra.protocols import WorkerHandle

    assert hasattr(WorkerHandle, "stop_team")
    sig = inspect.signature(WorkerHandle.stop_team)
    assert "team_id" in sig.parameters


def test_worker_handle_has_delete_team() -> None:
    """WorkerHandle defines delete_team with team_id parameter."""
    from akgentic.infra.protocols import WorkerHandle

    assert hasattr(WorkerHandle, "delete_team")
    sig = inspect.signature(WorkerHandle.delete_team)
    assert "team_id" in sig.parameters


def test_worker_handle_has_resume_team() -> None:
    """WorkerHandle defines resume_team with team_id parameter."""
    from akgentic.infra.protocols import WorkerHandle

    assert hasattr(WorkerHandle, "resume_team")
    sig = inspect.signature(WorkerHandle.resume_team)
    assert "team_id" in sig.parameters


def test_worker_handle_has_get_team() -> None:
    """WorkerHandle defines get_team with team_id parameter."""
    from akgentic.infra.protocols import WorkerHandle

    assert hasattr(WorkerHandle, "get_team")
    sig = inspect.signature(WorkerHandle.get_team)
    assert "team_id" in sig.parameters


def test_worker_handle_stop_team_returns_none() -> None:
    """WorkerHandle.stop_team returns None."""
    from akgentic.infra.protocols import WorkerHandle

    hints = get_type_hints(WorkerHandle.stop_team)
    assert hints["return"] is type(None)


def test_worker_handle_delete_team_returns_none() -> None:
    """WorkerHandle.delete_team returns None."""
    from akgentic.infra.protocols import WorkerHandle

    hints = get_type_hints(WorkerHandle.delete_team)
    assert hints["return"] is type(None)


def test_worker_handle_resume_team_returns_team_handle() -> None:
    """WorkerHandle.resume_team returns TeamHandle."""
    from akgentic.team.models import Process

    from akgentic.infra.protocols import TeamHandle, WorkerHandle

    hints = get_type_hints(
        WorkerHandle.resume_team,
        localns={"TeamHandle": TeamHandle, "Process": Process},
    )
    assert hints["return"] is TeamHandle


def test_worker_handle_get_team_returns_process_or_none() -> None:
    """WorkerHandle.get_team returns Process | None."""
    from akgentic.team.models import Process

    from akgentic.infra.protocols import WorkerHandle

    hints = get_type_hints(
        WorkerHandle.get_team,
        localns={"Process": Process},
    )
    assert hints["return"] == Process | None


def test_worker_handle_has_stop_all() -> None:
    """WorkerHandle defines stop_all with no parameters (besides self)."""
    from akgentic.infra.protocols import WorkerHandle

    assert hasattr(WorkerHandle, "stop_all")
    sig = inspect.signature(WorkerHandle.stop_all)
    params = [p for p in sig.parameters if p != "self"]
    assert params == []


def test_worker_handle_stop_all_returns_none() -> None:
    """WorkerHandle.stop_all returns None."""
    from akgentic.infra.protocols import WorkerHandle

    hints = get_type_hints(WorkerHandle.stop_all)
    assert hints["return"] is type(None)


def test_worker_handle_method_count() -> None:
    """WorkerHandle has exactly 5 public methods."""
    from akgentic.infra.protocols import WorkerHandle

    public_methods = [
        m for m in dir(WorkerHandle) if not m.startswith("_") and callable(getattr(WorkerHandle, m))
    ]
    assert len(public_methods) == 5
