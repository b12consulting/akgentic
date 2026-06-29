"""Tests for ``CommandRegistry`` and ``ToolFactory.get_command_registry()`` (Story 21.2).

Covers ADR-028 §Decision 1 (registry construction, collision), §Decision 2
(signature-derived arg schema, shlex dispatch grammar, positional coercion),
§Decision 4 (failure semantics: ``CommandNotRecognized`` vs caught result string),
and §Decision 6 (programmatic typed access). Behavioral assertions only — no
assertion checks for an ADR-reference string (Golden Rule #8 / NFR3).
"""

from __future__ import annotations

import warnings
from typing import Callable, Literal

import pytest

from akgentic.tool.core import (
    COMMAND,
    BaseToolParam,
    CommandRegistry,
    ToolCard,
    ToolFactory,
)
from akgentic.tool.errors import CommandNotRecognized, RetriableError
from akgentic.tool.event import CommandDescriptor

# ---------------------------------------------------------------------------
# Fixtures: ToolCards exposing command callables (TeamTool-shaped signatures)
# ---------------------------------------------------------------------------


class _HireParam(BaseToolParam):
    expose: set[str] = {COMMAND}


class _FireParam(BaseToolParam):
    expose: set[str] = {COMMAND}


class _HireCard(ToolCard):
    """Card exposing a ``hire_member(role, name=None)`` command."""

    name: str = "hire-card"
    description: str = "hire card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def hire_member(role: str, name: str | None = None) -> tuple[str, str | None]:
            """Hire a single new team member with the given role.

            Args:
                role: Role to hire.
                name: Optional specific name.
            """
            return (role, name)

        return {_HireParam: hire_member}


class _FireCard(ToolCard):
    """Card exposing a ``fire_member(name)`` command."""

    name: str = "fire-card"
    description: str = "fire card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def fire_member(name: str) -> str:
            """Fire a team member with the given name."""
            return f"Member {name} has been fired."

        return {_FireParam: fire_member}


class _IntCard(ToolCard):
    """Card exposing ``f(task_id: int)`` for coercion tests."""

    name: str = "int-card"
    description: str = "int card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def f(task_id: int) -> int:
            """Return double the task id."""
            return task_id * 2

        return {_HireParam: f}


class _RaisingCard(ToolCard):
    """Card whose command body raises when invoked."""

    name: str = "raising-card"
    description: str = "raising card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def boom(arg: str) -> str:
            """Always raises."""
            raise RuntimeError("kaboom")

        return {_HireParam: boom}


def _registry(*cards: ToolCard) -> CommandRegistry:
    factory = ToolFactory(tool_cards=list(cards))
    return factory.get_command_registry()


# ---------------------------------------------------------------------------
# AC 1 — Registry construction keyed by canonical name
# ---------------------------------------------------------------------------


def test_registry_keyed_by_callable_name() -> None:
    registry = _registry(_HireCard(), _FireCard())
    assert registry.has("hire_member") is True
    assert registry.has("fire_member") is True
    assert registry.has("not_a_command") is False


# ---------------------------------------------------------------------------
# AC 2 — Cross-card name-collision detection
# ---------------------------------------------------------------------------


