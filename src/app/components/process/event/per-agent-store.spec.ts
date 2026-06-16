import { TestBed } from '@angular/core/testing';
import { Observable } from 'rxjs';

import { AkgenticMessage } from '../../../protocol/message.types';
import { MessageLogService } from './message-log.service';
import {
  AgentId,
  appendWith,
  firstWith,
  PerAgentSpec,
  PerAgentStoreRegistry,
  replaceWith,
} from './per-agent-store';

// ---------------------------------------------------------------------
// Fixtures
//
// Mirrors `system-prompt.selector.spec.ts`: module-scope builders producing
// minimal `AkgenticMessage`-shaped objects. THIS story registers no real
// instance, so fixtures use SYNTHETIC `__model__`s chosen so a test `match`
// passes; no real type guard is needed.
// ---------------------------------------------------------------------

const MATCH_MODEL = 'test.PerAgentMatch';
const UNRELATED_MODEL = 'test.Unrelated';

function sender(agentId: string) {
  return {
    __actor_address__: true as const,
    agent_id: agentId,
    name: '@' + agentId,
    role: 'Agent',
    squad_id: 's1',
    user_message: false,
  };
}

let msgCounter = 0;

/** A matching message for `agentId` carrying a projected `value` + `model`. */
function makeMsg(
  agentId: string,
  value: string,
  model = MATCH_MODEL,
): AkgenticMessage {
  return {
    id: 'm-' + agentId + '-' + msgCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: value,
    __model__: model,
  } as unknown as AkgenticMessage;
}

/** A message whose `__model__` does not match the test spec. */
function makeUnrelated(): AkgenticMessage {
  return makeMsg('A', 'noise', UNRELATED_MODEL);
}

/** A matching message with NO `sender.agent_id` (default key resolves to undefined). */
function makeMsgNoSender(value: string): AkgenticMessage {
  return {
    id: 'ns-' + msgCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: { __actor_address__: true, name: '?', role: 'Agent', squad_id: 's1' },
    display_type: 'other',
    content: value,
    __model__: MATCH_MODEL,
  } as unknown as AkgenticMessage;
}

const matchModel = (msg: AkgenticMessage): boolean =>
  msg.__model__ === MATCH_MODEL;

/** Spec under test: latest `content` per agent (replace semantics). */
function replaceSpec(): PerAgentSpec<string> {
  return {
    name: 'latest-content',
    match: matchModel,
    reduce: replaceWith((m) => (m.content ?? '') as string),
  };
}

interface Counted {
  spec: PerAgentSpec<string>;
  matchCalls: () => number;
  reduceCalls: () => number;
}

/** A replace-spec instrumented to count `match` / `reduce` invocations (AC2). */
function countedSpec(): Counted {
  let matchCalls = 0;
  let reduceCalls = 0;
  const inner = replaceWith<string>((m) => (m.content ?? '') as string);
  return {
    spec: {
      name: 'counted',
      match: (m) => {
        matchCalls++;
        return matchModel(m);
      },
      reduce: (prev, m) => {
        reduceCalls++;
        return inner(prev, m);
      },
    },
    matchCalls: () => matchCalls,
    reduceCalls: () => reduceCalls,
  };
}

function setup(): {
  log: MessageLogService;
  registry: PerAgentStoreRegistry;
} {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [MessageLogService, PerAgentStoreRegistry],
  });
  return {
    log: TestBed.inject(MessageLogService),
    registry: TestBed.inject(PerAgentStoreRegistry),
  };
}

// ---------------------------------------------------------------------
// AC5 — reducer factories (pure, no TestBed)
// ---------------------------------------------------------------------

