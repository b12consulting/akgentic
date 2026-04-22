import { ActorAddress, SentMessage } from './message.types';
import { makeAgentNameUserFriendly } from '../lib/util';

export const ENTRY_POINT_NAME = '@Human';

export type MessageRule = 1 | 2 | 3 | 4;

export interface ChatMessage {
  /** Outer `SentMessage` envelope id — used to route human replies back to
   *  the backend (backend's `_find_message` looks up by this id). */
  id: string;
  /** Inner `BaseMessage.id` — the id that `parent_id` references. Used for
   *  notification-clearing / reply linkage. Mirrors the graph-data.service
   *  contract (`m.message.id` ↔ reply `m.message.parent_id`). */
  message_id: string;
  /** Inner `BaseMessage.parent_id` — points to the replied-to message's
   *  `message_id` (NOT its outer envelope `id`). */
  parent_id: string | null;
  content: string;
  sender: ActorAddress;
  recipient: ActorAddress;
  timestamp: Date;
  rule: MessageRule;
  alignment: 'left' | 'right';
  color: string;
  collapsed: boolean;
  label: string;
}

/**
 * Classify a SentMessage into one of 4 rules (first-match wins):
 *   Rule 1: sender is @Human -> right-aligned, persona color
 *   Rule 2: recipient is @Human -> left-aligned, blue
 *   Rule 3: recipient role is Human but not @Human -> left-aligned, blue, notification
 *   Rule 4: everything else (AI-to-AI) -> left-aligned, blue, collapsed
 */
export function classifyRule(msg: SentMessage): MessageRule {
  if (msg.sender.name === ENTRY_POINT_NAME) return 1;
  if (msg.recipient.name === ENTRY_POINT_NAME) return 2;
  if (msg.recipient.role === 'Human' && msg.recipient.name !== ENTRY_POINT_NAME) return 3;
  return 4;
}

/**
 * Build a display label per ADR-002 Decision 6 (revised 2026-04-12):
 *   Rule 1: "You ⇒ @{recipient}" (using makeAgentNameUserFriendly)
 *   Rule 2: "@{sender} ⇒ You" (recipient expanded explicitly for multi-human teams)
 *   Rule 3: "@{sender} ⇒ @{recipient}"
 *   Rule 4: "@{sender} ⇒ @{recipient}"
 */
export function buildLabel(msg: SentMessage, rule: MessageRule): string {
  const senderName = makeAgentNameUserFriendly(msg.sender.name);
  const recipientName = makeAgentNameUserFriendly(msg.recipient.name);

  switch (rule) {
    case 1:
      return `You ⇒ ${recipientName}`;
    case 2:
      return `${senderName} ⇒ You`;
    case 3:
    case 4:
      return `${senderName} ⇒ ${recipientName}`;
  }
}

const RULE_COLORS: Record<MessageRule, string> = {
  1: '#efeeee',
  2: '#9ebbcb',
  3: '#9ebbcb',
  4: '#9ebbcb',
};

/**
 * Build a single-line, markdown-stripped, ellipsised preview of message content
 * for display in the collapsed chat line (per ADR-002 Decision 5).
 *
 * Rules (in order):
 *   1. null/undefined/empty -> ""
 *   2. Strip markdown: fenced code, inline code, images, links, bold/italic,
 *      heading markers, blockquote markers, list markers
 *   3. Collapse whitespace runs to single space
 *   4. Trim
 *   5. If length > maxLength, truncate to maxLength chars and append literal "..."
 *
 * Pure: no side effects, no DOM, no service calls.
 */
export function buildPreview(
  content: string | null | undefined,
  maxLength = 60,
): string {
  if (!content) return '';

  let out = content;

  // 1. Fenced code blocks: ```...``` (multi-line, non-greedy)
  out = out.replace(/```[\s\S]*?```/g, '');
  // 2. Inline code: `foo` -> foo
  out = out.replace(/`([^`]*)`/g, '$1');
  // 3. Images: ![alt](url) -> alt
  out = out.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  // 4. Links: [text](url) -> text
  out = out.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  // 5. Bold/italic wrappers (__, **, _, *)
  out = out.replace(/__(.+?)__/g, '$1');
  out = out.replace(/\*\*(.+?)\*\*/g, '$1');
  out = out.replace(/_(.+?)_/g, '$1');
  out = out.replace(/\*(.+?)\*/g, '$1');
  // 6. Line-start markers: heading, blockquote, list markers
  out = out.replace(/^\s{0,3}#+\s+/gm, '');
  out = out.replace(/^\s{0,3}>\s+/gm, '');
  out = out.replace(/^\s*\d+\.\s+/gm, '');
  out = out.replace(/^\s*[-*+]\s+/gm, '');

  // Collapse whitespace
  out = out.replace(/\s+/g, ' ').trim();

  if (out.length > maxLength) {
    out = out.slice(0, maxLength) + '...';
  }

  return out;
}

/**
 * Convert a SentMessage into a ChatMessage with full classification.
 */
export function classifyMessage(msg: SentMessage): ChatMessage {
  const rule = classifyRule(msg);
  return {
    id: msg.id,
    message_id: msg.message.id,
    parent_id: msg.message.parent_id,
    content: msg.message.content ?? '',
    sender: msg.sender,
    recipient: msg.recipient,
    timestamp: new Date(msg.timestamp),
    rule,
    alignment: rule === 1 ? 'right' : 'left',
    color: RULE_COLORS[rule],
    collapsed: rule === 3 || rule === 4,
    label: buildLabel(msg, rule),
  };
}
