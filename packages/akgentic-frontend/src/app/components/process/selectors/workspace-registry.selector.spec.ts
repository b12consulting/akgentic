import { TestBed } from '@angular/core/testing';

import {
  AkgenticMessage,
  BaseConfig,
  isWorkspaceTool,
  StartMessage,
  StopMessage,
  ToolCardLite,
} from '../../../protocol/message.types';
import { MessageLogService } from '../event/message-log.service';
import {
  WorkspaceDescriptor,
  WorkspaceRegistryService,
  workspaceRegistryReduce,
} from './workspace-registry.selector';

// ---------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------

const TEAM_ID = 'team-1';
const WORKSPACE_MODEL = 'akgentic.tool.workspace.tool.WorkspaceTool';

function baseSender(agentName: string) {
  return {
    __actor_address__: true as const,
    agent_id: 'agent-' + agentName,
    name: agentName,
    role: 'Agent',
    squad_id: 's1',
    user_message: false,
  };
}

function workspaceTool(workspaceId?: string | null): ToolCardLite {
  return { __model__: WORKSPACE_MODEL, workspace_id: workspaceId };
}

// NOTE: the backend AgentConfig does NOT serialise `team_id` — it is absent
// from `config` on the wire. The fixtures omit it deliberately so the fold is
// exercised against the real payload shape (the team id is read from the
// message level, `StartMessage.team_id`, not from `config`).
function makeConfig(tools?: ToolCardLite[]): BaseConfig {
  return {
    name: 'cfg',
    role: 'Agent',
    user_id: 'u1',
    user_email: 'u@x',
    squad_id: 's1',
    orchestrator: baseSender('orchestrator'),
    tools,
  };
}

function makeStartMessage(
  agentName: string,
  tools?: ToolCardLite[],
): StartMessage {
  return {
    id: 'start-' + agentName,
    parent_id: null,
    team_id: TEAM_ID,
    timestamp: new Date().toISOString(),
    sender: baseSender(agentName),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: makeConfig(tools),
    parent: null,
  };
}

function makeStopMessage(agentName: string): StopMessage {
  return {
    id: 'stop-' + agentName,
    parent_id: null,
    team_id: TEAM_ID,
    timestamp: new Date().toISOString(),
    sender: baseSender(agentName),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StopMessage',
  };
}

function findById(
  result: WorkspaceDescriptor[],
  workspaceId: string,
): WorkspaceDescriptor | undefined {
  return result.find((d) => d.workspaceId === workspaceId);
}

describe('isWorkspaceTool (guard)', () => {
  it('matches a __model__ ending in WorkspaceTool', () => {
    expect(isWorkspaceTool(workspaceTool('w1'))).toBe(true);
  });

  it('rejects other tools, empty, and mid-string WorkspaceTool', () => {
    expect(
      isWorkspaceTool({ __model__: 'akgentic.tool.kg.tool.KnowledgeGraphTool' }),
    ).toBe(false);
    expect(
      isWorkspaceTool({ __model__: 'akgentic.tool.vector.VectorStoreTool' }),
    ).toBe(false);
    expect(isWorkspaceTool({ __model__: '' })).toBe(false);
    // Contains WorkspaceTool mid-string but does NOT end in it → rejected.
    expect(isWorkspaceTool({ __model__: 'WorkspaceToolFactory' })).toBe(false);
  });
});

