import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';

import { ENTRY_POINT_NAME } from '../models/chat-message.model';
import {
  ActorAddress,
  AkgenticMessage,
  BaseMessage,
  ErrorMessage,
  ProcessedMessage,
  ReceivedMessage,
  SentMessage,
  StartMessage,
  StateChangedMessage,
  StopMessage,
} from '../models/message.types';
import { NodeInterface } from '../models/types';
import { CategoryService } from './category.service';
import {
  EMPTY_GRAPH,
  GraphBuilder,
  GraphDataService,
  GraphState,
  graphFold,
  graphStep,
  HUMAN_ROLE,
  ORCHESTRATOR_CLASS,
} from './graph-data.service';
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

function makeBaseMessage(overrides: Partial<BaseMessage> = {}): BaseMessage {
  return {
    id: 'msg-inner-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: 'test',
    __model__: 'akgentic.core.messages.orchestrator.BaseMessage',
    ...overrides,
  };
}

function makeSentMessage(overrides: Partial<SentMessage> = {}): SentMessage {
  return {
    id: 'msg-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress({ name: '@Manager', role: 'Manager', agent_id: 'manager-1' }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    message: makeBaseMessage(),
    recipient: makeAddress({ name: '@QATester', role: 'Human', agent_id: 'qa-1' }),
    ...overrides,
  };
}

function makeStart(overrides: Partial<StartMessage> = {}): StartMessage {
  return {
    id: 'start-' + (overrides.sender?.agent_id ?? 'agent-1'),
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: {} as any,
    parent: null,
    ...overrides,
  };
}

