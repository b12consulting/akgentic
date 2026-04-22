import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';

import { ChatMessage, ENTRY_POINT_NAME } from '../models/chat-message.model';
import {
  ActorAddress,
  AkgenticMessage,
  BaseMessage,
  EventMessage,
  ProcessedMessage,
  ReceivedMessage,
  SentMessage,
  StartMessage,
  StateChangedMessage,
} from '../models/message.types';
import {
  chatFold,
  ChatService,
  ChatState,
  chatStep,
  computePendingNotifications,
  EMPTY_CHAT,
} from './chat.service';
import { MessageLogService } from './message-log.service';

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

function makeInnerBase(overrides: Partial<BaseMessage> = {}): BaseMessage {
  return {
    id: 'inner-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: 'hello',
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    ...overrides,
  };
}

function makeSent(overrides: Partial<SentMessage> = {}): SentMessage {
  return {
    id: 'outer-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress({ name: '@Manager', role: 'Manager', agent_id: 'manager-1' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    message: makeInnerBase(),
    recipient: makeAddress({ name: '@Human', role: 'Human', agent_id: 'human-1' }),
    ...overrides,
  };
}

function makeReceived(overrides: Partial<ReceivedMessage> = {}): ReceivedMessage {
  return {
    id: 'rcv-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.ReceivedMessage',
    message_id: 'inner-rcv-1',
    ...overrides,
  };
}

function makeStart(overrides: Partial<StartMessage> = {}): StartMessage {
  return {
    id: 'start-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress({ name: '@Worker' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: {} as any,
    parent: null,
    ...overrides,
  };
}

function makeStateChanged(): StateChangedMessage {
  return {
    id: 'sc-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StateChangedMessage',
    state: { phase: 'x' },
  };
}

function makeProcessed(overrides: Partial<ProcessedMessage> = {}): ProcessedMessage {
  return {
    id: 'proc-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.ProcessedMessage',
    message_id: 'inner-rcv-1',
    ...overrides,
  };
}

function makeEvent(
  inner: any,
  overrides: Partial<EventMessage> = {},
): EventMessage {
  return {
    id: 'evt-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: inner,
    ...overrides,
  };
}

function makeUnknown(): AkgenticMessage {
  return {
    id: 'unk',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-12T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.future.UnknownFutureMessage',
  } as unknown as AkgenticMessage;
}

// ---------------------------------------------------------------------------
// chatFold (pure function) — direct coverage of FR7 + AC1/AC3/AC6/AC7
// ---------------------------------------------------------------------------

