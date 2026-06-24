import { SimpleChange } from '@angular/core';
import { ComponentFixture, fakeAsync, TestBed, tick } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { BehaviorSubject, Observable, of } from 'rxjs';

import { AkgentChatComponent } from './akgent-chat.component';
import { ApiService } from '../../../../../core/http/api.service';
import { UtilService } from '../../../../../core/ui/utils.service';
import { ContextService } from '../../../../../core/context/context.service';
import { IngestionService } from '../../../event/ingestion.service';
import { MessageLogService } from '../../../event/message-log.service';
import { PerAgentStoreRegistry } from '../../../event/per-agent-store';
import {
  SystemPromptSelector,
  SystemPromptValue,
  systemPromptMatch,
  systemPromptReduce,
} from '../../../selectors/system-prompt.selector';
import { TokenUsageSelector } from '../../../selectors/token-usage.selector';
import { AgentTokenUsage } from '../../../event/per-agent-specs';
import {
  AkgenticMessage,
  CommandDescriptor,
} from '../../../../../protocol/message.types';

/**
 * Story 26-2 — the component injects the component-scoped TokenUsageSelector for
 * the member-chat usage pill. Existing TestBeds (head block / follow mode /
 * keyboard) don't exercise the pill, so they provide this neutral stub whose
 * `perAgent$` always emits `undefined` (never-run empty-state) — enough to let
 * the component construct without re-wiring the full tokenUsage store.
 */
const NEUTRAL_TOKEN_USAGE_SELECTOR = {
  provide: TokenUsageSelector,
  useValue: {
    perAgent$: (_id: string): Observable<AgentTokenUsage | undefined> =>
      of(undefined),
  },
};

/**
 * Story 15-1 (ADR-013) — member-chat `/` slash-command mention. The member
 * chat targets exactly one agent (its own `agentName`), so the `/` list is
 * unconditionally that agent's commands.
 */
describe('AkgentChatComponent — slash-command mention (Story 15-1 / 17-3)', () => {
  let component: AkgentChatComponent;
  // Story 17-3: the member chat reads `commands.snapshot(this.agentId)` keyed by
  // agent_id. The stub holds an agent_id → descriptors map + a `snapshot(id)`.
  let commandsById: Record<string, CommandDescriptor[]>;

  const HIRE: CommandDescriptor = {
    name: 'hire_member',
    description: 'Hire a new team member',
    args: [
      { name: 'role', type: 'string', required: true },
      { name: 'name', type: 'string', required: false },
    ],
    tool_card: 'TeamTool',
  };
  const ROSTER: CommandDescriptor = {
    name: 'roster',
    description: 'List the current team roster',
    args: [],
    tool_card: 'TeamTool',
  };

  beforeEach(() => {
    commandsById = {};

    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        { provide: ApiService, useValue: { sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined) } },
        { provide: UtilService, useValue: {} },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        // Story 16-2 / Epic 17 (17-4): AkgentChatComponent injects
        // SystemPromptSelector, now a thin façade over
        // `IngestionService.systemPrompt`. Provide a stub service exposing the
        // `commands` snapshot the `/` mention reads PLUS a REAL `systemPrompt`
        // PerAgentStore (registered on a real registry over the same
        // MessageLogService) so the façade resolves against a live store.
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: {
              snapshot: (id: string): CommandDescriptor[] | undefined =>
                commandsById[id],
            },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
      ],
    });

    const fixture = TestBed.createComponent(AkgentChatComponent);
    component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = 'a-mgr';
    component.agentName = '@Manager';
    fixture.detectChanges();
  });

  it('AC-4: commandItems are the panel agent\'s commands (keyed by agent_id)', () => {
    // Seed under the panel's agent_id (`component.agentId`), not the friendly name.
    commandsById['a-mgr'] = [HIRE, ROSTER];
    expect(component.commandItems.map((c) => c.name)).toEqual([
      'hire_member',
      'roster',
    ]);
  });

  it('hides internal `_`-prefixed commands from the / list', () => {
    const INTERNAL: CommandDescriptor = {
      name: '_expand_media_refs',
      description: 'Expand glob tokens into binary image content',
      args: [{ name: 'prompt', type: 'string', required: true }],
      tool_card: 'MediaTool',
    };
    commandsById['a-mgr'] = [INTERNAL, HIRE, ROSTER];
    // `_expand_media_refs` is internal — only user commands remain.
    expect(component.commandItems.map((c) => c.name)).toEqual([
      'hire_member',
      'roster',
    ]);
  });

  it('AC-6: empty / list until a CommandsAnnouncedEvent arrives for this agent', () => {
    expect(component.commandItems).toEqual([]);
    // A different agent_id's commands must not bleed into this panel.
    commandsById['a-other'] = [HIRE];
    expect(component.commandItems).toEqual([]);
  });

  it('AC-2: selectCommand inserts `/${name} ` (leading slash, trailing space)', () => {
    expect(component.selectCommand({ name: 'roster' })).toBe('/roster ');
  });

  it('mentionConfig exposes a single `/` trigger', () => {
    const triggers = component.mentionConfig.mentions.map((m) => m.triggerChar);
    expect(triggers).toEqual(['/']);
  });

  it('commandArgsHint renders required in <> and optional in []', () => {
    expect(component.commandArgsHint(HIRE.args)).toBe('<role> [name]');
    expect(component.commandArgsHint(ROSTER.args)).toBe('');
  });

  // Story 15-3 (AC-4) — tool-family ordering: the member chat's commandItems
  // order by `tool_card` then `name` (families contiguous, alphabetical
  // within), and the single `/` mentionConfig entry sets `disableSort: true`
  // so angular-mentions' label sort does not clobber that order.
  it('AC-4: orders commandItems by tool_card then name (tool families contiguous)', () => {
    const PLAN_BREAKDOWN: CommandDescriptor = {
      name: 'breakdown',
      description: 'Break a goal into tasks',
      args: [],
      tool_card: 'PlanningTool',
    };
    const PLAN_AUDIT: CommandDescriptor = {
      name: 'audit',
      description: 'Audit the current plan',
      args: [],
      tool_card: 'PlanningTool',
    };
    // Stored order: TeamTool first, PlanningTool names reversed — neither
    // tool-grouped nor globally alphabetical, so a naive sort can't fake it.
    // Seeded under the panel's agent_id (a-mgr).
    commandsById['a-mgr'] = [ROSTER, PLAN_BREAKDOWN, HIRE, PLAN_AUDIT];

    // PlanningTool family (audit, breakdown) before TeamTool family
    // (hire_member, roster); alphabetical within each family.
    expect(component.commandItems.map((c) => c.name)).toEqual([
      'audit',
      'breakdown',
      'hire_member',
      'roster',
    ]);
  });

  it('AC-4: the `/` mentionConfig entry sets disableSort === true', () => {
    const slash = component.mentionConfig.mentions.find(
      (m) => m.triggerChar === '/',
    );
    expect(slash).toBeTruthy();
    expect((slash as any).disableSort).toBeTrue();
  });
});

