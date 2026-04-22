"""Tool abstractions and factory for the akgentic tool package.

Defines the core contracts:
- ``BaseToolParam``: base for capability parameter models.
- ``ToolCard``: abstract base — tool configuration + callable factory in one class.
- ``ToolFactory``: resolves ``ToolCard`` instances into callable tools, prompts, and toolsets.
"""

import functools
from abc import ABC, abstractmethod
from collections import deque
from enum import StrEnum
from typing import Any, Callable, TypeVar

from akgentic.core.utils import SerializableBaseModel
from akgentic.tool.errors import RetriableError
from akgentic.tool.event import ToolObserver

T = TypeVar("T", bound="BaseToolParam")


def _resolve(value: "T | bool", cls: "type[T]") -> "T | None":
    """Resolve a ``ParamModel | bool`` field to a ``ParamModel`` or ``None``.

    Args:
        value: ``True`` (enable with defaults), ``False`` (disable), or a
            ``BaseToolParam`` instance (enable with custom parameters).
        cls: The param model class to instantiate when *value* is ``True``.

    Returns:
        A param model instance, or ``None`` if the capability is disabled.
    """
    if value is True:
        return cls()
    if value is False:
        return None
    return value  # already a ParamModel instance


class Channels(StrEnum):
    """Valid channel names for capability exposure."""

    SYSTEM_PROMPT = "system_prompt"
    """Expose as a system prompt injected into the LLM context."""

    TOOL_CALL = "tool_call"
    """Expose as a callable tool for the LLM."""

    COMMAND = "command"
    """Expose as a programmatic command for inter-agent orchestration."""


# Backward-compatible module-level aliases
SYSTEM_PROMPT = Channels.SYSTEM_PROMPT
TOOL_CALL = Channels.TOOL_CALL
COMMAND = Channels.COMMAND


class BaseToolParam(SerializableBaseModel):
    """Base for capability parameter models.

    Provides common fields that control how a capability is exposed
    and how its description can be customized.

    Each subclass can override the default ``expose`` set to declare the channels
    it participates in. Use the module-level channel constants:

    - ``TOOL_CALL``: callable tool invoked by the LLM (default).
    - ``SYSTEM_PROMPT``: prompt injected into the LLM context.
    - ``COMMAND``: programmatic call for inter-agent orchestration.
    """

    instructions: str | None = None
    """Additional instructions appended to the default tool docstring.

    When set, the factory appends these instructions to the built-in docstring
    under a structured header. When ``None``, only the default docstring is used.
    """

    expose: set[Channels] = {TOOL_CALL}
    """Set of channels this capability is exposed through.

    Defaults to ``{TOOL_CALL}``. Override in subclasses or at instantiation.
    Use ``Channels`` enum members or module-level aliases: ``TOOL_CALL``, ``SYSTEM_PROMPT``,
    ``COMMAND``.
    """

    def format_docstring(self, original: str | None) -> str | None:
        """Format the tool docstring with optional additional instructions.

        Args:
            original: The original docstring from the tool callable.

        Returns:
            The formatted docstring, or the original if no instructions are set.
        """
        if not self.instructions:
            return original

        base_doc = original or ""
        return f"{base_doc}\n\nAdditional Instructions:\n{self.instructions}"


class ToolCard(SerializableBaseModel, ABC):
    """Abstract base: tool configuration + callable factory in one class.

    Subclasses define typed fields for their capabilities and implement
    the factory methods that produce LLM-callable functions.

    Attributes:
        name: Human-readable tool provider name.
        description: Natural-language description of tool capabilities.
    """

    name: str
    description: str

    @property
    def depends_on(self) -> list[str]:
        """Class-name list of ToolCards that MUST be wired before this one.

        Default: no dependencies. Subclasses may override as a property
        whose return value depends on instance fields (e.g. the value of
        a ``vector_store`` field on consumer tools). The string is matched
        against ``type(card).__name__`` by ``ToolFactory``'s topological
        sort. Not a Pydantic field — does not appear in ``model_dump`` and
        cannot be set via ``model_validate``.
        """
        return []

    def observer(self, observer: ToolObserver) -> "ToolCard":
        """Attach an observer and perform runtime setup.

        Follows the same pattern as ``BaseState.observer()``.
        Override for setup that requires the observer (e.g., actor proxies).
        All methods can then access the observer via ``self._observer``.

        Args:
            observer: Optional observer for tool call events.

        Returns:
            Self, enabling method chaining.
        """
        self._observer = observer
        return self

    @abstractmethod
    def get_tools(self) -> list[Callable]:
        """Return callable tool functions for LLM agents.

        Use ``self._observer`` when tool callables need to emit events.
        """
        ...

    def get_system_prompts(self) -> list[Callable]:
        """Return system prompt callables injected into LLM context.

        Use ``self._observer`` when prompts need runtime data.
        """
        return []

    def get_commands(self) -> dict[type["BaseToolParam"], Callable]:
        """Return callable commands for programmatic invocation.

        Commands are methods exposed for inter-agent orchestration
        (e.g., ``hire_member``, ``fire_member``). Unlike tools (invoked by
        the LLM), commands are called programmatically by other agents
        or system components via ``proxy_call`` or similar mechanisms.

        Returns:
            Dict mapping param class (e.g., ``HireTeamMember``) to callable.
        """
        return {}

    def get_toolsets(self) -> list[Any]:
        """Return runtime toolset objects (e.g., MCP servers)."""
        return []


