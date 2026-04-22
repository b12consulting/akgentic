"""Team management tool implementation.

Provides hire, fire, and team awareness capabilities as a reusable ToolCard
that can be attached to any agent.
"""

import logging
import random
from typing import Callable

from pydantic import Field

from akgentic.core.actor_address import ActorAddress
from akgentic.core.agent import Akgent
from akgentic.core.agent_card import AgentCard
from akgentic.core.orchestrator import Orchestrator
from akgentic.tool.core import (
    COMMAND,
    SYSTEM_PROMPT,
    TOOL_CALL,
    BaseToolParam,
    Channels,
    ToolCard,
    _resolve,
)
from akgentic.tool.errors import RetriableError
from akgentic.tool.event import TeamManagementToolObserver

logger = logging.getLogger(__name__)


class HireTeamMember(BaseToolParam):
    """Hire new team members by role."""

    expose: set[Channels] = {TOOL_CALL, COMMAND}


class FireTeamMember(BaseToolParam):
    """Fire existing team members by name."""

    expose: set[Channels] = {TOOL_CALL, COMMAND}


class GetTeamRoster(BaseToolParam):
    """Get current team roster as system prompt."""

    expose: set[Channels] = {SYSTEM_PROMPT, COMMAND}


class GetRoleProfiles(BaseToolParam):
    """Get available role profiles as system prompt."""

    expose: set[Channels] = {SYSTEM_PROMPT, COMMAND}


def _hire_single_member(
    orchestrator_proxy: Orchestrator,
    observer: TeamManagementToolObserver,
    role: str,
    name: str | None,
    existing_names: set[str],
    agent_catalog: list[AgentCard] | None = None,
) -> ActorAddress:
    """Core hire logic for a single member.

    Args:
        orchestrator_proxy: Proxy to the orchestrator actor.
        observer: TeamManagementToolObserver for actor creation and hooks.
        role: Role to hire.
        name: Optional specific name. If None, auto-generated.
        existing_names: Set of existing member names (for uniqueness).
        agent_catalog: Pre-fetched catalog. If None, fetched from orchestrator.

    Returns:
        ActorAddress: Address of the newly hired child actor.

    Raises:
        RetriableError: If no agent card found for the role.
        ValueError: If agent class is a string (configuration error).
    """
    if agent_catalog is None:
        agent_catalog = orchestrator_proxy.get_agent_catalog()
    agent_card = next((card for card in agent_catalog if card.role == role), None)
    if agent_card is None:
        available_roles = orchestrator_proxy.get_available_roles()
        raise RetriableError(
            f"Hire error - cannot find agent card for role '{role}'. "
            f"Available roles: {available_roles}"
        )

    actor_class = agent_card.get_agent_class()
    if isinstance(actor_class, str):
        raise ValueError(f"Hire error - agent class for role {role} is a string, not a type.")

    if name is None:
        role_prefix = f"@{role.replace(' ', '')}"
        suffix = random.randint(100, 999)
        child_name = f"{role_prefix}{suffix}"
        while child_name in existing_names:
            suffix += 1
            child_name = f"{role_prefix}{suffix}"
    else:
        child_name = name
        if not isinstance(child_name, str):
            raise RetriableError("Hire error - member name must be a string.")
        child_name = child_name.strip()
        if not child_name:
            raise RetriableError("Hire error - member name cannot be empty.")
        if child_name in existing_names:
            raise RetriableError(
                f"Hire error - member name '{child_name}' already exists. "
                "Please choose a unique name."
            )

    agent_card_config = agent_card.get_config_copy()
    agent_card_config.name = child_name
    agent_card_config.role = role

    child_address = observer.createActor(actor_class, config=agent_card_config)
    observer.on_hire(child_address)

    logger.info(f"Hired {role} agent: {child_name} at {child_address}")
    return child_address


