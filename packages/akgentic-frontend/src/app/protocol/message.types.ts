/**
 * TypeScript definitions for Akgentic message types
 * Based on orchestrator.py and message.py Python classes
 */

export interface ActorAddress {
  __actor_address__: true;
  /** Fully qualified class name for deserialization (Python
   *  `ActorAddressDict.__actor_type__`). */
  __actor_type__?: string;
  agent_id: string;
  name: string;
  role: string;
  team_id?: string;
  squad_id: string;
  user_message: boolean;
}

/**
 * Lightweight projection of a backend `ToolCard` as it arrives on the wire
 * inside `StartMessage.config.tools` (Epic 23 / ADR-019). The config is
 * serialised in full (`msg.model_dump(mode="json")`, no projection), so each
 * tool carries at least its recursive `__model__` discriminator (e.g.
 * `"akgentic.tool.workspace.tool.WorkspaceTool"`) and, for a `WorkspaceTool`,
 * an optional `workspace_id`. We only type the fields the registry fold reads;
 * every other tool field is intentionally ignored.
 */
export interface ToolCardLite {
  __model__: string;
  workspace_id?: string | null;
}

export interface BaseConfig {
  name: string;
  role: string;
  user_id: string;
  user_email: string;
  squad_id: string;
  orchestrator: ActorAddress;
  /** Tools bound to this agent, serialised in full on the start config
   *  (Epic 23 / ADR-019). Optional: older payloads / agents without tools
   *  omit it. The WorkspaceRegistry fold reads `WorkspaceTool` entries here. */
  tools?: ToolCardLite[];
}

export interface BaseState {
  [key: string]: any;
}

export interface BaseMessage {
  id: string;
  parent_id: string | null;
  team_id: string;
  timestamp: string;
  sender: ActorAddress;
  display_type: 'human' | 'ai' | 'other';
  content: string | null;
  __model__: string;
}

// Core message types from orchestrator.py

export interface SentMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.SentMessage';
  message: BaseMessage;
  recipient: ActorAddress;
}

export interface ReceivedMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.ReceivedMessage';
  /** UUID of the inner message being received. Python class only carries
   *  the id (lightweight telemetry) — the full inner message is NOT
   *  serialised into this envelope. See
   *  `akgentic.core.messages.orchestrator.ReceivedMessage`. */
  message_id: string;
}

export interface ProcessedMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.ProcessedMessage';
  message_id: string;
}

export interface StartMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.StartMessage';
  config: BaseConfig;
  parent: ActorAddress | null;
}

export interface StopMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.StopMessage';
}

export interface ErrorMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.ErrorMessage';
  exception_type: string;
  exception_value: string;
  current_message?: BaseMessage | null;
}

export interface StateChangedMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.StateChangedMessage';
  state: BaseState | Record<string, any>;
}

export interface EventMessage extends BaseMessage {
  __model__: string; // contains 'EventMessage'
  event: any;
}

// Additional message types that might be used

export interface UserMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.UserMessage';
  content: string;
}

export interface ResultMessage extends BaseMessage {
  __model__: 'akgentic.core.messages.orchestrator.ResultMessage';
  content: string;
}

/**
 * Synthetic team startup greeting announced by the orchestrator on the team
 * event stream (akgentic-team ADR-17). On the chat path it only ever appears
 * as the inner `SentMessage.message` payload — see `isWelcomeAnnouncement`.
 * See ADR-011 (Welcome Message Chat Rendering) Decision 1.
 */
export interface WelcomeMessage extends BaseMessage {
  __model__: 'akgentic.team.messages.WelcomeMessage';
  content: string;
}

/**
 * One argument of a slash-command, mirroring akgentic-tool `CommandArg`
 * (ADR-028 §Decision 3). Ordered position in `CommandDescriptor.args` drives
 * both the backend positional parsing and the frontend args hint.
 */
export interface CommandArg {
  name: string;
  /** JSON-schema type name: "string", "integer", "boolean", … */
  type: string;
  required: boolean;
  description?: string | null;
}

/**
 * Metadata for one slash-command supported by an agent, mirroring
 * akgentic-tool `CommandDescriptor` (ADR-028 §Decision 3). Sourced from
 * `CommandsAnnouncedEvent` and rendered in the `/` mention dropdown.
 */
