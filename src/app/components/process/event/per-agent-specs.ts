import {
  AkgenticMessage,
  CommandDescriptor,
  CommandsAnnouncedEvent,
  EventMessage,
  isCommandsAnnouncedEvent,
  isEventMessage,
  isLlmContextClearedEvent,
  isLlmContextCompactedEvent,
  isLlmSystemPromptEvent,
  isLlmUsageEvent,
  isStateChangedMessage,
  LlmContextCompactedEvent,
  LlmUsageEvent,
  StateChangedMessage,
  SystemPromptPartSnapshot,
} from '../../../protocol/message.types';
import {
  PerAgentSpec,
  replaceWith,
} from './per-agent-store';

/**
 * `event/per-agent-specs.ts` — the declarative `PerAgentSpec` definitions
 * (Epic 18 / ADR-015 §2,§3) for the four per-agent stores
 * (`state` / `context` / `commands` / `systemPrompt`) plus the system-prompt
 * reducer surface they depend on. Extracting these out of `ingestion.service.ts`
 * (the inline state/context/commands helpers) and out of
 * `system-prompt.selector.ts` (the reducer surface) breaks the two
 * `event → selectors` import edges and removes the real
 * `ingestion ↔ system-prompt.selector` circular import: `ingestion` now sources
 * its specs from this `event/` sibling, and the `SystemPromptSelector` façade
 * reads `ingestion.systemPrompt` one-directionally (selectors → event).
 *
 * This module imports ONLY `./per-agent-store` (factories/types) and the
 * `../../../protocol/message.types` discriminators — NO `services/` / selectors,
 * NO `ui-state/`, NO `components/`. The reducer bodies, `match` predicates, and
 * default `sender.agent_id` keying (ADR-014 §2 ordering) are preserved
 * byte-for-byte from their prior homes; behavior is identical.
 */

// ===========================================================================
// system-prompt reducer surface (relocated from system-prompt.selector.ts)
// ===========================================================================

/**
 * One rendered row of the trace head system block. Mirrors the shape the
 * `AkgentChatComponent` already produces for `part_kind === 'system-prompt'`
 * parts (`akgent-chat.component.ts` label/mapping) so Story 16-2 can render it
 * with no shape change: `name` is the human label, `content` the rendered text.
 */
export interface SystemPromptRow {
  type: 'system';
  name: string;
  content: string;
}

// ---------------------------------------------------------------------------
// Module-scope pure helpers (ADR-005 §Decision 4; ADR-004 §5b).
//
// These are the SINGLE implementation of the system-prompt mapping + part
// extraction logic. They are shared by the `systemPromptReduce` per-message
// reducer (registered on the `systemPrompt` PerAgentStore in
// `IngestionService`, Epic 17 / ADR-014) — there is no longer a whole-log
// fold. Each maps to a fresh array; none mutates its input; none throws.
// ---------------------------------------------------------------------------

/**
 * Label for a system-prompt block — reuses the exact logic from
 * `akgent-chat.component.ts`: the trailing segment of the pydantic-ai
 * `dynamic_ref` (e.g. `team.roster` → `roster`), or `'System'` for static parts
 * (`dynamic_ref` null/empty). Defensive: never throws on null/undefined.
 */
export function systemPromptLabel(
  dynamic_ref: string | null | undefined,
): string {
  return dynamic_ref ? (dynamic_ref.split('.').pop() ?? 'System') : 'System';
}

/**
 * Map a list of rendered parts (either `SystemPromptPartSnapshot` from a
 * `LlmSystemPromptEvent`, or pydantic-ai `SystemPromptPart`s from a
 * `LlmMessageEvent` fallback — both expose `dynamic_ref` + `content`) to render
 * rows. Defensive reads only: a missing/empty `parts` yields `[]`, a part with
 * no `content` contributes an empty string rather than throwing. Always returns
 * a freshly-allocated array (OnPush safety, AC9).
 */
function mapPartsToRows(
  parts: ReadonlyArray<{ dynamic_ref?: string | null; content?: string }>
    | null
    | undefined,
): SystemPromptRow[] {
  return (parts ?? []).map((p) => ({
    type: 'system' as const,
    name: systemPromptLabel(p?.dynamic_ref),
    content: p?.content ?? '',
  }));
}

