import { TestBed } from '@angular/core/testing';

import {
  AkgenticMessage,
  BaseConfig,
  StartMessage,
} from '../../../protocol/message.types';
import { MessageLogService } from '../event/message-log.service';
import {
  AgentsById,
  AgentsByIdService,
  agentsByIdReduce,
} from './agents-by-id.selector';

// ---------------------------------------------------------------------
// Fixture helpers — sender shape mirrors workspace-registry.selector.spec.
// ---------------------------------------------------------------------

const TEAM_ID = 'team-1';

function sender(agentId: string, name: string, role: string) {
  return {
    __actor_address__: true as const,
    agent_id: agentId,
    name,
    role,
    squad_id: 's1',
    user_message: false,
  };
}

function makeConfig(): BaseConfig {
  // No team_id: the backend AgentConfig does not serialise it (absent on the
  // wire). The team id is read from the message level, not config.
  return {
    name: 'cfg',
    role: 'Agent',
    user_id: 'u1',
    user_email: 'u@x',
    squad_id: 's1',
    orchestrator: sender('orch', 'orchestrator', 'Orchestrator'),
  };
}

function makeStartMessage(
  agentId: string,
  name: string,
  role: string,
): StartMessage {
  return {
    id: 'start-' + agentId,
    parent_id: null,
    team_id: TEAM_ID,
    timestamp: new Date().toISOString(),
    sender: sender(agentId, name, role),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: makeConfig(),
    parent: null,
  };
}

describe('agentsByIdReduce (pure function)', () => {
  it('(AC1) empty log → empty map', () => {
    expect(agentsByIdReduce([])).toEqual({});
  });

  it('(AC1) two StartMessages → both agent_id entries with name + role', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('a1', 'Bob', 'Scrum Master'),
      makeStartMessage('a2', 'Amelia', 'Developer'),
    ];
    expect(agentsByIdReduce(log)).toEqual({
      a1: { name: 'Bob', role: 'Scrum Master' },
      a2: { name: 'Amelia', role: 'Developer' },
    });
  });

  it('(AC1) a later StartMessage for the same agent_id supersedes (last-wins)', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage('a1', 'Bob', 'Scrum Master'),
      makeStartMessage('a1', 'Bobby', 'Lead'),
    ];
    expect(agentsByIdReduce(log)).toEqual({
      a1: { name: 'Bobby', role: 'Lead' },
    });
  });

  it('(NFR1) ignores non-Start messages', () => {
    const stop = {
      id: 'stop-a1',
      parent_id: null,
      team_id: TEAM_ID,
      timestamp: new Date().toISOString(),
      sender: sender('a1', 'Bob', 'Scrum Master'),
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StopMessage',
    } as unknown as AkgenticMessage;
    // A Stop does NOT remove the identity recorded by the prior Start
    // (display identity persists; membership is governed by the descriptor fold).
    const log: AkgenticMessage[] = [
      makeStartMessage('a1', 'Bob', 'Scrum Master'),
      stop,
    ];
    expect(agentsByIdReduce(log)).toEqual({
      a1: { name: 'Bob', role: 'Scrum Master' },
    });
  });
});

describe('AgentsByIdService (selector over MessageLogService.log$)', () => {
  let log: MessageLogService;
  let service: AgentsByIdService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, AgentsByIdService],
    });
    log = TestBed.inject(MessageLogService);
    service = TestBed.inject(AgentsByIdService);
  });

  function currentValue(): AgentsById {
    let v: AgentsById | undefined;
    const sub = service.agentsById$.subscribe((x) => (v = x));
    sub.unsubscribe();
    return v as AgentsById;
  }

  it('(AC1) initial empty log → empty map', () => {
    expect(currentValue()).toEqual({});
  });

  it('(AC1) live append of a StartMessage → identity registered', () => {
    log.append(makeStartMessage('a1', 'Bob', 'Scrum Master'));
    expect(currentValue()).toEqual({
      a1: { name: 'Bob', role: 'Scrum Master' },
    });
  });

  it('(AC1) distinctUntilChanged suppresses structurally identical re-emissions', () => {
    const emissions: AgentsById[] = [];
    const sub = service.agentsById$.subscribe((v) => emissions.push(v));

    const start1 = makeStartMessage('a1', 'Bob', 'Scrum Master');
    start1.id = 'a-1';
    const start2 = makeStartMessage('a1', 'Bob', 'Scrum Master');
    start2.id = 'a-2';
    log.append(start1);
    log.append(start2);

    // [initial empty, after first start]. The second start for the same agent
    // yields the same identity → structurally identical fold → suppressed.
    expect(emissions.length).toBe(2);
    sub.unsubscribe();
  });

  it('(AC1) log.reset() returns to empty map on team switch', () => {
    log.append(makeStartMessage('a1', 'Bob', 'Scrum Master'));
    expect(Object.keys(currentValue()).length).toBe(1);
    log.reset();
    expect(currentValue()).toEqual({});
  });
});
