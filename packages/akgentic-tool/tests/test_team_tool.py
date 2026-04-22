"""Unit tests for TeamTool."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from akgentic.core import ActorAddressProxy
from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.agent_config import BaseConfig
from akgentic.core.orchestrator import Orchestrator

from akgentic.tool.core import TOOL_CALL
from akgentic.tool.errors import RetriableError
from akgentic.tool.event import TeamManagementToolObserver
from akgentic.tool.team import (
    FireTeamMember,
    GetRoleProfiles,
    GetTeamRoster,
    HireTeamMember,
    TeamTool,
)


def create_test_address(name: str, role: str = "Agent") -> ActorAddressProxy:
    """Create a mock ActorAddress for testing."""
    return ActorAddressProxy(
        {
            "__actor_address__": True,
            "__actor_type__": "test.Agent",
            "agent_id": str(uuid.uuid4()),
            "name": name,
            "role": role,
            "team_id": str(uuid.uuid4()),
            "squad_id": str(uuid.uuid4()),
            "user_message": True,
        }
    )


def mock_observer() -> Mock:
    """Create a mock TeamManagementToolObserver."""
    observer = Mock(spec=TeamManagementToolObserver)
    observer.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer.myAddress = create_test_address("@Manager", "Manager")

    # Mock orchestrator proxy
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team.return_value = []
    observer.proxy_ask.return_value = orchestrator_mock

    return observer


def test_team_tool_observer_attachment():
    """TeamTool.observer() sets up orchestrator proxy."""
    observer = mock_observer()

    tool = TeamTool()
    result = tool.observer(observer)

    assert result is tool  # Method chaining
    assert tool._observer is observer
    observer.proxy_ask.assert_called_once_with(observer.orchestrator, Orchestrator)


def test_team_tool_observer_requires_orchestrator():
    """TeamTool.observer() raises ValueError if no orchestrator."""
    observer = Mock(spec=TeamManagementToolObserver)
    observer.orchestrator = None

    tool = TeamTool()
    with pytest.raises(ValueError, match="requires access to the orchestrator"):
        tool.observer(observer)


def test_team_tool_get_tools_default():
    """TeamTool.get_tools() returns hire + fire by default."""
    tool = TeamTool()
    tool.observer(mock_observer())

    tools = tool.get_tools()
    assert len(tools) == 2
    assert tools[0].__name__ == "hire_members"
    assert tools[1].__name__ == "fire_members"


def test_team_tool_get_tools_disabled():
    """TeamTool.get_tools() excludes disabled capabilities."""
    tool = TeamTool(hire_team_members=False, fire_team_members=False)
    tool.observer(mock_observer())

    tools = tool.get_tools()
    assert len(tools) == 0


def test_team_tool_get_tools_partial():
    """TeamTool.get_tools() includes only enabled capabilities."""
    tool = TeamTool(hire_team_members=True, fire_team_members=False)
    tool.observer(mock_observer())

    tools = tool.get_tools()
    assert len(tools) == 1
    assert tools[0].__name__ == "hire_members"


def test_hire_members_tool_execution():
    """hire_members validates role, creates actor, calls on_hire hook."""
    # Mock AgentCard with agent_class
    agent_card = Mock(spec=AgentCard)
    agent_card.role = "Developer"
    agent_card.agent_class = Mock  # Dynamic agent class (type)

    config_mock = Mock(spec=BaseConfig)
    agent_card.get_config_copy.return_value = config_mock

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = [agent_card]
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.myAddress = create_test_address("@Manager", "Manager")
    observer_mock.proxy_ask.return_value = orchestrator_mock
    observer_mock.createActor.return_value = create_test_address("@Developer123", "Developer")

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_members = tool.get_tools()[0]

    result = hire_members(["Developer"])

    assert isinstance(result, str)
    assert "Members hired:" in result
    assert "@Developer" in result
    observer_mock.createActor.assert_called_once()  # Agent primitive called
    observer_mock.on_hire.assert_called_once()  # Hook called


def test_hire_members_empty_list():
    """hire_members raises RetriableError for empty roles list."""
    tool = TeamTool()
    tool.observer(mock_observer())
    hire_members = tool.get_tools()[0]

    with pytest.raises(RetriableError, match="No roles provided"):
        hire_members([])


def test_fire_members_empty_list():
    """fire_members raises RetriableError for empty names list."""
    tool = TeamTool()
    tool.observer(mock_observer())
    fire_members = tool.get_tools()[1]

    with pytest.raises(RetriableError, match="No names provided"):
        fire_members([])


def test_hire_members_invalid_role():
    """hire_members raises RetriableError for invalid role."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = []  # Empty catalog
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_members = tool.get_tools()[0]

    with pytest.raises(RetriableError, match="cannot find agent card"):
        hire_members(["InvalidRole"])


