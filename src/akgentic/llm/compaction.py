"""Pluggable context-compaction strategies (ADR-010).

Defines the async ``CompactionStrategy`` Protocol, the frozen ``CompactionResult``,
a public mutable ``COMPACTION_STRATEGIES`` registry with a ``create_compaction``
resolver (registry id, else a dotted FQCN via stdlib ``importlib``), and three
built-in strategies. ``SummarizingCompaction`` implements an LLM history-summarization
algorithm: the summarizer is ``await``-ed (never ``run_sync``) and reuses the agent's
shared httpx client.

Imports no akgentic sibling package — the FQCN escape hatch uses stdlib ``importlib``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeGuard, runtime_checkable

from httpx import AsyncClient
from pydantic_ai import Agent
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

from .config import CompactionConfig, ModelConfig
from .providers import create_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a compaction pass — primitives only (no ``ModelMessage``).

    Attributes:
        summary: The produced summary text (empty when nothing was folded).
        replaced_message_count: Leading non-system messages this summary stands in for.
        tokens_after: Optional post-compaction token estimate over the retained context
            (system + summary + tail). ``None`` when nothing was folded (NoOp / empty middle).
    """

    summary: str
    replaced_message_count: int
    tokens_after: int | None = None


@runtime_checkable
class CompactionStrategy(Protocol):
    """How to summarize (the algorithm) and where the fold boundary is.

    The framework owns the mechanical fold and persistence; the strategy only returns
    a ``CompactionResult``. ``compact`` is async so the auto path can ``await`` it
    inside the agent's already-running loop (ADR-009).
    """

    async def compact(self, messages: list[ModelMessage]) -> CompactionResult: ...


# ---------------------------------------------------------------------------
# Message-classification helpers
# ---------------------------------------------------------------------------


def _extract_text_from_part(part: Any) -> str:
    """Best-effort text extraction from any message part."""
    if isinstance(part, (SystemPromptPart, UserPromptPart)):
        prompt_content = part.content
        return prompt_content if isinstance(prompt_content, str) else str(prompt_content)
    if isinstance(part, TextPart):
        return part.content
    if isinstance(part, ToolCallPart):
        args_str = str(part.args) if part.args else ""
        return f"[tool_call:{part.tool_name}] {args_str}"
    if isinstance(part, ToolReturnPart):
        ret = part.content
        ret_text = ret if isinstance(ret, str) else str(ret)
        return f"[tool_return:{part.tool_name}] {ret_text}"
    # Fallback for RetryPromptPart, ThinkingPart, etc.
    return str(part)


def _is_system_prompt_message(msg: ModelMessage) -> bool:
    """Return True if the message is a pure system-prompt injection."""
    if not isinstance(msg, ModelRequest):
        return False
    return all(isinstance(p, SystemPromptPart) for p in msg.parts)


def _is_tool_result_part(part: Any) -> TypeGuard[ToolReturnPart | RetryPromptPart]:
    """Return True if *part* serialises to an OpenAI ``role=tool`` message.

    Both ``ToolReturnPart`` and ``RetryPromptPart`` (when ``tool_name`` is set — a
    tool-call validation retry rather than an output-validation retry) are emitted as
    ``role=tool`` messages, so either orphans at the OpenAI layer if its issuing
    assistant message is not the immediately-preceding one.
    """
    if isinstance(part, ToolReturnPart):
        return True
    if isinstance(part, RetryPromptPart) and part.tool_name is not None:
        return True
    return False


def _tool_result_call_ids(msg: ModelMessage) -> set[str]:
    """Return ``tool_call_id``s referenced by tool-result parts in *msg*."""
    if not isinstance(msg, ModelRequest):
        return set()
    return {p.tool_call_id for p in msg.parts if _is_tool_result_part(p)}


def _tool_call_issued_ids(msg: ModelMessage) -> set[str]:
    """Return ``tool_call_id``s issued by ``ToolCallPart``s in a response."""
    if not isinstance(msg, ModelResponse):
        return set()
    return {p.tool_call_id for p in msg.parts if isinstance(p, ToolCallPart)}


def _has_tool_return(msg: ModelMessage) -> bool:
    """Return True if the message contains any tool-result part."""
    return bool(_tool_result_call_ids(msg))


def _has_tool_call(msg: ModelMessage) -> bool:
    """Return True if the message contains any ToolCallPart."""
    return bool(_tool_call_issued_ids(msg))


