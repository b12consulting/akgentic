import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject } from 'rxjs';
import { MessageService } from 'primeng/api';
import { WebSocketSubject } from 'rxjs/webSocket';

import { AgentTabsComponent } from './agent-tabs.component';
import { Akgent, AkgentService } from '../../../../core/ui/akgent.service';
import { GraphDataService } from '../../selectors/graph.selector';
import { IngestionService } from '../../event/ingestion.service';
import { MessageLogService } from '../../event/message-log.service';
import { PerAgentStoreRegistry } from '../../event/per-agent-store';
import { ChatService } from '../../selectors/chat.selector';
import { ApiService } from '../../../../core/http/api.service';

/**
 * Story 17-2 (ADR-014) — the agent-state panel and agent-chat context view are
 * now sourced from `ingestionService.state.forAgent(id)` /
 * `ingestionService.context.forAgent(id)` (PerAgentStore instances) instead of the
 * deleted `stateDict$` / `contextDict$`. These specs verify the host wiring:
 * the local `state$` / `context$` bridge subjects reflect the store values, with
 * `undefined` mapped to the existing defaults (`null` / `[]`) so the template
 * guards behave identically. Drives the real log fold (no store mocking).
 */
describe('AgentTabsComponent — store-backed state/context wiring (Story 17-2)', () => {
  let component: AgentTabsComponent;
  let log: MessageLogService;
  let ingestionService: IngestionService;
  let selectedAkgent$: BehaviorSubject<Akgent | null>;
  let nodes$: BehaviorSubject<any[]>;
  let categories$: BehaviorSubject<any[]>;
  let fakeSocket: Subject<any>;

  function addr(agentId: string) {
    return {
      __actor_address__: true,
      name: '@' + agentId,
      role: 'Worker',
      agent_id: agentId,
      team_id: 'team-1',
      squad_id: 's1',
      user_message: false,
    };
  }

  function mkStateChanged(agentId: string, state: any, id: string): any {
    return {
      id,
      parent_id: null,
      team_id: 'team-1',
      timestamp: '2026-06-13T00:00:00Z',
      sender: addr(agentId),
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StateChangedMessage',
      state,
    };
  }

  function mkLlmEvent(agentId: string, message: any, id: string): any {
    return {
      id,
      parent_id: null,
      team_id: 'team-1',
      timestamp: '2026-06-13T00:00:00Z',
      sender: addr(agentId),
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.EventMessage',
      event: {
        __model__: 'akgentic.llm.event.LlmMessageEvent',
        message,
      },
    };
  }

  beforeEach(async () => {
    jasmine.clock().install();
    jasmine.clock().mockDate(new Date(0));

    selectedAkgent$ = new BehaviorSubject<Akgent | null>(null);
    nodes$ = new BehaviorSubject<any[]>([]);
    categories$ = new BehaviorSubject<any[]>([]);
    fakeSocket = new Subject<any>();

    TestBed.configureTestingModule({
      providers: [
        MessageLogService,
        PerAgentStoreRegistry,
        IngestionService,
        ChatService,
        {
          provide: ApiService,
          useValue: {
            getEvents: jasmine.createSpy('getEvents').and.resolveTo([]),
            // Story 25-1 (!running gate): init() seeds the state store from
            // getAgentStates ONLY for stopped teams. This spec inits with
            // running=true (live WS), so getAgentStates is never called — no
            // stub needed.
          },
        },
        {
          provide: MessageService,
          useValue: { add: jasmine.createSpy('add'), clear: jasmine.createSpy('clear') },
        },
        {
          provide: AkgentService,
          useValue: { selectedAkgent$, select: jasmine.createSpy('select') },
        },
        {
          provide: GraphDataService,
          useValue: { nodes$, categories$ },
        },
      ],
    });

    ingestionService = TestBed.inject(IngestionService);
    log = TestBed.inject(MessageLogService);
    spyOn<any>(ingestionService, 'createWebSocket').and.returnValue(
      fakeSocket as unknown as WebSocketSubject<any>,
    );
    // Wire the WS pipeline so the registry's log$ subscription is live.
    await ingestionService.init('proc-1', true);

    component = TestBed.createComponent(AgentTabsComponent).componentInstance;
    component.ngOnInit();
  });

  afterEach(() => {
    try {
      fakeSocket.complete();
    } catch {
      /* already closed */
    }
    jasmine.clock().uninstall();
  });

  it('AC2/AC3: selecting an agent feeds context$/state$ from the store', () => {
    log.appendAll([
      mkStateChanged('agent-A', { phase: 'busy' }, 's1'),
      mkLlmEvent('agent-A', { role: 'user', content: 'hi' }, 'e1'),
    ]);

    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    expect(component.state$.value).toEqual({ schema: {}, state: { phase: 'busy' } });
    expect(component.context$.value).toEqual([{ role: 'user', content: 'hi' }]);
  });

  it('AC2/AC3: an agent with no store value maps undefined → null (state) / [] (context)', () => {
    selectedAkgent$.next({ name: '@unknown', agentId: 'unknown' });

    // Template guards (`state$ | async`, `(context$ | async)?.length`) depend
    // on these exact defaults — undefined must never reach the template.
    expect(component.state$.value).toBeNull();
    expect(component.context$.value).toEqual([]);
  });

  it('AC2/AC3: switching agents swaps the source (no cross-agent leak)', () => {
    log.appendAll([
      mkStateChanged('agent-A', { who: 'A' }, 's1'),
      mkStateChanged('agent-B', { who: 'B' }, 's2'),
    ]);

    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });
    expect(component.state$.value).toEqual({ schema: {}, state: { who: 'A' } });

    selectedAkgent$.next({ name: '@agent-B', agentId: 'agent-B' });
    expect(component.state$.value).toEqual({ schema: {}, state: { who: 'B' } });
  });

  it('AC3: a later context message for the selected agent updates context$ live', () => {
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });
    expect(component.context$.value).toEqual([]);

    log.append(mkLlmEvent('agent-A', { role: 'user', content: 'm1' }, 'e1'));
    expect(component.context$.value).toEqual([{ role: 'user', content: 'm1' }]);

    log.append(mkLlmEvent('agent-A', { role: 'assistant', content: 'm2' }, 'e2'));
    expect(component.context$.value).toEqual([
      { role: 'user', content: 'm1' },
      { role: 'assistant', content: 'm2' },
    ]);
  });

  it('deselecting clears context$/state$ to defaults', () => {
    log.append(mkStateChanged('agent-A', { phase: 'busy' }, 's1'));
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });
    expect(component.state$.value).toEqual({ schema: {}, state: { phase: 'busy' } });

    selectedAkgent$.next(null);
    expect(component.state$.value).toBeNull();
    expect(component.context$.value).toEqual([]);
  });

  /** Synchronously read the current value of `chatTabVisible$`. */
  function tabVisible(): boolean {
    let visible: boolean | undefined;
    component.chatTabVisible$.subscribe((v) => (visible = v)).unsubscribe();
    return visible as boolean;
  }

  /** Synchronously read the current value of `backstory$`. */
  function backstory(): string {
    let value = '';
    component.backstory$.subscribe((v) => (value = v)).unsubscribe();
    return value;
  }

  // ===========================================================================
  // Story 20-1 (akgentic-agent ADR-007 §4) — never-run backstory head block + chat-tab
  // visibility from AgentState.backstory. Visibility gates on conversation
  // context OR a non-empty trimmed `state.backstory` (a running agent always has
  // context; a never-run agent shows its backstory). The head-block fallback
  // itself is verified at the consumer in akgent-chat.component.spec.ts.
  // ===========================================================================

  it('AC1/AC3 never-run: no system-prompt event but a non-empty state.backstory → backstory$ projects it and the chat tab is visible', () => {
    // A freshly created agent: only a StateChangedMessage carrying the backstory,
    // NO LlmSystemPromptEvent and NO LlmMessageEvent (never run).
    log.append(mkStateChanged('agent-A', { backstory: 'You are Bob.' }, 's1'));
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    // No context …
    expect(component.context$.value).toEqual([]);
    // … but the backstory is on the client via the `state` store.
    expect(backstory()).toBe('You are Bob.');
    // The chat tab is reachable from state.backstory alone (no white panel).
    expect(tabVisible()).toBeTrue();
  });

  it('AC1 trims: backstory$ projects the TRIMMED state.backstory', () => {
    log.append(
      mkStateChanged('agent-A', { backstory: '  You are Bob.\n' }, 's1'),
    );
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    expect(backstory()).toBe('You are Bob.');
    expect(tabVisible()).toBeTrue();
  });

  it('AC4 no false-positive: empty/whitespace state.backstory, no context → chat tab hidden', () => {
    log.append(mkStateChanged('agent-A', { backstory: '   \n\t ' }, 's1'));
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    expect(component.context$.value).toEqual([]);
    // Whitespace-only backstory trims to '' — it must NOT force the tab open.
    expect(backstory()).toBe('');
    expect(tabVisible()).toBeFalse();
  });

  it('AC4 no false-positive: a state with no backstory field at all → backstory$ is "" and the tab is hidden', () => {
    log.append(mkStateChanged('agent-A', { phase: 'busy' }, 's1'));
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    expect(backstory()).toBe('');
    expect(tabVisible()).toBeFalse();
  });

  it('chatTabVisible$ is false for an agent with neither context nor backstory', () => {
    selectedAkgent$.next({ name: '@unknown', agentId: 'unknown' });

    expect(backstory()).toBe('');
    expect(tabVisible()).toBeFalse();
  });

  it('AC3 context-only: an agent with conversation context (no backstory) is visible', () => {
    log.append(mkLlmEvent('agent-A', { role: 'user', content: 'hi' }, 'e1'));
    selectedAkgent$.next({ name: '@agent-A', agentId: 'agent-A' });

    expect(component.context$.value).toEqual([{ role: 'user', content: 'hi' }]);
    expect(tabVisible()).toBeTrue();
  });
});
