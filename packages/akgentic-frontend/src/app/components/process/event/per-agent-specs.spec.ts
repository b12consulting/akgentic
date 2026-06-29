import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';

import {
  AkgenticMessage,
  LlmContextCompactedEvent,
} from '../../../protocol/message.types';
import { ApiService } from '../../../core/http/api.service';
import { IngestionService } from './ingestion.service';
import { MessageLogService } from './message-log.service';
import { PerAgentStore, PerAgentStoreRegistry } from './per-agent-store';
import {
  AgentTokenUsage,
  contextMatch,
  contextReduce,
  CONVERSATION_SUMMARY_PREFIX,
  foldContextCompaction,
  tokenUsageReduce,
  tokenUsageSpec,
} from './per-agent-specs';

// ---------------------------------------------------------------------
// Fixtures — EventMessage(LlmUsageEvent) envelopes (ADR-022 §Decision 1
// wire JSON). Driven through the REAL registry via MessageLogService so the
// tests exercise the actual `tokenUsageSpec` fold (no mocking).
// ---------------------------------------------------------------------

function sender(agentId: string) {
  return {
    __actor_address__: true as const,
    agent_id: agentId,
    name: '@' + agentId,
    role: 'Worker',
    squad_id: 's1',
    user_message: false,
  };
}

let envCounter = 0;

interface UsageFields {
  run_id?: string;
  model_name?: string;
  provider_name?: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  requests?: number;
}

/** EventMessage envelope carrying an LlmUsageEvent for `agentId`. */
function makeUsageEnvelope(agentId: string, ev: UsageFields): AkgenticMessage {
  return {
    id: 'usage-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: {
      __model__: 'akgentic.llm.event.LlmUsageEvent',
      run_id: ev.run_id ?? 'run-1',
      model_name: ev.model_name ?? 'claude-sonnet',
      provider_name: ev.provider_name ?? 'anthropic',
      input_tokens: ev.input_tokens,
      output_tokens: ev.output_tokens,
      cache_read_tokens: ev.cache_read_tokens ?? 0,
      cache_write_tokens: ev.cache_write_tokens ?? 0,
      requests: ev.requests ?? 1,
    },
  } as unknown as AkgenticMessage;
}

/** An unrelated inner event (must never fold into the tokenUsage store). */
function makeUnrelatedEnvelope(id: string): AkgenticMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender('whoever'),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: { __model__: 'akgentic.llm.event.LlmSystemPromptEvent' },
  } as unknown as AkgenticMessage;
}

// ---------------------------------------------------------------------
// Fixtures — context-store fold (Epic 29 / ADR-010 §4/§8): LlmMessageEvent
// (append source), LlmContextCompactedEvent (fold), LlmContextClearedEvent
// (reset), plus the ModelMessage-shaped entries the fold operates on.
// ---------------------------------------------------------------------

/** A non-system ModelRequest-shaped context entry (one user-prompt part). */
function userEntry(text: string): unknown {
  return { kind: 'request', parts: [{ part_kind: 'user-prompt', content: text }] };
}

/** A system ModelRequest-shaped context entry (one system-prompt part) — the
 *  fold exempts these (mirrors backend `_is_system_message`). */
function systemEntry(text: string): unknown {
  return {
    kind: 'request',
    parts: [{ part_kind: 'system-prompt', dynamic_ref: null, content: text }],
  };
}

/** EventMessage envelope carrying an LlmMessageEvent whose inner `message` is
 *  the given ModelMessage-shaped entry (appended verbatim to the context store). */
function makeMessageEnvelope(agentId: string, message: unknown): AkgenticMessage {
  return {
    id: 'msg-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: { __model__: 'akgentic.llm.event.LlmMessageEvent', message },
  } as unknown as AkgenticMessage;
}

interface CompactionFields {
  summary: string;
  replaced_message_count: number;
  tokens_after?: number | null;
}

