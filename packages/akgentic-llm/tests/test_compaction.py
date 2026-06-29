"""Tests for the compaction strategy seam, registry, and summarizer (Story 12-2)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

import akgentic.llm.compaction as comp
import akgentic.llm.context as ctx
from akgentic.llm.compaction import (
    COMPACTION_STRATEGIES,
    CompactionResult,
    CompactionStrategy,
    NoOpCompaction,
    SlidingWindowCompaction,
    SummarizingCompaction,
    _drop_orphan_tool_results,
    _extract_text_from_part,
    _format_request_part,
    _format_response_part,
    _has_tool_call,
    _has_tool_return,
    _is_system_message,
    _split_messages,
    create_compaction,
)
from akgentic.llm.config import CompactionConfig, ModelConfig
from akgentic.llm.context import ContextManager
from akgentic.llm.event import LlmContextCompactedEvent, LlmMessageEvent

# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def _sys(text: str = "system") -> ModelRequest:
    return ModelRequest(parts=[SystemPromptPart(content=text)])


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _calls(*specs: tuple[str, str]) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=n, tool_call_id=cid, args="{}") for n, cid in specs]
    )


def _returns(*specs: tuple[str, str]) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=n, tool_call_id=cid, content="ok") for n, cid in specs]
    )


class _StubSummarizer:
    """Async ``run`` stub with a ``run_sync`` sentinel to assert await-not-run_sync."""

    def __init__(self, output: str = "SUMMARY") -> None:
        self.run = AsyncMock(return_value=SimpleNamespace(output=output))
        self.run_sync = MagicMock()


# ---------------------------------------------------------------------------
# AC 1 / AC 2 — CompactionResult + Protocol
# ---------------------------------------------------------------------------


def test_compaction_result_field_order_and_default() -> None:
    names = [f.name for f in dataclasses.fields(CompactionResult)]
    assert names == ["summary", "replaced_message_count", "tokens_after"]
    assert CompactionResult(summary="s", replaced_message_count=3).tokens_after is None


def test_compaction_result_is_frozen() -> None:
    result = CompactionResult(summary="s", replaced_message_count=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.summary = "x"  # type: ignore[misc]


def test_builtins_satisfy_protocol() -> None:
    assert isinstance(NoOpCompaction(), CompactionStrategy)
    assert isinstance(SlidingWindowCompaction(4), CompactionStrategy)
    assert isinstance(SummarizingCompaction(CompactionConfig(), ModelConfig(), None), CompactionStrategy)


# ---------------------------------------------------------------------------
# AC 3 / AC 4 — registry + resolver
# ---------------------------------------------------------------------------


def test_create_compaction_resolves_builtin_ids() -> None:
    mc = ModelConfig()
    assert isinstance(create_compaction(CompactionConfig(strategy="none"), mc), NoOpCompaction)
    assert isinstance(
        create_compaction(CompactionConfig(strategy="sliding_window"), mc), SlidingWindowCompaction
    )
    assert isinstance(
        create_compaction(CompactionConfig(strategy="summarize"), mc), SummarizingCompaction
    )


def test_sliding_window_factory_uses_keep_recent() -> None:
    strat = create_compaction(
        CompactionConfig(strategy="sliding_window", keep_recent_messages=7), ModelConfig()
    )
    assert isinstance(strat, SlidingWindowCompaction)
    assert strat._keep_recent == 7


def test_summarize_factory_passes_http_client() -> None:
    sentinel = object()
    strat = create_compaction(CompactionConfig(strategy="summarize"), ModelConfig(), sentinel)  # type: ignore[arg-type]
    assert isinstance(strat, SummarizingCompaction)
    assert strat._http_client is sentinel


def test_registry_is_mutable_open_extension() -> None:
    sentinel = NoOpCompaction()
    COMPACTION_STRATEGIES["custom_test"] = lambda cfg, mc, hc=None: sentinel
    try:
        assert create_compaction(CompactionConfig(strategy="custom_test"), ModelConfig()) is sentinel
    finally:
        del COMPACTION_STRATEGIES["custom_test"]


def test_unknown_bare_id_raises_listing_registered() -> None:
    with pytest.raises(ValueError) as exc:
        create_compaction(CompactionConfig(strategy="nope"), ModelConfig())
    message = str(exc.value)
    assert "nope" in message
    for registered in ("none", "sliding_window", "summarize"):
        assert registered in message


# ---------------------------------------------------------------------------
# AC 5 — FQCN resolution of an external user class (no sibling import)
# ---------------------------------------------------------------------------


def test_fqcn_resolves_external_user_class() -> None:
    module = types.ModuleType("throwaway_compaction_mod")

    class UserStrategy:
        def __init__(self, cfg: object, model_cfg: object, http_client: object = None) -> None:
            self.cfg = cfg

        async def compact(self, messages: list[ModelMessage]) -> CompactionResult:
            return CompactionResult(summary="user", replaced_message_count=0)

    module.UserStrategy = UserStrategy  # type: ignore[attr-defined]
    sys.modules["throwaway_compaction_mod"] = module
    try:
        cfg = CompactionConfig(strategy="throwaway_compaction_mod.UserStrategy")
        resolved = create_compaction(cfg, ModelConfig())
        assert isinstance(resolved, UserStrategy)
        assert isinstance(resolved, CompactionStrategy)
    finally:
        del sys.modules["throwaway_compaction_mod"]


# ---------------------------------------------------------------------------
# AC 6 — NoOpCompaction
# ---------------------------------------------------------------------------


async def test_noop_zero_replacement_on_nonempty_history() -> None:
    msgs = [_user("hi"), _assistant("hello"), _user("more")]
    assert await NoOpCompaction().compact(msgs) == CompactionResult("", 0, None)


# ---------------------------------------------------------------------------
# AC 7 — SlidingWindowCompaction
# ---------------------------------------------------------------------------


async def test_sliding_window_drops_head_with_marker() -> None:
    msgs: list[ModelMessage] = [_user(f"u{i}") for i in range(6)]
    result = await SlidingWindowCompaction(2).compact(msgs)
    assert result.replaced_message_count == 4
    assert "dropped 4" in result.summary
    # Story 12-4: a folding sliding window reports a non-null retained-context estimate.
    assert result.tokens_after is not None and result.tokens_after > 0


async def test_sliding_window_nothing_to_drop_is_zero_replacement() -> None:
    result = await SlidingWindowCompaction(4).compact([_user("a"), _user("b")])
    assert result == CompactionResult("", 0, None)


# ---------------------------------------------------------------------------
# AC 8 — _split_messages
# ---------------------------------------------------------------------------


def test_split_exempts_system_prompts_from_anywhere() -> None:
    msgs = [_sys("s0"), _user("u1"), _sys("s-mid"), _assistant("a1"), _user("u2"), _user("u3")]
    system, middle, tail = _split_messages(msgs, 2)
    assert system == [msgs[0], msgs[2]]
    assert middle == [msgs[1], msgs[3]]
    assert tail == [msgs[4], msgs[5]]


def test_split_empty_middle_when_rest_within_keep() -> None:
    msgs = [_sys(), _user("u1"), _user("u2")]
    system, middle, tail = _split_messages(msgs, 4)
    assert system == [msgs[0]]
    assert middle == []
    assert tail == [msgs[1], msgs[2]]


def test_split_exact_tail_size_without_tool_pairs() -> None:
    msgs: list[ModelMessage] = [_user(f"u{i}") for i in range(10)]
    _system, middle, tail = _split_messages(msgs, 3)
    assert len(tail) == 3
    assert len(middle) == 7


def test_split_parallel_tool_call_not_orphaned() -> None:
    call = _calls(("f", "c1"), ("g", "c2"))
    ret = _returns(("f", "c1"), ("g", "c2"))
    msgs = [_user("start"), call, ret, _user("next")]
    _system, middle, tail = _split_messages(msgs, 2)
    assert call in tail and ret in tail
    assert middle == [msgs[0]]
    needed: set[str] = set()
    issued: set[str] = set()
    for m in tail:
        needed |= comp._tool_result_call_ids(m)
        issued |= comp._tool_call_issued_ids(m)
    assert needed.issubset(issued)


def test_split_retry_prompt_with_tool_name_treated_as_tool_result() -> None:
    call = _calls(("f", "c1"))
    retry = ModelRequest(parts=[RetryPromptPart(content="bad", tool_name="f", tool_call_id="c1")])
    msgs = [_user("start"), call, retry, _user("next")]
    _system, middle, tail = _split_messages(msgs, 2)
    assert call in tail and retry in tail
    assert middle == [msgs[0]]


def test_split_symmetric_pull_trailing_response_answered_in_tail() -> None:
    # Defensive symmetric guard: a trailing ModelResponse whose issued id is answered
    # by a tool-result already in the tail is pulled into the tail.
    issuer = _calls(("f", "c1"))
    dup_issuer = _calls(("f", "c1"))
    ret = _returns(("f", "c1"))
    msgs = [issuer, dup_issuer, ret]
    _system, middle, tail = _split_messages(msgs, 2)
    assert middle == []
    assert tail == msgs


def test_split_symmetric_guard_breaks_on_textonly_trailing_response() -> None:
    text_resp = _assistant("just text")
    msgs = [text_resp, _user("u1"), _user("u2")]
    _system, middle, tail = _split_messages(msgs, 2)
    assert middle == [text_resp]
    assert tail == [msgs[1], msgs[2]]


def test_has_tool_helpers() -> None:
    assert _has_tool_call(_calls(("f", "c1"))) is True
    assert _has_tool_call(_user("x")) is False
    assert _has_tool_return(_returns(("f", "c1"))) is True
    assert _has_tool_return(_assistant("x")) is False


# ---------------------------------------------------------------------------
# Summary-prompt rendering helpers (exercised on the SummarizingCompaction path)
# ---------------------------------------------------------------------------


def test_extract_text_from_part_branches() -> None:
    assert _extract_text_from_part(SystemPromptPart(content="sys")) == "sys"
    assert _extract_text_from_part(UserPromptPart(content="u")) == "u"
    assert _extract_text_from_part(TextPart(content="t")) == "t"
    assert _extract_text_from_part(
        ToolCallPart(tool_name="f", tool_call_id="c1", args="{}")
    ).startswith("[tool_call:f]")
    ret = _extract_text_from_part(ToolReturnPart(tool_name="f", tool_call_id="c1", content=123))
    assert ret.startswith("[tool_return:f]") and "123" in ret
    # RetryPromptPart is unhandled -> str(part) fallback
    assert _extract_text_from_part(RetryPromptPart(content="x")) != ""


def test_format_request_part_branches() -> None:
    assert _format_request_part(SystemPromptPart(content="sys")) is None
    assert _format_request_part(UserPromptPart(content="hi")) == "USER: hi"
    long_ret = _format_request_part(
        ToolReturnPart(tool_name="f", tool_call_id="c1", content="x" * 4000)
    )
    assert long_ret is not None and long_ret.startswith("TOOL_RESULT (f):") and "[truncated]" in long_ret
    retry_tool = _format_request_part(
        RetryPromptPart(content="bad", tool_name="f", tool_call_id="c1")
    )
    assert retry_tool is not None and retry_tool.startswith("TOOL_RETRY (f):")
    retry_out = _format_request_part(RetryPromptPart(content="y" * 4000))
    assert retry_out is not None and retry_out.startswith("TOOL_RETRY (output):")
    assert "[truncated]" in retry_out


def test_format_response_part_branches() -> None:
    assert _format_response_part(TextPart(content="hello")) == "ASSISTANT: hello"
    long_call = _format_response_part(
        ToolCallPart(tool_name="f", tool_call_id="c1", args="z" * 2000)
    )
    assert long_call.startswith("TOOL_CALL (f):") and "[truncated]" in long_call


# ---------------------------------------------------------------------------
# AC 9 — _drop_orphan_tool_results
# ---------------------------------------------------------------------------


def test_drop_orphan_dangling_tool_return_removed() -> None:
    msgs = [_user("hi"), _returns(("f", "ghost"))]
    assert _drop_orphan_tool_results(msgs) == [msgs[0]]


def test_drop_orphan_preserves_valid_parallel_sequence() -> None:
    msgs = [_user("start"), _calls(("f", "c1"), ("g", "c2")), _returns(("f", "c1"), ("g", "c2"))]
    assert _drop_orphan_tool_results(msgs) == msgs


def test_drop_orphan_rebuilds_partial_request() -> None:
    mixed = ModelRequest(
        parts=[
            ToolReturnPart(tool_name="f", tool_call_id="c1", content="ok"),
            ToolReturnPart(tool_name="g", tool_call_id="ghost", content="bad"),
        ]
    )
    cleaned = _drop_orphan_tool_results([_calls(("f", "c1")), mixed])
    assert len(cleaned) == 2
    kept = cleaned[1]
    assert isinstance(kept, ModelRequest)
    assert {p.tool_call_id for p in kept.parts if isinstance(p, ToolReturnPart)} == {"c1"}


# ---------------------------------------------------------------------------
# AC 10 / AC 11 / AC 12 — SummarizingCompaction
# ---------------------------------------------------------------------------


async def test_summarizing_awaits_run_never_run_sync() -> None:
    strat = SummarizingCompaction(CompactionConfig(keep_recent_messages=1), ModelConfig(), None)
    stub = _StubSummarizer("THE SUMMARY")
    strat._summarizer = stub  # type: ignore[assignment]
    result = await strat.compact([_user("a"), _assistant("b"), _user("c")])
    # 12-7 full-fold: all 3 non-system messages are folded (no keep_recent tail).
    assert result.replaced_message_count == 3
    assert result.summary == "THE SUMMARY"
    # Story 12-4: the summary path reports a retained-context estimate.
    assert result.tokens_after is not None
    stub.run.assert_awaited_once()
    stub.run_sync.assert_not_called()


async def test_summarizing_no_non_system_content_skips_summarizer() -> None:
    # 12-7: the no-op trigger is "no foldable non-system content", not an empty middle.
    strat = SummarizingCompaction(CompactionConfig(keep_recent_messages=10), ModelConfig(), None)
    stub = _StubSummarizer()
    strat._summarizer = stub  # type: ignore[assignment]
    result = await strat.compact([_sys("only system")])
    assert result == CompactionResult("", 0, None)
    stub.run.assert_not_awaited()


async def test_summarizing_falls_back_to_truncation_on_error() -> None:
    strat = SummarizingCompaction(CompactionConfig(keep_recent_messages=1), ModelConfig(), None)
    stub = MagicMock()
    stub.run = AsyncMock(side_effect=RuntimeError("boom"))
    stub.run_sync = MagicMock()
    strat._summarizer = stub  # type: ignore[assignment]
    result = await strat.compact([_user("a"), _assistant("b"), _user("c")])
    # 12-7 full-fold: the truncation fallback also folds every non-system message.
    assert result.replaced_message_count == 3
    assert "truncated to fit the context window" in result.summary
    # Story 12-4: the truncation fallback also reports a retained-context estimate.
    assert result.tokens_after is not None
    stub.run_sync.assert_not_called()


async def test_summarizing_prompt_carries_target_tokens_and_conversation() -> None:
    cfg = CompactionConfig(keep_recent_messages=1, summary_target_tokens=4242)
    strat = SummarizingCompaction(cfg, ModelConfig(), None)
    stub = _StubSummarizer()
    strat._summarizer = stub  # type: ignore[assignment]
    await strat.compact([_user("FIND_DOSSIER_42"), _assistant("looking"), _user("recent")])
    prompt = stub.run.await_args.args[0]
    assert "4242" in prompt
    assert "FIND_DOSSIER_42" in prompt


def test_build_summarizer_wires_default_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-config (v1) summarizer is wired with the registry's default instructions."""
    captured: dict[str, object] = {}

    def fake_agent(**kwargs: object) -> _StubSummarizer:
        captured.update(kwargs)
        return _StubSummarizer()

    monkeypatch.setattr(comp, "create_model", lambda mc, hc=None: object())
    monkeypatch.setattr(comp, "Agent", fake_agent)
    strat = SummarizingCompaction(CompactionConfig(), ModelConfig(), None)
    built = strat._build_summarizer()
    assert strat._build_summarizer() is built  # cached on first build
    assert captured["instructions"] == comp.SUMMARY_INSTRUCTIONS["v1"]
    assert captured["instructions"] == comp._DEFAULT_SUMMARY_INSTRUCTIONS
    assert captured["output_type"] is str


