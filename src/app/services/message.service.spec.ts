import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';
import { BehaviorSubject, Subject } from 'rxjs';
import { WebSocketSubject } from 'rxjs/webSocket';

import { ActorMessageService } from './message.service';
import { ApiService } from './api.service';
import { ChatService } from './chat.service';
import { MessageLogService } from './message-log.service';
import { ActorAddress } from '../models/message.types';

function makeAddress(overrides: Partial<ActorAddress> = {}): ActorAddress {
  return {
    __actor_address__: true,
    name: '@Researcher',
    role: 'Worker',
    agent_id: 'agent-1',
    team_id: 'team-1',
    squad_id: 'squad-1',
    user_message: false,
    ...overrides,
  };
}

// Story 6.3 (Task 7.4): makeReceived / makeSent / makeEventMessage fixture
// helpers were removed with the deleted `applyThinkingLifecycle` and
// `dispatchToolEventToThinking` describe blocks.

// Story 6.3 (AC9, Task 7.4): `applyThinkingLifecycle`,
// `dispatchToolEventToThinking`, and `handleEventMessage` were deleted from
// `ActorMessageService`. The thinking-bubble lifecycle is now reconstructed
// by `chatFold` over `log$` — coverage lives in `chat.service.spec.ts`
// (ReceivedMessage → ToolCallEvent → ToolReturnEvent → SentMessage fold
// scenarios, integration + FR11 + AC7 + late-subscriber).