// ---------------------------------------------------------------------------
// Story 16-2 (ADR-004 §5b) — head system block rendered ONCE from
// `latestSystemPrompt$`, inline `system-prompt` branch removed from
// `updateContext()`. Driven through the real MessageLogService →
// SystemPromptSelector → component pipeline (log → selector → head render).
//
// Fixtures mirror the ADR-004 §5a wire JSON. The backend emitter (akgentic-llm)
// need NOT be merged/released — the event contract is frozen, so fixtures are an
// authoritative source. (Full live e2e is deferred per the story Open Questions.)
// ---------------------------------------------------------------------------

interface PartSnapshot {
  dynamic_ref: string | null;
  content: string;
}

function spSender(agentId: string) {
  return {
    __actor_address__: true as const,
    agent_id: agentId,
    name: '@' + agentId,
    role: 'Agent',
    squad_id: 's1',
    user_message: false,
  };
}

let spCounter = 0;

/** EventMessage envelope carrying a LlmSystemPromptEvent (primary path). */
function systemPromptEnvelope(
  agentId: string,
  parts: PartSnapshot[],
  contentHash = 'h-' + spCounter
): AkgenticMessage {
  return {
    id: 'sp-' + agentId + '-' + spCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: spSender(agentId),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: {
      __model__: 'akgentic.llm.event.LlmSystemPromptEvent',
      run_id: 'run-' + contentHash,
      content_hash: contentHash,
      parts: parts.map((p) => ({
        __model__: 'akgentic.llm.event.SystemPromptPartSnapshot',
        ...p,
      })),
    },
  } as unknown as AkgenticMessage;
}

/**
 * EventMessage envelope carrying a LlmMessageEvent whose inner ModelRequest
 * `message.parts` include `part_kind === 'system-prompt'` parts (fallback path,
 * pre-event teams).
 */