/**
 * Primary-path extraction (FR1): if `msg` is an
 * `EventMessage(LlmSystemPromptEvent)`, return its `parts` (possibly undefined
 * for a malformed event); otherwise `undefined`. Latest-wins is applied by the
 * reducer (the LAST such event replaces the value), so this only reads ONE
 * message's parts.
 */
function systemPromptEventParts(
  msg: AkgenticMessage,
): SystemPromptPartSnapshot[] | undefined {
  if (!isEventMessage(msg)) return undefined;
  const inner = (msg as EventMessage).event as
    | { __model__?: string; parts?: SystemPromptPartSnapshot[] }
    | undefined;
  if (!isLlmSystemPromptEvent(inner)) return undefined;
  return inner.parts;
}

/**
 * Fallback-path extraction (FR2): if `msg` is an
 * `EventMessage(LlmMessageEvent)` whose inner `message.parts` contain
 * `part_kind === 'system-prompt'` entries, return those system parts; otherwise
 * `undefined`. Mirrors the pre-event seeding read in `ingestion.service.ts`
 * (inner is the `ModelRequest`, whose `.parts` carry `part_kind`).
 */
function llmMessageSystemParts(
  msg: AkgenticMessage,
): Array<{ dynamic_ref?: string | null; content?: string }> | undefined {
  if (!isEventMessage(msg)) return undefined;
  const inner = (msg as EventMessage).event as
    | { __model__?: string; message?: { parts?: unknown[] } }
    | undefined;
  if (!inner?.__model__?.includes('LlmMessageEvent')) return undefined;
  const parts = (inner.message?.parts ?? []) as Array<{
    part_kind?: string;
    dynamic_ref?: string | null;
    content?: string;
  }>;
  const systemParts = parts.filter((p) => p?.part_kind === 'system-prompt');
  return systemParts.length > 0 ? systemParts : undefined;
}

/**
 * Per-agent reduced value for the `systemPrompt` store (Epic 17 / ADR-014). The
 * incremental reducer cannot be a stock factory because the precedence is
 * "latest primary OR first fallback" — it must remember whether a primary
 * (`LlmSystemPromptEvent`) has ever been seen for the agent so a later
 * `LlmMessageEvent` cannot clobber a primary, and a second `LlmMessageEvent`
 * cannot overwrite an earlier fallback.
 *
 * - `rows`       — the rendered head block (what the façade exposes).
 * - `hasPrimary` — true once any `LlmSystemPromptEvent` has been folded for the
 *                  agent (primary supersedes fallback from that point on).
 */
export interface SystemPromptValue {
  rows: SystemPromptRow[];
  hasPrimary: boolean;
}

/**
 * Incremental per-message reducer (the store's `(prev, msg) => next` contract,
 * ADR-014 §Decision 1 custom reducer) that reproduces EXACTLY the precedence of
 * the old whole-log fold:
 *
 *   1. Primary (latest-wins, FR1): a `LlmSystemPromptEvent` always replaces the
 *      value with its parts and marks `hasPrimary` — the LAST one wins.
 *   2. Fallback (FR2, ONLY while `!hasPrimary`): the FIRST `LlmMessageEvent`
 *      with system parts captures the value; once captured, later
 *      `LlmMessageEvent`s do NOT overwrite it (first-wins fallback). A
 *      `LlmMessageEvent` never overrides a primary.
 *   3. Anything else (unrelated inner, no system parts) → passthrough `prev`.
 *
 * Malformed/empty parts map to `[]` without throwing (defensive `mapPartsToRows`).
 * Returns a FRESH `rows` array on real change (OnPush safety).
 */
export function systemPromptReduce(
  prev: SystemPromptValue | undefined,
  msg: AkgenticMessage,
): SystemPromptValue | undefined {
  const primary = systemPromptEventParts(msg);
  if (primary !== undefined) {
    return { rows: mapPartsToRows(primary), hasPrimary: true };
  }
  // Primary already established → a message-event fallback never overrides it.
  if (prev?.hasPrimary) return prev;
  // Fallback already captured → first-wins; later message events do not replace.
  if (prev !== undefined) return prev;
  const fallback = llmMessageSystemParts(msg);
  if (fallback !== undefined) {
    return { rows: mapPartsToRows(fallback), hasPrimary: false };
  }
  return prev;
}