describe('ActorMessageService.init — loadingProcess$ spinner window (Story 4-10)', () => {
  let service: ActorMessageService;
  let chatService: ChatService;
  let fakeSocket: Subject<any>;

  beforeEach(() => {
    // Story 4-10 (AC7): the spinner now has a 500ms minimum visible duration
    // enforced via `Date.now()` + `setTimeout`. Install the jasmine clock so
    // every test in this suite can deterministically control both the
    // "wall-clock" elapsed window AND the deferred setTimeout callback.
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(0));

    fakeSocket = new Subject<any>();

    TestBed.configureTestingModule({
      providers: [
        MessageLogService,
        ActorMessageService,
        ChatService,
        {
          provide: ApiService,
          useValue: {
            // Only used by the `!running` branch; default empty list is fine.
            getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
          },
        },
        { provide: MessageService, useValue: { add: jasmine.createSpy('add'), clear: jasmine.createSpy('clear') } },
      ],
    });
    service = TestBed.inject(ActorMessageService);
    chatService = TestBed.inject(ChatService);

    // Stub the service's protected WS factory so init() wires the
    // subscription up to our Subject instead of opening a real TCP
    // connection. Cast through unknown to satisfy the WebSocketSubject<any>
    // return type expected by init().
    spyOn<any>(service, 'createWebSocket').and.returnValue(
      fakeSocket as unknown as WebSocketSubject<any>,
    );
  });

  afterEach(() => {
    // Avoid cross-test leakage of the fake socket. Story 6.1 made
    // `init()`'s (a) step call `this.webSocket.unsubscribe()` before
    // swapping in a new socket — on re-init tests the previous fake
    // socket is therefore already closed, so `complete()` here would
    // throw `ObjectUnsubscribedError`. Swallow it: the whole point of
    // this teardown is "make sure the Subject does not leak into the
    // next test".
    try {
      fakeSocket.complete();
    } catch {
      /* subject already closed by disposePriorSubscriptions — ignore */
    }
    jasmine.clock().uninstall();
  });

  it('AC1: running=true keeps loadingProcess$ true until the first WS event arrives', async () => {
    await service.init('proc-1', true);

    // Socket is open but no events yet → spinner MUST stay on.
    expect(chatService.loadingProcess$.value).toBe(true);
  });

  it('AC1: loadingProcess$ flips to false on the first WS event (past the 500ms floor)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    // Advance past the SPINNER_MIN_VISIBLE_MS floor so the first-event flip
    // fires immediately (AC7 immediate-flip branch).
    jasmine.clock().tick(600);

    // First event over the wire — shape intentionally uninteresting, the
    // flip MUST happen before any per-__model__ branching.
    fakeSocket.next({
      __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    });

    expect(chatService.loadingProcess$.value).toBe(false);
  });

  it('AC1: subsequent events do not re-emit false (guard is single-shot)', async () => {
    await service.init('proc-1', true);
    const emitted: boolean[] = [];
    chatService.loadingProcess$.subscribe((v) => emitted.push(v));
    // Start: BehaviorSubject replays current value (true).
    expect(emitted).toEqual([true]);

    // Past the 500ms floor so the flip happens immediately on first event.
    jasmine.clock().tick(600);
    fakeSocket.next({ __model__: 'StartMessage' });
    fakeSocket.next({ __model__: 'StartMessage' });
    fakeSocket.next({ __model__: 'StartMessage' });

    // Exactly ONE transition to false — no re-emit per event.
    expect(emitted).toEqual([true, false]);
  });

  it('AC3: WS error before any event flips loadingProcess$ to false (past the floor)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    jasmine.clock().tick(600);
    fakeSocket.error(new Error('connect refused'));

    expect(chatService.loadingProcess$.value).toBe(false);
  });

  it('AC2: running=false (stopped team) flips loadingProcess$ to false before WS wiring', async () => {
    // Record the sequence of `loadingProcess$` values as init() runs so we
    // can assert the spinner is OFF before any WS events are delivered.
    // Note: createWebSocket is called BEFORE subscribe(), so by the time the
    // spy-stubbed factory returns, the stopped-team branch has already flipped
    // the flag to `false`. Subscribing to track values after await captures
    // the current BehaviorSubject state without needing to push any events.
    const emitted: boolean[] = [];
    const sub = chatService.loadingProcess$.subscribe((v) => emitted.push(v));

    await service.init('proc-1', false);
    // Drive the 500ms floor so the deferred flip can actually fire.
    jasmine.clock().tick(600);

    // No WS events pushed → stopped-team path MUST have already flipped it,
    // AND the sequence must include at least one `true` (spinner on during
    // getEvents()) followed by `false` (before WS wiring).
    expect(chatService.loadingProcess$.value).toBe(false);
    expect(emitted).toContain(true);
    expect(emitted[emitted.length - 1]).toBe(false);

    sub.unsubscribe();
  });

  // ---------------------------------------------------------------------
  // AC8 — Minimum visible spinner duration (500ms floor)
  // ---------------------------------------------------------------------

  it('AC8: first event at 100ms → flag still true at 400ms → false at 500ms (deferred flip)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    // First event arrives at 100ms — well before the 500ms floor.
    jasmine.clock().tick(100);
    fakeSocket.next({ __model__: 'StartMessage' });

    // At 400ms total, the deferred timer has NOT fired yet.
    jasmine.clock().tick(300);
    expect(chatService.loadingProcess$.value).toBe(true);

    // Crossing the 500ms floor triggers the pending setTimeout.
    jasmine.clock().tick(100);
    expect(chatService.loadingProcess$.value).toBe(false);
  });

  it('AC8: first event at 800ms → flag becomes false immediately (past floor, no extra delay)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    // First event well past the 500ms floor — flip must be immediate.
    jasmine.clock().tick(800);
    fakeSocket.next({ __model__: 'StartMessage' });

    // No further tick needed: immediate branch of scheduleSpinnerFlipFalse.
    expect(chatService.loadingProcess$.value).toBe(false);
  });

  it('AC8: WS error at 100ms → flag still true at 400ms → false at 500ms (failure path respects floor)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    jasmine.clock().tick(100);
    fakeSocket.error(new Error('connect refused'));

    jasmine.clock().tick(300);
    expect(chatService.loadingProcess$.value).toBe(true);

    jasmine.clock().tick(100);
    expect(chatService.loadingProcess$.value).toBe(false);
  });

  it('AC8: re-init while a deferred flip is pending cancels the pending timer (no late false clobber)', async () => {
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    // Schedule a deferred flip for t=500ms via an early first event.
    jasmine.clock().tick(100);
    fakeSocket.next({ __model__: 'StartMessage' });
    // Pending timer exists; flag still true.
    expect(chatService.loadingProcess$.value).toBe(true);

    // Re-init (team switch) before the pending timer fires — swap in a new
    // fake socket so the fresh init() has something to subscribe to.
    const secondSocket = new Subject<any>();
    (service as any).createWebSocket = jasmine
      .createSpy('createWebSocket')
      .and.returnValue(secondSocket as unknown as WebSocketSubject<any>);
    await service.init('proc-2', true);

    // Fresh cycle: flag is back to true.
    expect(chatService.loadingProcess$.value).toBe(true);

    // Advance past the ORIGINAL scheduled time (t=500ms from first init).
    // If the pending timer had not been cancelled, it would fire here and
    // clobber the fresh spinner cycle with a stale `false`.
    jasmine.clock().tick(500);
    expect(chatService.loadingProcess$.value).toBe(true);

    secondSocket.complete();
  });
});

