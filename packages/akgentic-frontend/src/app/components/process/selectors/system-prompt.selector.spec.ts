import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';

import { AkgenticMessage } from '../../../protocol/message.types';
import { ApiService } from '../../../core/http/api.service';
import { ChatService } from './chat.selector';
import { MessageLogService } from '../event/message-log.service';
import { IngestionService } from '../event/ingestion.service';
import { PerAgentStore, PerAgentStoreRegistry } from '../event/per-agent-store';
import {
  SystemPromptRow,
  SystemPromptSelector,
  SystemPromptValue,
  systemPromptLabel,
} from './system-prompt.selector';

// ---------------------------------------------------------------------
// Fixtures
//
// These mirror the ADR-004 §5a wire JSON for `EventMessage(LlmSystemPromptEvent)`
// and the existing `EventMessage(LlmMessageEvent)` envelope (the fallback path).
// The backend emitter (akgentic-llm) need NOT be merged/released: the event
// contract is frozen, so fixtures are a sufficient and authoritative source.
//
// Story 17-4 (Epic 17 / ADR-014): the whole-log `latestSystemPromptFold` is
// retired; the latest-wins + FR2-fallback + row-mapping logic now lives in
// `systemPromptReduce`, registered as the `systemPrompt` PerAgentStore instance
// on `IngestionService`. These fixtures are the parity oracle — they are
// driven through `MessageLogService` and read via `store.systemPrompt` and the
// `SystemPromptSelector` façade, exercising the REAL fold (no mocking).
// ---------------------------------------------------------------------

interface PartSnapshot {
  __model__?: string;
  dynamic_ref: string | null;
  content: string;
}

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

let envCounter = 0;

/** EventMessage envelope carrying a LlmSystemPromptEvent (primary path). */
function makeSystemPromptEnvelope(
  agentId: string,
  parts: PartSnapshot[],
  content_hash = 'h-' + envCounter,
): AkgenticMessage {
  return {
    id: 'sp-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: {
      __model__: 'akgentic.llm.event.LlmSystemPromptEvent',
      run_id: 'run-' + content_hash,
      content_hash,
      // `parts` may be deliberately undefined (malformed-event fixture) — guard
      // the eager map so the malformation reaches the fold, not the builder.
      parts: parts?.map((p) => ({
        __model__: 'akgentic.llm.event.SystemPromptPartSnapshot',
        ...p,
      })),
    },
  } as unknown as AkgenticMessage;
}

/**
 * EventMessage envelope carrying a LlmMessageEvent whose inner ModelRequest
 * `message.parts` include `part_kind === 'system-prompt'` parts (fallback path).
 */
function makeLlmMessageEnvelope(
  agentId: string,
  systemParts: Array<{ dynamic_ref: string | null; content: string }>,
): AkgenticMessage {
  return {
    id: 'llm-' + agentId + '-' + envCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: {
      __model__: 'akgentic.llm.event.LlmMessageEvent',
      message: {
        parts: [
          ...systemParts.map((p) => ({ part_kind: 'system-prompt', ...p })),
          { part_kind: 'user-prompt', content: 'hi' },
        ],
      },
    },
  } as unknown as AkgenticMessage;
}

function makeUnknownEnvelope(id: string): AkgenticMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: sender('whoever'),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: { __model__: 'akgentic.llm.event.ToolStateEvent' },
  } as unknown as AkgenticMessage;
}

// ---------------------------------------------------------------------
// TestBed wiring — drive the REAL store + façade via MessageLogService.
// ---------------------------------------------------------------------

function configureBed(): {
  log: MessageLogService;
  service: IngestionService;
  selector: SystemPromptSelector;
} {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      MessageLogService,
      PerAgentStoreRegistry,
      IngestionService,
      SystemPromptSelector,
      ChatService,
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
    selector: TestBed.inject(SystemPromptSelector),
  };
}

/** Read the rendered rows for `agentId` straight off the store snapshot
 *  (synchronous; exercises the real `systemPromptReduce`). */
function storeRows(
  store: PerAgentStore<SystemPromptValue>,
  agentId: string,
): SystemPromptRow[] {
  return store.snapshot(agentId)?.rows ?? [];
}

// ---------------------------------------------------------------------
// Tests — the systemPrompt reducer (parity with the retired fold)
// ---------------------------------------------------------------------