def test_hire_members_string_agent_class():
    """hire_members raises ValueError if get_agent_class() returns a string."""
    # Mock AgentCard with get_agent_class returning a string
    agent_card = Mock(spec=AgentCard)
    agent_card.role = "Developer"
    agent_card.get_agent_class.return_value = "some.module.Agent"  # String instead of type

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = [agent_card]
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_members = tool.get_tools()[0]

    # ValueError (not RetriableError) because LLM cannot fix configuration errors
    with pytest.raises(ValueError, match="is a string, not a type"):
        hire_members(["Developer"])


def test_fire_members_tool_execution():
    """fire_members looks up address, stops via proxy_ask, calls on_fire hook."""
    member_address = Mock()
    member_address.name = "@Developer123"
    member_address.role = "Developer"

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team_member.return_value = member_address

    stop_proxy_mock = Mock()

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")

    def proxy_ask_side_effect(target, actor_type):
        if actor_type is Orchestrator:
            return orchestrator_mock
        if actor_type is Akgent:
            return stop_proxy_mock
        return Mock()

    observer_mock.proxy_ask.side_effect = proxy_ask_side_effect

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_members = tool.get_tools()[1]

    result = fire_members(["@Developer123"])

    assert "Developer123" in result
    assert "fired" in result.lower()
    orchestrator_mock.get_team_member.assert_called_once_with("@Developer123")
    observer_mock.proxy_ask.assert_any_call(member_address, Akgent)
    stop_proxy_mock.stop.assert_called_once()  # proxy-based stop called
    observer_mock.on_fire.assert_called_once()  # Hook called


def test_fire_members_not_found():
    """fire_members raises RetriableError if member not found."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team_member.return_value = None
    orchestrator_mock.get_team.return_value = [create_test_address("@Developer456", "Developer")]

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_members = tool.get_tools()[1]

    with pytest.raises(RetriableError, match="Fire errors"):
        fire_members(["@Developer123"])


def test_team_tool_get_system_prompts_default():
    """TeamTool.get_system_prompts() returns roster + profiles by default."""
    tool = TeamTool()
    tool.observer(mock_observer())

    prompts = tool.get_system_prompts()
    assert len(prompts) == 2
    # Callables don't have __name__ reliably, check they're callable
    assert all(callable(p) for p in prompts)


def test_team_tool_get_system_prompts_disabled():
    """TeamTool.get_system_prompts() excludes disabled prompts."""
    tool = TeamTool(
        get_team_roster=GetTeamRoster(expose=set()),
        get_role_profiles=GetRoleProfiles(expose=set()),
    )
    tool.observer(mock_observer())

    prompts = tool.get_system_prompts()
    assert len(prompts) == 0


def test_team_roster_prompt():
    """team_roster_prompt returns formatted team members."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team.return_value = [
        create_test_address("@Manager", "Manager"),
        create_test_address("@Developer", "Developer"),
        create_test_address("#PlanningTool", "ToolActor"),  # Should be excluded
    ]

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.myAddress = create_test_address("@Manager", "Manager")
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    prompts = tool.get_system_prompts()
    roster_prompt = prompts[0]

    result = roster_prompt()

    assert "Team members:" in result
    assert "@Manager (role: Manager)" in result
    assert "[you]" in result  # Current agent marked
    assert "@Developer (role: Developer)" in result
    assert "#PlanningTool" not in result  # Tool actors excluded


def test_team_roster_prompt_empty():
    """team_roster_prompt returns empty string if no members."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    prompts = tool.get_system_prompts()
    roster_prompt = prompts[0]

    result = roster_prompt()
    assert result == ""


def test_role_profiles_prompt():
    """role_profiles_prompt returns role descriptions + skills from agent catalog."""
    card1 = Mock(spec=AgentCard)
    card1.role = "Developer"
    card1.description = "Writes code"
    card1.skills = ["python", "testing"]

    card2 = Mock(spec=AgentCard)
    card2.role = "Tester"
    card2.description = "Tests code"
    card2.skills = ["selenium", "pytest"]

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_agent_catalog.return_value = [card1, card2]

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    prompts = tool.get_system_prompts()
    profiles_prompt = prompts[1]

    result = profiles_prompt()

    assert "Available team roles:" in result
    assert "Developer: Writes code (Skills: python, testing)" in result
    assert "Tester: Tests code (Skills: selenium, pytest)" in result


def test_role_profiles_prompt_empty():
    """role_profiles_prompt returns empty string if no roles."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_agent_catalog.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    prompts = tool.get_system_prompts()
    profiles_prompt = prompts[1]

    result = profiles_prompt()
    assert result == ""