describe('workspaceRegistryReduce (pure function)', () => {
  it('(AC6) empty log → no descriptors (no always-present default)', () => {
    const result = workspaceRegistryReduce([], TEAM_ID);
    expect(result).toEqual([]);
  });

  it('(AC3) StartMessage with a named WorkspaceTool only → one named descriptor, no default', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('ws-named')]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    expect(result.length).toBe(1);
    const named = findById(result, 'ws-named');
    expect(named?.isDefault).toBe(false);
    expect(named?.agentIds).toEqual(['agent-A']);
    // No agent declared a no-workspace_id tool → there is NO default descriptor.
    expect(findById(result, TEAM_ID)).toBeUndefined();
  });

  it('(AC4) no workspace_id → creates the default descriptor keyed by the team id', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool(null)]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    // A no-workspace_id tool resolves to the team id → the (only) default.
    expect(result.length).toBe(1);
    expect(findById(result, TEAM_ID)?.isDefault).toBe(true);
    expect(findById(result, TEAM_ID)?.agentIds).toEqual(['agent-A']);
  });

  it('(AC4) effective-id — explicit workspace_id resolves to that id', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('ws-x')]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    expect(findById(result, 'ws-x')).toBeDefined();
    expect(findById(result, 'ws-x')?.agentIds).toEqual(['agent-A']);
  });

  it('(order) default sorts first, named workspaces follow alphabetically', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('shared_workspace')]),
      makeStartMessage('B', [workspaceTool(null)]), // default, declared later
      makeStartMessage('C', [workspaceTool('alpha')]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    // Default first despite being declared second; named ones alphabetical.
    expect(result.map((d) => d.workspaceId)).toEqual([
      TEAM_ID,
      'alpha',
      'shared_workspace',
    ]);
    expect(result[0].isDefault).toBe(true);
  });

  it('(AC5) two agents sharing one effective id collapse to one descriptor recording both', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('ws-shared')]),
      makeStartMessage('B', [workspaceTool('ws-shared')]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    const shared = findById(result, 'ws-shared');
    expect(shared?.agentIds).toEqual(['agent-A', 'agent-B']);
    expect(result.filter((d) => d.workspaceId === 'ws-shared').length).toBe(1);
  });

  it('(AC6) a WorkspaceTool with effective id == team id folds into the default, no second default', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool(TEAM_ID)]),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    const defaults = result.filter((d) => d.isDefault);
    expect(defaults.length).toBe(1);
    expect(defaults[0].agentIds).toEqual(['agent-A']);
  });

  it('(AC7) Stop drops the member but KEEPS the workspace (sticky — operator retains access)', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('ws-named')]),
      makeStopMessage('A'),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    const named = findById(result, 'ws-named');
    expect(named).toBeDefined();
    // The fired member is gone, but the workspace persists with no members.
    expect(named?.agentIds).toEqual([]);
  });

  it('(AC7) Stop — descriptor backed by another agent survives, stopped agent removed', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool('ws-shared')]),
      makeStartMessage('B', [workspaceTool('ws-shared')]),
      makeStopMessage('A'),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    const shared = findById(result, 'ws-shared');
    expect(shared).toBeDefined();
    expect(shared?.agentIds).toEqual(['agent-B']);
  });

  it('(AC7) ordered last-wins — Start → Stop → Start ends with the named workspace present', () => {
    const start1 = makeStartMessage('A', [workspaceTool('ws-named')]);
    start1.id = 'a-start-1';
    const stop1 = makeStopMessage('A');
    stop1.id = 'a-stop-1';
    const start2 = makeStartMessage('A', [workspaceTool('ws-named')]);
    start2.id = 'a-start-2';
    const result = workspaceRegistryReduce([start1, stop1, start2], TEAM_ID);
    expect(findById(result, 'ws-named')?.agentIds).toEqual(['agent-A']);
  });

  it('(AC7) Stop keeps the default workspace (sticky), drops its member', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('A', [workspaceTool(TEAM_ID)]),
      makeStopMessage('A'),
    ];
    const result = workspaceRegistryReduce(log, TEAM_ID);
    const def = findById(result, TEAM_ID);
    expect(def?.isDefault).toBe(true);
    expect(def?.agentIds).toEqual([]);
  });

  it('(sticky) a team that never declared a WorkspaceTool stays empty', () => {
    const log: AkgenticMessage[] = [makeStartMessage('A', [])];
    expect(workspaceRegistryReduce(log, TEAM_ID)).toEqual([]);
  });

  it('(NFR4) realistic serialized StartMessage fixture → named descriptor registered', () => {
    // A JSON-shaped StartMessage as it arrives on the wire: config serialised
    // in full, config.tools holding a nested WorkspaceTool object with both a
    // recursive __model__ and a workspace_id. If a future infra serialization
    // change drops `tools` or renames the discriminator, this fails in CI
    // rather than silently collapsing the registry to the default-only tab.
    const serialized = {
      id: 'start-serialized',
      parent_id: null,
      team_id: TEAM_ID,
      timestamp: '2026-06-16T00:00:00.000Z',
      sender: {
        __actor_address__: true,
        agent_id: 'agent-serialized',
        name: 'Researcher',
        role: 'Agent',
        squad_id: 's1',
        user_message: false,
      },
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StartMessage',
      parent: null,
      // NOTE: NO `team_id` in config — the backend AgentConfig does not
      // serialise it. The team id is only present at the MESSAGE level above.
      config: {
        name: 'Researcher',
        role: 'Agent',
        user_id: 'u1',
        user_email: 'u@x',
        squad_id: 's1',
        orchestrator: {
          __actor_address__: true,
          agent_id: 'orch',
          name: 'orchestrator',
          role: 'Orchestrator',
          squad_id: 's1',
          user_message: false,
        },
        tools: [
          {
            __model__: WORKSPACE_MODEL,
            workspace_id: 'ws-from-wire',
          },
          // Default workspace tool: NO workspace_id → must resolve to the
          // team default via the message-level team_id (NOT config.team_id,
          // which is absent on the wire). Regression guard for the bug where
          // default-workspace members showed no chips.
          {
            __model__: WORKSPACE_MODEL,
            workspace_id: null,
          },
        ],
      },
    } as unknown as AkgenticMessage;

    const result = workspaceRegistryReduce([serialized], TEAM_ID);
    const named = findById(result, 'ws-from-wire');
    expect(named).toBeDefined();
    expect(named?.isDefault).toBe(false);
    expect(named?.agentIds).toEqual(['agent-serialized']);
    // The default tool (no workspace_id, config without team_id) still lands
    // in the team-default descriptor — resolved from the message team_id.
    const def = findById(result, TEAM_ID);
    expect(def?.isDefault).toBe(true);
    expect(def?.agentIds).toEqual(['agent-serialized']);
  });
});

