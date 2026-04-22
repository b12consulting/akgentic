import {
  buildPreview,
  classifyRule,
  buildLabel,
  classifyMessage,
  ENTRY_POINT_NAME,
  ChatMessage,
} from './chat-message.model';
import { ActorAddress, SentMessage, BaseMessage } from './message.types';

function makeAddress(overrides: Partial<ActorAddress> = {}): ActorAddress {
  return {
    __actor_address__: true,
    name: '@Agent',
    role: 'Worker',
    agent_id: 'agent-1',
    squad_id: 'squad-1',
    user_message: false,
    ...overrides,
  };
}

function makeBaseMessage(overrides: Partial<BaseMessage> = {}): BaseMessage {
  return {
    id: 'msg-inner-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: 'Hello world',
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    ...overrides,
  };
}

function makeSentMessage(overrides: Partial<SentMessage> = {}): SentMessage {
  const sender = overrides.sender ?? makeAddress();
  const recipient = overrides.recipient ?? makeAddress({ name: '@Manager', role: 'Manager' });
  return {
    id: 'msg-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender,
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    message: makeBaseMessage({ content: 'test content' }),
    recipient,
    ...overrides,
  };
}

describe('ENTRY_POINT_NAME', () => {
  it('should be @Human', () => {
    expect(ENTRY_POINT_NAME).toBe('@Human');
  });
});

describe('classifyRule', () => {
  it('Rule 1: sender is @Human', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    expect(classifyRule(msg)).toBe(1);
  });

  it('Rule 2: recipient is @Human', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
    });
    expect(classifyRule(msg)).toBe(2);
  });

  it('Rule 3: recipient role is Human but name is not @Human', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Agent', role: 'Worker' }),
      recipient: makeAddress({ name: '@OtherHuman', role: 'Human' }),
    });
    expect(classifyRule(msg)).toBe(3);
  });

  it('Rule 4: AI-to-AI (no Human involved)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Worker', role: 'Worker' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    expect(classifyRule(msg)).toBe(4);
  });

  it('first-match wins: sender @Human takes priority even if recipient is also @Human', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
    });
    expect(classifyRule(msg)).toBe(1);
  });

  it('Rule 2 takes priority over Rule 3 when recipient name is @Human', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Worker', role: 'Worker' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
    });
    expect(classifyRule(msg)).toBe(2);
  });
});

describe('buildLabel', () => {
  it('Rule 1: "You ⇒ {recipient}"', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager-manager', role: 'Manager' }),
    });
    const label = buildLabel(msg, 1);
    expect(label).toContain('You ⇒');
    expect(label).toContain('Manager');
    expect(label).not.toContain('->');
  });

  it('Rule 2: "{sender} ⇒ You"', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Manager-manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
    });
    const label = buildLabel(msg, 2);
    expect(label).toContain('⇒ You');
    expect(label).toContain('Manager');
    expect(label.endsWith('⇒ You')).toBe(true);
    expect(label).not.toContain('->');
  });

  it('Rule 3: "@{sender} ⇒ @{recipient}"', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Agent-worker', role: 'Worker' }),
      recipient: makeAddress({ name: '@OtherHuman-human', role: 'Human' }),
    });
    const label = buildLabel(msg, 3);
    expect(label).toContain('⇒');
    expect(label).toContain('Agent');
    expect(label).toContain('OtherHuman');
    expect(label).not.toContain('->');
  });

  it('Rule 4: "@{sender} ⇒ @{recipient}"', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Worker-worker', role: 'Worker' }),
      recipient: makeAddress({ name: '@Manager-manager', role: 'Manager' }),
    });
    const label = buildLabel(msg, 4);
    expect(label).toContain('⇒');
    expect(label).toContain('Worker');
    expect(label).toContain('Manager');
    expect(label).not.toContain('->');
  });

  it('Rule 2 explicit: result ends with "⇒ You" (Story 4.3)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Manager-manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
    });
    const label = buildLabel(msg, 2);
    expect(label.endsWith('⇒ You')).toBe(true);
  });
});