def test_hire_members_custom_instructions():
    """HireTeamMembers.instructions appended to docstring."""
    tool = TeamTool(
        hire_team_members=HireTeamMember(instructions="Only hire when explicitly requested.")
    )
    tool.observer(mock_observer())

    hire_members = tool.get_tools()[0]
    assert hire_members.__doc__ is not None
    assert "Only hire when explicitly requested" in hire_members.__doc__


def test_fire_members_custom_instructions():
    """FireTeamMembers.instructions appended to docstring."""
    tool = TeamTool(
        fire_team_members=FireTeamMember(instructions="Only fire when explicitly requested.")
    )
    tool.observer(mock_observer())

    fire_members = tool.get_tools()[1]
    assert fire_members.__doc__ is not None
    assert "Only fire when explicitly requested" in fire_members.__doc__


def test_hire_members_batch_with_multiple_errors():
    """hire_members collects all errors before raising."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = []  # Empty catalog - no cards found
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_members = tool.get_tools()[0]

    # Try to hire multiple invalid roles
    with pytest.raises(RetriableError) as exc_info:
        hire_members(["InvalidRole1", "InvalidRole2"])

    # Should contain both errors
    error_msg = str(exc_info.value)
    assert "Hire errors" in error_msg
    assert "InvalidRole1" in error_msg
    assert "InvalidRole2" in error_msg


def test_hire_members_batch_partial_success():
    """hire_members continues on errors and hires valid roles."""
    agent_card = Mock(spec=AgentCard)
    agent_card.role = "Developer"
    agent_card.agent_class = Mock
    agent_card.get_config_copy.return_value = Mock(spec=BaseConfig)

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = [agent_card]
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.myAddress = create_test_address("@Manager", "Manager")
    observer_mock.proxy_ask.return_value = orchestrator_mock
    observer_mock.createActor.return_value = create_test_address("@Developer123", "Developer")

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_members = tool.get_tools()[0]

    # Mix valid and invalid roles
    with pytest.raises(RetriableError) as exc_info:
        hire_members(["Developer", "InvalidRole"])

    # Should have hired the valid one before failing
    assert observer_mock.createActor.call_count == 1
    # Error should mention partial success and the invalid role
    assert "Partial success" in str(exc_info.value)
    assert "InvalidRole" in str(exc_info.value)


def test_fire_members_batch_with_multiple_errors():
    """fire_members collects all errors before raising."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team_member.return_value = None
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_members = tool.get_tools()[1]

    # Try to fire multiple non-existent members
    with pytest.raises(RetriableError) as exc_info:
        fire_members(["@NonExistent1", "@NonExistent2"])

    # Should contain both errors
    error_msg = str(exc_info.value)
    assert "Fire errors" in error_msg
    assert "NonExistent1" in error_msg
    assert "NonExistent2" in error_msg


def test_fire_members_batch_partial_success():
    """fire_members continues on errors and fires valid members via proxy-based stop."""
    valid_address = Mock()
    valid_address.name = "@Valid123"
    valid_address.role = "Developer"

    orchestrator_mock = Mock(spec=Orchestrator)

    def get_member_side_effect(name):
        if name == "@Valid123":
            return valid_address
        return None

    orchestrator_mock.get_team_member.side_effect = get_member_side_effect
    orchestrator_mock.get_team.return_value = []

    stop_proxy_mock = Mock()

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")

    def proxy_ask_side_effect(target, actor_type):
        if actor_type is Orchestrator:
            return orchestrator_mock
        if actor_type is Akgent:
            return stop_proxy_mock
        return Mock()

    observer_mock.proxy_ask.side_effect = proxy_ask_side_effect

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_members = tool.get_tools()[1]

    # Mix valid and invalid members
    with pytest.raises(RetriableError) as exc_info:
        fire_members(["@Valid123", "@NonExistent"])

    # Should have stopped the valid one via proxy-based stop
    observer_mock.proxy_ask.assert_any_call(valid_address, Akgent)
    stop_proxy_mock.stop.assert_called_once()
    # Error should mention partial success and the invalid member
    assert "Partial success" in str(exc_info.value)
    assert "NonExistent" in str(exc_info.value)


# ── Command tests ──────────────────────────────────────────────────────────


def test_team_tool_get_commands_default():
    """TeamTool.get_commands() returns dict keyed by param class with 4 commands."""
    tool = TeamTool()
    tool.observer(mock_observer())

    commands = tool.get_commands()
    assert len(commands) == 4
    assert HireTeamMember in commands
    assert FireTeamMember in commands
    assert GetTeamRoster in commands
    assert GetRoleProfiles in commands
    assert all(callable(c) for c in commands.values())