/**
 * `match` predicate for the `systemPrompt` spec: admit BOTH inner model types
 * so both reach `systemPromptReduce` (the reducer — not `match` — decides
 * primary-vs-fallback). Any `EventMessage` whose inner is a
 * `LlmSystemPromptEvent` OR a `LlmMessageEvent` is folded.
 */
export function systemPromptMatch(msg: AkgenticMessage): boolean {
  if (!isEventMessage(msg)) return false;
  return (
    systemPromptEventParts(msg) !== undefined ||
    llmMessageSystemParts(msg) !== undefined
  );
}

// ===========================================================================
// per-agent spec inputs (relocated from ingestion.service.ts)
// ===========================================================================

/**
 * Per-agent `state` value shape (Epic 17 / ADR-014 §5). Mirrors what the
 * deleted `stateDict$` produced for `AkgentStateComponent.generateForm`:
 * V2 sends an empty schema and the raw state is rendered as JSON.
 */
export interface AgentStateValue {
  schema: object;
  state: unknown;
}

/**
 * Inner-event reader for the `context` instance (Epic 17 / ADR-014 §5).
 * Mirrors the exact predicate the deleted `applyEventMessageDicts` /
 * replay loop used: an `EventMessage` whose inner `__model__` includes
 * `LlmMessageEvent` AND whose inner `message` is present. Returns the inner
 * `message` to append, or `undefined` when the guard does not hold.
 */
function innerLlmMessage(msg: AkgenticMessage): unknown {
  const inner = (msg as EventMessage).event;
  if (!inner?.__model__?.includes('LlmMessageEvent')) return undefined;
  return inner.message ?? undefined;
}

/**
 * Inner-event reader for the `commands` instance (Epic 17 / ADR-014 §5).
 * Mirrors `innerLlmMessage`: reads the inner `event` of an `EventMessage` and
 * returns it iff it passes `isCommandsAnnouncedEvent`, else `undefined`. Keeps
 * the `commands` spec's `match` and `reduce` reading the SAME inner payload.
 */
function innerCommandsEvent(
  msg: AkgenticMessage,
): CommandsAnnouncedEvent | undefined {
  const inner = (msg as EventMessage).event;
  return isCommandsAnnouncedEvent(inner) ? inner : undefined;
}

/**
 * Epic 17 (ADR-014 §5): per-agent latest `{ schema, state }` derived from
 * `StateChangedMessage`. Replaces the bespoke `stateDict$`. Default key
 * `sender.agent_id`; `schema` is an empty object literal exactly as before
 * (V2 sends an empty schema; raw state rendered as JSON). Read via
 * `state.forAgent(id)`.
 */
export const stateSpec: PerAgentSpec<AgentStateValue> = {
  name: 'state',
  match: isStateChangedMessage,
  reduce: replaceWith<AgentStateValue>((m) => ({
    schema: {},
    state: (m as StateChangedMessage).state,
  })),
};

/**
 * Marker prefix the backend's synthetic compaction summary carries on its single
 * `UserPromptPart` (`ContextManager.fold_compaction`, ADR-010 §4). The member
 * trace prepends the same prefix so `AkgentChatComponent` can label the
 * folded-in row as a summary rather than a plain user turn.
 */
export const CONVERSATION_SUMMARY_PREFIX = '[Conversation summary] ';

/**
 * True when a context entry is a "system" message — one whose `parts` include a
 * `part_kind === 'system-prompt'` part. Mirrors the backend `_is_system_message`
 * exemption (and `llmMessageSystemParts` above): system entries are NEVER folded
 * by a compaction, exactly like the sliding window. Defensive: a missing or
 * non-array `parts` is treated as non-system.
 */
function isContextSystemEntry(entry: unknown): boolean {
  const parts = (entry as { parts?: unknown } | null | undefined)?.parts;
  return (
    Array.isArray(parts) &&
    parts.some(
      (p) => (p as { part_kind?: string } | null)?.part_kind === 'system-prompt',
    )
  );
}