def test_build_summarizer_resolves_instructions_by_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The programmatic override seam: a registered prompt version is resolved from
    SUMMARY_INSTRUCTIONS and reaches the Agent. The config/start event carries only the id."""
    captured: dict[str, object] = {}

    def fake_agent(**kwargs: object) -> _StubSummarizer:
        captured.update(kwargs)
        return _StubSummarizer()

    monkeypatch.setattr(comp, "create_model", lambda mc, hc=None: object())
    monkeypatch.setattr(comp, "Agent", fake_agent)
    comp.SUMMARY_INSTRUCTIONS["custom-test"] = "CUSTOM-XYZ instructions for the summarizer"
    try:
        cfg = CompactionConfig(summarizer_prompt_version="custom-test")
        SummarizingCompaction(cfg, ModelConfig(), None)._build_summarizer()
        assert captured["instructions"] == "CUSTOM-XYZ instructions for the summarizer"
    finally:
        del comp.SUMMARY_INSTRUCTIONS["custom-test"]


def test_build_summarizer_unknown_version_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unregistered prompt version falls back to the domain-agnostic default."""
    captured: dict[str, object] = {}

    def fake_agent(**kwargs: object) -> _StubSummarizer:
        captured.update(kwargs)
        return _StubSummarizer()

    monkeypatch.setattr(comp, "create_model", lambda mc, hc=None: object())
    monkeypatch.setattr(comp, "Agent", fake_agent)
    cfg = CompactionConfig(summarizer_prompt_version="does-not-exist")
    SummarizingCompaction(cfg, ModelConfig(), None)._build_summarizer()
    assert captured["instructions"] == comp._DEFAULT_SUMMARY_INSTRUCTIONS


