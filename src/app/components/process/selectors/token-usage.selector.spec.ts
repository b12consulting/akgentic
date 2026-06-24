import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';

import { AkgenticMessage } from '../../../protocol/message.types';
import { ApiService } from '../../../core/http/api.service';
import { IngestionService } from '../event/ingestion.service';
import { MessageLogService } from '../event/message-log.service';
import { PerAgentStoreRegistry } from '../event/per-agent-store';
import { AgentTokenUsage } from '../event/per-agent-specs';
import { TeamTokenTotals, TokenUsageSelector } from './token-usage.selector';

// ---------------------------------------------------------------------
// Fixtures — EventMessage(LlmUsageEvent) envelopes driven through the REAL
// registry + selector via MessageLogService (no mocking of the fold).
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

function makeUsageEnvelope(
  agentId: string,
  input_tokens: number,
  output_tokens: number,
): AkgenticMessage {
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
      run_id: 'run-' + envCounter,
      model_name: 'claude-sonnet',
      provider_name: 'anthropic',
      input_tokens,
      output_tokens,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      requests: 1,
    },
  } as unknown as AkgenticMessage;
}

/** An unrelated event that does NOT change any usage total. */
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

function configureBed(): {
  log: MessageLogService;
  selector: TokenUsageSelector;
} {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      MessageLogService,
      PerAgentStoreRegistry,
      IngestionService,
      TokenUsageSelector,
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
    selector: TestBed.inject(TokenUsageSelector),
  };
}

// ---------------------------------------------------------------------
// AC #8 — perAgent$ behaviour
// ---------------------------------------------------------------------

describe('TokenUsageSelector.perAgent$ (AC #8)', () => {
  it('emits the agent current usage and re-emits on change', () => {
    const { log, selector } = configureBed();
    const emissions: (AgentTokenUsage | undefined)[] = [];
    const sub = selector.perAgent$('A').subscribe((v) => emissions.push(v));

    log.append(makeUsageEnvelope('A', 100, 10));
    log.append(makeUsageEnvelope('A', 5, 1));

    // [undefined (never-run), after first event, after second event]
    expect(emissions.length).toBe(3);
    expect(emissions[0]).toBeUndefined();
    expect(emissions[1]).toEqual(
      jasmine.objectContaining({ totalSent: 100, totalReceived: 10 }),
    );
    expect(emissions[2]).toEqual(
      jasmine.objectContaining({ totalSent: 105, totalReceived: 11 }),
    );
    sub.unsubscribe();
  });

  it('passes undefined THROUGH for a never-run agent (OQ2: view owns empty-state)', () => {
    const { selector } = configureBed();
    let received: AgentTokenUsage | undefined = {
      lastContextWindow: -1,
    } as AgentTokenUsage;
    const sub = selector.perAgent$('ghost').subscribe((v) => (received = v));
    expect(received).toBeUndefined();
    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// AC #8 — teamTotals$ behaviour
// ---------------------------------------------------------------------

describe('TokenUsageSelector.teamTotals$ (AC #8)', () => {
  it('empty team yields { totalSent: 0, totalReceived: 0 }', () => {
    const { selector } = configureBed();
    let received: TeamTokenTotals | undefined;
    const sub = selector.teamTotals$.subscribe((t) => (received = t));
    expect(received).toEqual({ totalSent: 0, totalReceived: 0 });
    sub.unsubscribe();
  });

  it('emits the structural sum across all agents', () => {
    const { log, selector } = configureBed();
    let received: TeamTokenTotals | undefined;
    const sub = selector.teamTotals$.subscribe((t) => (received = t));

    log.appendAll([
      makeUsageEnvelope('A', 100, 10),
      makeUsageEnvelope('B', 200, 20),
      makeUsageEnvelope('A', 50, 5),
    ]);

    expect(received).toEqual({ totalSent: 350, totalReceived: 35 });
    sub.unsubscribe();
  });

  it('de-dupes: a log frame that changes no usage total produces no new emission', () => {
    const { log, selector } = configureBed();
    const emissions: TeamTokenTotals[] = [];
    const sub = selector.teamTotals$.subscribe((t) => emissions.push(t));

    log.append(makeUsageEnvelope('A', 100, 10)); // changes totals → emit
    log.append(makeUnrelatedEnvelope('u1')); // no usage change → NO emit

    // initial zeros + one usage change = 2 emissions; the unrelated tick adds none
    expect(emissions.length).toBe(2);
    expect(emissions[0]).toEqual({ totalSent: 0, totalReceived: 0 });
    expect(emissions[1]).toEqual({ totalSent: 100, totalReceived: 10 });
    sub.unsubscribe();
  });
});