/**
 * Build the synthetic summary entry inserted at a compaction's fold point: a
 * `ModelRequest`-shaped object with one `user-prompt` part prefixed
 * `CONVERSATION_SUMMARY_PREFIX`, mirroring the backend synthetic `ModelRequest` /
 * `UserPromptPart`. The shape matches what `AkgentChatComponent.updateContext`
 * already renders for a user-prompt part.
 */
function buildSummaryEntry(summary: string): unknown {
  return {
    kind: 'request',
    parts: [
      {
        part_kind: 'user-prompt',
        content: `${CONVERSATION_SUMMARY_PREFIX}${summary}`,
      },
    ],
  };
}

/**
 * Fold `messages` per a compaction event, mirroring `ContextManager.fold_compaction`
 * (ADR-010 §4): drop the first `replaced_message_count` NON-system entries and
 * insert ONE synthetic summary entry at the fold point; system entries are never
 * dropped. Returns a FRESH array on a real fold (OnPush); a non-positive
 * `replaced_message_count` is a no-op (returns the input unchanged). The backend's
 * trailing `_drop_orphan_tool_results` (an OpenAI role=tool adjacency guard for
 * the NEXT provider request) is intentionally not mirrored — it does not affect
 * what the member trace displays.
 */
export function foldContextCompaction(
  messages: unknown[],
  event: LlmContextCompactedEvent,
): unknown[] {
  if (event.replaced_message_count <= 0) return messages;
  const summary = buildSummaryEntry(event.summary ?? '');
  const folded: unknown[] = [];
  let remaining = event.replaced_message_count;
  let inserted = false;
  for (const entry of messages) {
    if (isContextSystemEntry(entry)) {
      folded.push(entry);
      continue;
    }
    if (remaining > 0) {
      remaining -= 1;
      if (!inserted) {
        folded.push(summary);
        inserted = true;
      }
      continue;
    }
    folded.push(entry);
  }
  return folded;
}

/**
 * `match` for the `context` spec: admit an `EventMessage` whose inner is a
 * `LlmMessageEvent` (the append source) OR a `LlmContextCompactedEvent` (fold)
 * OR a `LlmContextClearedEvent` (reset). The reducer — not `match` — decides
 * which transition applies; anything else passes through.
 */
export function contextMatch(msg: AkgenticMessage): boolean {
  if (!isEventMessage(msg)) return false;
  const inner = (msg as EventMessage).event as
    | { __model__?: string }
    | undefined;
  return (
    innerLlmMessage(msg) !== undefined ||
    isLlmContextCompactedEvent(inner) ||
    isLlmContextClearedEvent(inner)
  );
}

/**
 * Incremental per-message reducer for the `context` store — the client-side
 * ordered fold of the same event log the backend folds (`ContextManager`):
 *   - `LlmMessageEvent`          → append the inner `message` (fresh array).
 *   - `LlmContextCompactedEvent` → `foldContextCompaction` (drop prefix + insert
 *                                  summary), mirroring `compact` (ADR-010 §4).
 *   - `LlmContextClearedEvent`   → reset to `[]`, mirroring `clear_context` (§8).
 *   - anything else              → passthrough `prev`.
 * The synthetic summary is derivable from the event (the backend emits no
 * `LlmMessageEvent` for it), so folding the event never double-applies (§4).
 */
export function contextReduce(
  prev: unknown[] | undefined,
  msg: AkgenticMessage,
): unknown[] | undefined {
  if (!isEventMessage(msg)) return prev;
  const inner = (msg as EventMessage).event as
    | { __model__?: string }
    | undefined;
  if (isLlmContextClearedEvent(inner)) return [];
  if (isLlmContextCompactedEvent(inner)) {
    return foldContextCompaction(prev ?? [], inner);
  }
  const message = innerLlmMessage(msg);
  if (message !== undefined) return [...(prev ?? []), message];
  return prev;
}

/**
 * Epic 17 (ADR-014 §5) + Epic 29 (ADR-010 §4/§8): per-agent ordered conversation
 * array. Appends each `LlmMessageEvent`'s inner `message`, FOLDS on
 * `LlmContextCompactedEvent`, and RESETS on `LlmContextClearedEvent` — so the
 * Member trace reflects the agent's actual post-compaction state, not the stale
 * pre-compaction history. Custom `contextMatch`/`contextReduce` (no stock factory
 * fits the three-way fold). Default key `sender.agent_id`; O(Δ)/frame; fresh
 * value on real change. Read via `context.forAgent(id)`.
 */