/** The inner LlmContextCompactedEvent payload (ADR-010 §3 wire JSON). */
function compactionEvent(f: CompactionFields): LlmContextCompactedEvent {
  return {
    __model__: 'akgentic.llm.event.LlmContextCompactedEvent',
    run_id: null,
    strategy_id: 'sliding-window',
    summary: f.summary,
    replaced_message_count: f.replaced_message_count,
    summarizer_prompt_version: 'v1',
    tokens_before: null,
    tokens_after: f.tokens_after ?? null,
  };
}

/** EventMessage envelope carrying an LlmContextCompactedEvent for `agentId`. */
function makeCompactionEnvelope(
  agentId: string,
  f: CompactionFields,
): AkgenticMessage {
  return {
    id: 'compact-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: compactionEvent(f),
  } as unknown as AkgenticMessage;
}

/** EventMessage envelope carrying an LlmContextClearedEvent for `agentId`. */
function makeClearEnvelope(agentId: string, clearedCount = 0): AkgenticMessage {
  return {
    id: 'clear-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: {
      __model__: 'akgentic.llm.event.LlmContextClearedEvent',
      run_id: null,
      cleared_message_count: clearedCount,
    },
  } as unknown as AkgenticMessage;
}

// ---------------------------------------------------------------------
// TestBed wiring — drive the REAL store via MessageLogService.
// ---------------------------------------------------------------------

function configureBed(): {
  log: MessageLogService;
  service: IngestionService;
} {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      MessageLogService,
      PerAgentStoreRegistry,
      IngestionService,
      {
        provide: ApiService,
        useValue: {
          getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
        },
      },
      {
        provide: MessageService,
        useValue: {
          add: jasmine.createSpy('add'),
          clear: jasmine.createSpy('clear'),
        },
      },
    ],
  });
  return {
    log: TestBed.inject(MessageLogService),
    service: TestBed.inject(IngestionService),
  };
}

function usage(
  store: PerAgentStore<AgentTokenUsage>,
  agentId: string,
): AgentTokenUsage | undefined {
  return store.snapshot(agentId);
}

// ---------------------------------------------------------------------
// AC #6 — tokenUsageSpec reducer correctness (via the real registry fold).
// ---------------------------------------------------------------------

