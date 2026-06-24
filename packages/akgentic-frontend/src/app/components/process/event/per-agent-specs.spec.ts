import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';

import { AkgenticMessage } from '../../../protocol/message.types';
import { ApiService } from '../../../core/http/api.service';
import { IngestionService } from './ingestion.service';
import { MessageLogService } from './message-log.service';
import { PerAgentStore, PerAgentStoreRegistry } from './per-agent-store';
import {
  AgentTokenUsage,
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