def test_team_tool_get_commands_disabled():
    """TeamTool.get_commands() excludes fully disabled capabilities."""
    tool = TeamTool(
        hire_team_members=False,
        fire_team_members=False,
        get_team_roster=False,
        get_role_profiles=False,
    )
    tool.observer(mock_observer())

    commands = tool.get_commands()
    assert commands == {}


def test_team_tool_get_commands_partial():
    """TeamTool.get_commands() respects per-capability expose sets."""
    tool = TeamTool(
        hire_team_members=HireTeamMember(expose={TOOL_CALL}),  # no command
        fire_team_members=True,  # default includes command
    )
    tool.observer(mock_observer())

    commands = tool.get_commands()
    # hire excluded (no command in expose), fire + roster + profiles = 3
    assert len(commands) == 3
    assert HireTeamMember not in commands
    assert FireTeamMember in commands


def test_hire_member_command_execution():
    """hire_member command hires a single member and returns (name, address)."""
    agent_card = Mock(spec=AgentCard)
    agent_card.role = "Developer"
    agent_card.agent_class = Mock

    config_mock = Mock(spec=BaseConfig)
    agent_card.get_config_copy.return_value = config_mock

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_agent_catalog.return_value = [agent_card]
    orchestrator_mock.get_team.return_value = []

    address = create_test_address("@Developer123", "Developer")

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.myAddress = create_test_address("@Manager", "Manager")
    observer_mock.proxy_ask.return_value = orchestrator_mock
    observer_mock.createActor.return_value = address

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_member = tool.get_commands()[HireTeamMember]

    result = hire_member("Developer")

    assert isinstance(result, ActorAddress)
    assert result.name == "@Developer123"
    observer_mock.createActor.assert_called_once()
    observer_mock.on_hire.assert_called_once()


def test_hire_member_command_with_name():
    """hire_member command uses provided name."""
    agent_card = Mock(spec=AgentCard)
    agent_card.role = "Developer"
    agent_card.agent_class = Mock

    config_mock = Mock(spec=BaseConfig)
    agent_card.get_config_copy.return_value = config_mock

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_agent_catalog.return_value = [agent_card]
    orchestrator_mock.get_team.return_value = []

    address = create_test_address("@MyDev", "Developer")

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.myAddress = create_test_address("@Manager", "Manager")
    observer_mock.proxy_ask.return_value = orchestrator_mock
    observer_mock.createActor.return_value = address

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_member = tool.get_commands()[HireTeamMember]

    result = hire_member("Developer", "@MyDev")

    assert result.name == "@MyDev"
    observer_mock.createActor.assert_called_once()


def test_hire_member_command_invalid_role():
    """hire_member command raises RetriableError for invalid role."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_available_roles.return_value = ["Developer"]
    orchestrator_mock.get_agent_catalog.return_value = []
    orchestrator_mock.get_team.return_value = []

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    hire_member = tool.get_commands()[HireTeamMember]

    with pytest.raises(RetriableError, match="cannot find agent card"):
        hire_member("InvalidRole")


def test_fire_member_command_execution():
    """fire_member command fires a single member via proxy-based stop."""
    member_address = Mock()
    member_address.name = "@Developer123"
    member_address.role = "Developer"

    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team_member.return_value = member_address

    stop_proxy_mock = Mock()

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")

    def proxy_ask_side_effect(target, actor_type):
        if actor_type is Orchestrator:
            return orchestrator_mock
        if actor_type is Akgent:
            return stop_proxy_mock
        return Mock()

    observer_mock.proxy_ask.side_effect = proxy_ask_side_effect

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_member = tool.get_commands()[FireTeamMember]

    result = fire_member("@Developer123")

    assert "Developer123" in result
    assert "fired" in result.lower()
    observer_mock.proxy_ask.assert_any_call(member_address, Akgent)
    stop_proxy_mock.stop.assert_called_once()  # proxy-based stop called
    observer_mock.on_fire.assert_called_once()


def test_fire_member_command_not_found():
    """fire_member command raises RetriableError if member not found."""
    orchestrator_mock = Mock(spec=Orchestrator)
    orchestrator_mock.get_team_member.return_value = None
    orchestrator_mock.get_team.return_value = [create_test_address("@Developer456", "Developer")]

    observer_mock = Mock(spec=TeamManagementToolObserver)
    observer_mock.orchestrator = create_test_address("@Orchestrator", "Orchestrator")
    observer_mock.proxy_ask.return_value = orchestrator_mock

    tool = TeamTool()
    tool.observer(observer_mock)
    fire_member = tool.get_commands()[FireTeamMember]

    with pytest.raises(RetriableError, match="Fire error"):
        fire_member("@Developer123")
