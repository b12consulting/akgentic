import { inject, Injectable } from '@angular/core';
import {
  BehaviorSubject,
  distinctUntilChanged,
  map,
  Observable,
  shareReplay,
} from 'rxjs';

import {
  buildPreview,
  ChatMessage,
  classifyMessage,
  ENTRY_POINT_NAME,
} from '../models/chat-message.model';
import {
  AkgenticMessage,
  EventMessage,
  isEventMessage,
  isProcessedMessage,
  isReceivedMessage,
  isSentMessage,
  ProcessedMessage,
  ReceivedMessage,
  SentMessage,
} from '../models/message.types';
import { MessageLogService } from './message-log.service';

const HUMAN_ROLE = 'Human';
const ACTOR_SYSTEM_ROLE = 'ActorSystem';

// [CUSTOM] Chat body and chat answer interfaces should be customized according to your API.
export interface ChatBody {
  message: string;
}

export interface ChatAnswer {
  messages: {
    kwargs: {
      content: string;
    };
  }[];
}

/**
 * One entry in a thinking bubble's tool list.
 *
 * Populated from ToolCallEvent (`done: false`) and flipped to `done: true`
 * by the matching ToolReturnEvent (same `tool_call_id`).
 */
export interface ThinkingToolEntry {
  tool_call_id: string;
  tool_name: string;
  arguments_preview: string;
  done: boolean;
}

/**
 * Per-agent thinking-bubble state (Story 4-8 / ADR-002 Decision 10).
 *
 * Story 6.3 (FR7) reshaped the lifecycle as a pure fold over `log$`:
 *   ReceivedMessage             → appended (tools: [], final: false)
 *   EventMessage+ToolCallEvent  → pushes an entry into tools
 *   EventMessage+ToolReturnEvent → flips entry.done to true
 *   SentMessage                 → if tools empty: removed; else final = true
 *   ProcessedMessage            → if tools empty: removed; else final = true
 */
export interface ThinkingState {
  /** Stable UUID of the receiving agent actor (from ReceivedMessage.sender). */
  agent_id: string;
  /** Display name — header label source (e.g. "@Researcher"). */
  agent_name: string;
  /** Timestamp of the triggering ReceivedMessage — chronological anchor. */
  start_time: Date;
  /** Tool calls observed while the agent was "thinking". */
  tools: ThinkingToolEntry[];
  /** Inner BaseMessage id of the triggering ReceivedMessage — stable trackBy key
   *  across ephemeral → persistent transitions. */
  anchor_message_id: string;
  /** false while animation is active; true once finalised as chat history. */
  final: boolean;
}

/**
 * Story 6.3 (FR7) — pure chat state. `messages` holds the classified
 * `ChatMessage` list; `thinkingAgents` tracks the per-agent thinking-bubble
 * lifecycle. Both slices preserve reference equality across no-op transitions
 * (AC7).
 */
export interface ChatState {
  messages: ChatMessage[];
  thinkingAgents: ThinkingState[];
}

export const EMPTY_CHAT: ChatState = { messages: [], thinkingAgents: [] };

/**
 * Compute pending notification state from classified chat messages (per-message).
 *
 * Scans all messages in order:
 *   - Rule 3 messages (recipient.role === 'Human' and recipient.name !== @Human)
 *     add their `message_id` (inner `BaseMessage.id`) to the unanswered set.
 *   - Any message whose `parent_id` is non-null removes that parent id from
 *     the unanswered set (a reply clears only the specific message it
 *     answers — identified by `parent_id === original.message_id`).
 *
 * Pure: no side effects, no DOM, no service calls. Deterministic.
 */
export function computePendingNotifications(
  messages: ChatMessage[],
): Set<string> {
  const unanswered = new Set<string>();

  for (const msg of messages) {
    if (
      msg.recipient.role === HUMAN_ROLE &&
      msg.recipient.name !== ENTRY_POINT_NAME
    ) {
      unanswered.add(msg.message_id);
    }
    if (msg.parent_id !== null) {
      unanswered.delete(msg.parent_id);
    }
  }

  return unanswered;
}