describe('systemPrompt store reducer (Epic 17 / ADR-014, parity oracle)', () => {
  it('latest-wins: last LlmSystemPromptEvent for the agent yields its parts as rows', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
      ]),
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'roster v2' },
        { dynamic_ref: null, content: 'backstory' },
      ]),
    ]);
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'roster', content: 'roster v2' },
      { type: 'system', name: 'System', content: 'backstory' },
    ]);
  });

  it('label: dynamic_ref takes its trailing segment, null → "System"', () => {
    const { log, service } = configureBed();
    log.append(
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'current_date', content: 'today' },
        { dynamic_ref: 'a.b.c', content: 'deep' },
        { dynamic_ref: null, content: 'static' },
      ]),
    );
    expect(storeRows(service.systemPrompt, 'A').map((r) => r.name)).toEqual([
      'current_date',
      'c',
      'System',
    ]);
  });

  it('per-agent isolation: A reflects only A, B only B', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'A-prompt' }]),
      makeSystemPromptEnvelope('B', [{ dynamic_ref: null, content: 'B-prompt' }]),
    ]);
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'System', content: 'A-prompt' },
    ]);
    expect(storeRows(service.systemPrompt, 'B')).toEqual([
      { type: 'system', name: 'System', content: 'B-prompt' },
    ]);
  });

  it('fallback: no LlmSystemPromptEvent → first LlmMessageEvent system parts (first-wins)', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'legacy roster' },
      ]),
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'second message roster' },
      ]),
    ]);
    // First such message wins for the fallback.
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'roster', content: 'legacy roster' },
    ]);
  });

  it('precedence: LlmSystemPromptEvent wins over an earlier LlmMessageEvent fallback (AC-2)', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'legacy roster' },
      ]),
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'event roster' },
      ]),
    ]);
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'roster', content: 'event roster' },
    ]);
  });

  it('primary supersedes fallback even when the message event arrives AFTER (AC-2)', () => {
    const { log, service } = configureBed();
    log.appendAll([
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'event roster' },
      ]),
      // A later LlmMessageEvent with system parts must NOT override the primary.
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'late legacy roster' },
      ]),
    ]);
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'roster', content: 'event roster' },
    ]);
  });

  it('malformed/empty latest event yields [] (latest-wins still applies)', () => {
    // Missing parts entirely.
    {
      const { log, service } = configureBed();
      log.append(
        makeSystemPromptEnvelope('A', undefined as unknown as PartSnapshot[]),
      );
      expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
    }
    // Empty parts array.
    {
      const { log, service } = configureBed();
      log.append(makeSystemPromptEnvelope('A', []));
      expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
    }
    // A malformed LATEST event above an earlier well-formed one → [] (latest-wins).
    {
      const { log, service } = configureBed();
      log.appendAll([
        makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'good' }]),
        makeSystemPromptEnvelope('A', []),
      ]);
      expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
    }
  });

  it('a part missing content maps to empty string, no throw', () => {
    const { log, service } = configureBed();
    log.append(
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'current_date' } as unknown as PartSnapshot,
      ]),
    );
    expect(storeRows(service.systemPrompt, 'A')).toEqual([
      { type: 'system', name: 'current_date', content: '' },
    ]);
  });

  it('a log of only unrelated __model__s yields [] for the agent', () => {
    const { log, service } = configureBed();
    log.appendAll([makeUnknownEnvelope('u1'), makeUnknownEnvelope('u2')]);
    expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
  });

  it('absent agent: empty log and no-match log both yield []', () => {
    {
      const { service } = configureBed();
      expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
    }
    {
      const { log, service } = configureBed();
      log.append(
        makeSystemPromptEnvelope('B', [{ dynamic_ref: null, content: 'B' }]),
      );
      expect(storeRows(service.systemPrompt, 'A')).toEqual([]);
    }
  });
});

describe('systemPromptLabel (pure helper)', () => {
  it('returns trailing segment for dotted refs, the ref itself when undotted', () => {
    expect(systemPromptLabel('team.roster')).toBe('roster');
    expect(systemPromptLabel('current_date')).toBe('current_date');
  });

  it('returns "System" for null/undefined/empty', () => {
    expect(systemPromptLabel(null)).toBe('System');
    expect(systemPromptLabel(undefined)).toBe('System');
    expect(systemPromptLabel('')).toBe('System');
  });
});

