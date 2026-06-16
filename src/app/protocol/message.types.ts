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

export interface BaseConfig {
  name: string;
  role: string;
  user_id: string;
  user_email: string;
  squad_id: string;
  team_id: string;
  parent?: ActorAddress;
  orchestrator: ActorAddress;
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