def _split_messages(
    messages: list[ModelMessage],
    keep_recent: int,
) -> tuple[list[ModelMessage], list[ModelMessage], list[ModelMessage]]:
    """Split messages into (system_prompts, summarizable_middle, recent_tail).

    * ``system_prompts`` — ALL system-prompt-only ``ModelRequest``s from anywhere in
      the conversation (durable context that must never be summarized away).
    * ``summarizable_middle`` — the bulk of the conversation that can be summarized.
    * ``recent_tail`` — the last *keep_recent* non-system messages (kept verbatim).

    The boundary is adjusted so tool-call / tool-return pairs are never broken: if the
    tail would start with an orphaned tool result, preceding messages are pulled from
    middle into tail until every referenced ``tool_call_id`` is issued in tail; the
    symmetric guard pulls a trailing ``ModelResponse`` from middle when its issued ids
    are answered by tool-results already in tail.
    """
    # 1. Extract ALL system-prompt-only messages from anywhere in the list so injected
    #    context (memory notes, anchors, restart markers) is never summarized away.
    system_prompts: list[ModelMessage] = []
    rest: list[ModelMessage] = []
    for msg in messages:
        if _is_system_prompt_message(msg):
            system_prompts.append(msg)
        else:
            rest.append(msg)

    # 2. Split rest into middle + tail.
    if len(rest) <= keep_recent:
        return system_prompts, [], rest

    middle = rest[:-keep_recent]
    tail = rest[-keep_recent:]

    # 3. Fix the boundary so we never orphan tool returns or tool calls. Parallel tool
    #    calls and retry-after-validation flows make a "pull one preceding message" loop
    #    insufficient, so we pair by ``tool_call_id``: keep extending tail backwards
    #    until every tool-result id referenced inside tail is issued in tail.
    needed: set[str] = set()
    issued: set[str] = set()
    for m in tail:
        needed |= _tool_result_call_ids(m)
        issued |= _tool_call_issued_ids(m)
    while middle and not needed.issubset(issued):
        m = middle.pop()
        tail.insert(0, m)
        needed |= _tool_result_call_ids(m)
        issued |= _tool_call_issued_ids(m)

    # Symmetric guard: if middle now ends with a ``ModelResponse`` whose ``ToolCallPart``
    # ids are answered by tool-results already in tail, pull it into tail too.
    while middle and isinstance(middle[-1], ModelResponse):
        last_call_ids = _tool_call_issued_ids(middle[-1])
        if last_call_ids and last_call_ids & needed:
            m = middle.pop()
            tail.insert(0, m)
            issued |= _tool_call_issued_ids(m)
        else:
            break

    return system_prompts, middle, tail


