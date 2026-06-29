import {
  ActorAddress,
  BaseMessage,
  isLlmContextClearedEvent,
  isLlmContextCompactedEvent,
  isLlmMessageEvent,
  isLlmSystemPromptEvent,
  isLlmUsageEvent,
  isWelcomeAnnouncement,
  isWelcomeMessage,
  SentMessage,
  WelcomeMessage,
} from './message.types';

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

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

function makeWelcome(overrides: Partial<WelcomeMessage> = {}): WelcomeMessage {
  return {
    id: 'welcome-inner-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-05-18T10:00:00Z',
    sender: makeAddress({ name: '@Orchestrator', role: 'Orchestrator' }),
    display_type: 'other',
    content: 'Welcome to the agent team !',
    __model__: 'akgentic.team.messages.WelcomeMessage',
    ...overrides,
  };
}

function makeOrdinaryInner(overrides: Partial<BaseMessage> = {}): BaseMessage {
  return {
    id: 'inner-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-05-18T10:00:00Z',
    sender: makeAddress(),
    display_type: 'ai',
    content: 'ordinary content',
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    ...overrides,
  };
}

function makeSent(inner: BaseMessage, senderRole = 'ActorSystem'): SentMessage {
  return {
    id: 'outer-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-05-18T10:00:00Z',
    sender: makeAddress({ name: '@ActorSystem', role: senderRole }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    message: inner,
    recipient: makeAddress({ name: '@Human', role: 'Human' }),
  };
}

// ---------------------------------------------------------------------------
// isWelcomeMessage — inner-payload check
// ---------------------------------------------------------------------------

describe('isWelcomeMessage', () => {
  it('returns true for a WelcomeMessage inner payload', () => {
    expect(isWelcomeMessage(makeWelcome())).toBe(true);
  });

  it('returns false for an ordinary BaseMessage', () => {
    expect(isWelcomeMessage(makeOrdinaryInner())).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isWelcomeAnnouncement — envelope check (BOTH signals required, ADR-011 D1)
// ---------------------------------------------------------------------------

describe('isWelcomeAnnouncement', () => {
  it('returns true when inner is a WelcomeMessage AND inner display_type is "other"', () => {
    const msg = makeSent(makeWelcome({ display_type: 'other' }));
    expect(isWelcomeAnnouncement(msg)).toBe(true);
  });

  it('returns false when the inner payload is not a WelcomeMessage (missing __model__ signal)', () => {
    const msg = makeSent(makeOrdinaryInner({ display_type: 'other' }));
    expect(isWelcomeAnnouncement(msg)).toBe(false);
  });

  it('returns false when the inner WelcomeMessage display_type is not "other"', () => {
    const msg = makeSent(makeWelcome({ display_type: 'ai' }));
    expect(isWelcomeAnnouncement(msg)).toBe(false);
  });

  it('returns false for a non-SentMessage envelope', () => {
    const notSent = makeWelcome({
      __model__:
        'akgentic.core.messages.orchestrator.StartMessage' as unknown as WelcomeMessage['__model__'],
    });
    expect(isWelcomeAnnouncement(notSent)).toBe(false);
  });

  it('returns false when the SentMessage has no inner message', () => {
    const msg = makeSent(makeWelcome());
    (msg as { message?: BaseMessage }).message = undefined;
    expect(isWelcomeAnnouncement(msg)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isLlmUsageEvent — inner-event check (ADR-022 §Decision 1)
// ---------------------------------------------------------------------------

describe('isLlmUsageEvent', () => {
  it('returns true for an inner event whose __model__ includes "LlmUsageEvent"', () => {
    expect(
      isLlmUsageEvent({ __model__: 'akgentic.llm.event.LlmUsageEvent' }),
    ).toBe(true);
  });

  it('returns false for null / undefined / missing __model__', () => {
    expect(isLlmUsageEvent(null)).toBe(false);
    expect(isLlmUsageEvent(undefined)).toBe(false);
    expect(isLlmUsageEvent({})).toBe(false);
  });

  it('returns false for a different inner event', () => {
    expect(
      isLlmUsageEvent({ __model__: 'akgentic.llm.event.LlmSystemPromptEvent' }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// AC #3 — the three Llm*Event guards are mutually exclusive.
// For any one inner event, AT MOST ONE of (usage / system-prompt) guards fires,
// and the message-event check never co-fires with either. The discriminators
// must never overlap for the same event.
// ---------------------------------------------------------------------------

describe('Llm*Event guard mutual exclusion (AC #3)', () => {
  const usage = { __model__: 'akgentic.llm.event.LlmUsageEvent' };
  const systemPrompt = { __model__: 'akgentic.llm.event.LlmSystemPromptEvent' };
  const message = { __model__: 'akgentic.llm.event.LlmMessageEvent' };

  it('for an LlmUsageEvent, only isLlmUsageEvent fires', () => {
    expect(isLlmUsageEvent(usage)).toBe(true);
    expect(isLlmSystemPromptEvent(usage)).toBe(false);
    expect(isLlmMessageEvent(usage)).toBe(false);
  });

  it('for an LlmSystemPromptEvent, only isLlmSystemPromptEvent fires', () => {
    expect(isLlmSystemPromptEvent(systemPrompt)).toBe(true);
    expect(isLlmUsageEvent(systemPrompt)).toBe(false);
    expect(isLlmMessageEvent(systemPrompt)).toBe(false);
  });

  it('for an LlmMessageEvent, neither usage nor system-prompt guard fires', () => {
    expect(isLlmMessageEvent(message)).toBe(true);
    expect(isLlmUsageEvent(message)).toBe(false);
    expect(isLlmSystemPromptEvent(message)).toBe(false);
  });

  it('the three guards never co-fire for the same event', () => {
    for (const evt of [usage, systemPrompt, message]) {
      const fired = [
        isLlmUsageEvent(evt),
        isLlmSystemPromptEvent(evt),
        isLlmMessageEvent(evt),
      ].filter(Boolean);
      expect(fired.length).toBe(1);
    }
  });
});

// ---------------------------------------------------------------------------
// isLlmContextCompactedEvent — inner-event check (ADR-010 §3, AC #2)
// ---------------------------------------------------------------------------

describe('isLlmContextCompactedEvent', () => {
  it('returns true for an inner event whose __model__ includes "LlmContextCompactedEvent"', () => {
    expect(
      isLlmContextCompactedEvent({
        __model__: 'akgentic.llm.event.LlmContextCompactedEvent',
      }),
    ).toBe(true);
  });

  it('returns false for null / undefined / missing __model__', () => {
    expect(isLlmContextCompactedEvent(null)).toBe(false);
    expect(isLlmContextCompactedEvent(undefined)).toBe(false);
    expect(isLlmContextCompactedEvent({})).toBe(false);
  });

  it('returns false for the sibling clear event (no substring collision)', () => {
    expect(
      isLlmContextCompactedEvent({
        __model__: 'akgentic.llm.event.LlmContextClearedEvent',
      }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isLlmContextClearedEvent — inner-event check (ADR-010 §8, AC #2)
// ---------------------------------------------------------------------------

describe('isLlmContextClearedEvent', () => {
  it('returns true for an inner event whose __model__ includes "LlmContextClearedEvent"', () => {
    expect(
      isLlmContextClearedEvent({
        __model__: 'akgentic.llm.event.LlmContextClearedEvent',
      }),
    ).toBe(true);
  });

  it('returns false for null / undefined / missing __model__', () => {
    expect(isLlmContextClearedEvent(null)).toBe(false);
    expect(isLlmContextClearedEvent(undefined)).toBe(false);
    expect(isLlmContextClearedEvent({})).toBe(false);
  });

  it('returns false for the sibling compaction event (no substring collision)', () => {
    expect(
      isLlmContextClearedEvent({
        __model__: 'akgentic.llm.event.LlmContextCompactedEvent',
      }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// AC #2 — the FIVE Llm*Event / context guards are mutually exclusive.
// For any single inner event exactly ONE of the five guards fires. Crucially
// this protects the two new substrings against collision IN EITHER DIRECTION:
// 'LlmContextCompactedEvent' vs 'LlmContextClearedEvent' (shared 'LlmContext'
// prefix) and against 'LlmMessageEvent' / 'LlmUsageEvent' /
// 'LlmSystemPromptEvent'.
// ---------------------------------------------------------------------------

describe('Llm*Event five-guard mutual exclusion (AC #2)', () => {
  const compacted = { __model__: 'akgentic.llm.event.LlmContextCompactedEvent' };
  const cleared = { __model__: 'akgentic.llm.event.LlmContextClearedEvent' };
  const usage = { __model__: 'akgentic.llm.event.LlmUsageEvent' };
  const systemPrompt = { __model__: 'akgentic.llm.event.LlmSystemPromptEvent' };
  const message = { __model__: 'akgentic.llm.event.LlmMessageEvent' };

  const guards = [
    isLlmContextCompactedEvent,
    isLlmContextClearedEvent,
    isLlmUsageEvent,
    isLlmSystemPromptEvent,
    isLlmMessageEvent,
  ] as const;

  it('for a compaction event, ONLY isLlmContextCompactedEvent fires', () => {
    expect(isLlmContextCompactedEvent(compacted)).toBe(true);
    expect(isLlmContextClearedEvent(compacted)).toBe(false);
    expect(isLlmUsageEvent(compacted)).toBe(false);
    expect(isLlmSystemPromptEvent(compacted)).toBe(false);
    expect(isLlmMessageEvent(compacted)).toBe(false);
  });

  it('for a clear event, ONLY isLlmContextClearedEvent fires', () => {
    expect(isLlmContextClearedEvent(cleared)).toBe(true);
    expect(isLlmContextCompactedEvent(cleared)).toBe(false);
    expect(isLlmUsageEvent(cleared)).toBe(false);
    expect(isLlmSystemPromptEvent(cleared)).toBe(false);
    expect(isLlmMessageEvent(cleared)).toBe(false);
  });

  it('the five guards never co-fire for the same event', () => {
    for (const evt of [compacted, cleared, usage, systemPrompt, message]) {
      const fired = guards.map((g) => g(evt)).filter(Boolean);
      expect(fired.length).toBe(1);
    }
  });
});