describe('tokenUsageSpec reducer (Epic 26 / ADR-022 §Decision 2-4)', () => {
  it('AC #6 single event: seeds lastContextWindow / totalSent / totalReceived and captures labels', () => {
    const { log, service } = configureBed();
    log.append(
      makeUsageEnvelope('A', {
        run_id: 'run-7',
        model_name: 'claude-opus',
        input_tokens: 12_031,
        output_tokens: 412,
      }),
    );
    expect(usage(service.tokenUsage, 'A')).toEqual({
      lastContextWindow: 12_031,
      lastRunId: 'run-7',
      lastModelName: 'claude-opus',
      totalSent: 12_031,
      totalReceived: 412,
    });
  });

  it('AC #6 multi-event: sums totals and overwrites lastContextWindow with the newest input (even when smaller)', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUsageEnvelope('A', {
        run_id: 'run-1',
        input_tokens: 1000,
        output_tokens: 100,
      }),
      makeUsageEnvelope('A', {
        run_id: 'run-2',
        model_name: 'claude-haiku',
        input_tokens: 200, // newest is SMALLER than the prior 1000
        output_tokens: 50,
      }),
    ]);
    expect(usage(service.tokenUsage, 'A')).toEqual({
      lastContextWindow: 200, // overwritten with the newest input_tokens
      lastRunId: 'run-2',
      lastModelName: 'claude-haiku',
      totalSent: 1200, // 1000 + 200
      totalReceived: 150, // 100 + 50
    });
  });

  it('AC #6 multi-agent: independent entries; A never bleeds into B', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUsageEnvelope('A', { input_tokens: 100, output_tokens: 10 }),
      makeUsageEnvelope('B', { input_tokens: 999, output_tokens: 88 }),
      makeUsageEnvelope('A', { input_tokens: 5, output_tokens: 1 }),
    ]);
    expect(usage(service.tokenUsage, 'A')).toEqual(
      jasmine.objectContaining({ totalSent: 105, totalReceived: 11 }),
    );
    expect(usage(service.tokenUsage, 'B')).toEqual(
      jasmine.objectContaining({ totalSent: 999, totalReceived: 88 }),
    );
  });

  it('AC #6 reset on log-shrink: MessageLogService.reset() clears the store', () => {
    const { log, service } = configureBed();
    log.append(makeUsageEnvelope('A', { input_tokens: 100, output_tokens: 10 }));
    expect(usage(service.tokenUsage, 'A')).toBeTruthy();

    let allAfterReset: ReadonlyMap<string, AgentTokenUsage> | undefined;
    const sub = service.tokenUsage.all$.subscribe((m) => (allAfterReset = m));

    log.reset();

    expect(usage(service.tokenUsage, 'A')).toBeUndefined();
    expect(allAfterReset?.size).toBe(0);
    sub.unsubscribe();
  });

  it('only LlmUsageEvent folds: unrelated inner events leave the store empty', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUnrelatedEnvelope('u1'),
      makeUnrelatedEnvelope('u2'),
    ]);
    expect(usage(service.tokenUsage, 'whoever')).toBeUndefined();
  });

  it('a fresh object is returned on each change (OnPush safety)', () => {
    const { log, service } = configureBed();
    log.append(makeUsageEnvelope('A', { input_tokens: 100, output_tokens: 10 }));
    const first = usage(service.tokenUsage, 'A');
    log.append(makeUsageEnvelope('A', { input_tokens: 5, output_tokens: 1 }));
    const second = usage(service.tokenUsage, 'A');
    expect(first).not.toBe(second);
  });
});

// ---------------------------------------------------------------------
// tokenUsageReduce — direct pure-function unit tests (no registry).
// ---------------------------------------------------------------------

describe('tokenUsageReduce (pure reducer)', () => {
  function envelope(input: number, output: number): AkgenticMessage {
    return makeUsageEnvelope('A', { input_tokens: input, output_tokens: output });
  }

  it('seeds from undefined prev (first event)', () => {
    const next = tokenUsageReduce(undefined, envelope(300, 40));
    expect(next).toEqual({
      lastContextWindow: 300,
      lastRunId: 'run-1',
      lastModelName: 'claude-sonnet',
      totalSent: 300,
      totalReceived: 40,
    });
  });

  it('accumulates onto a prior value', () => {
    const prev: AgentTokenUsage = {
      lastContextWindow: 300,
      lastRunId: 'run-1',
      lastModelName: 'claude-sonnet',
      totalSent: 300,
      totalReceived: 40,
    };
    const next = tokenUsageReduce(prev, envelope(7, 3));
    expect(next).toEqual(
      jasmine.objectContaining({
        lastContextWindow: 7,
        totalSent: 307,
        totalReceived: 43,
      }),
    );
  });

  it('passes prev through for a non-usage message (defensive)', () => {
    const prev: AgentTokenUsage = {
      lastContextWindow: 1,
      lastRunId: 'r',
      lastModelName: 'm',
      totalSent: 1,
      totalReceived: 1,
    };
    expect(tokenUsageReduce(prev, makeUnrelatedEnvelope('x'))).toBe(prev);
  });

  it('coalesces a missing numeric field to 0 (no NaN)', () => {
    const malformed = {
      id: 'm1',
      __model__: 'akgentic.core.messages.orchestrator.EventMessage',
      sender: sender('A'),
      event: {
        __model__: 'akgentic.llm.event.LlmUsageEvent',
        run_id: 'run-x',
        model_name: 'm',
        // input_tokens / output_tokens deliberately absent
      },
    } as unknown as AkgenticMessage;
    const next = tokenUsageReduce(undefined, malformed);
    expect(next).toEqual(
      jasmine.objectContaining({
        lastContextWindow: 0,
        totalSent: 0,
        totalReceived: 0,
      }),
    );
  });
});

