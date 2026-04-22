"""Tests for TeamRuntime model — persistent/ephemeral field separation and serialization."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from akgentic.core.agent import Akgent
from akgentic.core.messages.message import UserMessage
from akgentic.core.user_proxy import UserProxy
from akgentic.core.utils.deserializer import ActorAddressDict
from pydantic import ValidationError

from akgentic.team.models import TeamCard, TeamCardMember, TeamRuntime
from tests.models.conftest import (
    make_agent_card,
    make_stub_actor_system,
    make_stub_addr,
    make_team_card,
    make_team_runtime,
)


class TestTeamRuntimeConstruction:
    """AC 1-4: TeamRuntime construction with persistent and ephemeral fields."""

    def test_construction_with_all_fields(self) -> None:
        runtime = make_team_runtime()
        assert isinstance(runtime.id, uuid.UUID)
        assert runtime.team is not None
        assert runtime.actor_system is not None
        assert runtime.orchestrator_addr is not None
        assert runtime.entry_addr is not None
        assert isinstance(runtime.supervisor_addrs, dict)
        assert isinstance(runtime.addrs, dict)

    def test_id_has_no_default(self) -> None:
        """AC4: Construction without explicit id raises ValidationError."""
        system = make_stub_actor_system()
        tc = make_team_card(agent_class=Akgent)
        with pytest.raises(ValidationError):
            TeamRuntime(
                team=tc,
                actor_system=system,
                orchestrator_addr=make_stub_addr(),
                entry_addr=make_stub_addr(),
            )

    def test_supervisor_addrs_defaults_to_empty_dict(self) -> None:
        system = make_stub_actor_system()
        tc = make_team_card(agent_class=Akgent)
        rt = TeamRuntime(
            id=uuid.uuid4(),
            team=tc,
            actor_system=system,
            orchestrator_addr=make_stub_addr(),
            entry_addr=make_stub_addr(),
        )
        assert rt.supervisor_addrs == {}
        assert rt.addrs == {}

    def test_model_post_init_rebuilds_proxies(self) -> None:
        """AC3: model_post_init rebuilds all proxies from addresses."""
        runtime = make_team_runtime()
        assert runtime._orchestrator_proxy is not None
        assert runtime._entry_proxy is not None

    def test_model_post_init_is_idempotent(self) -> None:
        """AC3: model_post_init is idempotent — safe to call multiple times."""
        runtime = make_team_runtime()
        initial_call_count = runtime.actor_system.proxy_ask.call_count
        runtime.model_post_init(None)
        # proxy_ask was called again, confirming proxies were rebuilt
        assert runtime.actor_system.proxy_ask.call_count > initial_call_count
        assert runtime._orchestrator_proxy is not None

    def test_model_post_init_builds_supervisor_proxies(self) -> None:
        """AC3: model_post_init rebuilds supervisor proxies from addresses."""
        supervisor_card = make_agent_card(name="supervisor", role="Supervisor", agent_class=Akgent)
        worker_card = make_agent_card(name="worker", role="Worker", agent_class=Akgent)
        entry_member = TeamCardMember(
            card=make_agent_card(name="lead", role="Lead", agent_class=Akgent),
        )
        # Supervisor has a subordinate worker — makes it a supervisor
        supervisor_member = TeamCardMember(
            card=supervisor_card,
            members=[TeamCardMember(card=worker_card)],
        )
        tc = TeamCard(
            name="team-with-supervisors",
            description="A team with supervisor hierarchy",
            entry_point=entry_member,
            members=[supervisor_member],
            message_types=[UserMessage],
        )
        sup_addr = make_stub_addr("supervisor")
        runtime = make_team_runtime(
            team_card=tc,
            supervisor_addrs={"supervisor": sup_addr},
        )
        assert "supervisor" in runtime._supervisor_proxies
        runtime.actor_system.proxy_ask.assert_any_call(sup_addr, Akgent)


class TestTeamRuntimeSerialization:
    """AC 2, 7: Persistent/ephemeral field separation and serialization round-trip."""

    def test_ephemeral_fields_excluded_from_dump(self) -> None:
        runtime = make_team_runtime()
        data = runtime.model_dump()
        assert "_orchestrator_proxy" not in data
        assert "_entry_proxy" not in data
        assert "_supervisor_proxies" not in data
        assert "_message_cls" not in data

    def test_actor_system_excluded_from_dump(self) -> None:
        runtime = make_team_runtime()
        data = runtime.model_dump()
        assert "actor_system" not in data

    def test_persistent_fields_in_dump(self) -> None:
        runtime = make_team_runtime()
        data = runtime.model_dump()
        assert "id" in data
        assert "team" in data
        assert "orchestrator_addr" in data
        assert "entry_addr" in data
        assert "supervisor_addrs" in data
        assert "addrs" in data


class TestTeamRuntimeMessaging:
    """AC 5, 6: send() and send_to() messaging facade."""

    def test_send_broadcasts_to_supervisors_only(self) -> None:
        """AC5: send() routes through entry proxy to supervisors only, not entry."""
        sup_addr_1 = make_stub_addr("supervisor-1")
        sup_addr_2 = make_stub_addr("supervisor-2")
        runtime = make_team_runtime(
            message_types=[UserMessage],
            supervisor_addrs={"sup1": sup_addr_1, "sup2": sup_addr_2},
        )
        runtime.send("hello")
        # 2 supervisors only (entry does NOT receive)
        assert runtime._entry_proxy.send.call_count == 2
        call_addrs = {call.args[0] for call in runtime._entry_proxy.send.call_args_list}
        assert call_addrs == {sup_addr_1, sup_addr_2}
        assert runtime.entry_addr not in call_addrs
        # Verify all messages are UserMessage with correct content
        for call in runtime._entry_proxy.send.call_args_list:
            msg = call.args[1]
            assert isinstance(msg, UserMessage)
            assert msg.content == "hello"

    def test_send_with_empty_supervisors_is_noop(self) -> None:
        """AC5: send() with no supervisors is a no-op (no recipients)."""
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime.send("hello")
        assert runtime._entry_proxy.send.call_count == 0

    def test_send_does_not_send_to_entry_addr(self) -> None:
        """send() never sends to entry_addr, only to supervisor addrs."""
        sup_addr = make_stub_addr("supervisor")
        runtime = make_team_runtime(
            message_types=[UserMessage],
            supervisor_addrs={"sup": sup_addr},
        )
        runtime.send("test")
        assert runtime._entry_proxy.send.call_count == 1
        call_addr = runtime._entry_proxy.send.call_args.args[0]
        assert call_addr is sup_addr
        assert call_addr is not runtime.entry_addr

    def test_send_routes_to_multiple_supervisors(self) -> None:
        """send() routes to all supervisors, entry does not receive."""
        sup1 = make_stub_addr("sup1")
        sup2 = make_stub_addr("sup2")
        sup3 = make_stub_addr("sup3")
        runtime = make_team_runtime(
            message_types=[UserMessage],
            supervisor_addrs={"s1": sup1, "s2": sup2, "s3": sup3},
        )
        runtime.send("multi")
        assert runtime._entry_proxy.send.call_count == 3
        call_addrs = {call.args[0] for call in runtime._entry_proxy.send.call_args_list}
        assert call_addrs == {sup1, sup2, sup3}
        assert runtime.entry_addr not in call_addrs
        # Verify all messages carry the correct content
        for call in runtime._entry_proxy.send.call_args_list:
            msg = call.args[1]
            assert isinstance(msg, UserMessage)
            assert msg.content == "multi"

    def test_send_to_looks_up_agent(self) -> None:
        """AC6: send_to() looks up agent via orchestrator proxy."""
        target_addr = make_stub_addr("worker")
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=target_addr)
        runtime.send_to("worker", "hello")
        runtime._orchestrator_proxy.get_team_member.assert_called_once_with("worker")
        runtime._entry_proxy.send.assert_called_once()
        call_args = runtime._entry_proxy.send.call_args
        assert call_args.args[0] is target_addr
        assert isinstance(call_args.args[1], UserMessage)
        assert call_args.args[1].content == "hello"

    def test_send_to_raises_for_unknown_agent(self) -> None:
        """AC6: send_to() raises ValueError if agent not found."""
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            runtime.send_to("nonexistent", "hello")

    def test_make_message_raises_when_no_message_types(self) -> None:
        runtime = make_team_runtime(message_types=[])
        with pytest.raises(RuntimeError, match="No message type"):
            runtime._make_message("hello")

    def test_make_message_creates_correct_type(self) -> None:
        runtime = make_team_runtime(message_types=[UserMessage])
        msg = runtime._make_message("hello world")
        assert isinstance(msg, UserMessage)
        assert msg.content == "hello world"

    def test_read_only_properties(self) -> None:
        runtime = make_team_runtime()
        assert runtime.orchestrator_proxy is runtime._orchestrator_proxy
        assert runtime.entry_proxy is runtime._entry_proxy
        assert runtime.supervisor_proxies is runtime._supervisor_proxies


class TestTeamRuntimeSendToResolution:
    """AC 2 (Story 12.3): send_to() resolves proxy addresses via addr_map."""

    def test_send_to_resolves_proxy_address(self) -> None:
        """If orchestrator returns a proxy, send_to() resolves it via addr_map."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        agent_id = uuid.uuid4()
        team_id = uuid.uuid4()

        # Create a proxy address (as would come from deserialized data)
        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.agent.Akgent",
            "agent_id": str(agent_id),
            "name": "worker",
            "role": "Worker",
            "team_id": str(team_id),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        proxy_addr = ActorAddressProxy(addr_dict)

        # Create a live address mock
        live_addr = make_stub_addr("worker")
        live_addr.agent_id = agent_id

        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=proxy_addr)
        runtime._addr_map = {agent_id: live_addr}

        runtime.send_to("worker", "hello")

        # Verify send was called with the live address, not the proxy
        runtime._entry_proxy.send.assert_called_once()
        call_args = runtime._entry_proxy.send.call_args
        assert call_args.args[0] is live_addr
        assert isinstance(call_args.args[1], UserMessage)

    def test_send_to_raises_for_unmapped_proxy(self) -> None:
        """If proxy has no mapping in addr_map, raise ValueError."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.agent.Akgent",
            "agent_id": str(uuid.uuid4()),
            "name": "ghost",
            "role": "Ghost",
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        proxy_addr = ActorAddressProxy(addr_dict)

        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=proxy_addr)
        runtime._addr_map = {}  # No mapping

        with pytest.raises(ValueError, match="stale proxy address"):
            runtime.send_to("ghost", "hello")


class TestTeamRuntimeSendFromTo:
    """send_from_to() sends a message with the correct sender identity."""

    def test_send_from_to_valid_sender_and_recipient(self) -> None:
        """AC1: sender proxy is obtained via proxy_tell and used to send."""
        sender_addr = make_stub_addr("developer")
        recipient_addr = make_stub_addr("manager")
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: sender_addr if name == "developer" else recipient_addr,
        )
        runtime.actor_system.proxy_tell.reset_mock()

        runtime.send_from_to("developer", "manager", "hello")

        runtime.actor_system.proxy_tell.assert_called_once_with(sender_addr, Akgent)
        sender_proxy = runtime.actor_system.proxy_tell.return_value
        sender_proxy.send.assert_called_once()
        call_args = sender_proxy.send.call_args
        assert call_args.args[0] is recipient_addr
        assert isinstance(call_args.args[1], UserMessage)
        assert call_args.args[1].content == "hello"

    def test_send_from_to_unknown_sender(self) -> None:
        """AC2: ValueError when sender is not found."""
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            runtime.send_from_to("unknown", "manager", "hello")

    def test_send_from_to_unknown_recipient(self) -> None:
        """AC3: ValueError when recipient is not found."""
        sender_addr = make_stub_addr("developer")
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: sender_addr if name == "developer" else None,
        )

        with pytest.raises(ValueError, match="not found"):
            runtime.send_from_to("developer", "unknown", "hello")

    def test_send_from_to_resolves_stale_sender_proxy(self) -> None:
        """AC4: stale sender proxy is resolved via _addr_map."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        agent_id = uuid.uuid4()
        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.agent.Akgent",
            "agent_id": str(agent_id),
            "name": "developer",
            "role": "Developer",
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        proxy_addr = ActorAddressProxy(addr_dict)
        live_sender = make_stub_addr("developer")
        live_sender.agent_id = agent_id
        recipient_addr = make_stub_addr("manager")

        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: proxy_addr if name == "developer" else recipient_addr,
        )
        runtime._addr_map = {agent_id: live_sender}
        runtime.actor_system.proxy_tell.reset_mock()

        runtime.send_from_to("developer", "manager", "hello")

        runtime.actor_system.proxy_tell.assert_called_once_with(live_sender, Akgent)

    def test_send_from_to_resolves_stale_recipient_proxy(self) -> None:
        """AC4: stale recipient proxy is resolved via _addr_map."""
        from akgentic.core.actor_address_impl import ActorAddressProxy

        agent_id = uuid.uuid4()
        addr_dict: ActorAddressDict = {
            "__actor_address__": True,
            "__actor_type__": "akgentic.core.agent.Akgent",
            "agent_id": str(agent_id),
            "name": "manager",
            "role": "Manager",
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": False,
        }
        proxy_addr = ActorAddressProxy(addr_dict)
        live_recipient = make_stub_addr("manager")
        live_recipient.agent_id = agent_id
        sender_addr = make_stub_addr("developer")

        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: sender_addr if name == "developer" else proxy_addr,
        )
        runtime._addr_map = {agent_id: live_recipient}

        runtime.send_from_to("developer", "manager", "hello")

        sender_proxy = runtime.actor_system.proxy_tell.return_value
        call_args = sender_proxy.send.call_args
        assert call_args.args[0] is live_recipient

    def test_send_to_still_works_after_refactor(self) -> None:
        """AC6: existing send_to() is not broken by the refactoring."""
        target_addr = make_stub_addr("worker")
        runtime = make_team_runtime(message_types=[UserMessage])
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=target_addr)
        runtime.send_to("worker", "hello")
        runtime._orchestrator_proxy.get_team_member.assert_called_once_with("worker")
        runtime._entry_proxy.send.assert_called_once()
        call_args = runtime._entry_proxy.send.call_args
        assert call_args.args[0] is target_addr
        assert isinstance(call_args.args[1], UserMessage)
        assert call_args.args[1].content == "hello"


