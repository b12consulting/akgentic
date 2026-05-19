import {
  ActorAddress,
  BaseMessage,
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