def test_summary_instructions_registry_default_business_free_and_keeps_intent() -> None:
    """The old hardcoded constant is gone; the registry's "v1" default is domain-agnostic
    (no HR/payroll terms) yet preserves the summary intent."""
    assert not hasattr(comp, "_SUMMARY_INSTRUCTIONS")
    assert "v1" in comp.SUMMARY_INSTRUCTIONS
    text = comp.SUMMARY_INSTRUCTIONS["v1"]
    assert text == comp._DEFAULT_SUMMARY_INSTRUCTIONS
    lowered = text.lower()
    forbidden = [
        "payco",
        "dossier",
        "joint committee",
        "joined committee",
        "employee",
        "customer",
        "payroll",
        "salary",
    ]
    present = [term for term in forbidden if term in lowered]
    assert not present, f"default summary instructions still carry business terms: {present}"
    assert "Key entities" in text
    assert "verbatim" in lowered


# ---------------------------------------------------------------------------
# AC 13 — no sibling import; no tiktoken
# ---------------------------------------------------------------------------


def test_module_imports_no_akgentic_sibling_and_no_tiktoken() -> None:
    tree = ast.parse(inspect.getsource(comp))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    # intra-package imports are relative (.config/.providers) -> never surface as "akgentic.*"
    assert not any(name.startswith("akgentic") for name in imported)
    assert "tiktoken" not in imported