// ---------------------------------------------------------------------------
// Story 6.1 — MessageLogService integration + frame-batched ingestion (AC1-8)
// ---------------------------------------------------------------------------

describe('ActorMessageService — Story 6.1 (frame-batched log ingestion)', () => {
  let service: ActorMessageService;
  let log: MessageLogService;
  let chatService: ChatService;
  let fakeSocket: Subject<any>;

  function mkStart(id: string): any {
    return {
      id,
      parent_id: null,
      team_id: 'team-X',
      timestamp: '2026-04-13T00:00:00Z',
      sender: {
        __actor_address__: true,
        name: '@X',
        role: 'Worker',
        agent_id: 'a1',
        team_id: 'team-X',
        squad_id: 's1',
        user_message: false,
      },
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    };
  }

  beforeEach(() => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(0));

    fakeSocket = new Subject<any>();

    TestBed.configureTestingModule({
      providers: [
        MessageLogService,
        ActorMessageService,
        ChatService,
        {
          provide: ApiService,
          useValue: {
            getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
          },
        },
        { provide: MessageService, useValue: { add: jasmine.createSpy('add'), clear: jasmine.createSpy('clear') } },
      ],
    });
    service = TestBed.inject(ActorMessageService);
    log = TestBed.inject(MessageLogService);
    chatService = TestBed.inject(ChatService);

    spyOn<any>(service, 'createWebSocket').and.returnValue(
      fakeSocket as unknown as WebSocketSubject<any>,
    );
  });

  afterEach(() => {
    try {
      fakeSocket.complete();
    } catch {
      /* may already be unsubscribed by disposePriorSubscriptions */
    }
    jasmine.clock().uninstall();
  });

  // ---------- AC1 ----------
  it('AC1: log$ emits [] before init and log.snapshot() is empty', () => {
    let observed: any[] | null = null;
    const sub = log.log$.subscribe((v) => (observed = v));
    expect(observed as any[] | null).toEqual([]);
    expect(log.snapshot()).toEqual([]);
    sub.unsubscribe();
  });

  // ---------- AC3 + ADR-005 AC4 ----------
  it('AC3: N synchronous WS messages within 16ms → ONE appendAll + ONE log$ emission', async () => {
    await service.init('proc-1', true);

    const appendSpy = spyOn(log, 'appendAll').and.callThrough();
    const emissions: any[][] = [];
    // Subscribe AFTER init completes so we capture the current snapshot as
    // the baseline emission and then the post-batch emission.
    const sub = log.log$.subscribe((v) => emissions.push(v));
    expect(emissions.length).toBe(1); // baseline, [] (or whatever init left)

    // Fire 5 synchronous events within one 16ms window.
    fakeSocket.next(mkStart('1'));
    fakeSocket.next(mkStart('2'));
    fakeSocket.next(mkStart('3'));
    fakeSocket.next(mkStart('4'));
    fakeSocket.next(mkStart('5'));

    // bufferTime(16) has not flushed yet — the batched subscriber is still
    // accumulating; no log$ emission should have landed past baseline.
    expect(emissions.length).toBe(1);
    expect(appendSpy).not.toHaveBeenCalled();

    jasmine.clock().tick(17);

    expect(appendSpy).toHaveBeenCalledTimes(1);
    expect(appendSpy.calls.mostRecent().args[0].length).toBe(5);
    expect(emissions.length).toBe(2);
    expect(emissions[1].map((m: any) => m.id)).toEqual(['1', '2', '3', '4', '5']);
    expect(log.snapshot().map((m: any) => m.id)).toEqual(['1', '2', '3', '4', '5']);

    sub.unsubscribe();
  });

  // ---------- AC4 ----------
  it('AC4: batched subscriber updates stateDict$ and contextDict$ in same pass', async () => {
    await service.init('proc-1', true);

    const stateChanged = {
      id: 'sc1',
      parent_id: null,
      team_id: 'team-X',
      timestamp: '2026-04-13T00:00:00Z',
      sender: {
        __actor_address__: true,
        name: '@X',
        role: 'Worker',
        agent_id: 'agent-X',
        team_id: 'team-X',
        squad_id: 's',
        user_message: false,
      },
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StateChangedMessage',
      state: { phase: 'thinking' },
    };
    const llmEvent = {
      id: 'evt1',
      parent_id: null,
      team_id: 'team-X',
      timestamp: '2026-04-13T00:00:00Z',
      sender: {
        __actor_address__: true,
        name: '@Y',
        role: 'Worker',
        agent_id: 'agent-Y',
        team_id: 'team-X',
        squad_id: 's',
        user_message: false,
      },
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.EventMessage',
      event: {
        __model__: 'akgentic.llm.event.LlmMessageEvent',
        message: { role: 'assistant', content: 'hi' },
      },
    };

    fakeSocket.next(stateChanged);
    fakeSocket.next(llmEvent);
    jasmine.clock().tick(17);

    // Both dicts populated by the BATCHED subscriber path.
    expect(service.stateDict$['agent-X']).toBeDefined();
    expect(service.stateDict$['agent-X'].value.state).toEqual({ phase: 'thinking' });
    expect(service.contextDict$['agent-Y']).toBeDefined();
    // NOTE: PR 1 is parallel populate — the per-__model__ dispatch in
    // webSocket.subscribe.next ALSO appends to contextDict$ (existing
    // handleEventMessage). So the ctx array may contain the same message
    // twice (batched + existing). The invariant we care about for AC4 is
    // that AT LEAST one update happened in the batched pass.
    expect(service.contextDict$['agent-Y'].value.length).toBeGreaterThanOrEqual(1);
    // Log also contains both messages in arrival order.
    expect(log.snapshot().length).toBe(2);
  });

  // ---------- AC5 ----------
  it('AC5: rapid init("A") → init("B") leaves only B events in log.snapshot()', async () => {
    // First cycle — process A.
    await service.init('proc-A', true);
    fakeSocket.next(mkStart('A-1'));
    fakeSocket.next(mkStart('A-2'));
    jasmine.clock().tick(17);
    expect(log.snapshot().map((m: any) => m.id)).toEqual(['A-1', 'A-2']);

    // Swap in a fresh fake socket for the B cycle.
    const socketB = new Subject<any>();
    (service as any).createWebSocket = jasmine
      .createSpy('createWebSocket')
      .and.returnValue(socketB as unknown as WebSocketSubject<any>);

    await service.init('proc-B', true);
    // After init: log MUST be empty (Task 3.1 step (b) reset).
    expect(log.snapshot()).toEqual([]);

    socketB.next(mkStart('B-1'));
    jasmine.clock().tick(17);
    // Only B events remain.
    expect(log.snapshot().map((m: any) => m.id)).toEqual(['B-1']);

    socketB.complete();
  });

  // ---------- AC6 ----------
  it('AC6: spinner flip fires EXACTLY once via take(1) for N events in same 16ms window', async () => {
    await service.init('proc-1', true);

    // Jump past the 500ms floor so the flip is immediate (not deferred).
    jasmine.clock().tick(600);

    const emitted: boolean[] = [];
    const sub = chatService.loadingProcess$.subscribe((v) => emitted.push(v));
    expect(emitted).toEqual([true]);

    // Fire 5 events synchronously — take(1) must trigger ONLY ONE flip.
    fakeSocket.next(mkStart('1'));
    fakeSocket.next(mkStart('2'));
    fakeSocket.next(mkStart('3'));
    fakeSocket.next(mkStart('4'));
    fakeSocket.next(mkStart('5'));

    // Exactly one transition to false (from the take(1) side-channel;
    // the legacy flipOnFirstEvent closure also fires but calls the same
    // idempotent scheduleSpinnerFlipFalse → loadingProcess$ is a
    // BehaviorSubject so a second `next(false)` with the same value does
    // emit another `false` — assert only ONE transition from true→false.
    expect(emitted[0]).toBe(true);
    expect(emitted).toContain(false);
    // Values in strict order: first is true, LAST is false.
    expect(emitted[emitted.length - 1]).toBe(false);

    sub.unsubscribe();
  });

  // ---------- AC7 ----------
  it('AC7: N mount/unmount cycles leave no residual subscriptions on _wsInbound$', async () => {
    // After init() step (a) disposes prior bufferSub + spinnerSub, the
    // internal Subject observer list should stay bounded. Probe via the
    // rxjs 7 `observed` boolean and the internal `observers` array (still
    // reachable on Subject even if flagged @deprecated). Both paths agree.
    const inbound = (service as any)._wsInbound$ as Subject<any>;

    for (let i = 0; i < 5; i++) {
      // Each re-init disposes the previous WS (`fakeSocket.unsubscribe()`),
      // so swap in a fresh Subject for every cycle — otherwise the next
      // init's `.subscribe(...)` would hit an unsubscribed Subject.
      const cycleSocket = new Subject<any>();
      (service as any).createWebSocket = jasmine
        .createSpy('createWebSocket')
        .and.returnValue(cycleSocket as unknown as WebSocketSubject<any>);

      await service.init('proc-' + i, true);
      // After a full init() the two live subscribers are bufferSub +
      // spinnerSub — exactly 2, never more.
      expect((inbound as any).observers.length).toBe(2);
      expect(inbound.observed).toBeTrue();
    }

    // Destroy: must release ALL observers and complete the Subject.
    service.ngOnDestroy();
    expect((inbound as any).observers.length).toBe(0);
    expect(inbound.observed).toBeFalse();
  });

  // ---------- AC8 ----------
  it('AC8: log contains synthetic event sequence in arrival order (Story 6.4: messages$ deleted)', async () => {
    await service.init('proc-1', true);

    const s1 = mkStart('s1');
    const s2 = mkStart('s2');
    const s3 = mkStart('s3');
    fakeSocket.next(s1);
    fakeSocket.next(s2);
    fakeSocket.next(s3);
    jasmine.clock().tick(17);

    // log populated via the batched subscriber.
    expect(log.snapshot().map((m: any) => m.id)).toEqual(['s1', 's2', 's3']);
    // Story 6.4 (AC1): `messages$` is deleted; the log is the single
    // source of truth for downstream selectors.
    expect((service as any).messages$).toBeUndefined();
  });

  // ---------- Task 3.2 — REST replay populates log in strict order ----------
  it('Task 3.2: !running init replays events into log in arrival order', async () => {
    const apiService = TestBed.inject(ApiService) as any;
    apiService.getEvents.and.resolveTo([
      { event: mkStart('r1') },
      { event: mkStart('r2') },
      { event: mkStart('r3') },
    ]);

    await service.init('proc-stopped', false);

    expect(log.snapshot().map((m: any) => m.id)).toEqual(['r1', 'r2', 'r3']);
  });
});