export const contextSpec: PerAgentSpec<unknown[]> = {
  name: 'context',
  match: contextMatch,
  reduce: contextReduce,
};

/**
 * Epic 17 (ADR-014 §5): per-agent slash-command store derived from
 * `CommandsAnnouncedEvent` riding the `EventMessage` passthrough. Replaces
 * the bespoke `commandsByAgent$`. Default key `sender.agent_id` (ADR-013
 * keying fix — the emitting agent is the outer sender, so
 * `sender.agent_id === inner.agent.agent_id`, ADR-014 §2), so a fired/re-hired
 * display-name reuse can never serve the wrong agent's commands. `replaceWith`
 * gives the same replace-on-re-announce semantics the backend relies on (the
 * full list is re-emitted on change). Read via `commands.forAgent(id)` /
 * `commands.snapshot(id)` by the `/` mention consumers.
 */
export const commandsSpec: PerAgentSpec<CommandDescriptor[]> = {
  name: 'commands',
  match: (m) =>
    isEventMessage(m) &&
    isCommandsAnnouncedEvent((m as EventMessage).event),
  reduce: replaceWith<CommandDescriptor[]>(
    (m) => innerCommandsEvent(m)?.commands ?? [],
  ),
};

/**
 * Epic 17 (ADR-014 §5): per-agent system-prompt head block derived from
 * `LlmSystemPromptEvent` (primary, latest-wins, FR1) with a first
 * `LlmMessageEvent` system-part fallback (FR2). Replaces the bespoke
 * `SystemPromptSelector` `log$` fold — the selector is now a thin façade that
 * delegates to `systemPrompt.forAgent(id)`. The reducer is a custom one
 * (`systemPromptReduce`) because the precedence is "latest primary OR first
 * fallback", not a stock factory; `match` (`systemPromptMatch`) admits BOTH
 * `LlmSystemPromptEvent` and `LlmMessageEvent` inners so both reach the
 * reducer. Default key `sender.agent_id`. Read via the façade or directly via
 * `systemPrompt.forAgent(id)` (value `{ rows, hasPrimary }`; the façade
 * projects `.rows`).
 */
export const systemPromptSpec: PerAgentSpec<SystemPromptValue> = {
  name: 'systemPrompt',
  match: systemPromptMatch,
  reduce: systemPromptReduce,
};

// ===========================================================================
// token-usage reducer surface (Epic 26 / ADR-022 §Decision 2-4)
// ===========================================================================

/**
 * Per-agent reduced token-usage value (Epic 26 / ADR-022 §Decision 2). Derived
 * by folding every `EventMessage(LlmUsageEvent)` for the agent (keyed by the
 * outer `sender.agent_id`, which IS the agent that ran the model). Terminology
 * (ADR-022 §Decision 4): `totalSent` Σ `input_tokens`, `totalReceived` Σ
 * `output_tokens`. `lastContextWindow` is the NEWEST event's `input_tokens`
 * (ADR-022 §Decision 3 — there is no context-window field on the wire, so the
 * most-recent prompt size is the proxy). `lastRunId` / `lastModelName` are labels
 * only. Cache / `requests` fields ride the wire but are excluded from v1.
 */
export interface AgentTokenUsage {
  /** input_tokens of the most-recent event (overwritten each event). */
  lastContextWindow: number;
  /** run_id of that most-recent event (label only). */
  lastRunId: string;
  /** model_name of that most-recent event (label only). */
  lastModelName: string;
  /** running Σ of input_tokens across all this agent's events. */
  totalSent: number;
  /** running Σ of output_tokens across all this agent's events. */
  totalReceived: number;
}

/** Read the inner `LlmUsageEvent` off an `EventMessage`, or `undefined`. */
function innerLlmUsage(msg: AkgenticMessage): LlmUsageEvent | undefined {
  if (!isEventMessage(msg)) return undefined;
  const inner = (msg as EventMessage).event;
  return isLlmUsageEvent(inner) ? inner : undefined;
}