def test_cross_card_name_collision_raises() -> None:
    class _DupCard(ToolCard):
        name: str = "dup-card"
        description: str = "dup card"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def hire_member(role: str) -> str:
                """Duplicate hire_member."""
                return role

            return {_FireParam: hire_member}

    factory = ToolFactory(tool_cards=[_HireCard(), _DupCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry()
    assert "hire_member" in str(exc.value)


# ---------------------------------------------------------------------------
# AC 3 — Signature-derived argument schema (required/optional + order)
# ---------------------------------------------------------------------------


def test_signature_derived_schema_required_optional_and_order() -> None:
    registry = _registry(_HireCard())
    descriptors = registry.descriptors()
    assert len(descriptors) == 1
    desc = descriptors[0]
    assert [a.name for a in desc.args] == ["role", "name"]
    assert desc.args[0].required is True
    assert desc.args[1].required is False


# ---------------------------------------------------------------------------
# AC 4 — Loud rejection of un-derivable signatures
# ---------------------------------------------------------------------------


def test_var_positional_rejected_at_construction() -> None:
    class _VarArgsCard(ToolCard):
        name: str = "varargs"
        description: str = "varargs"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def variadic(*args: str) -> str:
                """Bad: *args."""
                return "x"

            return {_HireParam: variadic}

    factory = ToolFactory(tool_cards=[_VarArgsCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry()
    assert "variadic" in str(exc.value)


def test_var_keyword_rejected_at_construction() -> None:
    class _KwargsCard(ToolCard):
        name: str = "kwargs"
        description: str = "kwargs"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def variadic_kw(**kwargs: str) -> str:
                """Bad: **kwargs."""
                return "x"

            return {_HireParam: variadic_kw}

    factory = ToolFactory(tool_cards=[_KwargsCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry()
    assert "variadic_kw" in str(exc.value)


def test_untyped_param_rejected_at_construction() -> None:
    class _UntypedCard(ToolCard):
        name: str = "untyped"
        description: str = "untyped"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def untyped(role) -> str:  # type: ignore[no-untyped-def]
                """Bad: no annotation."""
                return "x"

            return {_HireParam: untyped}

    factory = ToolFactory(tool_cards=[_UntypedCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry()
    assert "untyped" in str(exc.value)


# ---------------------------------------------------------------------------
# AC 5 — Dispatch parses, coerces, invokes, returns a string
# ---------------------------------------------------------------------------


def test_dispatch_quoted_args_happy_path() -> None:
    registry = _registry(_HireCard())
    result = registry.dispatch('/hire_member Developer "Alice Smith"')
    assert isinstance(result, str)
    assert "Developer" in result
    assert "Alice Smith" in result


def test_dispatch_optional_arg_omitted() -> None:
    registry = _registry(_HireCard())
    result = registry.dispatch("/hire_member Developer")
    # name omitted -> callable default None applies; native return was ('Developer', None)
    assert "Developer" in result
    assert "None" in result


# ---------------------------------------------------------------------------
# AC 6 — Type coercion of positional args
# ---------------------------------------------------------------------------


def test_dispatch_coerces_int_arg() -> None:
    registry = _registry(_IntCard())
    # f(task_id) returns task_id * 2 -> 10 proves "5" coerced to int, not "55"
    assert registry.dispatch("/f 5") == "10"


# ---------------------------------------------------------------------------
# AC 7 — Unknown first token raises CommandNotRecognized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/frobnicate", "/etc/passwd", "/", "/ "])
def test_unknown_first_token_raises_command_not_recognized(text: str) -> None:
    registry = _registry(_HireCard())
    with pytest.raises(CommandNotRecognized):
        registry.dispatch(text)


def test_command_not_recognized_does_not_invoke_command() -> None:
    invoked: list[str] = []

    class _SpyCard(ToolCard):
        name: str = "spy"
        description: str = "spy"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def known(arg: str) -> str:
                """Spy command."""
                invoked.append(arg)
                return arg

            return {_HireParam: known}

    registry = _registry(_SpyCard())
    with pytest.raises(CommandNotRecognized):
        registry.dispatch("/unknown x")
    assert invoked == []


# ---------------------------------------------------------------------------
# AC 8 — Post-identification failures returned as result strings
# ---------------------------------------------------------------------------


def test_missing_required_arg_returns_string() -> None:
    registry = _registry(_HireCard())
    result = registry.dispatch("/hire_member")  # role is required
    assert isinstance(result, str)
    assert "hire_member" in result


def test_extra_args_returns_string() -> None:
    registry = _registry(_HireCard())
    result = registry.dispatch("/hire_member a b c")  # max 2 args
    assert isinstance(result, str)
    assert "hire_member" in result


def test_coercion_error_returns_string() -> None:
    registry = _registry(_IntCard())
    result = registry.dispatch("/f notanint")
    assert isinstance(result, str)
    assert "f" in result


def test_command_body_raises_returns_string() -> None:
    registry = _registry(_RaisingCard())
    result = registry.dispatch("/boom hello")
    assert isinstance(result, str)
    assert "boom" in result


@pytest.mark.parametrize(
    "text",
    ["/hire_member", "/hire_member a b c"],
)
def test_post_identification_failure_does_not_raise_command_not_recognized(text: str) -> None:
    registry = _registry(_HireCard())
    # Must NOT raise — identified command failures become result strings.
    result = registry.dispatch(text)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC 9 — Programmatic typed access
# ---------------------------------------------------------------------------


def test_callable_returns_native_value() -> None:
    registry = _registry(_HireCard())
    fn = registry.callable("hire_member")
    result = fn("Developer")
    # Native (non-stringified) return value preserved.
    assert result == ("Developer", None)


def test_callable_unknown_raises_command_not_recognized() -> None:
    registry = _registry(_HireCard())
    with pytest.raises(CommandNotRecognized):
        registry.callable("not_a_command")


# ---------------------------------------------------------------------------
# AC 10 — descriptors() returns serializable metadata
# ---------------------------------------------------------------------------


def test_descriptors_shape() -> None:
    registry = _registry(_HireCard(), _FireCard())
    descriptors = registry.descriptors()
    assert all(isinstance(d, CommandDescriptor) for d in descriptors)
    by_name = {d.name: d for d in descriptors}
    assert set(by_name) == {"hire_member", "fire_member"}

    hire = by_name["hire_member"]
    assert hire.tool_card == "_HireCard"
    assert "Hire a single new team member" in hire.description
    assert [a.name for a in hire.args] == ["role", "name"]
    assert hire.args[0].type == "string"
    assert hire.args[0].required is True
    assert hire.args[1].required is False

    fire = by_name["fire_member"]
    assert fire.tool_card == "_FireCard"
    assert [a.name for a in fire.args] == ["name"]


def test_descriptor_int_arg_type() -> None:
    registry = _registry(_IntCard())
    desc = registry.descriptors()[0]
    assert desc.args[0].name == "task_id"
    assert desc.args[0].type == "integer"


# ---------------------------------------------------------------------------
# AC 11 — Deprecated get_commands() alias retained
# ---------------------------------------------------------------------------


def test_get_commands_still_returns_legacy_dict_and_warns() -> None:
    factory = ToolFactory(tool_cards=[_HireCard()])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        commands = factory.get_commands()
    assert _HireParam in commands  # legacy param-class keying preserved
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ---------------------------------------------------------------------------
# retry_exception wrapping preserves name and translates RetriableError
# ---------------------------------------------------------------------------


def test_retry_exception_wrapping_preserves_name_and_translates() -> None:
    class _MyRetryError(Exception):
        pass

    class _RetriableCmdCard(ToolCard):
        name: str = "retriable-cmd"
        description: str = "retriable cmd"

        def get_tools(self) -> list[Callable]:
            return []

        def get_commands(self) -> dict[type[BaseToolParam], Callable]:
            def needy(arg: str) -> str:
                """Raises retriable when called."""
                raise RetriableError("retry me")

            return {_HireParam: needy}

    factory = ToolFactory(tool_cards=[_RetriableCmdCard()], retry_exception=_MyRetryError)
    registry = factory.get_command_registry()
    # Name survives functools.wraps so name-keying still works.
    assert registry.has("needy")
    # The wrapped command translates RetriableError -> _MyRetry, which dispatch
    # catches and returns as a string (identified-command failure semantics).
    result = registry.dispatch("/needy x")
    assert "needy" in result


# ===========================================================================
# Story 21.4 — keyword (key=value) arguments in command dispatch
# ===========================================================================

_Status = Literal["pending", "started", "completed", "abort"]


class _SearchCard(ToolCard):
    """``search_planning``-shaped all-optional, multi-parameter command.

    Mirrors the motivating signature (status, owner, creator, query, mode) whose
    all-optional shape positional-only dispatch cannot serve for later params.
    """

    name: str = "search-card"
    description: str = "search card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def search_planning(
            status: _Status | None = None,
            owner: str | None = None,
            creator: str | None = None,
            query: str | None = None,
            mode: str = "hybrid",
        ) -> dict[str, object]:
            """Search planning tasks by optional filters."""
            return {
                "status": status,
                "owner": owner,
                "creator": creator,
                "query": query,
                "mode": mode,
            }

        return {_HireParam: search_planning}


class _RequiredPairCard(ToolCard):
    """``f(a: str, b: str)`` with both required, for merged-arity tests."""

    name: str = "pair-card"
    description: str = "pair card"

    def get_tools(self) -> list[Callable]:
        return []

    def get_commands(self) -> dict[type[BaseToolParam], Callable]:
        def f(a: str, b: str) -> str:
            """Concatenate a and b."""
            return f"{a}|{b}"

        return {_HireParam: f}


# --- AC1: positional dispatch unchanged (backward compatibility) ------------


def test_kw_positional_dispatch_unchanged() -> None:
    registry = _registry(_HireCard())
    result = registry.dispatch('/hire_member Developer "Alice Smith"')
    assert "Developer" in result
    assert "Alice Smith" in result


# --- AC2: a name=value token binds by parameter name ------------------------


def test_kw_binds_by_name_with_coercion() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning status=pending")
    assert "'status': 'pending'" in result
    # Every other parameter falls back to its own default.
    assert "'owner': None" in result
    assert "'mode': 'hybrid'" in result


# --- AC3: keyword reaches a later optional without filling earlier ones ------


def test_kw_reaches_later_optional_only() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch('/search_planning query="deploy the service"')
    assert "'query': 'deploy the service'" in result
    assert "'status': None" in result
    assert "'owner': None" in result
    assert "'creator': None" in result


# --- AC4: mixed positional + keyword; positional-after-keyword errors --------


def test_kw_mixed_positional_and_keyword() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning pending owner=@Manager")
    assert "'status': 'pending'" in result
    assert "'owner': '@Manager'" in result


def test_kw_positional_after_keyword_is_result_string() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning status=pending @Manager")
    assert isinstance(result, str)
    # Post-identification failure: names the offending positional, not CommandNotRecognized.
    assert "search_planning" in result
    assert "@Manager" in result


def test_kw_positional_after_keyword_does_not_raise() -> None:
    registry = _registry(_SearchCard())
    # Must NOT raise CommandNotRecognized — command was identified.
    result = registry.dispatch("/search_planning status=pending @Manager")
    assert isinstance(result, str)


# --- AC5: keyword only when LHS is a real parameter name; first-= split ------


def test_kw_value_containing_equals_via_keyword() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch('/search_planning query="a=b"')
    # Split on the FIRST '=' only -> query value is "a=b".
    assert "'query': 'a=b'" in result


def test_kw_unknown_lhs_is_positional_value() -> None:
    registry = _registry(_HireCard())
    # 'x' is NOT a parameter name -> token 'x=y' is a POSITIONAL value bound to role.
    result = registry.dispatch("/hire_member x=y")
    # role coerces the literal string "x=y" (first-= split is irrelevant: positional).
    assert "x=y" in result


# --- AC6: unknown keyword name -> result string, not CommandNotRecognized ----


def test_kw_unknown_keyword_is_result_string() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning foo=bar")
    assert isinstance(result, str)
    assert "search_planning" in result
    assert "foo" in result


def test_kw_unknown_keyword_does_not_raise_command_not_recognized() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning foo=bar")
    assert isinstance(result, str)  # caught inside dispatch


# --- AC7: duplicate binding -> result string --------------------------------


def test_kw_duplicate_positional_and_keyword_is_result_string() -> None:
    registry = _registry(_SearchCard())
    result = registry.dispatch("/search_planning pending status=started")
    assert isinstance(result, str)
    assert "status" in result


def test_kw_duplicate_same_keyword_twice_is_result_string() -> None:
    registry = _registry(_SearchCard())
    # shlex keeps both tokens; first-= classification yields a repeated keyword.
    result = registry.dispatch("/search_planning status=pending status=started")
    assert isinstance(result, str)
    assert "status" in result


# --- AC8: arity / required checks over the merged binding -------------------


def test_kw_missing_required_over_merged_set() -> None:
    registry = _registry(_RequiredPairCard())
    result = registry.dispatch("/f a=x")  # b left unbound
    assert isinstance(result, str)
    assert "f" in result
    assert "b" in result


def test_kw_over_supply_positionals_is_result_string() -> None:
    registry = _registry(_RequiredPairCard())
    result = registry.dispatch("/f x y z")  # max 2
    assert isinstance(result, str)
    assert "f" in result


# --- AC9: coercion identical for keyword and positional values --------------


def test_kw_int_coercion() -> None:
    registry = _registry(_IntCard())
    # f(task_id) returns task_id * 2 -> 10 proves "5" coerced to int via keyword.
    assert registry.dispatch("/f task_id=5") == "10"


def test_kw_bad_int_coercion_is_result_string() -> None:
    registry = _registry(_IntCard())
    result = registry.dispatch("/f task_id=notanint")
    assert isinstance(result, str)
    assert "f" in result


# --- AC10: descriptors unchanged; unknown first token still raises -----------


def test_kw_descriptors_unchanged_with_keyword_support() -> None:
    registry = _registry(_HireCard(), _FireCard())
    descriptors = registry.descriptors()
    by_name = {d.name: d for d in descriptors}
    assert set(by_name) == {"hire_member", "fire_member"}
    hire = by_name["hire_member"]
    assert [a.name for a in hire.args] == ["role", "name"]
    assert hire.args[0].required is True
    assert hire.args[1].required is False


def test_kw_unknown_first_token_still_raises() -> None:
    registry = _registry(_SearchCard())
    with pytest.raises(CommandNotRecognized):
        registry.dispatch("/frobnicate status=pending")


# ===========================================================================
# Story 23.1 — additive ``extra_commands`` param on get_command_registry
#
# An agent-owned plain callable joins the same collision-checked,
# signature-derived command table as tool-card commands, under the
# "BaseAgent" owner label, without akgentic-tool importing llm/agent.
# ===========================================================================


# --- AC #1: additive, default-off param (parity with no-arg call) -----------


def test_extra_commands_default_none_is_byte_identical() -> None:
    factory = ToolFactory(tool_cards=[_HireCard(), _FireCard()])
    reg_noarg = factory.get_command_registry()
    reg_none = factory.get_command_registry(extra_commands=None)

    # Same command names.
    assert reg_noarg.has("hire_member") and reg_noarg.has("fire_member")
    assert reg_none.has("hire_member") and reg_none.has("fire_member")
    # Same descriptor set (field-wise model equality, same order).
    assert reg_none.descriptors() == reg_noarg.descriptors()
    # Same dispatch behaviour for an existing tool-card command.
    assert reg_none.dispatch("/fire_member Bob") == reg_noarg.dispatch("/fire_member Bob")


def test_extra_commands_empty_list_adds_nothing() -> None:
    registry = _registry(_HireCard()).descriptors()
    factory = ToolFactory(tool_cards=[_HireCard()])
    reg_empty = factory.get_command_registry(extra_commands=[])
    assert reg_empty.descriptors() == registry


# --- AC #2: extra callable becomes a dispatchable command -------------------


def test_extra_command_no_arg_dispatchable_under_baseagent() -> None:
    def compact() -> str:
        """Compact the conversation context."""
        return "compacted"

    factory = ToolFactory(tool_cards=[_HireCard()])
    registry = factory.get_command_registry(extra_commands=[compact])

    by_name = {d.name: d for d in registry.descriptors()}
    assert "compact" in by_name
    assert by_name["compact"].tool_card == "BaseAgent"
    # Tool-card command still present alongside the agent-owned one.
    assert by_name["hire_member"].tool_card == "_HireCard"
    # Dispatch invokes the callable and string-renders its result.
    assert registry.dispatch("/compact") == "compacted"


def test_extra_command_typed_param_coerced_via_existing_path() -> None:
    def echo(n: int) -> str:
        """Echo double the integer argument."""
        return str(n * 2)

    factory = ToolFactory(tool_cards=[_HireCard()])
    registry = factory.get_command_registry(extra_commands=[echo])

    desc = {d.name: d for d in registry.descriptors()}["echo"]
    assert [a.name for a in desc.args] == ["n"]
    assert desc.args[0].type == "integer"
    assert desc.args[0].required is True
    # "21" coerced to int 21 via the same TypeAdapter machinery -> 42, not "2121".
    assert registry.dispatch("/echo 21") == "42"


# --- AC #3: collision raises at build time ----------------------------------


def test_extra_command_collides_with_tool_card_command_raises() -> None:
    def hire_member() -> str:
        """Collides with _HireCard's hire_member."""
        return "x"

    factory = ToolFactory(tool_cards=[_HireCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry(extra_commands=[hire_member])
    assert "hire_member" in str(exc.value)


def test_two_extra_commands_same_name_collide_raises() -> None:
    def make(value: str) -> Callable[[], str]:
        def compact() -> str:
            """Duplicate-named extra callable."""
            return value

        return compact

    factory = ToolFactory(tool_cards=[_HireCard()])
    with pytest.raises(ValueError) as exc:
        factory.get_command_registry(extra_commands=[make("a"), make("b")])
    assert "compact" in str(exc.value)


# --- AC #4: retry-wrapping preserves name + signature -----------------------


def test_extra_command_retry_wrap_preserves_name_and_signature() -> None:
    class _MyRetryError(Exception):
        pass

    def compact(scope: str) -> str:
        """Raises retriable when invoked."""
        raise RetriableError("retry me")

    factory = ToolFactory(tool_cards=[_HireCard()], retry_exception=_MyRetryError)
    registry = factory.get_command_registry(extra_commands=[compact])

    # Registers under the ORIGINAL name (never "/wrapper") with its param intact.
    assert registry.has("compact")
    desc = {d.name: d for d in registry.descriptors()}["compact"]
    assert desc.tool_card == "BaseAgent"
    assert [a.name for a in desc.args] == ["scope"]
    # The wrapped callable converts RetriableError -> the configured retry_exception.
    fn = registry.callable("compact")
    with pytest.raises(_MyRetryError):
        fn("everything")
    # Through dispatch, that failure is caught and surfaced as a result string.
    assert "compact" in registry.dispatch("/compact everything")


# --- AC #5: unknown command + boundary parity -------------------------------


def test_unknown_command_raises_with_and_without_extra_commands() -> None:
    def compact() -> str:
        """Agent-owned built-in."""
        return "compacted"

    factory = ToolFactory(tool_cards=[_HireCard()])
    reg_plain = factory.get_command_registry()
    reg_extra = factory.get_command_registry(extra_commands=[compact])

    with pytest.raises(CommandNotRecognized):
        reg_plain.dispatch("/nope")
    with pytest.raises(CommandNotRecognized):
        reg_extra.dispatch("/nope")