// ---------------------------------------------------------------------
// Tests — SystemPromptSelector façade (AC-3, AC-4)
// ---------------------------------------------------------------------

describe('SystemPromptSelector (thin façade over store.systemPrompt)', () => {
  it('AC-3 late-subscriber: forAgent replay delivers the current block synchronously', () => {
    const { log, selector } = configureBed();
    log.appendAll([
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'v1' }]),
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'v2' }]),
    ]);

    let received: SystemPromptRow[] | undefined;
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => (received = r));
    expect(received).toEqual([{ type: 'system', name: 'System', content: 'v2' }]);
    sub.unsubscribe();
  });

  it('AC-4 no-rows agent yields [] (never undefined)', () => {
    const { selector } = configureBed();
    let received: SystemPromptRow[] | undefined;
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => (received = r));
    expect(received).toEqual([]);
    expect(received).not.toBeUndefined();
    sub.unsubscribe();
  });

  it('AC-3 no-op suppression: an unrelated log tick does not re-emit the same block', () => {
    const { log, selector } = configureBed();
    const emissions: SystemPromptRow[][] = [];
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => emissions.push(r));

    log.append(
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'v1' }]),
    );
    // A log tick that does NOT change A's head block (an unrelated message).
    log.append(makeUnknownEnvelope('u1'));

    // Initial [] (empty), then the v1 block. The unrelated tick leaves A's
    // value identical, so it must NOT re-emit.
    expect(emissions.length).toBe(2);
    expect(emissions[0]).toEqual([]);
    expect(emissions[1]).toEqual([
      { type: 'system', name: 'System', content: 'v1' },
    ]);
    sub.unsubscribe();
  });

  it('AC-3 fresh reference on real change: distinct array objects across changes', () => {
    const { log, selector } = configureBed();
    const emissions: SystemPromptRow[][] = [];
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => emissions.push(r));

    log.append(
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'v1' }]),
    );
    log.append(
      makeSystemPromptEnvelope('A', [{ dynamic_ref: null, content: 'v2' }]),
    );

    expect(emissions.length).toBe(3); // [], v1, v2
    expect(emissions[1]).not.toBe(emissions[2]);
    sub.unsubscribe();
  });

  it('AC-4 façade parity: selector yields the SAME rows the store yields', () => {
    const { log, service, selector } = configureBed();
    log.appendAll([
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'legacy' },
      ]),
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
        { dynamic_ref: 'current_date', content: 'day 1' },
      ]),
    ]);

    let received: SystemPromptRow[] | undefined;
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => (received = r));
    expect(received).toEqual(storeRows(service.systemPrompt, 'A'));
    expect(received).toEqual([
      { type: 'system', name: 'roster', content: 'roster v1' },
      { type: 'system', name: 'current_date', content: 'day 1' },
    ]);
    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// Tests — REST/WS replay-vs-live parity (Task 4)
// ---------------------------------------------------------------------

describe('systemPrompt parity — REST batch vs WS per-message', () => {
  function buildFixture(): AkgenticMessage[] {
    return [
      makeLlmMessageEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'legacy' },
      ]),
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
        { dynamic_ref: 'current_date', content: 'day 1' },
      ]),
      makeSystemPromptEnvelope('A', [
        { dynamic_ref: 'team.roster', content: 'roster v2' },
        { dynamic_ref: 'current_date', content: 'day 2' },
      ]),
    ];
  }

  function head(mode: 'rest' | 'ws'): SystemPromptRow[] {
    const { log, selector } = configureBed();

    const emissions: SystemPromptRow[][] = [];
    const sub = selector
      .latestSystemPrompt$('A')
      .subscribe((r) => emissions.push(r));

    const fixture = buildFixture();
    if (mode === 'rest') {
      log.appendAll(fixture);
    } else {
      for (const msg of fixture) log.append(msg);
    }

    const result = emissions[emissions.length - 1];
    sub.unsubscribe();
    return result;
  }

  it('REST batch and WS sequence produce identical head blocks', () => {
    const rest = head('rest');
    const ws = head('ws');
    expect(JSON.stringify(rest)).toBe(JSON.stringify(ws));
    // Latest-wins: roster v2 / day 2.
    expect(rest).toEqual([
      { type: 'system', name: 'roster', content: 'roster v2' },
      { type: 'system', name: 'current_date', content: 'day 2' },
    ]);
  });
});