describe('buildPreview', () => {
  it('returns "" for null', () => {
    expect(buildPreview(null)).toBe('');
  });

  it('returns "" for undefined', () => {
    expect(buildPreview(undefined)).toBe('');
  });

  it('returns "" for empty string', () => {
    expect(buildPreview('')).toBe('');
  });

  it('collapses multi-line content to a single line', () => {
    expect(buildPreview('hello\nworld\n\n!')).toBe('hello world !');
  });

  it('collapses tabs and multiple spaces', () => {
    expect(buildPreview('hello\t\tworld   !')).toBe('hello world !');
  });

  it('strips fenced code blocks', () => {
    expect(buildPreview('text\n```ts\ncode here\n```\nmore')).toBe('text more');
  });

  it('strips inline code ticks but keeps inner text', () => {
    expect(buildPreview('use `npm install` now')).toBe('use npm install now');
  });

  it('strips markdown link syntax to text', () => {
    expect(buildPreview('see [docs](http://x) here')).toBe('see docs here');
  });

  it('strips markdown image syntax to alt', () => {
    expect(buildPreview('![logo](x.png) ok')).toBe('logo ok');
  });

  it('strips bold (**) wrappers', () => {
    expect(buildPreview('**bold** text')).toBe('bold text');
  });

  it('strips bold (__) wrappers', () => {
    expect(buildPreview('__bold__ text')).toBe('bold text');
  });

  it('strips italic (_) wrappers', () => {
    expect(buildPreview('_italic_ text')).toBe('italic text');
  });

  it('strips italic (*) wrappers', () => {
    expect(buildPreview('*em* text')).toBe('em text');
  });

  it('strips combined emphasis wrappers', () => {
    expect(buildPreview('**bold** and _italic_ and *em*')).toBe(
      'bold and italic and em',
    );
  });

  it('strips heading markers', () => {
    expect(buildPreview('# Title')).toBe('Title');
  });

  it('strips blockquote markers', () => {
    expect(buildPreview('> quoted')).toBe('quoted');
  });

  it('strips bullet list markers', () => {
    expect(buildPreview('- item')).toBe('item');
  });

  it('strips numeric list markers', () => {
    expect(buildPreview('1. first')).toBe('first');
  });

  it('does NOT truncate when content length <= maxLength', () => {
    const forty = 'a'.repeat(40);
    expect(buildPreview(forty)).toBe(forty);
    expect(buildPreview(forty).endsWith('...')).toBe(false);
  });

  it('truncates with literal "..." when content > maxLength', () => {
    const eighty = 'a'.repeat(80);
    const result = buildPreview(eighty);
    expect(result.length).toBe(63);
    expect(result.endsWith('...')).toBe(true);
    expect(result.startsWith('a'.repeat(60))).toBe(true);
  });

  it('respects custom maxLength override', () => {
    expect(buildPreview('abcdefghijklmnop', 10)).toBe('abcdefghij...');
  });

  it('exactly maxLength boundary: no truncation at length === maxLength', () => {
    const sixty = 'b'.repeat(60);
    expect(buildPreview(sixty)).toBe(sixty);
  });
});

describe('classifyMessage', () => {
  it('should return a ChatMessage with correct fields for Rule 1', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    const result: ChatMessage = classifyMessage(msg);

    expect(result.id).toBe('msg-1');
    expect(result.content).toBe('test content');
    expect(result.rule).toBe(1);
    expect(result.alignment).toBe('right');
    expect(result.color).toBe('#efeeee');
    expect(result.collapsed).toBe(false);
    expect(result.label).toContain('You ⇒');
  });

  it('should return a ChatMessage with correct fields for Rule 4 (collapsed)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Worker', role: 'Worker' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    const result: ChatMessage = classifyMessage(msg);

    expect(result.rule).toBe(4);
    expect(result.alignment).toBe('left');
    expect(result.color).toBe('#9ebbcb');
    expect(result.collapsed).toBe(true);
  });

  it('should return collapsed=true for Rule 3 (non-@Human human recipient)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@QATester', role: 'Human' }),
    });
    const result: ChatMessage = classifyMessage(msg);

    expect(result.rule).toBe(3);
    expect(result.alignment).toBe('left');
    expect(result.collapsed).toBe(true);
  });

  it('should return collapsed=false for Rule 1 and Rule 2', () => {
    const r1 = classifyMessage(
      makeSentMessage({
        sender: makeAddress({ name: '@Human', role: 'Human' }),
        recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
      }),
    );
    const r2 = classifyMessage(
      makeSentMessage({
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: '@Human', role: 'Human' }),
      }),
    );
    expect(r1.rule).toBe(1);
    expect(r1.collapsed).toBe(false);
    expect(r2.rule).toBe(2);
    expect(r2.collapsed).toBe(false);
  });

  it('should handle null content gracefully', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    msg.message = makeBaseMessage({ content: null });
    const result = classifyMessage(msg);
    expect(result.content).toBe('');
  });

  it('should propagate parent_id from inner BaseMessage (Story 4.6)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@QATester', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
      message: makeBaseMessage({ parent_id: 'original-r3-id', content: 'reply' }),
    });
    const result = classifyMessage(msg);
    expect(result.parent_id).toBe('original-r3-id');
  });

  it('should populate message_id from inner BaseMessage.id (distinct from outer envelope id)', () => {
    // Regression guard for per-message notification tracking: the outer
    // SentMessage.id and the inner BaseMessage.id are DIFFERENT values in
    // production. ChatMessage.id holds the outer (for backend routing via
    // processHumanInput) while ChatMessage.message_id holds the inner (the
    // value reply.parent_id will reference). A fixture that makes them
    // coincide defeats the purpose — this test uses deliberately distinct
    // values to protect against the regression that shipped in Story 4.6.
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@QATester', role: 'Human' }),
      message: makeBaseMessage({ id: 'msg-inner-42', content: 'req' }),
    });
    // Force outer id distinct from inner id.
    msg.id = 'msg-outer-42';
    const result = classifyMessage(msg);
    expect(result.id).toBe('msg-outer-42');
    expect(result.message_id).toBe('msg-inner-42');
  });

  it('should default parent_id to null when inner BaseMessage has no parent (Story 4.6)', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    });
    const result = classifyMessage(msg);
    expect(result.parent_id).toBeNull();
  });

  it('should set timestamp as Date object', () => {
    const msg = makeSentMessage({
      sender: makeAddress({ name: '@Human', role: 'Human' }),
    });
    const result = classifyMessage(msg);
    expect(result.timestamp instanceof Date).toBe(true);
  });
});