function llmMessageEnvelope(
  agentId: string,
  systemParts: PartSnapshot[]
): AkgenticMessage {
  return {
    id: 'llm-' + agentId + '-' + spCounter++,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: spSender(agentId),
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

/**
 * A raw context message (as fed via `context$`) whose `parts` carry both a
 * `system-prompt` and a `user-prompt` part — the run-1 double-carry. After
 * Story 16-2, `updateContext()` must drop the `system-prompt` arm and keep the
 * `user-prompt` arm.
 */
function doubleCarryContextMessage(): any {
  return {
    kind: 'request',
    parts: [
      {
        part_kind: 'system-prompt',
        dynamic_ref: 'team.roster',
        content: 'inline roster v1',
      },
      { part_kind: 'user-prompt', content: 'hello team' },
    ],
  };
}

describe('AkgentChatComponent — head system block (Story 16-2)', () => {
  const AGENT = 'a-mgr';

  function setup(): {
    fixture: ComponentFixture<AkgentChatComponent>;
    component: AkgentChatComponent;
    log: MessageLogService;
  } {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine
              .createSpy('sendMessage')
              .and.resolveTo(undefined),
          },
        },
        {
          provide: UtilService,
          useValue: { copyToClipboard: () => {}, formatJSON: (v: any) => v },
        },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        // Story 17-3 / Epic 17 (17-4): member chat reads
        // `commands.snapshot(agentId)`; the head block reads the
        // SystemPromptSelector façade over `IngestionService.systemPrompt`.
        // The stub exposes `commands` PLUS a REAL `systemPrompt` PerAgentStore
        // (registered on a real registry over the same MessageLogService) so the
        // head block renders from the actual log-driven fold.
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: {
              snapshot: (_id: string): CommandDescriptor[] | undefined =>
                undefined,
            },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
        // PrimeNG p-fieldset registers a synthetic animation listener.
        provideNoopAnimations(),
      ],
    });

    const log = TestBed.inject(MessageLogService);
    const fixture = TestBed.createComponent(AkgentChatComponent);
    const component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = AGENT;
    component.agentName = '@' + AGENT;
    return { fixture, component, log };
  }

  /** All rendered head-block card headers, e.g. "System : roster". */
  function headHeaders(
    fixture: ComponentFixture<AkgentChatComponent>
  ): string[] {
    const el: HTMLElement = fixture.nativeElement;
    return Array.from(
      el.querySelectorAll('.head-system-container .card-header')
    ).map((n) => (n.textContent ?? '').trim());
  }

  /** Rendered head-block content bodies, in order. */
  function headBodies(
    fixture: ComponentFixture<AkgentChatComponent>
  ): string[] {
    const el: HTMLElement = fixture.nativeElement;
    return Array.from(
      el.querySelectorAll('.head-system-container .text-container')
    ).map((n) => (n.textContent ?? '').trim());
  }

  it('AC1 latest-wins: head fieldset shows the LAST event\'s rows', () => {
    const { fixture, component, log } = setup();
    log.appendAll([
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
      ]),
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster v2' },
        { dynamic_ref: null, content: 'backstory' },
      ]),
    ]);
    fixture.detectChanges();

    expect(headHeaders(fixture)).toEqual([
      'System : roster',
      'System : System',
    ]);
    expect(headBodies(fixture)).toEqual(['roster v2', 'backstory']);
    // No system row leaked into the conversation table.
    expect(component.context.some((m) => m.type === 'system')).toBeFalse();
  });

  it('regression: switching agentId (component reused) rebinds the head block to the new agent', () => {
    const { fixture, component, log } = setup();
    const OTHER = 'b-other';
    log.appendAll([
      systemPromptEnvelope(AGENT, [{ dynamic_ref: null, content: 'A backstory' }]),
      systemPromptEnvelope(OTHER, [{ dynamic_ref: null, content: 'B backstory' }]),
    ]);
    fixture.detectChanges();
    expect(headBodies(fixture)).toEqual(['A backstory']);

    // The agent-tabs dropdown REUSES this component when switching members, so
    // ngOnInit does not re-run — ngOnChanges must re-point systemPrompt$ at the
    // new agent. Without the fix the head block stays pinned to 'A backstory'.
    component.agentId = OTHER;
    component.ngOnChanges({ agentId: new SimpleChange(AGENT, OTHER, false) });
    fixture.detectChanges();

    expect(headBodies(fixture)).toEqual(['B backstory']);
  });

  it('AC2 single source: updateContext() emits NO system row from a system-prompt part', () => {
    const { component } = setup();
    component.updateContext([doubleCarryContextMessage()]);
    // The user-prompt arm still produces a human row...
    expect(component.context.some((m) => m.type === 'human')).toBeTrue();
    // ...but the system-prompt arm is gone — no system row from updateContext.
    expect(component.context.some((m) => m.type === 'system')).toBeFalse();
  });

  it('AC2 untouched arms: tool-call / tool-return / text parts still render', () => {
    const { component } = setup();
    component.updateContext([
      {
        parts: [
          {
            part_kind: 'tool-call',
            tool_name: 'search',
            args: '{"q":"x"}',
            tool_call_id: 't1',
          },
          {
            part_kind: 'tool-return',
            content: 'result-x',
            tool_call_id: 't1',
          },
          { part_kind: 'text', content: 'final answer' },
        ],
      },
    ]);
    const types = component.context.map((m) => m.type);
    expect(types).toContain('tool_call');
    expect(types).toContain('ai');
    // tool_call result merged from the tool-return.
    const toolCall = component.context.find((m) => m.type === 'tool_call');
    expect(toolCall.result).toBe('result-x');
  });

  it('AC3 no duplicate: run-1 double-carry renders the system block exactly once (at the head)', () => {
    const { fixture, component, log } = setup();
    // Same rendering carried in BOTH the first LlmMessageEvent (→ context$)
    // AND a LlmSystemPromptEvent (→ selector).
    log.appendAll([
      llmMessageEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster' },
      ]),
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster' },
      ]),
    ]);
    component.context$.next([doubleCarryContextMessage()]);
    fixture.detectChanges();

    // Head block renders once.
    expect(headHeaders(fixture)).toEqual(['System : roster']);
    // And there is no second system block inside the conversation rows.
    const el: HTMLElement = fixture.nativeElement;
    const allSystemHeaders = Array.from(
      el.querySelectorAll('.card-header')
    ).filter((n) => (n.textContent ?? '').includes('System :'));
    expect(allSystemHeaders.length).toBe(1);
  });

  it('AC5 old-team fallback: no LlmSystemPromptEvent → head block from first LlmMessageEvent system parts', () => {
    const { fixture, component, log } = setup();
    log.appendAll([
      llmMessageEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'legacy roster' },
      ]),
    ]);
    // The same first message also seeds the conversation context (double-carry).
    component.context$.next([doubleCarryContextMessage()]);
    fixture.detectChanges();

    expect(headHeaders(fixture)).toEqual(['System : roster']);
    expect(headBodies(fixture)).toEqual(['legacy roster']);
    // Still no inline duplicate.
    expect(component.context.some((m) => m.type === 'system')).toBeFalse();
  });

  it('AC7 REST/WS parity: batch appendAll and per-message append render identical head rows', () => {
    const fixtureEvents = (): AkgenticMessage[] => [
      llmMessageEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'legacy' },
      ]),
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
        { dynamic_ref: 'current_date', content: 'day 1' },
      ]),
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster v2' },
        { dynamic_ref: 'current_date', content: 'day 2' },
      ]),
    ];

    // REST: single batch.
    const rest = setup();
    rest.log.appendAll(fixtureEvents());
    rest.fixture.detectChanges();
    const restHead = headHeaders(rest.fixture).concat(headBodies(rest.fixture));

    // WS: per-message.
    const ws = setup();
    for (const ev of fixtureEvents()) ws.log.append(ev);
    ws.fixture.detectChanges();
    const wsHead = headHeaders(ws.fixture).concat(headBodies(ws.fixture));

    expect(restHead).toEqual(wsHead);
    // Latest-wins: roster v2 / day 2.
    expect(headBodies(rest.fixture)).toEqual(['roster v2', 'day 2']);
  });

  it('AC8 empty/absent: no system parts → no head fieldset, no throw', () => {
    const { fixture, component, log } = setup();
    // A log with only an unrelated event — no system parts for the agent.
    log.append({
      id: 'u1',
      parent_id: null,
      team_id: 'team-1',
      timestamp: new Date().toISOString(),
      sender: spSender(AGENT),
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.EventMessage',
      event: { __model__: 'akgentic.llm.event.ToolStateEvent' },
    } as unknown as AkgenticMessage);
    expect(() => fixture.detectChanges()).not.toThrow();

    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelectorAll('.head-system-container .card-header').length).toBe(
      0
    );
    expect(component.context.some((m) => m.type === 'system')).toBeFalse();
  });
});