describe('chatFold / chatStep (pure)', () => {
  it('empty log → EMPTY_CHAT', () => {
    expect(chatFold([])).toEqual(EMPTY_CHAT);
  });

  it('SentMessage appends a classified ChatMessage', () => {
    const msg = makeSent();
    const state = chatFold([msg]);
    expect(state.messages.length).toBe(1);
    expect(state.messages[0].id).toBe('outer-1');
  });

  it('SentMessage from ActorSystem is skipped (no message appended)', () => {
    const msg = makeSent({
      sender: makeAddress({ role: 'ActorSystem', name: '@System' }),
    });
    const state = chatFold([msg]);
    expect(state.messages.length).toBe(0);
  });

  it('SentMessage with empty content is skipped', () => {
    const msg = makeSent({
      message: makeInnerBase({ content: '' }),
    });
    const state = chatFold([msg]);
    expect(state.messages.length).toBe(0);
  });

  it('ReceivedMessage appends a non-final thinking state (port of beginThinking)', () => {
    const msg = makeReceived();
    const state = chatFold([msg]);
    expect(state.thinkingAgents.length).toBe(1);
    expect(state.thinkingAgents[0].agent_id).toBe('agent-1');
    expect(state.thinkingAgents[0].final).toBe(false);
    expect(state.thinkingAgents[0].tools).toEqual([]);
    expect(state.thinkingAgents[0].anchor_message_id).toBe('inner-rcv-1');
  });

  it('two consecutive ReceivedMessages for same agent → idempotent (single entry)', () => {
    const a = makeReceived();
    const b = makeReceived({ id: 'rcv-2', message_id: 'inner-rcv-2' });
    const state = chatFold([a, b]);
    expect(state.thinkingAgents.length).toBe(1);
    expect(state.thinkingAgents[0].anchor_message_id).toBe('inner-rcv-1');
  });

  it('ReceivedMessage with sender.role === Human is skipped (no thinking entry)', () => {
    const msg = makeReceived({
      sender: makeAddress({ role: 'Human', name: '@Human' }),
    });
    const state = chatFold([msg]);
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('EventMessage ToolCallEvent appends a tool entry after an active Received', () => {
    const rcv = makeReceived();
    const evt = makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{"q":"x"}',
    });
    const state = chatFold([rcv, evt]);
    expect(state.thinkingAgents[0].tools.length).toBe(1);
    expect(state.thinkingAgents[0].tools[0].tool_call_id).toBe('call-1');
    expect(state.thinkingAgents[0].tools[0].done).toBe(false);
    expect(state.thinkingAgents[0].tools[0].arguments_preview.length).toBeGreaterThan(0);
  });

  it('EventMessage ToolCallEvent with NO active thinking state → no-op, console.debug', () => {
    const debugSpy = spyOn(console, 'debug');
    const evt = makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{}',
    });
    const state = chatFold([evt]);
    expect(state.thinkingAgents.length).toBe(0);
    expect(debugSpy).toHaveBeenCalled();
  });

  it('EventMessage ToolReturnEvent flips tool entry.done = true', () => {
    const rcv = makeReceived();
    const call = makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{}',
    });
    const ret = makeEvent({
      __model__: 'akgentic.llm.event.ToolReturnEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      success: true,
    });
    const state = chatFold([rcv, call, ret]);
    expect(state.thinkingAgents[0].tools[0].done).toBe(true);
  });

  it('SentMessage with no tools in active thinking → ephemeral exit (entry removed)', () => {
    const rcv = makeReceived();
    const sent = makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    });
    const state = chatFold([rcv, sent]);
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('SentMessage with tools in active thinking → persistent (final=true, entry kept)', () => {
    const rcv = makeReceived();
    const call = makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{}',
    });
    const sent = makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    });
    const state = chatFold([rcv, call, sent]);
    expect(state.thinkingAgents.length).toBe(1);
    expect(state.thinkingAgents[0].final).toBe(true);
  });

  it('SentMessage from ActorSystem does NOT trigger finalise', () => {
    const rcv = makeReceived();
    const sentSys = makeSent({
      sender: makeAddress({
        name: '@System',
        role: 'ActorSystem',
        agent_id: 'agent-1',
      }),
    });
    const state = chatFold([rcv, sentSys]);
    expect(state.thinkingAgents.length).toBe(1);
  });

  it('ProcessedMessage with no tools in active thinking → ephemeral exit (entry removed)', () => {
    const rcv = makeReceived();
    const proc = makeProcessed();
    const state = chatFold([rcv, proc]);
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('ProcessedMessage with tools in active thinking → persistent (final=true, entry kept)', () => {
    const rcv = makeReceived();
    const call = makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{}',
    });
    const proc = makeProcessed();
    const state = chatFold([rcv, call, proc]);
    expect(state.thinkingAgents.length).toBe(1);
    expect(state.thinkingAgents[0].final).toBe(true);
  });

  it('ProcessedMessage with no prior thinking state → no-op', () => {
    const proc = makeProcessed();
    const state = chatFold([proc]);
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('ProcessedMessage for different agent does NOT clear another agent thinking', () => {
    const rcv = makeReceived(); // agent-1
    const proc = makeProcessed({
      sender: makeAddress({ name: '@Writer', agent_id: 'agent-2' }),
    });
    const state = chatFold([rcv, proc]);
    expect(state.thinkingAgents.length).toBe(1);
    expect(state.thinkingAgents[0].agent_id).toBe('agent-1');
    expect(state.thinkingAgents[0].final).toBe(false);
  });

  it('ProcessedMessage after SentMessage already finalised → idempotent no-op', () => {
    const rcv = makeReceived();
    const sent = makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    });
    const proc = makeProcessed();
    const state = chatFold([rcv, sent, proc]);
    // SentMessage already removed the entry (no tools), ProcessedMessage is a no-op
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('(AC6 / FR11) UnknownFutureMessage interleaved is a pure no-op', () => {
    const rcv = makeReceived();
    const unk = makeUnknown();
    const sent = makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    });
    const withUnk = chatFold([rcv, unk, sent]);
    const withoutUnk = chatFold([rcv, sent]);
    expect(withUnk).toEqual(withoutUnk);
  });

  it('(AC7) neutral event (StartMessage) returns same state reference', () => {
    const before = chatFold([]);
    const after = chatStep(before, makeStart());
    expect(after).toBe(before);
  });

  it('(AC7) neutral event (StateChangedMessage) returns same state reference', () => {
    const before = chatFold([]);
    const after = chatStep(before, makeStateChanged());
    expect(after).toBe(before);
  });
});

