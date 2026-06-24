"""Tests for the command-discovery models and ``CommandNotRecognized`` (Story 21.3).

Covers ADR-028 §Decision 3 (discovery models) and §Decision 4 (failure handling):
field shape, defaults, required-vs-optional validation, Pydantic round-trip with the
``__model__`` marker that drives consumer dispatch, exception type/raisability, and
public-API importability. Mirrors the round-trip style of ``test_tool_state_event.py``.
"""

from __future__ import annotations

import uuid

import pytest
from akgentic.core.actor_address import ActorAddress
from akgentic.core.actor_address_impl import ActorAddressProxy
from pydantic import ValidationError

from akgentic.tool import (
    CommandArg,
    CommandDescriptor,
    CommandNotRecognized,
    CommandsAnnouncedEvent,
)
from akgentic.tool.errors import RetriableError


class SerializableActorAddress(ActorAddress):
    """A concrete ``ActorAddress`` whose ``serialize()`` carries the dispatch marker.

    Unlike the bare ``MockActorAddress`` in conftest, this produces a full
    ``ActorAddressDict`` (with ``__actor_address__``) so a round-trip reconstructs
    it as an ``ActorAddressProxy`` — letting us assert "equivalent ActorAddress".
    """

    def __init__(self, name: str = "hiring-agent", role: str = "manager") -> None:
        self._name = name
        self._role = role
        self._agent_id = uuid.uuid4()
        self._team_id = uuid.uuid4()

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
    def team_id(self) -> uuid.UUID:
        return self._team_id

    @property
    def squad_id(self) -> uuid.UUID | None:
        return None

    def send(self, recipient: object, message: object) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    def handle_user_message(self) -> bool:
        return False

    def serialize(self) -> dict:  # type: ignore[type-arg]
        return {
            "__actor_address__": True,
            "__actor_type__": "tests.test_command_models.SerializableActorAddress",
            "agent_id": str(self._agent_id),
            "name": self._name,
            "role": self._role,
            "team_id": str(self._team_id),
            "squad_id": "",
            "user_message": False,
        }

    def __repr__(self) -> str:
        return f"SerializableActorAddress(name={self._name})"


# ---------------------------------------------------------------------------
# CommandArg (AC #1, #2, #7)
# ---------------------------------------------------------------------------