export interface CommandDescriptor {
  /** Canonical command name, e.g. "hire_member". */
  name: string;
  /** Human-readable description (from the callable docstring). */
  description: string;
  /** Ordered argument list — drives the dropdown args hint. */
  args: CommandArg[];
  /** Provenance, e.g. "TeamTool". */
  tool_card: string;
}

/**
 * Inner event payload announcing the full command set for one agent
 * (akgentic-tool `CommandsAnnouncedEvent`, ADR-028 §Decision 3). It rides the
 * existing `EventMessage` passthrough; the frontend discriminates it by the
 * inner `__model__` (ADR-013). A later event for the same agent replaces the
 * previous set.
 */
export interface CommandsAnnouncedEvent {
  __model__: string; // contains 'CommandsAnnouncedEvent'
  /** The agent that executes these commands. */
  agent: ActorAddress;
  commands: CommandDescriptor[];
}

/**
 * Immutable snapshot of one rendered system-prompt part, mirroring the
 * akgentic-llm `SystemPromptPartSnapshot` frozen dataclass (ADR-004 §Decision 1).
 * `dynamic_ref` is the pydantic-ai dynamic-prompt function name (e.g.
 * `current_date`) or `null` for static parts; `content` is the rendered text
 * actually sent to the model. `__model__` is the serializer-injected tag
 * (`akgentic.llm.event.SystemPromptPartSnapshot`) and is optional on read.
 */
export interface SystemPromptPartSnapshot {
  __model__?: string;
  dynamic_ref: string | null;
  content: string;
}

/**
 * Inner event payload announcing the effective system-prompt rendering for one
 * run, mirroring the akgentic-llm `LlmSystemPromptEvent` frozen dataclass
 * (ADR-004 §Decision 1). It rides the standard `EventMessage` envelope (outer
 * `sender.agent_id` identifies the agent) and is discriminated frontend-side by
 * the inner `__model__` — exactly like `LlmMessageEvent` / `ToolStateEvent` /
 * `CommandsAnnouncedEvent`. A later event for the same agent supersedes the
 * previous rendering (latest-wins). See ADR-004 §5a for the wire JSON.
 */
export interface LlmSystemPromptEvent {
  __model__: string; // contains 'LlmSystemPromptEvent'
  run_id: string;
  content_hash: string;
  parts: SystemPromptPartSnapshot[];
}

/**
 * Inner event payload announcing per-`ModelResponse` token usage for one run,
 * mirroring the akgentic-llm `LlmUsageEvent` frozen dataclass (ADR-022 §Decision
 * 1). It rides the standard `EventMessage` envelope (outer `sender.agent_id`
 * identifies the agent that ran the model) and is discriminated frontend-side by
 * the inner `__model__` — exactly like `LlmSystemPromptEvent` / `LlmMessageEvent`
 * / `CommandsAnnouncedEvent`. The serializer tags the dataclass with the
 * fully-qualified `__model__` and preserves integer token counts (no
 * stringification). Terminology (ADR-022 §Decision 4): `input_tokens` is "sent",
 * `output_tokens` is "received". `cache_read_tokens` / `cache_write_tokens` fold
 * into the true context-window figure (ADR-024 §Decision 2); `requests` still
 * rides the wire unused.
 */
export interface LlmUsageEvent {
  __model__: string; // contains 'LlmUsageEvent'
  run_id: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  requests: number;
}

/**
 * Inner event payload announcing that a summary replaced a leading prefix of an
 * agent's conversation, mirroring the akgentic-llm `LlmContextCompactedEvent`
 * frozen dataclass (ADR-010 §3). It rides the standard `EventMessage` envelope
 * (outer `sender.agent_id` identifies the agent that compacted) and is
 * discriminated frontend-side by the inner `__model__` — exactly like
 * `LlmUsageEvent` / `LlmSystemPromptEvent`. The serializer tags the dataclass
 * with the fully-qualified `__model__` and preserves the primitive field values
 * (integer counts, string summary — no `repr` stringification). `run_id` is
 * `null` for a manual `/compact` between runs; `tokens_before` / `tokens_after`
 * are observability-only and may be `null`.
 */