// ---------------------------------------------------------------------------
// Story 20-1 (akgentic-agent ADR-007 §4) — never-run backstory head-block FALLBACK. A
// never-run agent has NO LlmSystemPromptEvent; its backstory comes from the
// host via the `backstory$` input (projected from AgentState.backstory). The
// head block renders the synthetic backstory row when no event rows exist, the
// event rows when present (latest-wins), and nothing when both are empty.
// ---------------------------------------------------------------------------
describe('AkgentChatComponent — never-run backstory head block (Story 20-1)', () => {
  const AGENT = 'a-mgr';

  function setup(backstory = ''): {
    fixture: ComponentFixture<AkgentChatComponent>;
    component: AkgentChatComponent;
    log: MessageLogService;
    backstory$: BehaviorSubject<string>;
  } {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined),
          },
        },
        {
          provide: UtilService,
          useValue: { copyToClipboard: () => {}, formatJSON: (v: any) => v },
        },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: { snapshot: (_id: string) => undefined },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
        provideNoopAnimations(),
      ],
    });

    const log = TestBed.inject(MessageLogService);
    const fixture = TestBed.createComponent(AkgentChatComponent);
    const component = fixture.componentInstance;
    const backstory$ = new BehaviorSubject<string>(backstory);
    component.context$ = new BehaviorSubject<any[]>([]);
    component.backstory$ = backstory$;
    component.agentId = AGENT;
    component.agentName = '@' + AGENT;
    return { fixture, component, log, backstory$ };
  }

  function headHeaders(fixture: ComponentFixture<AkgentChatComponent>): string[] {
    const el: HTMLElement = fixture.nativeElement;
    return Array.from(
      el.querySelectorAll('.head-system-container .card-header')
    ).map((n) => (n.textContent ?? '').trim());
  }

  function headBodies(fixture: ComponentFixture<AkgentChatComponent>): string[] {
    const el: HTMLElement = fixture.nativeElement;
    return Array.from(
      el.querySelectorAll('.head-system-container .text-container')
    ).map((n) => (n.textContent ?? '').trim());
  }

  it('AC1 never-run fallback: no event → head block shows a single backstory row from backstory$ (agent_backstory label parity)', () => {
    const { fixture } = setup('You are Bob.');
    // No LlmSystemPromptEvent in the log — never-run agent.
    fixture.detectChanges();

    expect(headHeaders(fixture)).toEqual(['System : agent_backstory']);
    expect(headBodies(fixture)).toEqual(['You are Bob.']);
  });

  it('AC4 empty backstory + no event → no head fieldset, no throw', () => {
    const { fixture } = setup('');
    expect(() => fixture.detectChanges()).not.toThrow();
    expect(headHeaders(fixture)).toEqual([]);
    expect(headBodies(fixture)).toEqual([]);
  });

  it('AC2 event wins: a LlmSystemPromptEvent is present → head block renders the EVENT rows, not the backstory fallback', () => {
    const { fixture, log } = setup('You are Bob.');
    log.append(
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'team.roster', content: 'roster v1' },
      ]),
    );
    fixture.detectChanges();

    // Event rows win — the synthetic backstory row is NOT shown.
    expect(headHeaders(fixture)).toEqual(['System : roster']);
    expect(headBodies(fixture)).toEqual(['roster v1']);
  });

  it('AC2 latest-wins handoff: state-sourced backstory → first run event replaces it with NO duplicate and NO leftover backstory row', () => {
    const { fixture, log } = setup('You are Bob.');
    // Phase 1 — never-run: the state backstory row renders.
    fixture.detectChanges();
    expect(headBodies(fixture)).toEqual(['You are Bob.']);

    // Phase 2 — first run emits its system-prompt event.
    log.append(
      systemPromptEnvelope(AGENT, [
        { dynamic_ref: 'agent_backstory', content: 'You are Bob (run 1).' },
        { dynamic_ref: 'current_date', content: 'day 1' },
      ]),
    );
    fixture.detectChanges();

    // The head block switches to the event rendering; exactly the event rows,
    // no leftover/duplicate synthetic backstory row.
    expect(headHeaders(fixture)).toEqual([
      'System : agent_backstory',
      'System : current_date',
    ]);
    expect(headBodies(fixture)).toEqual(['You are Bob (run 1).', 'day 1']);
  });

  it('backstory$ updates live: a later non-empty backstory renders the fallback row when no event exists', () => {
    const { fixture, backstory$ } = setup('');
    fixture.detectChanges();
    expect(headHeaders(fixture)).toEqual([]);

    backstory$.next('Backstory arrived.');
    fixture.detectChanges();
    expect(headBodies(fixture)).toEqual(['Backstory arrived.']);
  });

  it('omitted backstory$ input (legacy caller): no event, no backstory → empty head block, no throw', () => {
    // A caller that does NOT bind backstory$ keeps the event-only head block.
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined),
          },
        },
        {
          provide: UtilService,
          useValue: { copyToClipboard: () => {}, formatJSON: (v: any) => v },
        },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: { snapshot: (_id: string) => undefined },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
        provideNoopAnimations(),
      ],
    });
    const fixture = TestBed.createComponent(AkgentChatComponent);
    const component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = AGENT;
    component.agentName = '@' + AGENT;
    // backstory$ intentionally left undefined.
    expect(() => fixture.detectChanges()).not.toThrow();
    expect(headHeaders(fixture)).toEqual([]);
  });
});

