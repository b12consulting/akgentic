"""Tool abstractions and factory for the akgentic tool package.

Defines the core contracts:
- ``BaseToolParam``: base for capability parameter models.
- ``ToolCard``: abstract base — tool configuration + callable factory in one class.
- ``ToolFactory``: resolves ``ToolCard`` instances into callable tools, prompts, and toolsets.
"""

import functools
import inspect
import shlex
import warnings
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, TypeVar, get_type_hints

from pydantic import TypeAdapter

from akgentic.core.utils import SerializableBaseModel
from akgentic.tool.errors import CommandNotRecognized, RetriableError
from akgentic.tool.event import CommandArg, CommandDescriptor, ToolObserver

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
    the factory methods that produce LLM-callable functions. Identity and
    human-readable description live on the catalog ``Entry`` envelope, not
    on the card payload.
    """

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
                    f"{type(card).__name__} depends on {dep} but it was not found in the tool list"
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
        raise ValueError(f"ToolCard dependency cycle detected: {remaining}")

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


@dataclass(frozen=True)
class _CommandArgSpec:
    """Ordered metadata + per-arg coercion adapter for one command parameter.

    Runtime-only (not serialized). Captured from the callable signature at
    registry-construction time. Drives both positional coercion (dispatch) and
    :class:`CommandDescriptor` building.
    """

    name: str
    annotation: Any
    required: bool
    adapter: TypeAdapter


@dataclass(frozen=True)
class _CommandEntry:
    """Runtime record for a single registered command (not a serialized model).

    Holds the callable plus the ordered, per-argument coercion metadata derived
    from its signature. Per Golden Rule #1b, runtime callables live here in a
    plain dataclass — never inside a serialized Pydantic field.
    """

    name: str
    fn: Callable
    args: tuple[_CommandArgSpec, ...]
    tool_card: str

    @property
    def required_count(self) -> int:
        """Number of leading required (no-default) parameters."""
        return sum(1 for spec in self.args if spec.required)


def _json_type_name(annotation: Any) -> str:
    """Return the JSON-schema type name for a parameter annotation.

    Falls back to ``"string"`` when the schema has no top-level ``type`` (e.g.
    a union like ``str | None`` produces ``anyOf``), matching how the human help
    surface renders un-typed-or-optional args.
    """
    try:
        schema = TypeAdapter(annotation).json_schema()
    except Exception:
        return "string"
    return schema.get("type", "string")


def _build_command_entry(fn: Callable, tool_card: str) -> _CommandEntry:
    """Derive a per-command arg model + ordered metadata from a callable signature.

    Mirrors how pydantic-ai derives a tool schema from a function signature:
    inspect the parameters, reject anything un-derivable (``*args``, ``**kwargs``,
    or an un-annotated parameter), and build a :class:`TypeAdapter` over the
    ordered positional parameter types for later coercion.

    Raises:
        ValueError: If the signature has a ``VAR_POSITIONAL`` (``*args``),
            ``VAR_KEYWORD`` (``**kwargs``), or un-annotated parameter. The message
            names the command and the offending parameter.
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    specs: list[_CommandArgSpec] = []
    for pname, param in sig.parameters.items():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            raise ValueError(
                f"Command '{fn.__name__}' cannot be registered: parameter '*{pname}' "
                "(*args) has no derivable argument schema."
            )
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            raise ValueError(
                f"Command '{fn.__name__}' cannot be registered: parameter '**{pname}' "
                "(**kwargs) has no derivable argument schema."
            )
        annotation = hints.get(pname, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise ValueError(
                f"Command '{fn.__name__}' cannot be registered: parameter '{pname}' "
                "has no type annotation."
            )
        required = param.default is inspect.Parameter.empty
        specs.append(
            _CommandArgSpec(
                name=pname,
                annotation=annotation,
                required=required,
                adapter=TypeAdapter(annotation),
            )
        )

    return _CommandEntry(name=fn.__name__, fn=fn, args=tuple(specs), tool_card=tool_card)


class CommandRegistry:
    """Name-keyed registry of command callables with signature-derived dispatch.

    Built by :meth:`ToolFactory.get_command_registry` from the ``get_commands()``
    output of every wired :class:`ToolCard`. Each command is keyed by its
    callable's ``__name__`` (e.g. ``hire_member``). The registry exposes a typed
    programmatic surface (:meth:`callable`), a membership test (:meth:`has`),
    discovery metadata (:meth:`descriptors`), and a human text surface
    (:meth:`dispatch`) that parses ``/``-prefixed commands.

    This is a runtime object holding callables — deliberately a plain class, not a
    ``BaseModel`` (Golden Rule #1b): runtime callables must never live in a
    serialized field.
    """

    def __init__(self, entries: dict[str, _CommandEntry]) -> None:
        """Store the name → command-entry mapping. Use the factory to build one."""
        self._entries = entries

    def has(self, name: str) -> bool:
        """Return ``True`` if a command named *name* is registered."""
        return name in self._entries

    def callable(self, name: str) -> Callable:
        """Return the bound, typed command callable for programmatic invocation.

        The returned callable preserves its **native** (non-stringified) return
        value, so callers (e.g. ``StructuredOutput`` hire-by-role) can invoke it
        with native arguments and use the result directly.

        Raises:
            CommandNotRecognized: If *name* is not a registered command.
        """
        try:
            return self._entries[name].fn
        except KeyError:
            raise CommandNotRecognized(name) from None

    def descriptors(self) -> list[CommandDescriptor]:
        """Return serializable discovery metadata, one entry per command."""
        result: list[CommandDescriptor] = []
        for entry in self._entries.values():
            args = [
                CommandArg(
                    name=spec.name,
                    type=_json_type_name(spec.annotation),
                    required=spec.required,
                )
                for spec in entry.args
            ]
            description = inspect.getdoc(entry.fn) or ""
            result.append(
                CommandDescriptor(
                    name=entry.name,
                    description=description,
                    args=args,
                    tool_card=entry.tool_card,
                )
            )
        return result

    def dispatch(self, text: str) -> str:
        """Parse a ``/``-prefixed command, invoke it, and return a result string.

        Strips the leading ``/``, ``shlex.split``s the remainder, resolves the
        first token to a command, classifies the remaining tokens as positional or
        ``name=value`` keyword arguments, coerces and merges them, invokes the
        command, and string-renders the result.

        A token is a **keyword** only when the text before its first ``=`` matches a
        real parameter name on the command; otherwise it is positional (so values
        containing ``=`` are never silently swallowed). Positionals must precede
        keywords. ``key=value`` is opt-in — purely-positional dispatch is unchanged.

        Raises:
            CommandNotRecognized: If the first token does not name a known command
                (so the caller may fall back to normal LLM processing). No command
                is invoked in this case.

        Post-identification failures (missing/extra args, coercion errors, unknown
        keyword, duplicate binding, positional-after-keyword, or the command body
        raising) are caught **inside** this method and returned as a plain result
        string — ``CommandNotRecognized`` is never raised once a command has been
        identified.
        """
        tokens = shlex.split(text[1:] if text.startswith("/") else text)
        if not tokens:
            raise CommandNotRecognized(text)
        name, args = tokens[0], tokens[1:]
        if name not in self._entries:
            raise CommandNotRecognized(name)
        return self._invoke(name, args)

    def _invoke(self, name: str, args: list[str]) -> str:
        """Classify, merge, coerce *args* for command *name* and invoke it.

        Any failure (positional-after-keyword, too many/missing args, unknown
        keyword, duplicate binding, coercion error, or the command body raising) is
        caught and returned as a result string.
        """
        entry = self._entries[name]
        try:
            positional, keyword = self._classify_tokens(entry, args)
            bound = self._bind(entry, positional, keyword)
            return str(entry.fn(**bound))
        except Exception as exc:  # noqa: BLE001 — failures become result strings (ADR-028 §4)
            return f"Command '{name}' failed: {exc}"

    @staticmethod
    def _classify_tokens(
        entry: _CommandEntry, args: list[str]
    ) -> tuple[list[str], dict[str, str]]:
        """Partition *args* into ``(positional, keyword)`` for *entry*.

        A token is a keyword iff it contains ``=`` AND the substring before the
        **first** ``=`` is a known parameter name on *entry*; the value is the
        remainder after that first ``=``. All other tokens are positional. A
        positional token appearing after any keyword token is rejected.

        Raises:
            ValueError: If a positional token follows a keyword token (names the
                offending positional value).
        """
        names = {spec.name for spec in entry.args}
        positional: list[str] = []
        keyword: dict[str, str] = {}
        for token in args:
            key, sep, value = token.partition("=")
            if sep and key in names:
                keyword[key] = value
            elif keyword:
                raise ValueError(
                    f"positional argument '{token}' cannot follow a keyword argument"
                )
            else:
                positional.append(token)
        return positional, keyword

    @staticmethod
    def _bind(
        entry: _CommandEntry, positional: list[str], keyword: dict[str, str]
    ) -> dict[str, Any]:
        """Merge *positional* + *keyword* onto *entry*'s params and coerce each.

        Maps positionals onto the leading parameters in signature order, then binds
        keywords by name, detecting unknown names and duplicate bindings. Validates
        arity (at least ``required_count``, at most ``len(args)``) over the merged
        set, then coerces every bound value through its per-arg :class:`TypeAdapter`.
        Unbound trailing optionals are omitted so the callable applies its defaults.

        Raises:
            ValueError: Too many positionals, unknown keyword, duplicate binding, a
                required parameter left unbound, or a coercion failure.
        """
        if len(positional) > len(entry.args):
            raise ValueError(
                f"accepts at most {len(entry.args)} argument(s), got {len(positional)}"
            )
        raw: dict[str, str] = {
            spec.name: token
            for spec, token in zip(entry.args, positional, strict=False)
        }
        specs_by_name = {spec.name: spec for spec in entry.args}
        for key, value in keyword.items():
            if key not in specs_by_name:
                raise ValueError(f"unknown keyword argument '{key}'")
            if key in raw:
                raise ValueError(f"got multiple values for argument '{key}'")
            raw[key] = value
        missing = [spec.name for spec in entry.args if spec.required and spec.name not in raw]
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(missing)}")
        return {
            name: specs_by_name[name].adapter.validate_python(token)
            for name, token in raw.items()
        }


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

        Deprecated:
            Use :meth:`get_command_registry` instead. This param-class-keyed dict
            is retained for one migration cycle. The registry keys by canonical
            command name and adds signature-derived dispatch + discovery metadata.

        Returns:
            Dict mapping param class to callable, merged from all tool cards.
        """
        warnings.warn(
            "ToolFactory.get_commands() is deprecated; use get_command_registry() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        commands: dict[type[BaseToolParam], Callable] = {}
        for card in self.tool_cards:
            commands.update(card.get_commands())

        if self._retry_exception is not None:
            commands = {k: self._wrap_with_retry(v) for k, v in commands.items()}
        return commands

    def get_command_registry(self) -> CommandRegistry:
        """Build a name-keyed :class:`CommandRegistry` from every wired tool card.

        Iterates ``self.tool_cards`` in dependency order, calls each card's
        ``get_commands()``, and registers every callable under its ``__name__``.
        Each command's arg schema is derived from its signature at this point, so
        an un-derivable signature (``*args``/``**kwargs``/un-annotated param) fails
        loudly here. When ``retry_exception`` is configured, each command is
        wrapped via :meth:`_wrap_with_retry` (``functools.wraps`` preserves
        ``__name__``/``__doc__``), matching :meth:`get_commands` behavior.

        Raises:
            ValueError: If two tool cards expose commands with the same canonical
                name (collision is a wiring-time error, never a silent overwrite),
                or if a command has an un-derivable signature. The message names
                the offending command.
        """
        entries: dict[str, _CommandEntry] = {}
        for card in self.tool_cards:
            tool_card_name = type(card).__name__
            for fn in card.get_commands().values():
                wrapped = self._wrap_with_retry(fn) if self._retry_exception is not None else fn
                name = wrapped.__name__
                if name in entries:
                    raise ValueError(
                        f"Command name collision: '{name}' is exposed by both "
                        f"'{entries[name].tool_card}' and '{tool_card_name}'."
                    )
                entries[name] = _build_command_entry(wrapped, tool_card_name)
        return CommandRegistry(entries)

    def get_toolsets(self) -> list[Any]:
        """Return toolset instances aggregated from all tool cards."""
        return [ts for card in self.tool_cards for ts in card.get_toolsets()]