describe('reducer factories (pure)', () => {
  it('replaceWith is latest-wins', () => {
    const r = replaceWith<string>((m) => (m.content ?? '') as string);
    const v1 = r('prev', makeMsg('A', 'v1'));
    const v2 = r(v1, makeMsg('A', 'v2'));
    expect(v1).toBe('v1');
    expect(v2).toBe('v2');
  });

  it('appendWith accumulates and returns a FRESH array each time', () => {
    const r = appendWith<string>((m) => (m.content ?? '') as string);
    const a1 = r(undefined, makeMsg('A', 'x'));
    const a2 = r(a1, makeMsg('A', 'y'));
    expect(a1).toEqual(['x']);
    expect(a2).toEqual(['x', 'y']);
    expect(a2).not.toBe(a1); // fresh reference, not in-place mutation
    expect(a1).toEqual(['x']); // prior array untouched
  });

  it('firstWith keeps the first value; later matches do not overwrite', () => {
    const r = firstWith<string>((m) => (m.content ?? '') as string);
    const f1 = r(undefined, makeMsg('A', 'first'));
    const f2 = r(f1, makeMsg('A', 'second'));
    expect(f1).toBe('first');
    expect(f2).toBe('first');
  });
});

// ---------------------------------------------------------------------
// AC1, AC8 — replace reduce + per-agent read + default key
// ---------------------------------------------------------------------

describe('PerAgentStore — replace reduce + read (AC1, AC8)', () => {
  it('AC1 forAgent yields the latest value; snapshot mirrors it; unmatched agent is undefined', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    log.appendAll([makeMsg('A', 'v1'), makeMsg('A', 'v2')]);

    let latest: string | undefined = 'unset';
    const sub = store.forAgent('A').subscribe((v) => (latest = v));
    expect(latest).toBe('v2');
    expect(store.snapshot('A')).toBe('v2');

    let other: string | undefined = 'unset';
    const sub2 = store.forAgent('Z').subscribe((v) => (other = v));
    expect(other).toBeUndefined();
    expect(store.snapshot('Z')).toBeUndefined();

    sub.unsubscribe();
    sub2.unsubscribe();
  });

  it('AC8 default key is sender.agent_id; a message missing it is skipped (no undefined key)', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    log.appendAll([makeMsg('A', 'a'), makeMsgNoSender('orphan')]);

    expect(store.snapshot('A')).toBe('a');
    const all = collectAll(store.all$);
    const map = all[all.length - 1];
    expect(map.has(undefined as unknown as AgentId)).toBe(false);
    expect([...map.keys()]).toEqual(['A']);
  });

  it('AC8 a spec MAY override key to read a different field', () => {
    const { log, registry } = setup();
    const store = registry.register<string>({
      name: 'keyed-by-team',
      match: matchModel,
      key: (m) => m.team_id,
      reduce: replaceWith((m) => (m.content ?? '') as string),
    });

    log.append(makeMsg('A', 'hello'));
    expect(store.snapshot('team-1')).toBe('hello');
    expect(store.snapshot('A')).toBeUndefined();
  });
});

// ---------------------------------------------------------------------
// AC2 — O(Δ) cursor: only the new tail is walked
// ---------------------------------------------------------------------

describe('PerAgentStoreRegistry — O(Δ) cursor (AC2)', () => {
  it('processes only log.slice(processedCount); cursor advances by the tail length', () => {
    const { log, registry } = setup();
    const counted = countedSpec();
    registry.register(counted.spec);

    // Frame 1: M matching messages.
    log.appendAll([makeMsg('A', '1'), makeMsg('B', '2'), makeMsg('A', '3')]);
    expect(registry.cursor).toBe(3);
    expect(counted.reduceCalls()).toBe(3);
    const matchAfterFrame1 = counted.matchCalls();

    // Frame 2: a tail of N=2. Only the 2 new messages are reduced/matched.
    log.appendAll([makeMsg('A', '4'), makeMsg('C', '5')]);
    expect(registry.cursor).toBe(5);
    expect(counted.reduceCalls()).toBe(5); // +2, NOT +5 (no re-fold of prefix)
    expect(counted.matchCalls() - matchAfterFrame1).toBe(2);
  });

  it('a no-op append (dedup → no new tail) does not re-walk the prefix', () => {
    const { log, registry } = setup();
    const counted = countedSpec();
    registry.register(counted.spec);

    const m = makeMsg('A', '1');
    log.append(m);
    const reduceAfter = counted.reduceCalls();
    const matchAfter = counted.matchCalls();

    // Same id → MessageLogService dedup drops it → no log$ emission → no walk.
    log.append(m);
    expect(registry.cursor).toBe(1);
    expect(counted.reduceCalls()).toBe(reduceAfter);
    expect(counted.matchCalls()).toBe(matchAfter);
  });
});