describe('AkgentChatComponent — follow mode + status pill', () => {
  let running: BehaviorSubject<boolean>;

  function setup(): { component: AkgentChatComponent } {
    running = new BehaviorSubject<boolean>(true);
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined),
          },
        },
        {
          provide: UtilService,
          useValue: { copyToClipboard: () => {}, formatJSON: (v: any) => v },
        },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: running,
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: { snapshot: (_id: string) => undefined },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
        provideNoopAnimations(),
      ],
    });
    const fixture = TestBed.createComponent(AkgentChatComponent);
    const component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = 'a-mgr';
    component.agentName = '@a-mgr';
    return { component };
  }

  /** Install a mock trace-scroll element with controllable geometry. */
  function installScroll(
    component: AkgentChatComponent,
    scrollHeight: number,
    clientHeight: number,
    scrollTop = 0,
  ): { scrollHeight: number; clientHeight: number; scrollTop: number } {
    const el = {
      scrollHeight,
      clientHeight,
      scrollTop,
      scrollTo(o: { top: number; behavior: ScrollBehavior }) {
        this.scrollTop = o.top;
      },
    };
    (component as any).traceScroll = { nativeElement: el };
    return el;
  }

  it('does NOT auto-scroll a new message when not following', fakeAsync(() => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 0); // scrolled up, content below
    component.updateContext([{ type: 'ai', name: 'x', content: 'hi' }] as any);
    tick(0); // flush the post-render setTimeout
    expect(el.scrollTop).toBe(0); // stayed put
    expect(component.indicatorLabel).toBe('Messages'); // newest below the fold
  }));

  it('submitting enters follow mode and scrolls to the bottom', async () => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 0);
    component.userInput = 'hello';
    await component.sendMessage();
    expect((component as any).following).toBeTrue();
    expect(el.scrollTop).toBe(2000);
    expect(component.indicatorLabel).toBe('Auto scrolling');
  });

  it('clicking the pill enters follow mode, scrolls down, shows "Auto scrolling"', () => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 0);
    component.onFollowLatest();
    expect((component as any).following).toBeTrue();
    expect(el.scrollTop).toBe(2000);
    expect(component.indicatorLabel).toBe('Auto scrolling');
  });

  it('shows "Messages" when the newest message is below the fold (not following)', () => {
    const { component } = setup();
    installScroll(component, 2000, 500, 100); // far from bottom
    component.onScroll();
    expect(component.indicatorLabel).toBe('Messages');
  });

  it('reaching the bottom activates follow ("Auto scrolling")', () => {
    const { component } = setup();
    installScroll(component, 2000, 500, 1500); // at the bottom (dist 0)
    component.onScroll();
    expect((component as any).following).toBeTrue();
    expect(component.indicatorLabel).toBe('Auto scrolling');
  });

  it('a manual upward scroll exits follow mode → "Messages"', () => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 1500);
    (component as any).following = true;
    (component as any).lastScrollTop = 1500;
    el.scrollTop = 200; // user scrolled up
    component.onScroll();
    expect((component as any).following).toBeFalse();
    expect(component.indicatorLabel).toBe('Messages');
  });

  it('the smooth tail (moving DOWN) does not exit follow mode', () => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 1500);
    (component as any).following = true;
    (component as any).lastScrollTop = 1000; // tail moved down the page
    void el;
    component.onScroll();
    expect((component as any).following).toBeTrue();
    expect(component.indicatorLabel).toBe('Auto scrolling');
  });

  it('does NOT show "Auto scrolling" when the process is stopped', () => {
    const { component } = setup();
    running.next(false);
    installScroll(component, 2000, 500, 1500);
    (component as any).following = true;
    component.onScroll();
    expect(component.indicatorLabel).not.toBe('Auto scrolling');
  });

  it('in follow mode, a new message tails to the bottom', fakeAsync(() => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 0);
    (component as any).following = true;
    component.updateContext([{ type: 'ai', name: 'x', content: 'hi' }] as any);
    tick(0);
    expect(el.scrollTop).toBe(2000);
  }));

  it('switching member jumps the trace to the TOP (instant), no follow carry-over', fakeAsync(() => {
    const { component } = setup();
    const el = installScroll(component, 2000, 500, 1500); // prev agent: scrolled down
    (component as any).following = true; // was following the previous member
    component.agentId = 'a-other';
    component.ngOnChanges({
      agentId: new SimpleChange('a-mgr', 'a-other', false),
    });
    tick(0); // post-render scroll-to-top
    expect((component as any).following).toBeFalse();
    expect(el.scrollTop).toBe(0); // jumped to the top

    // The new member's content streams in — it must STAY at the top (not tail).
    component.updateContext([{ type: 'ai', name: 'x', content: 'hi' }] as any);
    tick(0);
    expect(el.scrollTop).toBe(0);
  }));
});