export interface LlmContextCompactedEvent {
  __model__: string; // contains 'LlmContextCompactedEvent'
  run_id: string | null;
  strategy_id: string;
  summary: string;
  replaced_message_count: number;
  summarizer_prompt_version: string;
  tokens_before: number | null;
  tokens_after: number | null;
}

/**
 * Inner event payload announcing that an agent's conversation was wiped to empty
 * (system prompt re-injects on the next run), mirroring the akgentic-llm
 * `LlmContextClearedEvent` frozen dataclass (ADR-010 §8). Same native
 * `EventMessage` passthrough + inner `__model__` discrimination as
 * `LlmContextCompactedEvent`. `run_id` is `null` for a manual `/clear` between
 * runs; `cleared_message_count` is the number of messages removed.
 */
export interface LlmContextClearedEvent {
  __model__: string; // contains 'LlmContextClearedEvent'
  run_id: string | null;
  cleared_message_count: number;
}

// Union type for all possible messages
export type AkgenticMessage =
  | SentMessage
  | ReceivedMessage
  | ProcessedMessage
  | StartMessage
  | StopMessage
  | ErrorMessage
  | StateChangedMessage
  | EventMessage
  | UserMessage
  | ResultMessage;

// Type guards for message discrimination

export function isSentMessage(msg: BaseMessage): msg is SentMessage {
  return msg.__model__.includes('SentMessage');
}

export function isReceivedMessage(msg: BaseMessage): msg is ReceivedMessage {
  return msg.__model__.includes('ReceivedMessage');
}

export function isProcessedMessage(msg: BaseMessage): msg is ProcessedMessage {
  return msg.__model__.includes('ProcessedMessage');
}

export function isStartMessage(msg: BaseMessage): msg is StartMessage {
  return msg.__model__.includes('StartMessage');
}

export function isStopMessage(msg: BaseMessage): msg is StopMessage {
  return msg.__model__.includes('StopMessage');
}

export function isErrorMessage(msg: BaseMessage): msg is ErrorMessage {
  return msg.__model__.includes('ErrorMessage');
}

export function isStateChangedMessage(
  msg: BaseMessage,
): msg is StateChangedMessage {
  return msg.__model__.includes('StateChangedMessage');
}

export function isEventMessage(msg: BaseMessage): msg is EventMessage {
  return msg.__model__.includes('EventMessage');
}

export function isUserMessage(msg: BaseMessage): msg is UserMessage {
  return msg.__model__.includes('UserMessage');
}

export function isResultMessage(msg: BaseMessage): msg is ResultMessage {
  return msg.__model__.includes('ResultMessage');
}

/**
 * ToolCard discriminator check (Epic 23 / ADR-019): true when `t` is a
 * `WorkspaceTool`. Matches on the recursive `__model__` *ending in*
 * `WorkspaceTool` (so `"akgentic.tool.workspace.tool.WorkspaceTool"` matches),
 * deliberately stricter than the `.includes()` used by the message guards: a
 * `__model__` that merely contains `WorkspaceTool` mid-string (or a different
 * tool such as `...KnowledgeGraphTool`, or the empty string) is rejected.
 */
export function isWorkspaceTool(t: ToolCardLite): t is ToolCardLite {
  return t.__model__.endsWith('WorkspaceTool');
}

/**
 * Inner-payload check: true when the message itself is a `WelcomeMessage`.
 * Consistent with the other `is*` guards — matches on `__model__`.
 */
export function isWelcomeMessage(msg: BaseMessage): msg is WelcomeMessage {
  return msg.__model__.includes('WelcomeMessage');
}

/**
 * Inner-event check (ADR-013): true when the inner event carried by an
 * `EventMessage` is a `CommandsAnnouncedEvent`. Matches on the inner
 * `__model__`, the same discrimination already used for `LlmMessageEvent` /
 * `ToolCallEvent` / `ToolStateEvent`. `event` is the `EventMessage.event`
 * payload (loosely typed on the wire); the guard narrows it to
 * `CommandsAnnouncedEvent`.
 */