// ---------------------------------------------------------------------------
// ChatService (selector over MessageLogService.log$)
// ---------------------------------------------------------------------------

describe('ChatService (selector over log$)', () => {
  let log: MessageLogService;
  let service: ChatService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, ChatService],
    });
    log = TestBed.inject(MessageLogService);
    service = TestBed.inject(ChatService);
  });

  it('retired imperative mutators are not exposed', () => {
    expect((service as any).beginThinking).toBeUndefined();
    expect((service as any).appendToolCall).toBeUndefined();
    expect((service as any).markToolDone).toBeUndefined();
    expect((service as any).finaliseOrDiscard).toBeUndefined();
  });

  it('initial messages$ / thinkingAgents$ emit []', async () => {
    expect(await firstValueFrom(service.messages$)).toEqual([]);
    expect(await firstValueFrom(service.thinkingAgents$)).toEqual([]);
  });

  it('ReceivedMessage → thinkingAgents$ emits state with that agent', async () => {
    log.append(makeReceived());
    const states = await firstValueFrom(service.thinkingAgents$);
    expect(states.length).toBe(1);
    expect(states[0].agent_id).toBe('agent-1');
  });

  it('ReceivedMessage idempotent: two consecutive for same agent → one entry', async () => {
    log.append(makeReceived());
    log.append(makeReceived({ id: 'rcv-2', message_id: 'inner-rcv-2' }));
    const states = await firstValueFrom(service.thinkingAgents$);
    expect(states.length).toBe(1);
  });

  it('SentMessage appended → messages$ emits classified list', async () => {
    log.append(makeSent());
    const msgs = await firstValueFrom(service.messages$);
    expect(msgs.length).toBe(1);
    expect(msgs[0].id).toBe('outer-1');
  });

  it('full lifecycle — ReceivedMessage + SentMessage (no tools) ends in empty thinking', async () => {
    log.append(makeReceived());
    log.append(makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    }));
    expect((await firstValueFrom(service.thinkingAgents$)).length).toBe(0);
  });

  it('(AC4 late-subscriber) full lifecycle appended BEFORE subscribe → first emission has final state', async () => {
    log.append(makeReceived());
    log.append(makeEvent({
      __model__: 'akgentic.llm.event.ToolCallEvent',
      tool_call_id: 'call-1',
      tool_name: 'search_web',
      arguments: '{}',
    }));
    log.append(makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    }));

    let received: ChatState | undefined;
    const sub = service.chat$.subscribe((v) => (received = v));
    expect(received).toBeDefined();
    expect(received!.thinkingAgents.length).toBe(1);
    expect(received!.thinkingAgents[0].final).toBe(true);
    expect(received!.thinkingAgents[0].tools.length).toBe(1);
    sub.unsubscribe();
  });

  it('(AC7) StartMessage (non-chat-relevant) does NOT re-emit thinkingAgents$', async () => {
    const emissions: ThinkingStateSnapshot[] = [];
    const sub = service.thinkingAgents$.subscribe((v) =>
      emissions.push({ ref: v, length: v.length }),
    );
    log.append(makeStart());
    log.append(makeStart({ id: 'start-2' }));
    // Only the initial baseline emission should be present (no thinking
    // changes), because `distinctUntilChanged()` deduplicates references.
    expect(emissions.length).toBe(1);
    sub.unsubscribe();
  });

  it('(AC6) UnknownFutureMessage interleaved → state identical to without', async () => {
    log.append(makeReceived());
    log.append(makeUnknown());
    log.append(makeSent({
      sender: makeAddress({ name: '@Researcher', agent_id: 'agent-1', role: 'Worker' }),
    }));
    const state = await firstValueFrom(service.chat$);
    expect(state.thinkingAgents.length).toBe(0);
  });

  it('log.reset() clears derived chat state', async () => {
    log.append(makeReceived());
    expect((await firstValueFrom(service.thinkingAgents$)).length).toBe(1);
    log.reset();
    expect((await firstValueFrom(service.thinkingAgents$)).length).toBe(0);
  });

  it('pendingNotifications$ reacts to Rule 3 messages via the derived messages$', async () => {
    // Make a Rule 3 SentMessage: recipient.role='Human' and recipient.name != @Human
    const rule3 = makeSent({
      recipient: makeAddress({ name: '@QATester', role: 'Human', agent_id: 'qa-1' }),
    });
    log.append(rule3);
    const pending = await firstValueFrom(service.pendingNotifications$);
    // computePendingNotifications keys on inner message_id, which in the
    // fixture equals 'inner-1'.
    expect(pending.has('inner-1')).toBe(true);
  });
});