function makeStop(agent_id: string): StopMessage {
  return {
    id: 'stop-' + agent_id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress({ agent_id }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StopMessage',
  };
}

function makeError(agent_id: string): ErrorMessage {
  return {
    id: 'err-' + agent_id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress({ agent_id }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.ErrorMessage',
    exception_type: 'RuntimeError',
    exception_value: 'boom',
  };
}

function makeReceived(agent_id: string, role = 'Worker'): ReceivedMessage {
  return {
    id: 'rcv-' + agent_id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress({ agent_id, role }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.ReceivedMessage',
    message_id: 'inner-' + agent_id,
  };
}

function makeProcessed(agent_id: string): ProcessedMessage {
  return {
    id: 'prc-' + agent_id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress({ agent_id }),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.ProcessedMessage',
    message_id: 'inner-' + agent_id,
  };
}

function makeStateChanged(): StateChangedMessage {
  return {
    id: 'sc-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StateChangedMessage',
    state: { phase: 'x' },
  };
}

function makeUnknown(): AkgenticMessage {
  return {
    id: 'unk-1',
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.future.UnknownFutureMessage',
  } as unknown as AkgenticMessage;
}

function makeNode(overrides: Partial<NodeInterface> = {}): NodeInterface {
  return {
    name: 'manager-1',
    role: 'Manager',
    actorName: '@Manager',
    parentId: 'parent-1',
    squadId: 'squad-1',
    symbol: 'roundRect',
    category: 0,
    userMessage: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// GraphBuilder (unchanged module-scope helper — regression coverage)
// ---------------------------------------------------------------------------

describe('ENTRY_POINT_NAME constant', () => {
  it('should be defined as @Human', () => {
    expect(ENTRY_POINT_NAME).toBe('@Human');
  });

  it('should differ from HUMAN_ROLE (no @ prefix)', () => {
    expect(HUMAN_ROLE).toBe('Human');
    expect(ENTRY_POINT_NAME).not.toBe(HUMAN_ROLE);
  });
});

describe('GraphBuilder.setHumanRequestPure', () => {
  it('adds notification for non-entry-point human recipient (returns new array + new node)', () => {
    const msg = makeSentMessage({
      recipient: makeAddress({ name: '@QATester', role: 'Human', agent_id: 'qa-1' }),
      message: makeBaseMessage({ display_type: 'other' }),
    });
    const builder = new GraphBuilder(msg);
    const nodes = [makeNode({ name: 'manager-1', actorName: '@Manager' })];
    const next = builder.setHumanRequestPure(nodes);
    expect(next).not.toBe(nodes); // AC7: new array when changed
    expect(next[0]).not.toBe(nodes[0]); // new node object
    expect(next[0].humanRequests).toBeDefined();
    expect(next[0].humanRequests![0]).toBe(msg);
    // Original nodes MUST remain unmutated (no in-place side effects).
    expect(nodes[0].humanRequests).toBeUndefined();
  });

  it('skips when recipient is @Human entry point (same reference)', () => {
    const msg = makeSentMessage({
      recipient: makeAddress({ name: '@Human', role: 'Human', agent_id: 'human-1' }),
      message: makeBaseMessage({ display_type: 'other' }),
    });
    const builder = new GraphBuilder(msg);
    const nodes = [makeNode({ name: 'manager-1', actorName: '@Manager' })];
    const next = builder.setHumanRequestPure(nodes);
    expect(next).toBe(nodes); // AC7: same reference when no-op
  });

  it('skips when recipient role is not Human (same reference)', () => {
    const msg = makeSentMessage({
      recipient: makeAddress({ name: '@Worker', role: 'Worker', agent_id: 'worker-1' }),
      message: makeBaseMessage({ display_type: 'other' }),
    });
    const builder = new GraphBuilder(msg);
    const nodes = [makeNode({ name: 'manager-1', actorName: '@Manager' })];
    const next = builder.setHumanRequestPure(nodes);
    expect(next).toBe(nodes);
  });
});

describe('GraphBuilder.unSetHumanRequestPure', () => {
  it('clears notification by parent_id for non-entry-point human (returns new array + new node)', () => {
    const originalMsg = makeSentMessage({
      id: 'sent-1',
      message: makeBaseMessage({ id: 'inner-1', display_type: 'other' }),
      recipient: makeAddress({ name: '@QATester', role: 'Human', agent_id: 'qa-1' }),
    });
    const replyMsg = makeSentMessage({
      id: 'reply-1',
      sender: makeAddress({ name: '@QATester', role: 'Human', agent_id: 'qa-1' }),
      recipient: makeAddress({ name: '@Manager', role: 'Manager', agent_id: 'manager-1' }),
      message: makeBaseMessage({ parent_id: 'inner-1' }),
    });
    const nodes = [
      makeNode({ name: 'manager-1', actorName: '@Manager', humanRequests: [originalMsg] }),
    ];
    const builder = new GraphBuilder(replyMsg);
    const next = builder.unSetHumanRequestPure(nodes);
    expect(next).not.toBe(nodes);
    expect(next[0]).not.toBe(nodes[0]);
    expect(next[0].humanRequests!.length).toBe(0);
    // Original untouched.
    expect(nodes[0].humanRequests!.length).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// graphFold / graphStep — pure function tests (AC1, AC6, AC7)
// ---------------------------------------------------------------------------

describe('graphFold / graphStep (pure)', () => {
  let cs: CategoryService;

  beforeEach(() => {
    cs = new CategoryService();
  });

  it('empty log → EMPTY_GRAPH', () => {
    const s = graphFold([], cs);
    expect(s.nodes).toEqual([]);
    expect(s.edges).toEqual([]);
    expect(s.squad).toEqual([]);
  });

  it('StartMessage adds a node with correct squad assignment', () => {
    const s = graphFold(
      [
        makeStart({
          sender: makeAddress({ agent_id: 'a1', squad_id: 'sq-1' }),
        }),
      ],
      cs,
    );
    expect(s.nodes.length).toBe(1);
    expect(s.nodes[0].name).toBe('a1');
    expect(s.squad.length).toBe(1);
    expect(s.squad[0].squadId).toBe('sq-1');
    expect(cs.squadDict['sq-1']).toBe(0);
  });

  it('orchestrator-role StartMessage is skipped (no node, no squad)', () => {
    const s = graphFold(
      [
        makeStart({
          sender: makeAddress({ agent_id: 'o1', __actor_type__: ORCHESTRATOR_CLASS }),
        }),
      ],
      cs,
    );
    expect(s.nodes.length).toBe(0);
  });

  it('StopMessage removes a previously-started node', () => {
    const s = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeStop('a1'),
      ],
      cs,
    );
    expect(s.nodes.length).toBe(0);
  });

  it('ErrorMessage marks node color darkred', () => {
    const s = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeError('a1'),
      ],
      cs,
    );
    expect(s.nodes[0].itemStyle?.color).toBe('darkred');
  });

  it('SentMessage adds an edge (dedup on same source/target)', () => {
    const s1 = {
      ...makeSentMessage(),
      sender: makeAddress({ agent_id: 'a1' }),
      recipient: makeAddress({ agent_id: 'a2', role: 'Worker' }),
    } as SentMessage;
    const s2 = {
      ...makeSentMessage({ id: 'msg-2' }),
      sender: makeAddress({ agent_id: 'a1' }),
      recipient: makeAddress({ agent_id: 'a2', role: 'Worker' }),
    } as SentMessage;
    const s = graphFold([s1, s2], cs);
    expect(s.edges.length).toBe(1);
  });

  it('ReceivedMessage sets borderColor on matching node', () => {
    const s = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeReceived('a1'),
      ],
      cs,
    );
    expect(s.nodes[0].itemStyle?.borderColor).toBe('darkred');
  });

  it('ReceivedMessage from Human sender does NOT change state', () => {
    const s = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeReceived('a1', HUMAN_ROLE),
      ],
      cs,
    );
    expect(s.nodes[0].itemStyle?.borderColor).toBeUndefined();
  });

  it('ProcessedMessage clears borderColor', () => {
    const s = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeReceived('a1'),
        makeProcessed('a1'),
      ],
      cs,
    );
    expect(s.nodes[0].itemStyle?.borderColor).toBeUndefined();
  });

  it('(AC6 / FR11) UnknownFutureMessage interleaved is a pure no-op', () => {
    const start = makeStart({ sender: makeAddress({ agent_id: 'a1' }) });
    const unk = makeUnknown();
    const stop = makeStop('a1');

    const csA = new CategoryService();
    const csB = new CategoryService();
    const withUnk = graphFold([start, unk, stop], csA);
    const withoutUnk = graphFold([start, stop], csB);
    expect(JSON.stringify(withUnk)).toBe(JSON.stringify(withoutUnk));
  });

  it('(AC7) neutral event (StateChangedMessage) returns same state reference', () => {
    const before: GraphState = graphFold(
      [makeStart({ sender: makeAddress({ agent_id: 'a1' }) })],
      cs,
    );
    const after = graphStep(before, makeStateChanged(), cs);
    expect(after).toBe(before);
    expect(after.nodes).toBe(before.nodes);
    expect(after.edges).toBe(before.edges);
    expect(after.squad).toBe(before.squad);
  });

  it('(AC7) slice reference equality: StartMessage changes nodes but not edges', () => {
    const before: GraphState = EMPTY_GRAPH;
    const after = graphStep(
      before,
      makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
      cs,
    );
    expect(after.nodes).not.toBe(before.nodes);
    expect(after.edges).toBe(before.edges);
  });

  it('(AC7) ReceivedMessage on existing node emits a NEW nodes reference (OnPush)', () => {
    const before: GraphState = graphFold(
      [makeStart({ sender: makeAddress({ agent_id: 'a1' }) })],
      cs,
    );
    const after = graphStep(before, makeReceived('a1'), cs);
    expect(after.nodes).not.toBe(before.nodes);
    expect(after.edges).toBe(before.edges);
    expect(after.squad).toBe(before.squad);
    // Original node object MUST NOT be mutated (immutability guard).
    expect(before.nodes[0].itemStyle?.borderColor).toBeUndefined();
    expect(after.nodes[0].itemStyle?.borderColor).toBe('darkred');
  });

  it('(AC7) ProcessedMessage clearing border emits a NEW nodes reference', () => {
    const before: GraphState = graphFold(
      [
        makeStart({ sender: makeAddress({ agent_id: 'a1' }) }),
        makeReceived('a1'),
      ],
      cs,
    );
    const after = graphStep(before, makeProcessed('a1'), cs);
    expect(after.nodes).not.toBe(before.nodes);
    // Prior snapshot retains the border (no retroactive mutation).
    expect(before.nodes[0].itemStyle?.borderColor).toBe('darkred');
    expect(after.nodes[0].itemStyle?.borderColor).toBeUndefined();
  });

  it('(AC7) ErrorMessage emits a NEW nodes reference and preserves previous snapshot', () => {
    const before: GraphState = graphFold(
      [makeStart({ sender: makeAddress({ agent_id: 'a1' }) })],
      cs,
    );
    const after = graphStep(before, makeError('a1'), cs);
    expect(after.nodes).not.toBe(before.nodes);
    expect(before.nodes[0].itemStyle?.color).toBeUndefined();
    expect(after.nodes[0].itemStyle?.color).toBe('darkred');
  });

  it('(AC7) ProcessedMessage on node with no border is a same-reference no-op', () => {
    const before: GraphState = graphFold(
      [makeStart({ sender: makeAddress({ agent_id: 'a1' }) })],
      cs,
    );
    const after = graphStep(before, makeProcessed('a1'), cs);
    expect(after).toBe(before);
    expect(after.nodes).toBe(before.nodes);
  });
});