# ---------------------------------------------------------------------------
# Story 12-4 — retained-context tokens_after estimate (ADR-010 §1)
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_len_div_four() -> None:
    assert comp._estimate_tokens("") == 0
    assert comp._estimate_tokens("abc") == 0  # 3 // 4
    assert comp._estimate_tokens("abcd") == 1
    assert comp._estimate_tokens("a" * 41) == 10


def test_estimate_retained_sums_system_summary_and_tail() -> None:
    system = [_sys("s" * 40)]  # 10 tokens
    tail = [_user("u" * 20)]  # 5 tokens
    assert comp._estimate_retained(system, "x" * 8, tail) == 10 + 2 + 5


async def test_summarizing_tokens_after_positive_and_below_full_estimate() -> None:
    # A large middle replaced by a short summary => retained estimate well below the
    # full-history estimate (the whole point of compaction). AC 1.
    msgs: list[ModelMessage] = [
        _sys("s" * 40),
        _user("x" * 400),
        _assistant("y" * 400),
        _user("recent question"),
    ]
    strat = SummarizingCompaction(CompactionConfig(keep_recent_messages=1), ModelConfig(), None)
    strat._summarizer = _StubSummarizer("short summary")  # type: ignore[assignment]
    result = await strat.compact(msgs)
    full_estimate = comp._estimate_tokens(comp._join_message_text(msgs))
    assert result.tokens_after is not None
    assert result.tokens_after > 0
    assert result.tokens_after < full_estimate