def _topological_sort(cards: list[ToolCard]) -> list[ToolCard]:
    """Return ``cards`` topologically sorted by ``ToolCard.depends_on``.

    Dependency keys are matched against ``type(card).__name__``. The sort uses
    Kahn's algorithm with a FIFO queue seeded in input order, which produces a
    deterministic ordering: independent nodes retain their relative input order.

    Duplicate class names in ``cards`` (e.g. two ``VectorStoreTool`` instances
    with different configuration) are permitted — later entries overwrite
    earlier entries in the internal name→card map. Dependency relationships
    are at the class level, not per-instance.

    Args:
        cards: Tool cards to sort. Input order is preserved for independent
            nodes.

    Returns:
        A new list containing the same cards in dependency-respecting order
        (prerequisites before dependents).

    Raises:
        ValueError: If a declared dependency is not present in ``cards``
            (message names both the dependent and the missing class), or if
            the dependency graph contains a cycle (message contains ``"cycle"``
            and lists the class names involved).
    """
    # Name → card map. Later duplicates overwrite earlier entries — dependency
    # relationships are at the class level, not per-instance.
    by_name: dict[str, ToolCard] = {type(card).__name__: card for card in cards}

    # Validate every declared dependency is present.
    for card in cards:
        for dep in card.depends_on:
            if dep not in by_name:
                raise ValueError(
                    f"{type(card).__name__} depends on {dep} but it was not "
                    f"found in the tool list"
                )

    # Build in-degree map keyed by class name (not instance — duplicates collapse).
    in_degree: dict[str, int] = {name: 0 for name in by_name}
    # Reverse adjacency: dep_name → list of class names that depend on it.
    dependents: dict[str, list[str]] = {name: [] for name in by_name}
    for name, card in by_name.items():
        for dep in card.depends_on:
            in_degree[name] += 1
            dependents[dep].append(name)

    # Seed the queue with zero-in-degree names in the order they appeared in
    # the input (FIFO → deterministic for the same input). We iterate cards to
    # preserve input order, skipping duplicates.
    queue: deque[str] = deque()
    seen: set[str] = set()
    for card in cards:
        name = type(card).__name__
        if name in seen:
            continue
        seen.add(name)
        if in_degree[name] == 0:
            queue.append(name)

    ordered_names: list[str] = []
    while queue:
        name = queue.popleft()
        ordered_names.append(name)
        for dependent in dependents[name]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(ordered_names) < len(by_name):
        remaining = sorted(set(by_name) - set(ordered_names))
        raise ValueError(
            f"ToolCard dependency cycle detected: {remaining}"
        )

    # Map sorted names back to ToolCard instances. Preserve input order for
    # duplicate class names: emit instances in the order they appeared in the
    # input, grouped by their class's position in the sorted name order.
    by_name_instances: dict[str, list[ToolCard]] = {name: [] for name in by_name}
    for card in cards:
        by_name_instances[type(card).__name__].append(card)
    ordered: list[ToolCard] = []
    for name in ordered_names:
        ordered.extend(by_name_instances[name])
    return ordered


class ToolFactory:
    """Resolves ``ToolCard`` instances into callable tools, prompts, and toolsets."""

    def __init__(
        self,
        tool_cards: list[ToolCard],
        observer: ToolObserver | None = None,
        retry_exception: type[Exception] | None = None,
    ) -> None:
        """Create a factory for one or more tool cards.

        Topologically sorts ``tool_cards`` by their ``depends_on`` class
        attribute, then attaches the observer to every card in dependency order
        (triggers runtime setup in ``ToolCard.observer()``). Prerequisites are
        wired before dependents, so a consumer card's ``observer()`` can safely
        look up actors or resources created by its prerequisites.

        Args:
            tool_cards: Tool cards to resolve into callable tools. The caller's
                list is not mutated; a new dependency-ordered list is stored on
                ``self.tool_cards``. Aggregators (``get_tools``,
                ``get_system_prompts``, ``get_commands``, ``get_toolsets``)
                iterate in this dependency order.
            observer: Optional observer notified by tool implementations during
                tool calls.
            retry_exception: Optional exception class to raise when a tool raises
                ``RetriableError``. Injected by the integration layer (e.g., ModelRetry
                from pydantic-ai) to keep the tool module framework-agnostic.

        Raises:
            ValueError: If the dependency graph is invalid — either a card
                declares ``depends_on`` for a class not present in
                ``tool_cards``, or a cycle exists. Raised before any observer
                is attached (fail fast at team creation).
        """
        self.tool_cards = _topological_sort(tool_cards)
        self.observer = observer
        self._retry_exception = retry_exception

        if self.observer is not None:
            for card in self.tool_cards:
                card.observer(self.observer)

    def _wrap_with_retry(self, fn: Callable) -> Callable:
        """Wrap a tool callable to convert ``RetriableError`` into retry_exception."""
        assert self._retry_exception is not None
        retry_exc = self._retry_exception

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except RetriableError as e:
                raise retry_exc(str(e)) from e

        return wrapper

    def get_tools(self) -> list[Callable]:
        """Return tool callables aggregated from all tool cards."""
        tools = [t for card in self.tool_cards for t in card.get_tools()]
        if self._retry_exception is not None:
            tools = [self._wrap_with_retry(t) for t in tools]
        return tools

    def get_system_prompts(self) -> list[Callable]:
        """Return system prompt callables aggregated from all tool cards."""
        return [p for card in self.tool_cards for p in card.get_system_prompts()]

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        """Return command callables aggregated from all tool cards.

        Returns:
            Dict mapping param class to callable, merged from all tool cards.
        """
        commands: dict[type[BaseToolParam], Callable] = {}
        for card in self.tool_cards:
            commands.update(card.get_commands())

        if self._retry_exception is not None:
            commands = {k: self._wrap_with_retry(v) for k, v in commands.items()}
        return commands

    def get_toolsets(self) -> list[Any]:
        """Return toolset instances aggregated from all tool cards."""
        return [ts for card in self.tool_cards for ts in card.get_toolsets()]