// ---------------------------------------------------------------------------
// GraphDataService (selector over log$) — AC1/AC4/AC7/AC10
// ---------------------------------------------------------------------------

describe('GraphDataService (selector over log$)', () => {
  let log: MessageLogService;
  let service: GraphDataService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, CategoryService, GraphDataService],
    });
    log = TestBed.inject(MessageLogService);
    service = TestBed.inject(GraphDataService);
  });

  it('initial graph$ emits EMPTY_GRAPH', async () => {
    const s = await firstValueFrom(service.graph$);
    expect(s.nodes).toEqual([]);
    expect(s.edges).toEqual([]);
    expect(s.squad).toEqual([]);
  });

  it('StartMessage appended → nodes$ contains one node', async () => {
    log.append(makeStart({ sender: makeAddress({ agent_id: 'a1' }) }));
    const nodes = await firstValueFrom(service.nodes$);
    expect(nodes.length).toBe(1);
  });

  it('(AC4 late-subscriber) state appended BEFORE subscribe → first emission carries full state synchronously', () => {
    log.append(makeStart({ sender: makeAddress({ agent_id: 'a1' }) }));
    log.append(makeStart({ sender: makeAddress({ agent_id: 'a2' }) }));
    log.append(makeStart({ sender: makeAddress({ agent_id: 'a3' }) }));

    let received: GraphState | undefined;
    const sub = service.graph$.subscribe((v) => (received = v));
    expect(received).toBeDefined();
    expect(received!.nodes.length).toBe(3);
    sub.unsubscribe();
  });

  it('(AC7) StateChangedMessage does NOT re-emit nodes$ / edges$ / categories$', () => {
    const nodeEmissions: number[] = [];
    const edgeEmissions: number[] = [];
    const catEmissions: number[] = [];
    const s1 = service.nodes$.subscribe((v) => nodeEmissions.push(v.length));
    const s2 = service.edges$.subscribe((v) => edgeEmissions.push(v.length));
    const s3 = service.categories$.subscribe((v) => catEmissions.push(v.length));

    log.append(makeStateChanged());
    log.append(makeStateChanged());

    // Only baseline emission — `distinctUntilChanged()` dedups neutral events.
    expect(nodeEmissions.length).toBe(1);
    expect(edgeEmissions.length).toBe(1);
    expect(catEmissions.length).toBe(1);
    s1.unsubscribe();
    s2.unsubscribe();
    s3.unsubscribe();
  });

  it('log.reset() clears derived graph state', async () => {
    log.append(makeStart({ sender: makeAddress({ agent_id: 'a1' }) }));
    expect((await firstValueFrom(service.nodes$)).length).toBe(1);
    log.reset();
    expect((await firstValueFrom(service.nodes$)).length).toBe(0);
  });

  it('(AC10) isLoading$ is preserved as imperative BehaviorSubject', () => {
    expect(service.isLoading$.value).toBe(false);
    service.isLoading = true;
    expect(service.isLoading$.value).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Parity — REST-batch vs WS-per-message equivalence (AC5)
// ---------------------------------------------------------------------------

describe('graphFold parity (AC5 — REST batch vs WS per-message)', () => {
  it('same fixture folded via appendAll vs append loop → identical JSON', () => {
    const messages: AkgenticMessage[] = [
      makeStart({ sender: makeAddress({ agent_id: 'a1', squad_id: 'sq-1' }) }),
      makeStart({ sender: makeAddress({ agent_id: 'a2', squad_id: 'sq-1' }) }),
      // Sent a1 → a2
      {
        ...makeSentMessage({ id: 's-1' }),
        sender: makeAddress({ agent_id: 'a1' }),
        recipient: makeAddress({ agent_id: 'a2', role: 'Worker' }),
      } as SentMessage,
      makeReceived('a2'),
      makeProcessed('a2'),
      makeStop('a2'),
    ];
    const csA = new CategoryService();
    const csB = new CategoryService();
    const batchState = graphFold(messages, csA);

    const wsState: GraphState = messages.reduce(
      (s, m) => graphStep(s, m, csB),
      EMPTY_GRAPH,
    );

    expect(JSON.stringify(batchState)).toBe(JSON.stringify(wsState));
  });
});