describe('WorkspaceRegistryService (selector over MessageLogService.log$)', () => {
  let log: MessageLogService;
  let service: WorkspaceRegistryService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, WorkspaceRegistryService],
    });
    log = TestBed.inject(MessageLogService);
    service = TestBed.inject(WorkspaceRegistryService);
  });

  function currentValue(): WorkspaceDescriptor[] {
    let v: WorkspaceDescriptor[] | undefined;
    const sub = service.workspaces$.subscribe((x) => (v = x));
    sub.unsubscribe();
    return v as WorkspaceDescriptor[];
  }

  it('(1) initial empty log → no descriptors', () => {
    const result = currentValue();
    expect(result).toEqual([]);
  });

  it('(2) live append of a named-WorkspaceTool StartMessage → one named descriptor', () => {
    log.append(makeStartMessage('A', [workspaceTool('ws-named')]));
    const result = currentValue();
    expect(result.length).toBe(1);
    expect(findById(result, 'ws-named')?.agentIds).toEqual(['agent-A']);
    expect(findById(result, TEAM_ID)).toBeUndefined();
  });

  it('(3) distinctUntilChanged suppresses structurally identical re-emissions', () => {
    const emissions: WorkspaceDescriptor[][] = [];
    const sub = service.workspaces$.subscribe((v) => emissions.push(v));

    const start1 = makeStartMessage('A', [workspaceTool('ws-named')]);
    start1.id = 'a-1';
    const start2 = makeStartMessage('A', [workspaceTool('ws-named')]);
    start2.id = 'a-2';
    log.append(start1);
    log.append(start2);

    // [initial empty, after first named start]. The second start for the same
    // agent yields the same effective contribution → structurally identical
    // fold → distinctUntilChanged suppresses a third emission.
    expect(emissions.length).toBe(2);
    sub.unsubscribe();
  });

  it('(4) log.reset() clears the registry on team switch', () => {
    log.append(makeStartMessage('A', [workspaceTool('ws-named')]));
    expect(currentValue().length).toBe(1);
    log.reset();
    expect(currentValue()).toEqual([]);
  });
});
