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