def _drop_orphan_tool_results(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Strip tool-result parts that violate OpenAI's adjacency rule.

    Walks the history left-to-right tracking the ``tool_call_id``s the *most recent*
    assistant ``ModelResponse`` can answer. The set is replaced (not merged) on every
    new ``ModelResponse``. Any tool-result part whose id is not in the active set is
    dropped, and a ``ModelRequest`` whose only parts were orphans is removed entirely.
    """
    active_ids: set[str] = set()
    cleaned: list[ModelMessage] = []
    dropped = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            # New assistant turn: only its own tool-calls can be answered by following
            # tool-results; any prior unanswered call-ids are now orphaned.
            active_ids = _tool_call_issued_ids(msg)
            cleaned.append(msg)
            continue
        if isinstance(msg, ModelRequest):
            new_parts: list[Any] = []
            for part in msg.parts:
                if _is_tool_result_part(part):
                    if part.tool_call_id in active_ids:
                        new_parts.append(part)
                    else:
                        dropped += 1
                else:
                    new_parts.append(part)
            if new_parts:
                cleaned.append(
                    msg if len(new_parts) == len(msg.parts) else ModelRequest(parts=new_parts)
                )
            continue
        cleaned.append(msg)
    if dropped:
        logger.warning(
            "Dropped %d orphan tool-result part(s) with no matching ToolCallPart.", dropped
        )
    return cleaned


# ---------------------------------------------------------------------------
# Summarizer instructions: domain-agnostic default + override registry
# ---------------------------------------------------------------------------

#: Domain-agnostic default summarizer system prompt. Business-free by design.
_DEFAULT_SUMMARY_INSTRUCTIONS = """\
You are a conversation summarizer. Given a sequence of messages from a conversation
between a user and an AI assistant, produce a concise summary that preserves:

1. **Named entities** — people, organizations, products, and places. These MUST be
   preserved verbatim; they are frequently referenced later in the conversation.
2. **Key identifiers** — reference numbers, record/case IDs, and any other identifiers.
3. **The original request/question** — what the user initially asked about. Summarize the core
   question in full so the agent never needs to ask again.
4. **Key facts and decisions** made during the conversation — answers found, conclusions reached.
5. **Important context** — dates, amounts, specific data retrieved from tools.
6. **Tool calls and their outcomes** — what tools were called, what was found.
7. **Unanswered questions or pending items**

Rules:
- Be concise but do NOT omit any critical information
- Use bullet points for clarity
- Preserve specific numbers, names, and identifiers VERBATIM — never paraphrase a name or ID
- Start the summary with a "Key entities" section listing all named entities and identifiers
- This summary will replace the original messages in the agent's context window
- Do NOT include any preamble like "Here is the summary" — just output the summary
"""

#: Public, mutable registry: prompt-version id -> summarizer instructions text.
#: Keyed by ``CompactionConfig.summarizer_prompt_version`` so the serialized config
#: (echoed in every start event) carries only the small id, never the full prompt.
#: Override programmatically before any agent is built (open-extension precedent:
#: ``COMPACTION_STRATEGIES``) — replace ``"v1"`` in place, or register a new id and
#: point ``summarizer_prompt_version`` at it. An unknown id falls back to the default.
SUMMARY_INSTRUCTIONS: dict[str, str] = {"v1": _DEFAULT_SUMMARY_INSTRUCTIONS}


# ---------------------------------------------------------------------------
# Summary formatting helpers
# ---------------------------------------------------------------------------


def _format_request_part(part: Any) -> str | None:
    """Format a single request part for the summary. Returns None to skip."""
    if isinstance(part, SystemPromptPart):
        return None  # Already preserved separately
    if isinstance(part, UserPromptPart):
        content = part.content if isinstance(part.content, str) else str(part.content)
        return f"USER: {content}"
    if isinstance(part, ToolReturnPart):
        content = part.content if isinstance(part.content, str) else str(part.content)
        if len(content) > 3000:
            content = content[:3000] + "... [truncated]"
        return f"TOOL_RESULT ({part.tool_name}): {content}"
    if isinstance(part, RetryPromptPart):
        # ``tool_name`` set => tool-validation retry (role=tool); unset => output retry
        # (user message). Either way we keep the text so the agent can recover context.
        content = part.model_response() if hasattr(part, "model_response") else str(part.content)
        if len(content) > 3000:
            content = content[:3000] + "... [truncated]"
        label = part.tool_name or "output"
        return f"TOOL_RETRY ({label}): {content}"
    return f"REQUEST_PART: {_extract_text_from_part(part)}"


def _format_response_part(part: Any) -> str:
    """Format a single response part for the summary."""
    if isinstance(part, TextPart):
        return f"ASSISTANT: {part.content}"
    if isinstance(part, ToolCallPart):
        args_str = str(part.args) if part.args else ""
        if len(args_str) > 1000:
            args_str = args_str[:1000] + "... [truncated]"
        return f"TOOL_CALL ({part.tool_name}): {args_str}"
    return f"RESPONSE_PART: {_extract_text_from_part(part)}"


def _format_messages_for_summary(messages: list[ModelMessage]) -> str:
    """Render messages into a readable text block for the summarizer."""
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for req_part in msg.parts:
                line = _format_request_part(req_part)
                if line is not None:
                    lines.append(line)
        elif isinstance(msg, ModelResponse):
            for resp_part in msg.parts:
                lines.append(_format_response_part(resp_part))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Retained-context token estimate (ADR-010 §1: display-only, tokenizer-free)
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token, no ``tiktoken``).

    A display estimate for observability — never the auto-compaction trigger,
    which reads provider-reported usage.
    """
    return len(text) // 4


def _join_message_text(messages: list[ModelMessage]) -> str:
    """Concatenate best-effort text across every part of *messages*."""
    return "\n".join(_extract_text_from_part(part) for msg in messages for part in msg.parts)


def _estimate_retained(
    system: list[ModelMessage], summary: str, tail: list[ModelMessage]
) -> int:
    """Estimate the post-compaction context size: system parts + summary + retained tail."""
    return (
        _estimate_tokens(_join_message_text(system))
        + _estimate_tokens(summary)
        + _estimate_tokens(_join_message_text(tail))
    )


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------


class NoOpCompaction:
    """No-op strategy: never folds a message, never calls an LLM."""

    async def compact(self, messages: list[ModelMessage]) -> CompactionResult:
        return CompactionResult(summary="", replaced_message_count=0, tokens_after=None)


class SlidingWindowCompaction:
    """Deterministic head-drop: folds the summarizable middle with a marker, no LLM."""

    def __init__(self, keep_recent_messages: int) -> None:
        self._keep_recent = keep_recent_messages

    async def compact(self, messages: list[ModelMessage]) -> CompactionResult:
        system, middle, tail = _split_messages(messages, self._keep_recent)
        replaced = len(middle)
        summary = f"[Sliding window: dropped {replaced} earlier message(s)]" if replaced else ""
        tokens_after = _estimate_retained(system, summary, tail) if replaced else None
        return CompactionResult(
            summary=summary, replaced_message_count=replaced, tokens_after=tokens_after
        )


class SummarizingCompaction:
    """Summarize the middle via an awaited LLM.

    Falls back to a count-based truncation marker (never raises) when the summarizer
    errors. Built from ``model_cfg`` on the agent's shared httpx client; the summarizer
    is constructed lazily so construction needs no provider env (registry resolution).
    """

    def __init__(
        self,
        cfg: CompactionConfig,
        model_cfg: ModelConfig,
        http_client: AsyncClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._model_cfg = model_cfg
        self._http_client = http_client
        self._summarizer: Agent[None, str] | None = None

    def _build_summarizer(self) -> Agent[None, str]:
        """Build (and cache) the summarizer pydantic-ai Agent. Overridable in tests.

        Instructions are resolved from the ``SUMMARY_INSTRUCTIONS`` registry by the
        config's ``summarizer_prompt_version`` (unknown id → domain-agnostic default).
        """
        if self._summarizer is None:
            instructions = SUMMARY_INSTRUCTIONS.get(
                self._cfg.summarizer_prompt_version, _DEFAULT_SUMMARY_INSTRUCTIONS
            )
            self._summarizer = Agent(
                model=create_model(self._model_cfg, self._http_client),
                instructions=instructions,
                output_type=str,
            )
        return self._summarizer

    async def compact(self, messages: list[ModelMessage]) -> CompactionResult:
        system, middle, tail = _split_messages(messages, self._cfg.keep_recent_messages)
        if not middle:
            return CompactionResult(summary="", replaced_message_count=0, tokens_after=None)
        prompt = (
            f"Summarize the following conversation in at most "
            f"~{self._cfg.summary_target_tokens} tokens.\n\n"
            f"---\n{_format_messages_for_summary(middle)}\n---"
        )
        try:
            output = (await self._build_summarizer().run(prompt)).output
        except Exception:
            return self._truncation_fallback(system, middle, tail)
        return CompactionResult(
            summary=output,
            replaced_message_count=len(middle),
            tokens_after=_estimate_retained(system, output, tail),
        )

    def _truncation_fallback(
        self,
        system: list[ModelMessage],
        middle: list[ModelMessage],
        tail: list[ModelMessage],
    ) -> CompactionResult:
        """Count-based degrade-to-truncation (no tiktoken): fold the whole middle."""
        count = len(middle)
        summary = (
            f"[NOTE: {count} earlier conversation message(s) were "
            f"truncated to fit the context window.]"
        )
        return CompactionResult(
            summary=summary,
            replaced_message_count=count,
            tokens_after=_estimate_retained(system, summary, tail),
        )


# ---------------------------------------------------------------------------
# Registry + resolver
# ---------------------------------------------------------------------------

#: Public, mutable, call-time registry (open-extension precedent: SANDBOX_ACTOR_CLASSES).
#: Downstream code may register its own factory before any agent is built.
COMPACTION_STRATEGIES: dict[
    str, Callable[[CompactionConfig, ModelConfig, AsyncClient | None], CompactionStrategy]
] = {
    "none": lambda cfg, mc, hc: NoOpCompaction(),
    "sliding_window": lambda cfg, mc, hc: SlidingWindowCompaction(cfg.keep_recent_messages),
    "summarize": lambda cfg, mc, hc: SummarizingCompaction(cfg, mc, hc),
}


def create_compaction(
    cfg: CompactionConfig,
    model_cfg: ModelConfig,
    http_client: AsyncClient | None = None,
) -> CompactionStrategy:
    """Resolve ``cfg.strategy`` to a strategy instance.

    A registered id is resolved via ``COMPACTION_STRATEGIES``; otherwise a dotted FQCN
    is resolved via stdlib ``importlib`` (no akgentic sibling import); an unknown bare
    id raises ``ValueError`` listing the registered ids.
    """
    factory = COMPACTION_STRATEGIES.get(cfg.strategy)
    if factory is not None:
        return factory(cfg, model_cfg, http_client)
    if "." in cfg.strategy:
        # Self-contained FQCN escape hatch — stdlib only, imports NO akgentic sibling.
        from importlib import import_module

        module_path, _, cls_name = cfg.strategy.rpartition(".")
        strategy: CompactionStrategy = getattr(import_module(module_path), cls_name)(
            cfg, model_cfg, http_client
        )
        return strategy
    raise ValueError(
        f"Unknown compaction strategy {cfg.strategy!r}; "
        f"registered: {', '.join(COMPACTION_STRATEGIES)}"
    )