/** Zero seed for an agent's token-usage value, used when a compaction/clear
 *  event re-points the context window before any `LlmUsageEvent` has been folded
 *  (ADR-022 §Decision 2). Only ever spread, never mutated — a shared constant is
 *  safe. */
const ZERO_TOKEN_USAGE: AgentTokenUsage = {
  lastContextWindow: 0,
  lastRunId: '',
  lastModelName: '',
  totalSent: 0,
  totalReceived: 0,
};

/**
 * Incremental per-message reducer (the store's `(prev, msg) => next` contract,
 * ADR-022 §Decision 2). NOT a stock factory:
 *   - `LlmUsageEvent` → accumulate (sum sent/received) AND overwrite (context
 *     window + labels); numeric reads coalesce a missing field to `0` (no NaN).
 *   - `LlmContextCompactedEvent` (Epic 29 / ADR-010 §4) → re-point
 *     `lastContextWindow` to `event.tokens_after` when it is a number; leave it
 *     unchanged when `tokens_after` is null/absent (defensive — no NaN, no reset).
 *   - `LlmContextClearedEvent` (§8) → reset `lastContextWindow` to `0`.
 * The two context events leave the cumulative I/O totals and run labels untouched
 * (neither is a model run) and seed from `prev ?? zero`. Every change returns a
 * FRESH object (OnPush safety); any other message passes `prev` through.
 */
export function tokenUsageReduce(
  prev: AgentTokenUsage | undefined,
  msg: AkgenticMessage,
): AgentTokenUsage | undefined {
  const ev = innerLlmUsage(msg);
  if (ev !== undefined) {
    const input = ev.input_tokens ?? 0;
    const output = ev.output_tokens ?? 0;
    return {
      lastContextWindow: input,
      lastRunId: ev.run_id,
      lastModelName: ev.model_name,
      totalSent: (prev?.totalSent ?? 0) + input,
      totalReceived: (prev?.totalReceived ?? 0) + output,
    };
  }
  if (!isEventMessage(msg)) return prev;
  const inner = (msg as EventMessage).event as
    | { __model__?: string }
    | undefined;
  if (isLlmContextCompactedEvent(inner)) {
    return typeof inner.tokens_after === 'number'
      ? { ...(prev ?? ZERO_TOKEN_USAGE), lastContextWindow: inner.tokens_after }
      : prev;
  }
  if (isLlmContextClearedEvent(inner)) {
    return { ...(prev ?? ZERO_TOKEN_USAGE), lastContextWindow: 0 };
  }
  return prev;
}

/**
 * `match` for the `tokenUsage` spec: admit an `EventMessage` whose inner is a
 * `LlmUsageEvent` (accumulate + overwrite) OR a `LlmContextCompactedEvent` /
 * `LlmContextClearedEvent` (Epic 29 — re-point the live context-window estimate).
 * The reducer decides the transition; any other message passes through.
 */
export function tokenUsageMatch(msg: AkgenticMessage): boolean {
  if (!isEventMessage(msg)) return false;
  const inner = (msg as EventMessage).event as
    | { __model__?: string }
    | undefined;
  return (
    isLlmUsageEvent(inner) ||
    isLlmContextCompactedEvent(inner) ||
    isLlmContextClearedEvent(inner)
  );
}

/**
 * Epic 26 (ADR-022 §Decision 2) + Epic 29 (ADR-010 §4/§8): per-agent token-usage
 * store derived from `LlmUsageEvent` (accumulate + overwrite) plus the two
 * context events (`tokenUsageMatch`), which re-point the live context-window
 * estimate after a `/compact` or `/clear`. Default key `sender.agent_id` (the
 * agent that ran the model). The custom `tokenUsageReduce` both accumulates and
 * overwrites, so no stock factory fits. Read via `tokenUsage.forAgent(id)` /
 * `tokenUsage.all$`; the team total is a pure derivation over `all$`
 * (TokenUsageSelector), never a separate aggregate.
 */
export const tokenUsageSpec: PerAgentSpec<AgentTokenUsage> = {
  name: 'tokenUsage',
  match: tokenUsageMatch,
  reduce: tokenUsageReduce,
};