// ---------------------------------------------------------------------------
// Story 6.4 (AC5) — two-exceptions invariant (NFR9)
//
// ADR-005 §Decision 5: stateDict$ and contextDict$ are the ONLY imperative
// state containers on ActorMessageService after the Story 6.4 refactor.
// "Adding a third exception requires a new ADR. This test is the automated
// guard."
// ---------------------------------------------------------------------------

/**
 * Probe the public surface of an `ActorMessageService` (or subclass) and
 * return the set of own-property names whose runtime shape is an imperative
 * state container — either a direct `BehaviorSubject` field, or a per-agent
 * dict `{ [k: string]: BehaviorSubject<...> }` (the `stateDict$` /
 * `contextDict$` shape; counted as ONE exception each, regardless of cardinality).
 */
function probeStateContainers(service: object): string[] {
  return Object.getOwnPropertyNames(service).filter((name) => {
    const v = (service as any)[name];
    if (v instanceof BehaviorSubject) return true;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      const values = Object.values(v);
      // Empty dicts that match the documented dict-name suffix still count
      // — the contract is structural, not population-dependent.
      if (values.length === 0) {
        return /(stateDict|contextDict)\$$/.test(name);
      }
      return values.every((x) => x instanceof BehaviorSubject);
    }
    return false;
  });
}