def _fire_single_member(
    orchestrator_proxy: Orchestrator,
    observer: TeamManagementToolObserver,
    name: str,
) -> str:
    """Core fire logic for a single member.

    Args:
        orchestrator_proxy: Proxy to the orchestrator actor.
        observer: TeamManagementToolObserver for hooks.
        name: Name of the member to fire.

    Returns:
        The fired member's name.

    Raises:
        RetriableError: If member not found in team.
    """
    address = orchestrator_proxy.get_team_member(name)
    if address is None:
        team_members = [member.name for member in orchestrator_proxy.get_team()]
        raise RetriableError(
            f"Fire error - member '{name}' not part of the team. "
            f"Current team members: {team_members}"
        )

    observer.proxy_ask(address, Akgent).stop()
    observer.on_fire(address)
    logger.info(f"Fired team member: {name}")
    return name


class TeamTool(ToolCard):
    """Team management tool for hiring, firing, and team awareness.

    Provides:
    - hire_members(roles: list[str]) -> str: Hire team members
    - fire_members(names: list[str]) -> str: Fire team members
    - Team roster system prompt: Current team composition
    - Role profiles system prompt: Available roles and descriptions
    """

    name: str = "Team"
    description: str = "Team management tool for hiring, firing, and team awareness"

    hire_team_members: HireTeamMember | bool = Field(
        default=True, description="Enable hiring team members (default: True)"
    )
    fire_team_members: FireTeamMember | bool = Field(
        default=True, description="Enable firing team members (default: True)"
    )
    get_role_profiles: GetRoleProfiles | bool = Field(
        default=True, description="Include role profiles in system prompt (default: True)"
    )
    get_team_roster: GetTeamRoster | bool = Field(
        default=True, description="Include team roster in system prompt (default: True)"
    )

    def observer(self, observer: TeamManagementToolObserver) -> "TeamTool":
        """Attach observer and set up the orchestrator proxy.

        Requires a TeamManagementToolObserver for actor system access.

        Args:
            observer: Observer implementing TeamManagementToolObserver protocol

        Returns:
            Self, enabling method chaining

        Raises:
            ValueError: If observer.orchestrator is None
        """
        self._observer = observer
        if observer.orchestrator is None:
            raise ValueError("TeamTool requires access to the orchestrator.")

        self._orchestrator_proxy = observer.proxy_ask(observer.orchestrator, Orchestrator)
        return self

    def get_system_prompts(self) -> list[Callable]:
        """Get dynamic system prompts for team context.

        Returns:
            List of callable system prompts (roster and/or profiles)
        """
        prompts: list[Callable] = []

        gtr = _resolve(self.get_team_roster, GetTeamRoster)
        if gtr and SYSTEM_PROMPT in gtr.expose:
            prompts.append(self._team_roster_prompt_factory(gtr))

        grp = _resolve(self.get_role_profiles, GetRoleProfiles)
        if grp and SYSTEM_PROMPT in grp.expose:
            prompts.append(self._role_profiles_prompt_factory(grp))

        return prompts

    def get_tools(self) -> list[Callable]:
        """Get LLM-callable tools for team management.

        Returns:
            List of callable tools (hire_members and/or fire_members)
        """
        tools: list[Callable] = []

        htm = _resolve(self.hire_team_members, HireTeamMember)
        if htm and TOOL_CALL in htm.expose:
            tools.append(self._hire_members_factory(htm))

        ftm = _resolve(self.fire_team_members, FireTeamMember)
        if ftm and TOOL_CALL in ftm.expose:
            tools.append(self._fire_members_factory(ftm))

        return tools

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        """Get programmatic commands for inter-agent orchestration.

        Returns:
            Dict mapping param class to callable.
        """
        commands: dict[type[BaseToolParam], Callable] = {}

        htm = _resolve(self.hire_team_members, HireTeamMember)
        if htm and COMMAND in htm.expose:
            commands[HireTeamMember] = self._hire_member_command_factory(htm)

        ftm = _resolve(self.fire_team_members, FireTeamMember)
        if ftm and COMMAND in ftm.expose:
            commands[FireTeamMember] = self._fire_member_command_factory(ftm)

        gtr = _resolve(self.get_team_roster, GetTeamRoster)
        if gtr and COMMAND in gtr.expose:
            commands[GetTeamRoster] = self._team_roster_prompt_factory(gtr)

        grp = _resolve(self.get_role_profiles, GetRoleProfiles)
        if grp and COMMAND in grp.expose:
            commands[GetRoleProfiles] = self._role_profiles_prompt_factory(grp)

        return commands

    def _hire_members_factory(self, params: HireTeamMember) -> Callable:
        """Create hire_members tool callable.

        Args:
            params: Configuration for hire capability

        Returns:
            Callable that hires team members
        """
        orchestrator_proxy = self._orchestrator_proxy
        observer = self._observer

        def hire_members(roles: list[str]) -> str:
            """Hire multiple new team members with the given roles.

            Creates new agent actors with specified roles. Names are auto-generated
            as @<Role><RandomNumber>. Validates roles against available roles.

            Note: Should only be used when explicitly requested by user to prevent
            unnecessary agent proliferation.

            Args:
                roles: List of roles to hire (each must be in available_roles)

            Returns:
                Confirmation message with hired member names
                (e.g., 'Members hired: [@Developer123, @Tester456]')
            """
            if not roles:
                raise RetriableError("No roles provided. Specify at least one role to hire.")

            hired_members = []
            errors = []
            existing_names = {member.name for member in orchestrator_proxy.get_team()}
            agent_catalog = orchestrator_proxy.get_agent_catalog()

            for role in roles:
                try:
                    child_address = _hire_single_member(
                        orchestrator_proxy,
                        observer,
                        role,
                        None,
                        existing_names,
                        agent_catalog=agent_catalog,
                    )
                    existing_names.add(child_address.name)
                    hired_members.append(child_address.name)
                except RetriableError:
                    errors.append(role)

            if errors:
                available_roles = orchestrator_proxy.get_available_roles()
                error_details = "; ".join([f"role '{e}'" for e in errors])
                error_message = f"Hire errors - cannot find agent card(s) for {error_details}. "
                error_message += f"Available roles: {available_roles}"
                if hired_members:
                    error_message = (
                        f"Partial success - Members hired: {hired_members}. " + error_message
                    )
                raise RetriableError(error_message)

            return f"Members hired: {hired_members}"

        hire_members.__doc__ = params.format_docstring(hire_members.__doc__)
        return hire_members

    def _hire_member_command_factory(self, params: HireTeamMember) -> Callable:
        """Create hire_member command callable.

        Args:
            params: Configuration for hire capability

        Returns:
            Callable that hires a single team member
        """
        orchestrator_proxy = self._orchestrator_proxy
        observer = self._observer

        def hire_member(role: str, name: str | None = None):
            """Hire a single new team member with the given role.

            Creates a new agent actor with the specified role. If no name is
            provided, one is auto-generated as @<Role><RandomNumber>.

            Args:
                role: Role to hire (must be in available_roles)
                name: Optional specific name for the member

            Returns:
                Tuple of (member_name, member_address)
            """
            existing_names = {member.name for member in orchestrator_proxy.get_team()}
            return _hire_single_member(orchestrator_proxy, observer, role, name, existing_names)

        return hire_member

    def _fire_members_factory(self, params: FireTeamMember) -> Callable:
        """Create fire_members tool callable.

        Args:
            params: Configuration for fire capability

        Returns:
            Callable that fires team members
        """
        orchestrator_proxy = self._orchestrator_proxy
        observer = self._observer

        def fire_members(names: list[str]) -> str:
            """Fire multiple team members with the given names.

            Stops member actors and removes them from team. Member names typically
            start with '@' (e.g., '@Developer123').

            Note: Should only be used when explicitly requested by user to prevent
            accidental team disruption.

            Args:
                names: List of member names to fire (e.g., ['@Developer123', '@Tester456'])

            Returns:
                Combined confirmation messages (e.g., "Members fired: @Developer123, @Tester456")
            """
            if not names:
                raise RetriableError("No names provided. Specify at least one member name to fire.")

            fired_members = []
            errors = []
            for name in names:
                try:
                    _fire_single_member(orchestrator_proxy, observer, name)
                    fired_members.append(name)
                except RetriableError:
                    errors.append(name)
                    logger.error(f"Fire error, team member not part of the team: {name}")

            if errors:
                team_members = [member.name for member in orchestrator_proxy.get_team()]
                error_details = "; ".join([f"member '{e}'" for e in errors])
                error_message = f"Fire errors - {error_details} not part of the team. "
                error_message += f"Current team members: {team_members}"
                if fired_members:
                    error_message = (
                        f"Partial success - Members fired: {fired_members}. " + error_message
                    )
                raise RetriableError(error_message)

            return f"Members fired: {', '.join(fired_members)}"

        fire_members.__doc__ = params.format_docstring(fire_members.__doc__)
        return fire_members

    def _fire_member_command_factory(self, params: FireTeamMember) -> Callable:
        """Create fire_member command callable.

        Args:
            params: Configuration for fire capability

        Returns:
            Callable that fires a single team member
        """
        orchestrator_proxy = self._orchestrator_proxy
        observer = self._observer

        def fire_member(name: str) -> str:
            """Fire a team member with the given name.

            Stops the member actor and removes them from the team.

            Args:
                name: Member name to fire (e.g., '@Developer123')

            Returns:
                Confirmation message (e.g., "Member @Developer123 has been fired.")
            """
            _fire_single_member(orchestrator_proxy, observer, name)
            return f"Member {name} has been fired."

        return fire_member

    def _team_roster_prompt_factory(self, params: GetTeamRoster) -> Callable:
        """Create team roster system prompt callable.

        Args:
            params: Configuration for roster prompt

        Returns:
            Callable that generates team roster prompt
        """
        orchestrator_proxy = self._orchestrator_proxy
        observer = self._observer

        def team_roster_prompt() -> str:
            """Get current team composition as context.

            Returns formatted list of team members with their roles, marking the
            current agent with '[you]'. Excludes tool actors (names starting with '#').

            Returns:
                Formatted team roster or empty string if no members
            """
            try:
                team_members = orchestrator_proxy.get_team()
                if not team_members:
                    return ""

                team_members_names = [
                    f"{member.name} (role: {member.role})"
                    + (" - [you]" if member.name == observer.myAddress.name else "")
                    for member in team_members
                    if not member.name.startswith("#")  # Exclude tool actors
                ]

                if not team_members_names:
                    return ""

                return "**Team members:**\n" + "\n".join(team_members_names)
            except Exception:
                logger.error("Failed to get team roster", exc_info=True)
                return "Cannot get team roster..."

        return team_roster_prompt

    def _role_profiles_prompt_factory(self, params: GetRoleProfiles) -> Callable:
        """Create role profiles system prompt callable.

        Args:
            params: Configuration for profiles prompt

        Returns:
            Callable that generates role profiles prompt
        """
        orchestrator_proxy = self._orchestrator_proxy

        def role_profiles_prompt() -> str:
            """Get available team roles and their descriptions.

            Returns formatted list of roles with descriptions and skills from the
            agent catalog.

            Returns:
                Formatted role profiles or empty string if no roles
            """
            try:
                agent_catalog = orchestrator_proxy.get_agent_catalog()
                if not agent_catalog:
                    return ""

                profiles = []
                for card in agent_catalog:
                    skills_str = ", ".join(card.skills) if card.skills else "none"
                    profiles.append(f"{card.role}: {card.description} (Skills: {skills_str})")

                if not profiles:
                    return ""

                return "**Available team roles:**\n" + "\n".join(profiles)
            except Exception:
                logger.error("Failed to get role profiles", exc_info=True)
                return "Cannot get role profiles..."

        return role_profiles_prompt