// ---------------------------------------------------------------------
// Epic 29 (ADR-010 §4/§8) — context store: fold compaction/clear.
// ---------------------------------------------------------------------

describe('contextMatch (Epic 29 / ADR-010 §4/§8)', () => {
  it('AC #1 admits LlmMessageEvent, LlmContextCompactedEvent, and LlmContextClearedEvent', () => {
    expect(contextMatch(makeMessageEnvelope('A', userEntry('u')))).toBeTrue();
    expect(
      contextMatch(
        makeCompactionEnvelope('A', { summary: 's', replaced_message_count: 1 }),
      ),
    ).toBeTrue();
    expect(contextMatch(makeClearEnvelope('A', 2))).toBeTrue();
  });

  it('AC #1 rejects unrelated inner events (LlmUsageEvent / LlmSystemPromptEvent)', () => {
    expect(
      contextMatch(makeUsageEnvelope('A', { input_tokens: 1, output_tokens: 1 })),
    ).toBeFalse();
    expect(contextMatch(makeUnrelatedEnvelope('u1'))).toBeFalse();
  });
});

describe('contextSpec fold — via the real registry (Epic 29 / ADR-010 §4/§8)', () => {
  function ctx(service: IngestionService, agentId: string): unknown[] | undefined {
    return service.context.snapshot(agentId);
  }

  it('AC #2 append parity: a pure LlmMessageEvent sequence appends inner messages in order', () => {
    const { log, service } = configureBed();
    const m1 = userEntry('one');
    const m2 = userEntry('two');
    log.appendAll([makeMessageEnvelope('A', m1), makeMessageEnvelope('A', m2)]);
    expect(ctx(service, 'A')).toEqual([m1, m2]);
  });

  it('AC #2 returns a FRESH array reference on each append (OnPush safety)', () => {
    const { log, service } = configureBed();
    log.append(makeMessageEnvelope('A', userEntry('u1')));
    const first = ctx(service, 'A');
    log.append(makeMessageEnvelope('A', userEntry('u2')));
    expect(first).not.toBe(ctx(service, 'A'));
  });

  it('AC #3 compaction drops the first replaced_message_count NON-system entries and inserts ONE summary at the fold point', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeMessageEnvelope('A', systemEntry('SYS')),
      makeMessageEnvelope('A', userEntry('u1')),
      makeMessageEnvelope('A', userEntry('u2')),
      makeMessageEnvelope('A', userEntry('u3')),
      makeCompactionEnvelope('A', { summary: 'recap', replaced_message_count: 2 }),
    ]);
    const result = ctx(service, 'A') as any[];
    // Leading system entry exempt; u1 + u2 replaced by exactly one summary; u3 kept.
    expect(result.length).toBe(3);
    expect(result[0]).toEqual(systemEntry('SYS'));
    expect(result[1].parts[0].part_kind).toBe('user-prompt');
    expect(result[1].parts[0].content).toBe(CONVERSATION_SUMMARY_PREFIX + 'recap');
    expect(result[2]).toEqual(userEntry('u3'));
  });

  it('AC #3 a system entry interleaved in the dropped prefix is preserved (never counted/folded)', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeMessageEnvelope('A', userEntry('u1')),
      makeMessageEnvelope('A', systemEntry('SYS')),
      makeMessageEnvelope('A', userEntry('u2')),
      makeMessageEnvelope('A', userEntry('u3')),
      makeCompactionEnvelope('A', { summary: 's', replaced_message_count: 2 }),
    ]);
    const result = ctx(service, 'A') as any[];
    // u1 dropped (summary inserted at the head), SYS exempt, u2 dropped, u3 kept.
    expect(result.length).toBe(3);
    expect(result[0].parts[0].content).toBe(CONVERSATION_SUMMARY_PREFIX + 's');
    expect(result[1]).toEqual(systemEntry('SYS'));
    expect(result[2]).toEqual(userEntry('u3'));
  });

  it('AC #5 clear empties the per-agent array; subsequent messages rebuild it', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeMessageEnvelope('A', userEntry('u1')),
      makeMessageEnvelope('A', userEntry('u2')),
      makeClearEnvelope('A', 2),
    ]);
    expect(ctx(service, 'A')).toEqual([]);
    log.append(makeMessageEnvelope('A', userEntry('fresh')));
    expect(ctx(service, 'A')).toEqual([userEntry('fresh')]);
  });

  it('AC #6 two sequential compactions compose (the first summary is itself foldable); no double-apply', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeMessageEnvelope('A', userEntry('u1')),
      makeMessageEnvelope('A', userEntry('u2')),
      makeMessageEnvelope('A', userEntry('u3')),
      makeCompactionEnvelope('A', { summary: 'first', replaced_message_count: 2 }),
      // After fold 1: [summary(first), u3]. Fold 2 (count 2) replaces BOTH.
      makeCompactionEnvelope('A', { summary: 'second', replaced_message_count: 2 }),
    ]);
    const result = ctx(service, 'A') as any[];
    expect(result.length).toBe(1);
    expect(result[0].parts[0].content).toBe(CONVERSATION_SUMMARY_PREFIX + 'second');
  });

  it('AC #6 ordered-fold parity: batch appendAll equals per-message append', () => {
    const events = (): AkgenticMessage[] => [
      makeMessageEnvelope('A', systemEntry('SYS')),
      makeMessageEnvelope('A', userEntry('u1')),
      makeMessageEnvelope('A', userEntry('u2')),
      makeCompactionEnvelope('A', { summary: 'r', replaced_message_count: 1 }),
      makeMessageEnvelope('A', userEntry('u3')),
    ];
    const batch = configureBed();
    batch.log.appendAll(events());
    const batchResult = ctx(batch.service, 'A');

    const ws = configureBed();
    for (const e of events()) ws.log.append(e);
    expect(ctx(ws.service, 'A')).toEqual(batchResult);
  });
});