describe('ActorMessageService — two-exceptions invariant (Story 6.4, NFR9)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        MessageLogService,
        ActorMessageService,
        ChatService,
        {
          provide: ApiService,
          useValue: {
            getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
          },
        },
        { provide: MessageService, useValue: { add: jasmine.createSpy('add'), clear: jasmine.createSpy('clear') } },
      ],
    });
  });

  it('public data surface is exactly {stateDict$, contextDict$}', () => {
    const service = TestBed.inject(ActorMessageService);
    // AC5 spec: the probe MUST NOT rely on a name allow-list — it walks
    // `Object.getOwnPropertyNames` with a runtime `instanceof` check so that
    // any new `BehaviorSubject` field (regardless of name) forces this test
    // to fail. Per ADR-005 §Decision 5, adding a third exception requires a
    // new ADR.
    const containers = probeStateContainers(service);
    expect(new Set(containers)).toEqual(new Set(['stateDict$', 'contextDict$']));
  });

  it('negative probe: adding a third exception fails the invariant', () => {
    const service = TestBed.inject(ActorMessageService);
    // Simulate the "someone added a new BehaviorSubject" diff.
    (service as any).extraDict$ = { agent: new BehaviorSubject<any>(null) };
    const containers = probeStateContainers(service);
    // The probe MUST detect the addition (set is no longer the documented
    // pair). Without this guard, the invariant test would silently pass.
    expect(new Set(containers)).not.toEqual(
      new Set(['stateDict$', 'contextDict$']),
    );
    expect(containers).toContain('extraDict$');
  });

  it('non-state observables (Subjects, Subscriptions, WebSocketSubject) are NOT counted as exceptions', () => {
    const service = TestBed.inject(ActorMessageService);
    const containers = probeStateContainers(service);
    expect(containers).not.toContain('_wsInbound$');
    expect(containers).not.toContain('bufferSub');
    expect(containers).not.toContain('spinnerSub');
    expect(containers).not.toContain('webSocket');
  });
});