// ---------------------------------------------------------------------
// AC3 — automatic reset on log shrink
// ---------------------------------------------------------------------

describe('PerAgentStoreRegistry — reset on shrink (AC3)', () => {
  it('clears all maps, resets cursor to 0, re-folds the smaller log — no per-store clear', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    log.appendAll([makeMsg('A', 'a'), makeMsg('B', 'b')]);
    expect(store.snapshot('A')).toBe('a');
    expect(registry.cursor).toBe(2);

    const seen: (string | undefined)[] = [];
    const sub = store.forAgent('A').subscribe((v) => seen.push(v));
    expect(seen[seen.length - 1]).toBe('a');

    log.reset();
    expect(registry.cursor).toBe(0);
    expect(store.snapshot('A')).toBeUndefined();
    expect(seen[seen.length - 1]).toBeUndefined(); // forAgent re-emitted undefined

    // Re-append a smaller log → re-folds correctly from the start.
    log.appendAll([makeMsg('A', 'a2')]);
    expect(registry.cursor).toBe(1);
    expect(store.snapshot('A')).toBe('a2');
    expect(seen[seen.length - 1]).toBe('a2');
    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// AC4 — replay / live parity (REST appendAll vs WS per-message append)
// ---------------------------------------------------------------------

describe('PerAgentStore — replay/live parity (AC4)', () => {
  function fixture(): AkgenticMessage[] {
    return [
      makeMsg('A', 'a1'),
      makeMsg('B', 'b1'),
      makeMsg('A', 'a2'),
      makeUnrelated(),
      makeMsg('C', 'c1'),
    ];
  }

  function ingest(mode: 'rest' | 'ws'): {
    a: string | undefined;
    b: string | undefined;
    c: string | undefined;
    all: Record<string, string>;
  } {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());
    const fx = fixture();
    if (mode === 'rest') {
      log.appendAll(fx);
    } else {
      for (const m of fx) log.append(m);
    }
    const all = collectAll(store.all$);
    const map = all[all.length - 1];
    return {
      a: store.snapshot('A'),
      b: store.snapshot('B'),
      c: store.snapshot('C'),
      all: Object.fromEntries(map),
    };
  }

  it('REST batch and WS sequence produce identical forAgent/snapshot/all$ results', () => {
    const rest = ingest('rest');
    const ws = ingest('ws');
    expect(JSON.stringify(rest)).toBe(JSON.stringify(ws));
    expect(rest.a).toBe('a2'); // latest-wins folds identically
    expect(rest.all).toEqual({ A: 'a2', B: 'b1', C: 'c1' });
  });
});

// ---------------------------------------------------------------------
// AC5 (engine) — non-match / undefined-key passthrough never throws/partial
// ---------------------------------------------------------------------

describe('PerAgentStore — passthrough safety (AC5)', () => {
  it('a non-matching message and an undefined-keyed message are silently skipped', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    expect(() =>
      log.appendAll([
        makeUnrelated(), // does not match → skipped
        makeMsgNoSender('x'), // matches but key undefined → skipped
        makeMsg('A', 'kept'),
      ]),
    ).not.toThrow();

    expect(store.snapshot('A')).toBe('kept');
    const all = collectAll(store.all$);
    expect([...all[all.length - 1].keys()]).toEqual(['A']);
  });
});

// ---------------------------------------------------------------------
// AC6 — all$ coalescing + distinctUntilChanged
// ---------------------------------------------------------------------

describe('PerAgentStore — all$ coalescing (AC6)', () => {
  it('several agents changing in one frame → exactly ONE all$ emission carrying all of them', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    const frames: ReadonlyMap<AgentId, string>[] = [];
    const sub = store.all$.subscribe((m) => frames.push(m));
    const baseline = frames.length; // initial empty-map emission

    log.appendAll([makeMsg('A', 'a'), makeMsg('B', 'b'), makeMsg('C', 'c')]);

    // One frame → one new emission (not one per changed agent).
    expect(frames.length - baseline).toBe(1);
    const last = frames[frames.length - 1];
    expect(Object.fromEntries(last)).toEqual({ A: 'a', B: 'b', C: 'c' });
    sub.unsubscribe();
  });

  it('a frame in which no registered agent changed does NOT re-emit (distinctUntilChanged)', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    log.append(makeMsg('A', 'a'));
    const frames: ReadonlyMap<AgentId, string>[] = [];
    const sub = store.all$.subscribe((m) => frames.push(m));
    const before = frames.length;

    // A log tick that matches no spec → bucket unchanged → no all$ re-emit.
    log.append(makeUnrelated());
    expect(frames.length).toBe(before);
    sub.unsubscribe();
  });

  it('all$ hands out a FRESH map reference on change (OnPush safety)', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    const frames: ReadonlyMap<AgentId, string>[] = [];
    const sub = store.all$.subscribe((m) => frames.push(m));
    log.append(makeMsg('A', 'a'));
    log.append(makeMsg('B', 'b'));
    expect(frames[frames.length - 1]).not.toBe(frames[frames.length - 2]);
    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// AC7 — forAgent stream semantics (shareReplay, distinctUntilChanged, refCount)
// ---------------------------------------------------------------------

describe('PerAgentStore — forAgent semantics (AC7)', () => {
  it('a late subscriber receives the current value immediately (shareReplay(1))', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());
    log.append(makeMsg('A', 'a'));

    let received: string | undefined = 'unset';
    const sub = store.forAgent('A').subscribe((v) => (received = v));
    expect(received).toBe('a');
    sub.unsubscribe();
  });

  it('a no-op frame for that agent does not re-emit (distinctUntilChanged)', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());

    const emissions: (string | undefined)[] = [];
    const sub = store.forAgent('A').subscribe((v) => emissions.push(v));

    log.append(makeMsg('A', 'a'));
    log.append(makeUnrelated()); // does not touch A
    log.append(makeMsg('B', 'b')); // changes B, not A

    expect(emissions).toEqual([undefined, 'a']); // no extra A re-emission
    sub.unsubscribe();
  });

  it('the shared source is released when the last forAgent subscriber unsubscribes (refCount)', () => {
    const { log, registry } = setup();
    const store = registry.register(replaceSpec());
    log.append(makeMsg('A', 'a'));

    const stream = store.forAgent('A');
    const s1 = stream.subscribe();
    const s2 = stream.subscribe();
    // Two subscribers share one source; releasing both tears it down. With
    // refCount, a fresh subscribe re-primes from the current value cleanly.
    s1.unsubscribe();
    s2.unsubscribe();

    let revived: string | undefined = 'unset';
    const s3 = stream.subscribe((v) => (revived = v));
    expect(revived).toBe('a'); // re-subscribe after refCount release still works
    s3.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// Test helper: synchronously collect all$ emissions during a subscription.
// ---------------------------------------------------------------------

function collectAll<V>(
  all$: Observable<ReadonlyMap<AgentId, V>>,
): ReadonlyMap<AgentId, V>[] {
  const out: ReadonlyMap<AgentId, V>[] = [];
  const sub = all$.subscribe((m) => out.push(m));
  sub.unsubscribe();
  return out;
}