def _make_proxy_addr(name: str, role: str = "Agent") -> ActorAddressProxy:
    """Create an ActorAddressProxy for testing."""
    addr_dict: ActorAddressDict = {
        "__actor_address__": True,
        "__actor_type__": "akgentic.core.agent.Akgent",
        "agent_id": str(uuid.uuid4()),
        "name": name,
        "role": role,
        "team_id": str(uuid.uuid4()),
        "squad_id": str(uuid.uuid4()),
        "user_message": False,
    }
    return ActorAddressProxy(addr_dict)


def _make_multi_human_runtime(
    *,
    human_class: type = UserProxy,
    support_class: type = UserProxy,
) -> TeamRuntime:
    """Build a TeamRuntime with @Human and @Support both as UserProxy."""
    human_card = make_agent_card(name="@Human", role="Human", agent_class=human_class)
    support_card = make_agent_card(
        name="@Support", role="Support", agent_class=support_class
    )
    manager_card = make_agent_card(name="@Manager", role="Manager", agent_class=Akgent)
    entry_card = make_agent_card(name="lead", role="Lead", agent_class=Akgent)

    tc = TeamCard(
        name="multi-human-team",
        description="Team with two UserProxy agents",
        entry_point=TeamCardMember(card=entry_card),
        members=[
            TeamCardMember(card=human_card),
            TeamCardMember(card=support_card),
            TeamCardMember(card=manager_card),
        ],
        message_types=[UserMessage],
    )
    return make_team_runtime(team_card=tc)