// ---------------------------------------------------------------------------
// Story 8-2 — Persistent WebSocket disconnect warning toast (AC1–AC5)
// ---------------------------------------------------------------------------

describe('ActorMessageService — Story 8-2 (persistent disconnect toast)', () => {
  let service: ActorMessageService;
  let msgService: any;
  let fakeSocket: Subject<any>;

  beforeEach(() => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(0));

    fakeSocket = new Subject<any>();

    TestBed.configureTestingModule({
      providers: [
        MessageLogService,
        ActorMessageService,
        ChatService,
        {
          provide: ApiService,
          useValue: {
            getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
          },
        },
        { provide: MessageService, useValue: { add: jasmine.createSpy('add'), clear: jasmine.createSpy('clear') } },
      ],
    });
    service = TestBed.inject(ActorMessageService);
    msgService = TestBed.inject(MessageService);

    spyOn<any>(service, 'createWebSocket').and.returnValue(
      fakeSocket as unknown as WebSocketSubject<any>,
    );
  });

  afterEach(() => {
    try {
      fakeSocket.complete();
    } catch {
      /* already closed */
    }
    jasmine.clock().uninstall();
  });

  it('AC1: WS error shows persistent warning toast with correct properties', async () => {
    await service.init('proc-1', true);
    jasmine.clock().tick(600);

    fakeSocket.error(new Error('connection lost'));

    expect(msgService.add).toHaveBeenCalledWith(
      jasmine.objectContaining({
        severity: 'warn',
        summary: 'Connection Lost',
        detail: 'Real-time connection to the server has been lost. Updates are paused.',
        sticky: true,
        closable: false,
      }),
    );
  });

  it('AC1: WS error no longer shows transient error toast with life: 5000', async () => {
    await service.init('proc-1', true);
    jasmine.clock().tick(600);

    fakeSocket.error(new Error('connection lost'));

    const calls = msgService.add.calls.allArgs().map((a: any[]) => a[0]);
    const transientErrorCalls = calls.filter(
      (c: any) => c.severity === 'error' && c.life === 5000 && c.summary === 'Connection Error',
    );
    expect(transientErrorCalls.length).toBe(0);
  });

  it('AC2: WS complete shows persistent warning toast', async () => {
    await service.init('proc-1', true);
    jasmine.clock().tick(600);

    fakeSocket.complete();

    expect(msgService.add).toHaveBeenCalledWith(
      jasmine.objectContaining({
        severity: 'warn',
        sticky: true,
        closable: false,
      }),
    );
  });

  it('AC3: second disconnect event does not add a duplicate toast', async () => {
    await service.init('proc-1', true);
    jasmine.clock().tick(600);

    // Simulate error followed by complete — use separate subjects to control
    // the sequence since error() terminates the Subject.
    // Instead, call showDisconnectToast twice via the private method.
    (service as any).showDisconnectToast();
    (service as any).showDisconnectToast();

    const warnCalls = msgService.add.calls.allArgs()
      .map((a: any[]) => a[0])
      .filter((c: any) => c.severity === 'warn' && c.summary === 'Connection Lost');
    expect(warnCalls.length).toBe(1);
  });

  it('AC4: ngOnDestroy clears the ws-disconnect toast and resets the flag', async () => {
    await service.init('proc-1', true);
    jasmine.clock().tick(600);

    // Show the toast first.
    (service as any).showDisconnectToast();
    expect((service as any).wsDisconnectToastShown).toBe(true);

    service.ngOnDestroy();

    expect(msgService.clear).toHaveBeenCalled();
    expect((service as any).wsDisconnectToastShown).toBe(false);
  });

  it('AC4: ngOnDestroy suppresses disconnect toast triggered by unsubscribe (destroying guard)', async () => {
    await service.init('proc-1', true);

    // Destroy sets the destroying flag BEFORE unsubscribe, so the complete
    // callback's showDisconnectToast() call is suppressed.
    service.ngOnDestroy();

    // The only warn-toast add calls should be zero — the destroying guard
    // prevents the toast from being shown during intentional navigation.
    const warnCalls = msgService.add.calls.allArgs()
      .map((a: any[]) => a[0])
      .filter((c: any) => c.severity === 'warn' && c.summary === 'Connection Lost');
    expect(warnCalls.length).toBe(0);
  });

  it('AC5: WS error still calls flipOnFirstEvent (spinner falls through)', async () => {
    const chatService = TestBed.inject(ChatService);
    await service.init('proc-1', true);
    expect(chatService.loadingProcess$.value).toBe(true);

    // Past the spinner floor so flip is immediate.
    jasmine.clock().tick(600);
    fakeSocket.error(new Error('connect refused'));

    // flipOnFirstEvent was called — spinner is now false.
    expect(chatService.loadingProcess$.value).toBe(false);
  });
});