async def test_truncation_fallback_reports_nonnull_tokens_after() -> None:
    # Summarizer error path still yields a non-null estimate over marker + tail. AC 2.
    strat = SummarizingCompaction(CompactionConfig(keep_recent_messages=1), ModelConfig(), None)
    stub = MagicMock()
    stub.run = AsyncMock(side_effect=RuntimeError("boom"))
    stub.run_sync = MagicMock()
    strat._summarizer = stub  # type: ignore[assignment]
    result = await strat.compact([_user("a" * 200), _assistant("b" * 200), _user("recent")])
    assert result.tokens_after is not None
    assert result.tokens_after > 0


async def test_sliding_window_tokens_after_matches_retained_estimate() -> None:
    # AC 3: SlidingWindowCompaction reports a non-null estimate over its retained content.
    msgs: list[ModelMessage] = [_sys("s" * 40)] + [_user(f"u{i}" * 10) for i in range(6)]
    result = await SlidingWindowCompaction(2).compact(msgs)
    system, _middle, tail = _split_messages(msgs, 2)
    assert result.tokens_after == comp._estimate_retained(system, result.summary, tail)
    assert result.tokens_after is not None and result.tokens_after > 0


# ---------------------------------------------------------------------------
# Story 12-6 — single is-system predicate across split + fold (ADR-010 §9)
# ---------------------------------------------------------------------------


def _mixed(sys_text: str, user_text: str) -> ModelRequest:
    """The /clear-then-operator-action shape: one ModelRequest with system + user parts."""
    return ModelRequest(
        parts=[SystemPromptPart(content=sys_text), UserPromptPart(content=user_text)]
    )


def _mixed_history() -> list[ModelMessage]:
    """Mixed system+user head, then a tool pair the boundary guard keeps in the tail.

    With ``keep_recent=2`` the split is: system=[m0]; middle=[m1, m2];
    tail=[m3(call), m4(return), m5] (the guard pulls the m3 call back to pair with m4).
    """
    return [
        _mixed("backstory", "first user turn"),  # m0 — mixed system+user (never-fold)
        _user("u1"),  # m1 — summarizable middle
        _assistant("a1"),  # m2 — summarizable middle
        _calls(("workspace_write", "c1")),  # m3 — tool call (boundary-guarded into tail)
        _returns(("workspace_write", "c1")),  # m4 — its tool return
        _user("u2"),  # m5 — recent tail
    ]


def _compacted_12_6(summary: str, replaced: int) -> LlmContextCompactedEvent:
    return LlmContextCompactedEvent(
        run_id=None,
        strategy_id="sliding_window",
        summary=summary,
        replaced_message_count=replaced,
        summarizer_prompt_version="v1",
        tokens_before=None,
        tokens_after=None,
    )


