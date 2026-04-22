"""Agent profile cards for team capability discovery.

AgentCards describe available agent profiles/roles in a team, enabling
dynamic discovery of capabilities and agent creation patterns.

IMPORTANT: Always use get_config() to obtain config for agent instantiation.
This ensures each agent gets an independent copy, preventing shared mutable state bugs."""

from __future__ import annotations

from typing import Any, cast

from pydantic import Field

from akgentic.core.agent_config import BaseConfig
from akgentic.core.utils.serializer import SerializableBaseModel


class AgentCard(SerializableBaseModel):
    """Describes an agent profile/role available in the team.

    AgentCards form a capability catalog, allowing agents to discover
    what profiles exist and how to work with them. This is not instance
    tracking—it's a profile directory.

    Example:
        >>> card = AgentCard(
        ...     role="ResearchAgent",
        ...     description="Performs web research and data gathering",
        ...     skills=["web_search", "pdf_extraction"],
        ...     agent_class="examples.multi_agent.ResearchAgent",
        ...     config=BaseConfig(name="research", role="ResearchAgent"),
        ...     routes_to=["WriterAgent", "AnalystAgent"]
        ... )
        >>> # Now other agents can discover this profile and its config
        >>> print(card.skills)
        ['web_search', 'pdf_extraction']
        >>> card.can_route_to("WriterAgent")
        True

    Attributes:
        role: Agent role/type identifier (e.g., "ResearchAgent")
        description: Human-readable description of what this agent does
        skills: List of capabilities this agent provides
        agent_class: Fully qualified class name (str) or actual class (type) for instantiation
        config: Default BaseConfig (or subclass) for this profile
        routes_to: List of roles this agent can send requests to.
                   Empty list means can route to any role (no restrictions).
                   Agents can always respond to requests regardless of this field.
        metadata: Extensible key-value storage for custom attributes
    """

    role: str
    description: str
    skills: list[str]
    agent_class: str | type
    config: BaseConfig = Field(default_factory=BaseConfig)
    routes_to: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_config_copy(self) -> BaseConfig:
        """Get a deep copy of the config as BaseConfig instance.

        **ALWAYS use this method when creating agents from an AgentCard.**
        Returns a fresh copy to prevent shared mutable state across multiple
        agent instances created from the same AgentCard.

        Returns:
            Deep copy of BaseConfig instance (converts dict if needed)

        Example:
            >>> card = orchestrator.get_agent_card("Developer")
            >>> config = card.get_config()  # Safe - returns independent copy
            >>> config.name = "alice"  # Won't affect other agents
        """

        if isinstance(self.config, BaseConfig):
            return self.config.model_copy(deep=True)

        return BaseConfig(**self.config)

    def get_agent_class(self) -> type:
        """Get the agent class as a type object.

        Returns:
            Agent class as a type object (converts from string if needed)

        Raises:
            ValueError: If agent_class is empty or not a fully qualified
                dotted path (e.g. ``"mypackage.agents.ResearchAgent"``).
            ImportError: If the module cannot be imported.
            AttributeError: If the class is not found in the module.
        """
        if isinstance(self.agent_class, type):
            return self.agent_class

        if not self.agent_class or "." not in self.agent_class:
            raise ValueError(
                f"agent_class must be a fully qualified dotted path "
                f"(e.g. 'mypackage.agents.MyAgent'), got: {self.agent_class!r}"
            )

        components = self.agent_class.split(".")
        module_path = ".".join(components[:-1])
        class_name = components[-1]

        module = __import__(module_path, fromlist=[class_name])
        return cast(type, getattr(module, class_name))

    def has_skill(self, skill: str) -> bool:
        """Check if this profile has a specific skill.

        Args:
            skill: Skill to check for

        Returns:
            True if skill is in the profile's skill list
        """
        return skill in self.skills

    def can_route_to(self, role: str) -> bool:
        """Check if this profile can send requests to a specific role.

        An empty routes_to list means no restrictions (can route to anyone).
        Otherwise, the target role must be in the routes_to list.

        Note: Agents can always RESPOND to requests from any role.
        This only controls which roles an agent can proactively SEND to.

        Args:
            role: Target role to check routing permission for

        Returns:
            True if this agent profile can send requests to the target role
        """
        if not self.routes_to:
            return True  # No restrictions - can route to any role
        return role in self.routes_to