describe('foldContextCompaction (pure)', () => {
  it('replaced_message_count <= 0 is a no-op (returns the SAME input array)', () => {
    const msgs = [userEntry('a'), userEntry('b')];
    expect(
      foldContextCompaction(
        msgs,
        compactionEvent({ summary: 's', replaced_message_count: 0 }),
      ),
    ).toBe(msgs);
  });

  it('returns a FRESH array on a real fold; the summary carries the prefixed content', () => {
    const msgs = [userEntry('a'), userEntry('b')];
    const out = foldContextCompaction(
      msgs,
      compactionEvent({ summary: 'recap', replaced_message_count: 1 }),
    ) as any[];
    expect(out).not.toBe(msgs);
    expect(out.length).toBe(2); // summary + b
    expect(out[0].parts[0].content).toBe(CONVERSATION_SUMMARY_PREFIX + 'recap');
    expect(out[1]).toEqual(userEntry('b'));
  });

  it('contextReduce passes a non-event message through unchanged (defensive)', () => {
    const prev = [userEntry('keep')];
    expect(contextReduce(prev, makeUnrelatedEnvelope('x'))).toBe(prev);
  });
});

// ---------------------------------------------------------------------
// Epic 29 (ADR-010 §4/§8) — tokenUsage store: re-point the context window
// on compaction/clear while leaving cumulative totals + labels untouched.
// ---------------------------------------------------------------------