class TestTeamRuntimeProcessHumanInput:
    """AC 1-7 (Story 16.1): process_human_input rehydrates and routes by recipient."""

    def test_recipient_none_raises_valueerror(self) -> None:
        """AC1: ValueError when message.recipient is None; resolver never called."""
        runtime = _make_multi_human_runtime()
        message = UserMessage(content="hello")
        assert message.recipient is None

        with pytest.raises(ValueError, match="no recipient"):
            runtime.process_human_input("response", message)

        # Orchestrator should never be consulted
        runtime._orchestrator_proxy.get_team_member.assert_not_called()

    def test_rehydration_replaces_proxy_addresses(self) -> None:
        """AC2: ActorAddressProxy sender/recipient are replaced with live addresses."""
        proxy_sender = _make_proxy_addr("@Manager", "Manager")
        proxy_recipient = _make_proxy_addr("@Support", "Support")

        live_sender = make_stub_addr("@Manager")
        live_recipient = make_stub_addr("@Support")
        target_addr = make_stub_addr("@Support")

        runtime = _make_multi_human_runtime()
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: {
                "@Manager": live_sender,
                "@Support": live_recipient,
            }.get(name)
        )
        # _lookup_member also calls get_team_member, return target_addr for it
        original_get = runtime._orchestrator_proxy.get_team_member.side_effect

        def combined_get(name: str) -> ActorAddress | None:
            result = original_get(name)
            return result if result is not None else target_addr if name == "@Support" else None

        runtime._orchestrator_proxy.get_team_member = MagicMock(side_effect=combined_get)

        message = UserMessage(content="question")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        runtime.process_human_input("I coordinate onboarding", message)

        # Verify the downstream proxy received a message with live addresses
        proxy = runtime.actor_system.proxy_ask.return_value
        proxy.process_human_input.assert_called_once()
        call_args = proxy.process_human_input.call_args
        live_msg = call_args.args[1]
        # The rehydrated message should NOT be the original
        assert live_msg is not message
        # Sender and recipient should be live (not ActorAddressProxy)
        assert not isinstance(live_msg.sender, ActorAddressProxy)
        assert not isinstance(live_msg.recipient, ActorAddressProxy)

    def test_orchestrator_lookup_miss_raises_valueerror(self) -> None:
        """AC3: ValueError with 'not found in team' when resolver can't find agent."""
        proxy_sender = _make_proxy_addr("@Ghost", "Ghost")
        proxy_recipient = _make_proxy_addr("@Support", "Support")

        runtime = _make_multi_human_runtime()
        runtime._orchestrator_proxy.get_team_member = MagicMock(return_value=None)

        message = UserMessage(content="question")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        with pytest.raises(ValueError, match="not found in team"):
            runtime.process_human_input("response", message)

    def test_routes_by_recipient_name_not_first_match(self) -> None:
        """AC4: Routes to @Support by name, not first UserProxy in dict order."""
        live_sender = make_stub_addr("@Manager")
        live_support = make_stub_addr("@Support")

        runtime = _make_multi_human_runtime()
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: {
                "@Manager": live_sender,
                "@Support": live_support,
            }.get(name)
        )

        proxy_sender = _make_proxy_addr("@Manager", "Manager")
        proxy_recipient = _make_proxy_addr("@Support", "Support")
        message = UserMessage(content="describe your role")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        runtime.process_human_input("I coordinate user onboarding", message)

        # Verify proxy_ask was called with the @Support address
        runtime.actor_system.proxy_ask.assert_any_call(live_support, UserProxy)

    def test_target_not_userproxy_raises_valueerror(self) -> None:
        """AC5: ValueError when target agent is not a UserProxy subclass."""
        live_sender = make_stub_addr("@Human")
        live_manager = make_stub_addr("@Manager")

        runtime = _make_multi_human_runtime()
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: {
                "@Human": live_sender,
                "@Manager": live_manager,
            }.get(name)
        )

        proxy_sender = _make_proxy_addr("@Human", "Human")
        proxy_recipient = _make_proxy_addr("@Manager", "Manager")
        message = UserMessage(content="hello")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        with pytest.raises(ValueError, match="is not a UserProxy"):
            runtime.process_human_input("response", message)

    def test_downstream_receives_rehydrated_message(self) -> None:
        """AC6: The message passed downstream is the rehydrated copy, not original."""
        live_sender = make_stub_addr("@Manager")
        live_support = make_stub_addr("@Support")

        runtime = _make_multi_human_runtime()
        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: {
                "@Manager": live_sender,
                "@Support": live_support,
            }.get(name)
        )

        proxy_sender = _make_proxy_addr("@Manager", "Manager")
        proxy_recipient = _make_proxy_addr("@Support", "Support")
        message = UserMessage(content="question")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        runtime.process_human_input("answer", message)

        proxy = runtime.actor_system.proxy_ask.return_value
        call_args = proxy.process_human_input.call_args
        delivered_msg = call_args.args[1]
        # Must be a different object (rehydrated copy)
        assert delivered_msg is not message
        assert delivered_msg.content == "question"

    def test_dynamic_agent_resolved_by_orchestrator(self) -> None:
        """AC7: Orchestrator resolves @Expert even if not in self.addrs."""
        live_sender = make_stub_addr("@Human")
        live_expert = make_stub_addr("@Expert")

        # Build a runtime where @Expert is in agent_cards as UserProxy
        expert_card = make_agent_card(
            name="@Expert", role="Expert", agent_class=UserProxy
        )
        human_card = make_agent_card(
            name="@Human", role="Human", agent_class=UserProxy
        )
        entry_card = make_agent_card(name="lead", role="Lead", agent_class=Akgent)

        tc = TeamCard(
            name="dynamic-team",
            description="Team where @Expert is hired at runtime",
            entry_point=TeamCardMember(card=entry_card),
            members=[
                TeamCardMember(card=human_card),
                TeamCardMember(card=expert_card),
            ],
            message_types=[UserMessage],
        )
        # addrs does NOT include @Expert -- simulating runtime hiring
        runtime = make_team_runtime(team_card=tc, addrs={})

        runtime._orchestrator_proxy.get_team_member = MagicMock(
            side_effect=lambda name: {
                "@Human": live_sender,
                "@Expert": live_expert,
            }.get(name)
        )

        proxy_sender = _make_proxy_addr("@Human", "Human")
        proxy_recipient = _make_proxy_addr("@Expert", "Expert")
        message = UserMessage(content="question")
        message.sender = proxy_sender
        message.recipient = proxy_recipient

        runtime.process_human_input("I am an expert", message)

        # Verify _lookup_member resolved via orchestrator (not self.addrs)
        runtime.actor_system.proxy_ask.assert_any_call(live_expert, UserProxy)