type ThinkingStateSnapshot = { ref: unknown; length: number };

// ---------------------------------------------------------------------------
// computePendingNotifications — existing coverage preserved (API-compatible)
// ---------------------------------------------------------------------------

describe('computePendingNotifications', () => {
  function makeChatMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
    const id = overrides.id ?? 'msg-1';
    return {
      id,
      message_id: id,
      parent_id: null,
      content: 'Hello world',
      sender: makeAddress({ name: '@Manager', role: 'Manager' }),
      recipient: makeAddress({ name: '@Human', role: 'Human' }),
      timestamp: new Date('2026-04-08T10:00:00Z'),
      rule: 2,
      alignment: 'left',
      color: '#9ebbcb',
      collapsed: false,
      label: 'Manager [Manager]',
      ...overrides,
    };
  }

  it('empty messages → empty set', () => {
    expect(computePendingNotifications([]).size).toBe(0);
  });

  it('Rule 3 message adds its inner message_id to the unanswered set', () => {
    const msgs: ChatMessage[] = [
      makeChatMessage({
        id: 'r3-1',
        rule: 3,
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
      }),
    ];
    const result = computePendingNotifications(msgs);
    expect(result.size).toBe(1);
    expect(result.has('r3-1')).toBe(true);
  });

  it('reply whose parent_id matches clears exactly that entry', () => {
    const msgs: ChatMessage[] = [
      makeChatMessage({
        id: 'r3-1',
        rule: 3,
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
      }),
      makeChatMessage({
        id: 'r3-2',
        rule: 3,
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
      }),
      makeChatMessage({
        id: 'reply-1',
        parent_id: 'r3-1',
        rule: 1,
        sender: makeAddress({ name: '@QATester', role: 'Human' }),
        recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
      }),
    ];
    const result = computePendingNotifications(msgs);
    expect(result.size).toBe(1);
    expect(result.has('r3-2')).toBe(true);
  });

  it('@Human entry-point recipient is NOT tracked', () => {
    const msgs: ChatMessage[] = [
      makeChatMessage({
        id: 'r2-1',
        rule: 2,
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: ENTRY_POINT_NAME, role: 'Human' }),
      }),
    ];
    expect(computePendingNotifications(msgs).size).toBe(0);
  });

  it('clearing keys on inner message_id (regression: outer/inner mismatch)', () => {
    const msgs: ChatMessage[] = [
      makeChatMessage({
        id: 'r3-outer-1',
        message_id: 'r3-inner-1',
        rule: 3,
        sender: makeAddress({ name: '@Manager', role: 'Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
      }),
      makeChatMessage({
        id: 'reply-outer-1',
        message_id: 'reply-inner-1',
        parent_id: 'r3-inner-1',
        rule: 1,
        sender: makeAddress({ name: '@QATester', role: 'Human' }),
        recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
      }),
    ];
    expect(computePendingNotifications(msgs).size).toBe(0);
  });
});