// ---------------------------------------------------------------------------
// Module-scope pure helpers (Task 3.2–3.5). Thinking-bubble lifecycle +
// message classification. Each helper preserves reference equality on
// unchanged slices (AC7); each is synchronous and deterministic given `log`.
// ---------------------------------------------------------------------------

/**
 * Convert a `SentMessage` envelope into a displayable `ChatMessage` — or
 * return `null` for messages that should not appear in the chat panel. Mirrors
 * the classification previously performed by `ChatPanelComponent.ngOnInit`.
 */
function messageFromSent(msg: SentMessage): ChatMessage | null {
  if (msg.sender.role === ACTOR_SYSTEM_ROLE) return null;
  if (msg.message.content == null || msg.message.content === '') return null;
  return classifyMessage(msg);
}

function applyReceivedToThinking(
  state: ChatState,
  msg: ReceivedMessage,
): ChatState {
  // HumanProxy agents wait for user input, not "thinking". Skip bubble.
  if (msg.sender.role === HUMAN_ROLE) return state;
  const hasNonFinal = state.thinkingAgents.some(
    (s) => s.agent_id === msg.sender.agent_id && !s.final,
  );
  if (hasNonFinal) return state;
  const next: ThinkingState = {
    agent_id: msg.sender.agent_id,
    agent_name: msg.sender.name,
    start_time: new Date(msg.timestamp),
    anchor_message_id: msg.message_id,
    tools: [],
    final: false,
  };
  return {
    ...state,
    thinkingAgents: [...state.thinkingAgents, next],
  };
}

function applyToolCallToThinking(
  state: ChatState,
  msg: EventMessage,
): ChatState {
  const agentId = msg.sender?.agent_id;
  const inner = msg.event;
  if (!agentId || !inner) return state;
  const idx = state.thinkingAgents.findIndex(
    (s) => s.agent_id === agentId && !s.final,
  );
  if (idx === -1) {
    console.debug(
      `[ChatService.applyToolCallToThinking] no active thinking state for ${agentId}`,
    );
    return state;
  }
  const existing = state.thinkingAgents[idx];
  const entry: ThinkingToolEntry = {
    tool_call_id: inner.tool_call_id,
    tool_name: inner.tool_name,
    arguments_preview: buildPreview(inner.arguments, 60),
    done: false,
  };
  const updated: ThinkingState = {
    ...existing,
    tools: [...existing.tools, entry],
  };
  const nextThinking = [...state.thinkingAgents];
  nextThinking[idx] = updated;
  return { ...state, thinkingAgents: nextThinking };
}

function applyToolReturnToThinking(
  state: ChatState,
  msg: EventMessage,
): ChatState {
  const agentId = msg.sender?.agent_id;
  const inner = msg.event;
  if (!agentId || !inner) return state;
  const toolCallId: string = inner.tool_call_id;
  const idx = state.thinkingAgents.findIndex(
    (s) => s.agent_id === agentId && !s.final,
  );
  if (idx === -1) return state;
  const existing = state.thinkingAgents[idx];
  const toolIdx = existing.tools.findIndex((t) => t.tool_call_id === toolCallId);
  if (toolIdx === -1) return state;
  const updatedTools = [...existing.tools];
  updatedTools[toolIdx] = { ...updatedTools[toolIdx], done: true };
  const updated: ThinkingState = { ...existing, tools: updatedTools };
  const nextThinking = [...state.thinkingAgents];
  nextThinking[idx] = updated;
  return { ...state, thinkingAgents: nextThinking };
}

function applySentToThinking(state: ChatState, msg: SentMessage): ChatState {
  if (msg.sender.role === ACTOR_SYSTEM_ROLE) return state;
  return finaliseThinking(state, msg.sender.agent_id);
}

function applyProcessedToThinking(
  state: ChatState,
  msg: ProcessedMessage,
): ChatState {
  return finaliseThinking(state, msg.sender.agent_id);
}

/**
 * Shared finalisation logic for thinking bubbles.
 * If the bubble has no tools: remove it. Otherwise: set `final: true`.
 */