describe('tokenUsageReduce — fold compaction/clear (Epic 29 / ADR-010 §4/§8)', () => {
  const PREV: AgentTokenUsage = {
    lastContextWindow: 30_000,
    lastRunId: 'run-9',
    lastModelName: 'claude-opus',
    totalSent: 50_000,
    totalReceived: 12_000,
  };

  it('AC #7 compaction sets lastContextWindow = tokens_after, leaving totals + labels untouched', () => {
    const next = tokenUsageReduce(
      PREV,
      makeCompactionEnvelope('A', {
        summary: 's',
        replaced_message_count: 3,
        tokens_after: 8_000,
      }),
    );
    expect(next).toEqual({
      lastContextWindow: 8_000,
      lastRunId: 'run-9',
      lastModelName: 'claude-opus',
      totalSent: 50_000,
      totalReceived: 12_000,
    });
  });

  it('AC #7 compaction with null/absent tokens_after leaves the window unchanged (prev passthrough, no NaN)', () => {
    const next = tokenUsageReduce(
      PREV,
      makeCompactionEnvelope('A', { summary: 's', replaced_message_count: 3 }),
    );
    expect(next).toBe(PREV);
  });

  it('AC #7 compaction before any usage seeds from zero (window only, totals stay 0)', () => {
    const next = tokenUsageReduce(
      undefined,
      makeCompactionEnvelope('A', {
        summary: 's',
        replaced_message_count: 1,
        tokens_after: 1_234,
      }),
    );
    expect(next).toEqual({
      lastContextWindow: 1_234,
      lastRunId: '',
      lastModelName: '',
      totalSent: 0,
      totalReceived: 0,
    });
  });

  it('AC #8 clear resets lastContextWindow to 0, keeping totals + labels', () => {
    const next = tokenUsageReduce(PREV, makeClearEnvelope('A', 5));
    expect(next).toEqual({
      lastContextWindow: 0,
      lastRunId: 'run-9',
      lastModelName: 'claude-opus',
      totalSent: 50_000,
      totalReceived: 12_000,
    });
  });
});

describe('tokenUsageSpec — context events via the real registry (Epic 29)', () => {
  it('AC #9 the next LlmUsageEvent after a compaction overrides the estimate with the real provider count', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUsageEnvelope('A', {
        run_id: 'r1',
        input_tokens: 30_000,
        output_tokens: 9_000,
      }),
      makeCompactionEnvelope('A', {
        summary: 's',
        replaced_message_count: 2,
        tokens_after: 6_000,
      }),
    ]);
    // Compaction re-points the window; totals + labels unchanged (not a run).
    expect(usage(service.tokenUsage, 'A')).toEqual({
      lastContextWindow: 6_000,
      lastRunId: 'r1',
      lastModelName: 'claude-sonnet',
      totalSent: 30_000,
      totalReceived: 9_000,
    });
    // The next real usage overrides the window AND accumulates totals.
    log.append(
      makeUsageEnvelope('A', {
        run_id: 'r2',
        input_tokens: 6_500,
        output_tokens: 800,
      }),
    );
    expect(usage(service.tokenUsage, 'A')).toEqual({
      lastContextWindow: 6_500,
      lastRunId: 'r2',
      lastModelName: 'claude-sonnet',
      totalSent: 36_500,
      totalReceived: 9_800,
    });
  });

  it('AC #8 clear via the real registry zeroes the window and keeps the cumulative totals', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUsageEnvelope('A', { input_tokens: 12_000, output_tokens: 3_000 }),
      makeClearEnvelope('A', 4),
    ]);
    expect(usage(service.tokenUsage, 'A')).toEqual(
      jasmine.objectContaining({
        lastContextWindow: 0,
        totalSent: 12_000,
        totalReceived: 3_000,
      }),
    );
  });

  it('AC #7 compaction with null tokens_after does NOT disturb the existing window via the registry', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeUsageEnvelope('A', { input_tokens: 20_000, output_tokens: 4_000 }),
      makeCompactionEnvelope('A', { summary: 's', replaced_message_count: 1 }),
    ]);
    expect(usage(service.tokenUsage, 'A')).toEqual(
      jasmine.objectContaining({
        lastContextWindow: 20_000, // unchanged — defensive no-op
        totalSent: 20_000,
        totalReceived: 4_000,
      }),
    );
  });
});
