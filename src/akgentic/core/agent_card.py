"""Agent profile cards for team capability discovery.

AgentCards describe available agent profiles/roles in a team, enabling
dynamic discovery of capabilities and agent creation patterns.

IMPORTANT: Always use get_config() to obtain config for agent instantiation.
This ensures each agent gets an independent copy, preventing shared mutable state bugs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, get_args, get_origin

from pydantic import Field, model_validator

from akgentic.core.agent_config import BaseConfig
from akgentic.core.utils import import_class
from akgentic.core.utils.serializer import SerializableBaseModel

if TYPE_CHECKING:
    pass


# Cache for resolved ConfigType per agent class — walking __orig_bases__ is
# non-trivial, and AgentCard.model_validate is hot on catalog load.
_CONFIG_TYPE_CACHE: dict[type, type[BaseConfig] | None] = {}


def _resolve_agent_class(value: str | type) -> type:
    """Resolve ``agent_class`` (str FQCN or type) to the actual class.

    Thin wrapper around :func:`akgentic.core.utils.import_class` that adds the
    type short-circuit and a clearer error for empty / unqualified inputs.
    Shared by ``AgentCard.get_agent_class()`` and the ``config`` coercion
    model-validator.

    Args:
        value: Either a class object or a fully qualified dotted path string.

    Returns:
        The resolved class object.

    Raises:
        ValueError: If *value* is an empty string or not a dotted path.
        ImportError / ModuleNotFoundError: If the module cannot be imported.
        AttributeError: If the class is not found in the module.
    """
    if isinstance(value, type):
        return value

    if not value or "." not in value:
        raise ValueError(
            f"agent_class must be a fully qualified dotted path "
            f"(e.g. 'mypackage.agents.MyAgent'), got: {value!r}"
        )

    return import_class(value)


def _extract_config_type(agent_cls: type) -> type[BaseConfig] | None:
    """Walk ``agent_cls.__mro__`` for the first concrete ``Akgent[ConfigType, …]`` binding.

    Inspects each base's ``__orig_bases__`` for an entry whose origin is
    :class:`akgentic.core.agent.Akgent`. Returns the first type argument when
    it is a concrete :class:`BaseConfig` subclass; returns ``None`` when the
    argument is a :class:`typing.TypeVar` (unparameterised subclass), when
    *agent_cls* is not an :class:`Akgent` subclass at all, or when no such
    binding is found.

    Results are memoised in ``_CONFIG_TYPE_CACHE`` keyed by *agent_cls*.

    Args:
        agent_cls: The candidate agent class.

    Returns:
        The concrete ``ConfigType`` (subclass of ``BaseConfig``) declared by the
        first ``Akgent[X, Y]`` binding found in the MRO, or ``None`` when no
        usable binding exists.
    """
    cached = _CONFIG_TYPE_CACHE.get(agent_cls)
    if cached is not None or agent_cls in _CONFIG_TYPE_CACHE:
        return cached

    # Lazy import to avoid module-initialisation cycles: agent.py imports
    # agent_card, and we cannot import Akgent at module top-level here.
    from akgentic.core.agent import Akgent

    if not (isinstance(agent_cls, type) and issubclass(agent_cls, Akgent)):
        _CONFIG_TYPE_CACHE[agent_cls] = None
        return None

    config_type: type[BaseConfig] | None = None
    for base_cls in agent_cls.__mro__:
        orig_bases = getattr(base_cls, "__orig_bases__", ())
        for orig in orig_bases:
            if get_origin(orig) is not Akgent:
                continue
            args = get_args(orig)
            if not args:
                continue
            candidate = args[0]
            if isinstance(candidate, TypeVar):
                # Unparameterised — keep searching up the MRO in case a
                # sibling/parent provides a concrete binding.
                continue
            if isinstance(candidate, type) and issubclass(candidate, BaseConfig):
                config_type = candidate
                break
        if config_type is not None:
            break

    _CONFIG_TYPE_CACHE[agent_cls] = config_type
    return config_type


class AgentCard(SerializableBaseModel):
    """Describes an agent profile/role available in the team.

    AgentCards form a capability catalog, allowing agents to discover
    what profiles exist and how to work with them. This is not instance
    tracking—it's a profile directory.

    Example:
        >>> card = AgentCard(
        ...     description="Performs web research and data gathering",
        ...     skills=["web_search", "pdf_extraction"],
        ...     agent_class="examples.multi_agent.ResearchAgent",
        ...     config=BaseConfig(name="research", role="ResearchAgent"),
        ...     routes_to=["WriterAgent", "AnalystAgent"]
        ... )
        >>> # ``role`` is a derived accessor sourced from ``config.role``
        >>> card.role
        'ResearchAgent'
        >>> # Other agents can discover this profile and its config
        >>> print(card.skills)
        ['web_search', 'pdf_extraction']
        >>> card.can_route_to("WriterAgent")
        True

    Attributes:
        description: Human-readable description of what this agent does
        skills: List of capabilities this agent provides
        agent_class: Fully qualified class name (str) or actual class (type) for instantiation
        config: Default BaseConfig (or subclass) for this profile — role lives here
        routes_to: List of roles this agent can send requests to.
                   Empty list means can route to any role (no restrictions).
                   Agents can always respond to requests regardless of this field.
        metadata: Extensible key-value storage for custom attributes

    Note:
        ``role`` is exposed as a ``@property`` that reads ``config.role`` — it is
        not a declared field. ``config.role`` is the single source of truth and
        must be non-empty; callers MUST set it via ``config=BaseConfig(role=...)``.
    """

    description: str
    skills: list[str]
    agent_class: str | type
    config: BaseConfig = Field(default_factory=BaseConfig)
    routes_to: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def role(self) -> str:
        """Agent role identifier, derived from ``config.role`` (single source of truth).

        ``role`` is no longer a declared Pydantic field on ``AgentCard``; it is a
        read-only accessor that delegates to ``self.config.role``. This removes
        the possibility of drift between ``card.role`` and ``card.config.role``
        — historically two independent slots that had to be kept in sync by
        convention.
        """
        return self.config.role

    @model_validator(mode="after")
    def require_non_empty_config_role(self) -> AgentCard:
        """Reject cards whose ``config.role`` is empty.

        ``config.role`` is the orchestrator's registry key (it is used at
        :func:`akgentic.core.orchestrator.Orchestrator.register_agent_profile`)
        so an empty role makes the card silently unroutable. This validator
        makes the failure loud and early (AC #5).
        """
        if not self.config.role:
            raise ValueError(
                "AgentCard.config.role is required and must be non-empty — "
                "it is the registry key used by the orchestrator."
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def coerce_config_to_agent_class_generic(cls, data: Any) -> Any:
        """Coerce ``config`` to the concrete ``ConfigType`` declared by ``agent_class``.

        Runs in ``mode="before"``, after ``SerializableBaseModel.deserialize_types``,
        so *data* is a plain dict (if validating from raw payload) by the time we
        see it. Leaves non-dict payloads and already-typed ``BaseConfig`` instances
        untouched.

        The resolution is intentionally tolerant:

        * Unresolvable ``agent_class`` (empty string, bad FQCN) is left for the
          field-level validator to report — we simply skip coercion in that case
          so that the underlying ``ValueError`` / ``AttributeError`` raised by the
          import path surfaces with its original message.
        * ``_extract_config_type`` returning ``None`` (unparameterised class, or
          a non-``Akgent`` class) falls back to the declared ``BaseConfig`` — the
          field-level validator still produces a valid ``BaseConfig`` from the
          input dict (AC #7).

        Args:
            data: Raw input passed to ``model_validate`` — typically a dict, but
                Pydantic may pass a model instance when re-validating.

        Returns:
            The (possibly updated) input data. When coercion applies,
            ``data["config"]`` is replaced with a concrete ``ConfigType``
            instance; otherwise *data* is returned unchanged.

        Raises:
            ValueError: When ``agent_class`` is a string FQCN that cannot be
                imported — re-raised as ``ValueError`` so Pydantic surfaces it
                as a ``ValidationError`` (AC #8).
        """
        if not isinstance(data, dict):
            return data

        agent_class_raw = data.get("agent_class")
        if agent_class_raw is None:
            return data

        # ``SerializableBaseModel.deserialize_types`` may not have run yet
        # (Pydantic does not guarantee a parent-first ordering for
        # ``mode="before"`` validators defined on the subclass). Accept the
        # serialised ``{"__type__": "pkg.Class"}`` marker here as well so
        # that round-trips through ``model_dump()``/``model_validate()``
        # work unconditionally (AC #9).
        if isinstance(agent_class_raw, dict) and "__type__" in agent_class_raw:
            agent_class_raw = agent_class_raw["__type__"]

        config_value = data.get("config")
        # Already a BaseConfig instance → leave it alone (AC #3).
        if isinstance(config_value, BaseConfig):
            return data

        # Only coerce dict config payloads; anything else is handed to the
        # field-level validator unchanged.
        if not isinstance(config_value, dict):
            return data

        try:
            agent_cls = _resolve_agent_class(agent_class_raw)
        except (ValueError, ImportError, AttributeError) as exc:
            # AC #8: surface import errors as ValidationError via ValueError.
            raise ValueError(f"Could not resolve agent_class={agent_class_raw!r}: {exc}") from exc

        config_type = _extract_config_type(agent_cls)
        if config_type is None or config_type is BaseConfig:
            # AC #2 / AC #7: no-op when the generic resolves to BaseConfig or
            # cannot be resolved — the declared annotation still applies.
            return data

        # Strip __model__ marker if the incoming dict tags itself as something
        # different; model_validate on the concrete type will re-add its own.
        # We only strip when it conflicts: if __model__ already names the
        # target type (or a subclass), SerializableBaseModel.deserialize_types
        # has already normalised things and we can pass the dict through.
        data = {**data, "config": config_type.model_validate(config_value)}
        return data

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
        return _resolve_agent_class(self.agent_class)

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