class TestCommandArg:
    def test_construct_all_fields(self) -> None:
        arg = CommandArg(name="role", type="string", required=True, description="Role to hire")
        assert arg.name == "role"
        assert arg.type == "string"
        assert arg.required is True
        assert arg.description == "Role to hire"

    def test_description_defaults_to_none(self) -> None:
        arg = CommandArg(name="name", type="string", required=False)
        assert arg.description is None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"type": "string", "required": True},  # missing name
            {"name": "x", "required": True},  # missing type
            {"name": "x", "type": "string"},  # missing required
        ],
    )
    def test_missing_required_raises(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            CommandArg(**kwargs)  # type: ignore[arg-type]

    def test_roundtrip_preserves_values(self) -> None:
        arg = CommandArg(name="count", type="integer", required=True, description="How many")
        dumped = arg.model_dump()
        assert "__model__" in dumped
        restored = CommandArg.model_validate(dumped)
        assert isinstance(restored, CommandArg)
        assert restored.name == "count"
        assert restored.type == "integer"
        assert restored.required is True
        assert restored.description == "How many"


# ---------------------------------------------------------------------------
# CommandDescriptor (AC #3, #4, #7)
# ---------------------------------------------------------------------------


def _two_args() -> list[CommandArg]:
    return [
        CommandArg(name="role", type="string", required=True, description="Role"),
        CommandArg(name="name", type="string", required=False),
    ]


class TestCommandDescriptor:
    def test_construct_with_ordered_args(self) -> None:
        desc = CommandDescriptor(
            name="hire_member",
            description="Hire a new team member",
            args=_two_args(),
            tool_card="TeamTool",
        )
        assert desc.name == "hire_member"
        assert desc.description == "Hire a new team member"
        assert desc.tool_card == "TeamTool"
        assert len(desc.args) == 2
        assert all(isinstance(a, CommandArg) for a in desc.args)
        assert [a.name for a in desc.args] == ["role", "name"]

    def test_empty_args_is_valid(self) -> None:
        desc = CommandDescriptor(name="x", description="d", args=[], tool_card="T")
        assert desc.args == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"description": "d", "args": [], "tool_card": "T"},  # missing name
            {"name": "x", "args": [], "tool_card": "T"},  # missing description
            {"name": "x", "description": "d", "tool_card": "T"},  # missing args
            {"name": "x", "description": "d", "args": []},  # missing tool_card
        ],
    )
    def test_missing_required_raises(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            CommandDescriptor(**kwargs)  # type: ignore[arg-type]

    def test_roundtrip_preserves_arg_order_and_types(self) -> None:
        desc = CommandDescriptor(
            name="hire_member",
            description="Hire a member",
            args=_two_args(),
            tool_card="TeamTool",
        )
        restored = CommandDescriptor.model_validate(desc.model_dump())
        assert isinstance(restored, CommandDescriptor)
        assert [a.name for a in restored.args] == ["role", "name"]
        assert all(isinstance(a, CommandArg) for a in restored.args)
        assert restored.args[0].required is True
        assert restored.args[0].description == "Role"
        assert restored.args[1].required is False
        assert restored.args[1].description is None


# ---------------------------------------------------------------------------
# CommandsAnnouncedEvent (AC #5, #6, #7)
# ---------------------------------------------------------------------------


def _descriptor() -> CommandDescriptor:
    return CommandDescriptor(
        name="hire_member",
        description="Hire a member",
        args=_two_args(),
        tool_card="TeamTool",
    )


class TestCommandsAnnouncedEvent:
    def test_construct(self) -> None:
        agent = SerializableActorAddress()
        desc = _descriptor()
        event = CommandsAnnouncedEvent(agent=agent, commands=[desc])
        assert event.agent is agent
        assert event.commands == [desc]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"commands": []},  # missing agent
            {"agent": SerializableActorAddress()},  # missing commands
        ],
    )
    def test_missing_required_raises(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(ValidationError):
            CommandsAnnouncedEvent(**kwargs)  # type: ignore[arg-type]

    def test_roundtrip(self) -> None:
        agent = SerializableActorAddress(name="hiring-agent", role="manager")
        event = CommandsAnnouncedEvent(agent=agent, commands=[_descriptor()])
        dumped = event.model_dump()
        assert "__model__" in dumped

        restored = CommandsAnnouncedEvent.model_validate(dumped)
        # agent deserializes back to an equivalent ActorAddress
        assert isinstance(restored.agent, ActorAddress)
        assert isinstance(restored.agent, ActorAddressProxy)
        assert restored.agent.agent_id == agent.agent_id
        assert restored.agent.name == "hiring-agent"
        assert restored.agent.role == "manager"
        # nested descriptor/arg models are restored as real models, not dicts
        assert isinstance(restored.commands[0], CommandDescriptor)
        assert isinstance(restored.commands[0].args[0], CommandArg)
        assert restored.commands[0].name == "hire_member"
        assert restored.commands[0].args[0].name == "role"


# ---------------------------------------------------------------------------
# CommandNotRecognized (AC #8)
# ---------------------------------------------------------------------------


class TestCommandNotRecognized:
    def test_raisable_and_catchable(self) -> None:
        with pytest.raises(CommandNotRecognized):
            raise CommandNotRecognized("frobnicate")

    def test_is_exception_subclass(self) -> None:
        assert issubclass(CommandNotRecognized, Exception)

    def test_is_not_retriable_error_subclass(self) -> None:
        assert not issubclass(CommandNotRecognized, RetriableError)


# ---------------------------------------------------------------------------
# Public-API export (AC #9)
# ---------------------------------------------------------------------------


def test_public_api_exports() -> None:
    import akgentic.tool as tool_pkg

    for name in (
        "CommandArg",
        "CommandDescriptor",
        "CommandsAnnouncedEvent",
        "CommandNotRecognized",
    ):
        assert name in tool_pkg.__all__
        assert hasattr(tool_pkg, name)