function finaliseThinking(state: ChatState, agentId: string): ChatState {
  const idx = state.thinkingAgents.findIndex(
    (s) => s.agent_id === agentId && !s.final,
  );
  if (idx === -1) return state;
  const existing = state.thinkingAgents[idx];
  if (existing.tools.length === 0) {
    const nextThinking = [
      ...state.thinkingAgents.slice(0, idx),
      ...state.thinkingAgents.slice(idx + 1),
    ];
    return { ...state, thinkingAgents: nextThinking };
  }
  const updated: ThinkingState = { ...existing, final: true };
  const nextThinking = [...state.thinkingAgents];
  nextThinking[idx] = updated;
  return { ...state, thinkingAgents: nextThinking };
}

function applyMessageFromSent(state: ChatState, msg: SentMessage): ChatState {
  const chatMsg = messageFromSent(msg);
  if (chatMsg === null) return state;
  return { ...state, messages: [...state.messages, chatMsg] };
}

/** Pure per-message transition (Task 3.4). Returns `state` unchanged for
 *  unhandled discriminants (FR11 passthrough — AC6). */
export function chatStep(state: ChatState, msg: AkgenticMessage): ChatState {
  if (!msg?.__model__) return state;
  if (isSentMessage(msg)) {
    const afterMsg = applyMessageFromSent(state, msg);
    return applySentToThinking(afterMsg, msg);
  }
  if (isProcessedMessage(msg)) {
    return applyProcessedToThinking(state, msg);
  }
  if (isReceivedMessage(msg)) {
    return applyReceivedToThinking(state, msg);
  }
  if (isEventMessage(msg)) {
    const inner = (msg as EventMessage).event;
    const kind: string | undefined = inner?.__model__;
    if (kind?.includes('ToolCallEvent')) {
      return applyToolCallToThinking(state, msg as EventMessage);
    }
    if (kind?.includes('ToolReturnEvent')) {
      return applyToolReturnToThinking(state, msg as EventMessage);
    }
  }
  return state;
}

/**
 * Pure fold over the full log (Task 3.5). `chatFold` is fully pure per
 * Task 3.6 purity assessment: no timers, no `Date.now()`, no DOM, no
 * out-of-order retroactive corrections. `new Date(msg.timestamp)` is
 * deterministic given the log.
 */
export function chatFold(log: AkgenticMessage[]): ChatState {
  return log.reduce(chatStep, EMPTY_CHAT);
}

/**
 * ChatService — Story 6.3 (ADR-005 §Decision 4).
 *
 * Exposes `chat$` as a pure selector over `MessageLogService.log$`. The
 * legacy observables `messages$` / `thinkingAgents$` / `pendingNotifications$`
 * are re-derived as sliced projections for downstream compatibility. The four
 * imperative mutators (`beginThinking`, `appendToolCall`, `markToolDone`,
 * `finaliseOrDiscard`) are deleted — the fold owns the lifecycle from this
 * story forward (FR7).
 *
 * `loadingProcess$` stays imperative (spinner state driven by
 * `ActorMessageService`, analogous to AC10 for `GraphDataService.isLoading$`).
 */
@Injectable()
export class ChatService {
  private readonly log: MessageLogService = inject(MessageLogService);

  readonly chat$: Observable<ChatState> = this.log.log$.pipe(
    map(chatFold),
    shareReplay(1),
  );

  readonly messages$: Observable<ChatMessage[]> = this.chat$.pipe(
    map((s) => s.messages),
    distinctUntilChanged(),
  );

  readonly thinkingAgents$: Observable<ThinkingState[]> = this.chat$.pipe(
    map((s) => s.thinkingAgents),
    distinctUntilChanged(),
  );

  /** Reactive set of unanswered Rule 3 message ids (per-message tracking).
   *  Pipes off the derived `messages$` — API-compatible with the pre-refactor
   *  contract (same `Observable<Set<string>>` shape). */
  readonly pendingNotifications$: Observable<Set<string>> = this.messages$.pipe(
    map(computePendingNotifications),
  );

  /**
   * Intentionally imperative (analogous to AC10 for
   * `GraphDataService.isLoading$`): reflects spinner state driven by
   * `ActorMessageService`, not message-log state.
   */
  loadingProcess$: BehaviorSubject<boolean> = new BehaviorSubject<boolean>(
    false,
  );
}