class _Story126Recorder:
    """Observer recording every emitted domain event in order."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def notify_event(self, event: object) -> None:
        self.events.append(event)


def _shape_12_6(messages: list[ModelMessage]) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    """Timestamp-independent projection: (message type, ((part type, content), ...))."""
    return [
        (
            type(m).__name__,
            tuple((type(p).__name__, getattr(p, "content", None)) for p in m.parts),
        )
        for m in messages
    ]


def test_split_exempts_mixed_system_user_message() -> None:
    """AC1: the any-part rule pulls a mixed system+user ModelRequest into system_prompts."""
    history = _mixed_history()
    system, middle, tail = _split_messages(history, 2)
    assert system == [history[0]]  # mixed message exempt
    assert middle == [history[1], history[2]]  # only the summarizable middle counts
    assert tail == [history[3], history[4], history[5]]  # tool pair kept verbatim


@pytest.mark.parametrize(
    ("msg", "is_system"),
    [
        (_sys("pure system"), True),
        (_mixed("sys", "user"), True),
        (_user("just user"), False),
        (ModelRequest(parts=[]), False),  # any([]) is False — non-system on both sides
    ],
    ids=["pure-system", "mixed-system-user", "no-system", "empty-parts"],
)
def test_is_system_predicate_single_source_and_parity(
    msg: ModelMessage, is_system: bool
) -> None:
    """AC6: one predicate object, shared by both call sites; split partition agrees with it."""
    # Single source of truth: context delegates to compaction's predicate (same object).
    assert ctx._is_system_message is _is_system_message
    assert _is_system_message(msg) is is_system
    # The _split_messages system partition agrees with the never-fold classifier.
    fillers: list[ModelMessage] = [_user(f"f{i}") for i in range(4)]
    system, _middle, _tail = _split_messages([msg, *fillers], 2)
    assert any(m is msg for m in system) is is_system


async def test_mixed_message_exempt_on_split_and_fold() -> None:
    """AC2/AC3: count and fold cover the same middle; the tool pair survives intact."""
    history = _mixed_history()
    keep = 2

    sliding = await SlidingWindowCompaction(keep).compact(history)
    summ_strat = SummarizingCompaction(
        CompactionConfig(keep_recent_messages=keep), ModelConfig(), None
    )
    summ_strat._summarizer = _StubSummarizer("SUM")  # type: ignore[assignment]
    summ = await summ_strat.compact(history)

    # Sliding window exempts the mixed message — the count is len(middle)=2, not 3.
    assert sliding.replaced_message_count == 2
    # 12-7: summarize full-folds every non-system message (m1, a1, call, return, u2 = 5).
    assert summ.replaced_message_count == 5

    event = _compacted_12_6(sliding.summary, sliding.replaced_message_count)
    folded = ContextManager.fold_compaction(history, event)

    assert len(folded) == 5
    assert folded[0] is history[0]  # mixed message never folded
    summary_msg = folded[1]
    assert isinstance(summary_msg, ModelRequest)
    assert isinstance(summary_msg.parts[0], UserPromptPart)
    assert summary_msg.parts[0].content.startswith("[Conversation summary]")
    assert folded[2] is history[3]  # tool_call intact
    assert folded[3] is history[4]  # tool_return intact
    assert folded[4] is history[5]

    # AC3: no orphan — the call/return pair is whole, so _drop_orphan removes nothing.
    assert _drop_orphan_tool_results(folded) == folded
    assert comp._tool_call_issued_ids(folded[2]) == {"c1"}
    assert comp._tool_result_call_ids(folded[3]) == {"c1"}


async def test_mixed_message_live_compact_equals_sequence_replay() -> None:
    """AC4: live ContextManager.compact equals the sequence-order event-log replay fold."""
    history = _mixed_history()
    sliding = await SlidingWindowCompaction(2).compact(history)
    event = _compacted_12_6(sliding.summary, sliding.replaced_message_count)

    live = ContextManager()
    recorder = _Story126Recorder()
    live.subscribe(recorder)
    for m in history:
        live.add_message(m)
    live.compact(event)

    # Sequence-order replay: LlmMessageEvent -> append; LlmContextCompactedEvent -> fold.
    replay: list[ModelMessage] = []
    for emitted in recorder.events:
        if isinstance(emitted, LlmContextCompactedEvent):
            replay = ContextManager.fold_compaction(replay, emitted)
        elif isinstance(emitted, LlmMessageEvent):
            replay.append(emitted.message)

    assert _shape_12_6(replay) == _shape_12_6(live.messages)
    # The mixed message and the tool pair survived in the reconstructed context.
    assert len(live.messages) == 5
    assert live.messages[2] is history[3] and live.messages[3] is history[4]


async def test_no_system_history_still_folds_exact_count() -> None:
    """AC5: a history with no SystemPromptPart still folds exactly replaced_message_count."""
    history: list[ModelMessage] = [_user(f"u{i}") for i in range(5)]
    sliding = await SlidingWindowCompaction(2).compact(history)
    assert sliding.replaced_message_count == 3  # middle = first 3 of 5, keep_recent=2

    event = _compacted_12_6(sliding.summary, sliding.replaced_message_count)
    folded = ContextManager.fold_compaction(history, event)
    # summary + last two originals
    assert len(folded) == 3
    assert folded[1] is history[3]
    assert folded[2] is history[4]


async def test_pure_system_head_still_exempt() -> None:
    """AC5: a pure-system ModelRequest is still never summarized, counted, or folded."""
    sys_msg = _sys("pure")
    history: list[ModelMessage] = [sys_msg, *[_user(f"u{i}") for i in range(4)]]
    sliding = await SlidingWindowCompaction(2).compact(history)
    assert sliding.replaced_message_count == 2  # 4 non-system, keep 2 -> middle is 2

    event = _compacted_12_6(sliding.summary, sliding.replaced_message_count)
    folded = ContextManager.fold_compaction(history, event)
    assert folded[0] is sys_msg  # pure-system head never folded


# ---------------------------------------------------------------------------
# Story 12-7 — full-fold summarize + part-level system exemption (ADR-010 §9)
# ---------------------------------------------------------------------------

#: The fused /clear-then-first-run user text the part-level fold must NOT keep verbatim.
_CLEAR_HEAD_USER = '[Operator action] "/clear" — context cleared\nfirst user turn'


def _mixed_history_12_7() -> list[ModelMessage]:
    """A mixed system+user head (fused /clear text) then N non-system turns w/ a tool pair."""
    return [
        _mixed("backstory", _CLEAR_HEAD_USER),  # m0 — mixed system+user
        _user("u1"),  # m1
        _assistant("a1"),  # m2
        _calls(("workspace_write", "c1")),  # m3 — tool call
        _returns(("workspace_write", "c1")),  # m4 — tool return
        _user("u2"),  # m5
    ]


def _summarize_event(result: CompactionResult) -> LlmContextCompactedEvent:
    """Wrap a SummarizingCompaction result in a ``strategy_id='summarize'`` event."""
    return LlmContextCompactedEvent(
        run_id=None,
        strategy_id="summarize",
        summary=result.summary,
        replaced_message_count=result.replaced_message_count,
        summarizer_prompt_version="v1",
        tokens_before=None,
        tokens_after=result.tokens_after,
    )


async def _summarize_via_manager(
    history: list[ModelMessage], output: str = "FULL SUMMARY", *, keep_recent: int = 2
) -> tuple[ContextManager, str]:
    """Run the live summarize compact over *history*; return (manager, captured prompt)."""
    strat = SummarizingCompaction(
        CompactionConfig(keep_recent_messages=keep_recent), ModelConfig(), None
    )
    stub = _StubSummarizer(output)
    strat._summarizer = stub  # type: ignore[assignment]
    mgr = ContextManager()
    for m in history:
        mgr.add_message(m)
    result = await strat.compact(mgr.messages)
    mgr.compact(_summarize_event(result))
    return mgr, stub.run.await_args.args[0]


async def test_summarize_full_fold_to_system_plus_single_summary() -> None:
    """AC1/AC2: live compact yields [system-parts-only head] + [one summary], all folded."""
    history = _mixed_history_12_7()
    mgr, prompt = await _summarize_via_manager(history, "FULL SUMMARY")

    msgs = mgr.messages
    assert len(msgs) == 2  # exactly [system head] + [summary]
    head = msgs[0]
    assert isinstance(head, ModelRequest)
    # AC2: part-level exemption — the fused UserPromptPart is gone from the head.
    assert [type(p).__name__ for p in head.parts] == ["SystemPromptPart"]
    assert head.parts[0].content == "backstory"
    summary_msg = msgs[1]
    assert isinstance(summary_msg, ModelRequest)
    assert isinstance(summary_msg.parts[0], UserPromptPart)
    assert summary_msg.parts[0].content == "[Conversation summary] FULL SUMMARY"

    # AC1: the summarizer input covered the whole non-system conversation, head-to-tail.
    assert "first user turn" in prompt  # the fused head user text was summarized
    assert "u1" in prompt and "u2" in prompt and "workspace_write" in prompt
    # AC1/AC2: no fused /clear text and no verbatim Q/A survives in the folded context.
    folded_repr = repr(_shape_12_6(msgs))
    assert '[Operator action] "/clear"' not in folded_repr
    assert "first user turn" not in folded_repr
    assert "u1" not in folded_repr and "u2" not in folded_repr


async def test_summarize_fold_ignores_replaced_message_count() -> None:
    """AC3: the summarize fold boundary is "all non-system", not replaced_message_count."""
    history = _mixed_history_12_7()
    # A deliberately "wrong" small count — full-fold must still drop everything non-system.
    event = LlmContextCompactedEvent(
        run_id=None,
        strategy_id="summarize",
        summary="S",
        replaced_message_count=1,
        summarizer_prompt_version="v1",
        tokens_before=None,
        tokens_after=None,
    )
    folded = ContextManager.fold_compaction(history, event)
    assert len(folded) == 2
    assert [type(p).__name__ for p in folded[0].parts] == ["SystemPromptPart"]
    assert folded[1].parts[0].content == "[Conversation summary] S"  # type: ignore[union-attr]


def test_count_fold_still_honors_count_for_non_summarize() -> None:
    """AC3: a non-summarize (sliding_window) event still folds exactly count messages,
    keeping the mixed head whole (message-level exemption, fused user text verbatim)."""
    history = _mixed_history_12_7()
    event = _compacted_12_6("S", 1)  # strategy_id="sliding_window", count=1
    folded = ContextManager.fold_compaction(history, event)
    assert folded[0] is history[0]  # mixed head kept whole — fused UserPromptPart survives
    assert isinstance(folded[1], ModelRequest)
    assert folded[1].parts[0].content == "[Conversation summary] S"  # type: ignore[union-attr]
    # Only one non-system message (m1) folded; the rest stay verbatim.
    assert folded[2] is history[2]


async def test_sequential_compaction_composes() -> None:
    """AC4: a second /compact folds summary1 + everything-since into summary2."""
    history = _mixed_history_12_7()
    mgr, _ = await _summarize_via_manager(history, "SUMMARY_ONE")
    assert len(mgr.messages) == 2  # [system] + [summary1]

    mgr.add_message(_user("u3"))
    mgr.add_message(_assistant("a3"))

    strat2 = SummarizingCompaction(CompactionConfig(), ModelConfig(), None)
    stub2 = _StubSummarizer("SUMMARY_TWO")
    strat2._summarizer = stub2  # type: ignore[assignment]
    result2 = await strat2.compact(mgr.messages)
    prompt2 = stub2.run.await_args.args[0]
    # summary1 (a non-system ModelRequest) and the new turns are all in summary2's input.
    assert "SUMMARY_ONE" in prompt2
    assert "u3" in prompt2 and "a3" in prompt2

    mgr.compact(_summarize_event(result2))
    msgs = mgr.messages
    assert len(msgs) == 2  # [system] + [summary2]
    assert msgs[1].parts[0].content == "[Conversation summary] SUMMARY_TWO"  # type: ignore[union-attr]


async def test_summarize_ignores_keep_recent_messages() -> None:
    """AC5: SummarizingCompaction output is independent of keep_recent_messages."""
    history = _mixed_history_12_7()
    mgr0, _ = await _summarize_via_manager(history, "SAME", keep_recent=0)
    mgr4, _ = await _summarize_via_manager(history, "SAME", keep_recent=4)
    assert _shape_12_6(mgr0.messages) == _shape_12_6(mgr4.messages)
    assert len(mgr0.messages) == 2  # keep_recent=0 foot-gun unreachable: still a clean fold


async def test_sliding_window_still_honors_keep_recent_boundary_guarded() -> None:
    """AC5: SlidingWindowCompaction keeps exactly keep_recent boundary-guarded tail msgs."""
    history = _mixed_history_12_7()
    system, middle, tail = _split_messages(history, 2)
    # keep_recent=2 but the guard pulls the call/return pair into the tail (3 msgs).
    assert system == [history[0]]
    assert middle == [history[1], history[2]]
    assert tail == [history[3], history[4], history[5]]


async def test_summarize_live_compact_equals_sequence_replay() -> None:
    """AC6: live ContextManager.compact equals the event-log replay fold (byte-identical)."""
    history = _mixed_history_12_7()
    strat = SummarizingCompaction(CompactionConfig(), ModelConfig(), None)
    strat._summarizer = _StubSummarizer("REPLAY_SUM")  # type: ignore[assignment]

    live = ContextManager()
    recorder = _Story126Recorder()
    live.subscribe(recorder)
    for m in history:
        live.add_message(m)
    result = await strat.compact(live.messages)
    live.compact(_summarize_event(result))

    replay: list[ModelMessage] = []
    for emitted in recorder.events:
        if isinstance(emitted, LlmContextCompactedEvent):
            replay = ContextManager.fold_compaction(replay, emitted)
        elif isinstance(emitted, LlmMessageEvent):
            replay.append(emitted.message)

    assert _shape_12_6(replay) == _shape_12_6(live.messages)


async def test_summarize_no_foldable_content_is_noop() -> None:
    """AC7: only-system history is a clean no-op (count 0, empty summary, no summarizer)."""
    strat = SummarizingCompaction(CompactionConfig(), ModelConfig(), None)
    stub = _StubSummarizer()
    strat._summarizer = stub  # type: ignore[assignment]
    result = await strat.compact([_sys("only system")])
    assert result == CompactionResult("", 0, None)
    stub.run.assert_not_awaited()


async def test_summarize_no_system_history_folds_to_summary_only() -> None:
    """AC7: a history with no SystemPromptPart folds to [summary] only (empty head)."""
    history: list[ModelMessage] = [_user("u0"), _assistant("a0"), _user("u1")]
    strat = SummarizingCompaction(CompactionConfig(), ModelConfig(), None)
    strat._summarizer = _StubSummarizer("NS")  # type: ignore[assignment]
    result = await strat.compact(history)
    assert result.replaced_message_count == 3
    folded = ContextManager.fold_compaction(history, _summarize_event(result))
    assert len(folded) == 1
    assert folded[0].parts[0].content == "[Conversation summary] NS"  # type: ignore[union-attr]