export function isCommandsAnnouncedEvent(
  event: { __model__?: string } | null | undefined,
): event is CommandsAnnouncedEvent {
  return !!event?.__model__?.includes('CommandsAnnouncedEvent');
}

/**
 * Inner-event check (ADR-004 §5a): true when the inner event carried by an
 * `EventMessage` is a `LlmSystemPromptEvent`. Matches on the inner `__model__`,
 * the same discrimination used for `LlmMessageEvent` / `ToolStateEvent` /
 * `CommandsAnnouncedEvent`. `event` is the `EventMessage.event` payload (loosely
 * typed on the wire); the guard narrows it to `LlmSystemPromptEvent`.
 */
export function isLlmSystemPromptEvent(
  event: { __model__?: string } | null | undefined,
): event is LlmSystemPromptEvent {
  return !!event?.__model__?.includes('LlmSystemPromptEvent');
}

/**
 * Inner-event check (ADR-022 §Decision 1): true when the inner event carried by
 * an `EventMessage` is a `LlmUsageEvent`. Matches on the inner `__model__`, the
 * same discrimination used for `LlmSystemPromptEvent` / `LlmMessageEvent` /
 * `CommandsAnnouncedEvent`. `event` is the `EventMessage.event` payload (loosely
 * typed on the wire); the guard narrows it to `LlmUsageEvent`.
 */
export function isLlmUsageEvent(
  event: { __model__?: string } | null | undefined,
): event is LlmUsageEvent {
  return !!event?.__model__?.includes('LlmUsageEvent');
}

/**
 * Inner-event check (ADR-010 §3): true when the inner event carried by an
 * `EventMessage` is a `LlmContextCompactedEvent`. Matches on the inner
 * `__model__`, the same discrimination used for `LlmUsageEvent` /
 * `LlmSystemPromptEvent`. Mutually exclusive with every other `Llm*Event` guard:
 * `'LlmContextCompactedEvent'` neither contains nor is contained by
 * `'LlmContextClearedEvent'`, `'LlmUsageEvent'`, `'LlmSystemPromptEvent'`, or
 * `'LlmMessageEvent'` (no substring collision in either direction).
 */
export function isLlmContextCompactedEvent(
  event: { __model__?: string } | null | undefined,
): event is LlmContextCompactedEvent {
  return !!event?.__model__?.includes('LlmContextCompactedEvent');
}

/**
 * Inner-event check (ADR-010 §8): true when the inner event carried by an
 * `EventMessage` is a `LlmContextClearedEvent`. Matches on the inner
 * `__model__`; mutually exclusive with `isLlmContextCompactedEvent` and the
 * other `Llm*Event` guards (no substring collision in either direction).
 */
export function isLlmContextClearedEvent(
  event: { __model__?: string } | null | undefined,
): event is LlmContextClearedEvent {
  return !!event?.__model__?.includes('LlmContextClearedEvent');
}

/**
 * Inner-event check: true when the inner event carried by an `EventMessage` is a
 * `LlmMessageEvent`. Named here for symmetry with `isLlmUsageEvent` /
 * `isLlmSystemPromptEvent` (ADR-022 §Decision 1, Open Question 1) so the
 * mutual-exclusion regression reads cleanly across the three `Llm*Event` guards.
 * The `per-agent-specs.ts` fold helpers keep their existing inline
 * `__model__.includes('LlmMessageEvent')` checks; this guard is additive.
 */
export function isLlmMessageEvent(
  event: { __model__?: string } | null | undefined,
): boolean {
  return !!event?.__model__?.includes('LlmMessageEvent');
}

/**
 * Envelope check (ADR-011 Decision 1): true only when `msg` is a `SentMessage`
 * whose inner `message` is a `WelcomeMessage` AND that inner payload carries
 * `display_type === 'other'`. Both signals are required (belt-and-suspenders):
 * `__model__` is the precise type identity, `display_type === 'other'`
 * confirms the render category.
 */
export function isWelcomeAnnouncement(msg: BaseMessage): boolean {
  if (!isSentMessage(msg)) return false;
  const inner = (msg as SentMessage).message;
  return (
    !!inner && isWelcomeMessage(inner) && inner.display_type === 'other'
  );
}