// ---------------------------------------------------------------------------
// Keyboard-submit parity with the main chat (user-input): Enter submits only
// when the `userInputEnterKeySubmit` setting is on; Cmd/Ctrl+Enter always send.
// ---------------------------------------------------------------------------
describe('AkgentChatComponent — keyboard submit parity', () => {
  let component: AkgentChatComponent;
  let fixture: ComponentFixture<AkgentChatComponent>;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined),
          },
        },
        { provide: UtilService, useValue: {} },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: { snapshot: (_id: string) => undefined },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        NEUTRAL_TOKEN_USAGE_SELECTOR,
        provideNoopAnimations(),
      ],
    });

    fixture = TestBed.createComponent(AkgentChatComponent);
    component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = 'a-mgr';
    component.agentName = '@Manager';
    component.userInput = 'hello';
    fixture.detectChanges();
  });

  function textareaEl() {
    return fixture.debugElement.query(By.css('textarea'));
  }

  it('Enter submits when userInputEnterKeySubmit is enabled', () => {
    const spy = spyOn(component, 'sendMessage');
    component.userInputEnterKeySubmit = true;
    textareaEl().triggerEventHandler('keydown.enter', {});
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('Enter does NOT submit when userInputEnterKeySubmit is disabled', () => {
    const spy = spyOn(component, 'sendMessage');
    component.userInputEnterKeySubmit = false;
    textareaEl().triggerEventHandler('keydown.enter', {});
    expect(spy).not.toHaveBeenCalled();
  });

  it('Cmd+Enter always submits regardless of the toggle', () => {
    const spy = spyOn(component, 'sendMessage');
    component.userInputEnterKeySubmit = false;
    textareaEl().triggerEventHandler('keydown.meta.enter', {});
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('Ctrl+Enter always submits regardless of the toggle', () => {
    const spy = spyOn(component, 'sendMessage');
    component.userInputEnterKeySubmit = false;
    textareaEl().triggerEventHandler('keydown.control.enter', {});
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Story 26-2 (ADR-022 §Decision 5) — member-chat token-usage pill. A compact,
// non-interactive pill sits as the FIRST child of `.input-row-buttons` (left of
// Submit), bound to `tokenUsageSelector.perAgent$(agentId) | async`: populated
// → `ctx <ctx> · ↑<sent> ↓<received>` (every number via `tokenCount`); never-run
// (`undefined`) → the neutral `ctx — · ↑0 ↓0`. Driven through a fake selector
// whose `perAgent$` returns a controllable BehaviorSubject so render / live-update
// / empty-state are deterministic.
// ---------------------------------------------------------------------------
describe('AkgentChatComponent — token-usage pill (Story 26-2)', () => {
  const AGENT = 'a-mgr';

  function usage(partial: Partial<AgentTokenUsage>): AgentTokenUsage {
    return {
      lastContextWindow: 0,
      lastRunId: 'run-1',
      lastModelName: 'gpt-4o',
      totalSent: 0,
      totalReceived: 0,
      ...partial,
    };
  }

  function setup(initial: AgentTokenUsage | undefined): {
    fixture: ComponentFixture<AkgentChatComponent>;
    component: AkgentChatComponent;
    usage$: BehaviorSubject<AgentTokenUsage | undefined>;
  } {
    const usage$ = new BehaviorSubject<AgentTokenUsage | undefined>(initial);
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [AkgentChatComponent],
      providers: [
        {
          provide: ApiService,
          useValue: {
            sendMessage: jasmine.createSpy('sendMessage').and.resolveTo(undefined),
          },
        },
        {
          provide: UtilService,
          useValue: { copyToClipboard: () => {}, formatJSON: (v: any) => v },
        },
        {
          provide: ContextService,
          useValue: {
            currentTeamRunning$: new BehaviorSubject<boolean>(true),
            currentProcessId$: new BehaviorSubject<string>('proc-1'),
          },
        },
        MessageLogService,
        PerAgentStoreRegistry,
        {
          provide: IngestionService,
          useFactory: (registry: PerAgentStoreRegistry) => ({
            commands: { snapshot: (_id: string) => undefined },
            systemPrompt: registry.register<SystemPromptValue>({
              name: 'systemPrompt',
              match: systemPromptMatch,
              reduce: systemPromptReduce,
            }),
          }),
          deps: [PerAgentStoreRegistry],
        },
        SystemPromptSelector,
        // Fake selector: `perAgent$` returns the controllable subject so the
        // spec drives populated / updated / empty emissions deterministically.
        {
          provide: TokenUsageSelector,
          useValue: {
            perAgent$: (_id: string) => usage$.asObservable(),
          },
        },
        provideNoopAnimations(),
      ],
    });

    const fixture = TestBed.createComponent(AkgentChatComponent);
    const component = fixture.componentInstance;
    component.context$ = new BehaviorSubject<any[]>([]);
    component.agentId = AGENT;
    component.agentName = '@' + AGENT;
    return { fixture, component, usage$ };
  }

  /** The `.input-row-buttons` row element. */
  function buttonsRow(
    fixture: ComponentFixture<AkgentChatComponent>,
  ): HTMLElement {
    const el: HTMLElement = fixture.nativeElement;
    return el.querySelector('.input-row-buttons') as HTMLElement;
  }

  /** The pill element (the `.usage-pill` span). */
  function pill(
    fixture: ComponentFixture<AkgentChatComponent>,
  ): HTMLElement | null {
    return buttonsRow(fixture).querySelector('.usage-pill');
  }

  /** The pill's rendered text with whitespace collapsed (OQ3: glyphs/order are
   *  the contract, not exact spacing). */
  function pillText(fixture: ComponentFixture<AkgentChatComponent>): string {
    return (pill(fixture)?.textContent ?? '').replace(/\s+/g, ' ').trim();
  }

  it('(a) renders left of Submit inside `.input-row-buttons` with the populated `ctx … · ↑… ↓…` format', () => {
    const { fixture } = setup(
      usage({
        lastContextWindow: 12_300,
        totalSent: 45_000,
        totalReceived: 12_100,
        lastModelName: 'gpt-4o',
      }),
    );
    fixture.detectChanges();

    // tokenCount: 12_300 → "12.3k", 45_000 → "45.0k", 12_100 → "12.1k".
    expect(pillText(fixture)).toBe('ctx 12.3k · ↑45.0k ↓12.1k');

    // The pill is the FIRST child of the buttons row, and Submit comes after it.
    const row = buttonsRow(fixture);
    expect(row.firstElementChild?.classList.contains('usage-pill')).toBeTrue();
    const children = Array.from(row.children);
    const pillIdx = children.findIndex((c) => c.classList.contains('usage-pill'));
    const submitIdx = children.findIndex((c) => c.tagName.toLowerCase() === 'button');
    expect(pillIdx).toBeLessThan(submitIdx);

    // Non-interactive: a <span>, not a <button>, no routerLink.
    const p = pill(fixture)!;
    expect(p.tagName.toLowerCase()).toBe('span');
    expect(p.getAttribute('href')).toBeNull();

    // Tooltip spells out the words + model (full grouped numbers).
    expect(p.getAttribute('title')).toBe(
      'Context window 12,300 · Sent 45,000 · Received 12,100 · gpt-4o',
    );
  });

  it('(b) live update: a fresh emission with a SMALLER newest ctx updates ctx to the newest value; totals reflect the sums', () => {
    const { fixture, usage$ } = setup(
      usage({ lastContextWindow: 30_000, totalSent: 30_000, totalReceived: 9_000 }),
    );
    fixture.detectChanges();
    expect(pillText(fixture)).toBe('ctx 30.0k · ↑30.0k ↓9.0k');

    // Newest event has a SMALLER context window than the prior one — ctx tracks
    // the newest input_tokens (ADR-022 §Decision 3, overwrite semantics), while
    // the totals keep accumulating.
    usage$.next(
      usage({ lastContextWindow: 8_000, totalSent: 38_000, totalReceived: 11_500 }),
    );
    fixture.detectChanges();
    expect(pillText(fixture)).toBe('ctx 8.0k · ↑38.0k ↓11.5k');
  });

  it('(c) never-run agent (`perAgent$` emits undefined) renders the neutral `ctx — · ↑0 ↓0` empty-state', () => {
    const { fixture } = setup(undefined);
    fixture.detectChanges();

    expect(pillText(fixture)).toBe('ctx — · ↑0 ↓0');
    // Still non-interactive, and its tooltip must not render undefined/null.
    const p = pill(fixture)!;
    expect(p.tagName.toLowerCase()).toBe('span');
    expect(p.getAttribute('title')).toBe('No usage yet');
  });

  it('the pill follows an agent switch (perAgent$ re-bound in ngOnChanges)', () => {
    const { fixture, component } = setup(
      usage({ lastContextWindow: 5_000, totalSent: 5_000, totalReceived: 1_000 }),
    );
    fixture.detectChanges();
    const before = pillText(fixture);
    expect(before).toBe('ctx 5.0k · ↑5.0k ↓1.0k');

    // Switching members reuses this component; ngOnChanges re-binds usage$.
    component.agentId = 'b-other';
    component.ngOnChanges({ agentId: new SimpleChange(AGENT, 'b-other', false) });
    fixture.detectChanges();
    // The fake selector returns the same subject for any id, so the pill still
    // renders (the re-bind path did not throw / null the stream).
    expect(pillText(fixture)).toBe('ctx 5.0k · ↑5.0k ↓1.0k');
  });

  it('(AC #7) adding the pill leaves the Submit disabled binding intact', () => {
    const { fixture, component } = setup(
      usage({ lastContextWindow: 1_000, totalSent: 1_000, totalReceived: 100 }),
    );
    fixture.detectChanges();

    const submit = buttonsRow(fixture).querySelector('button') as HTMLButtonElement;
    // Empty input → Submit disabled (existing rule unchanged).
    expect(submit.disabled).toBeTrue();

    // Typing enables it (team is running in this harness).
    component.userInput = 'hello';
    fixture.detectChanges();
    expect(submit.disabled).toBeFalse();
  });
});
