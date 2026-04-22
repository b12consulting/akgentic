import { TestBed } from '@angular/core/testing';

import {
  AkgenticMessage,
  StartMessage,
  StopMessage,
} from '../models/message.types';
import { MessageLogService } from './message-log.service';
import {
  KG_ACTOR_NAME,
  presenceReduce,
  ToolPresenceService,
} from './tool-presence.service';

// ---------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------

function baseSender(name: string) {
  return {
    __actor_address__: true as const,
    agent_id: 'agent-' + name,
    name,
    role: 'Agent',
    squad_id: 's1',
    user_message: false,
  };
}

function makeStartMessage(senderName: string): StartMessage {
  return {
    id: 'start-' + senderName,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: baseSender(senderName),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: {} as any,
    parent: null,
  };
}

function makeStopMessage(senderName: string): StopMessage {
  return {
    id: 'stop-' + senderName,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: baseSender(senderName),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StopMessage',
  };
}

describe('presenceReduce (pure function)', () => {
  it('empty log → false', () => {
    expect(presenceReduce([])).toBe(false);
  });

  it('KG StartMessage → true', () => {
    expect(presenceReduce([makeStartMessage(KG_ACTOR_NAME)])).toBe(true);
  });

  it('non-KG StartMessage → false (irrelevant sender ignored)', () => {
    expect(presenceReduce([makeStartMessage('@Worker')])).toBe(false);
    expect(presenceReduce([makeStartMessage('#VectorStoreTool')])).toBe(false);
  });

  it('(AC2) ordered-reduce — Start → Stop → Start ends as true (NOT some()&&!some() semantics)', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage(KG_ACTOR_NAME),
      makeStopMessage(KG_ACTOR_NAME),
      makeStartMessage(KG_ACTOR_NAME),
    ];
    expect(presenceReduce(log)).toBe(true);
  });

  it('Start → Stop ends as false', () => {
    const log: AkgenticMessage[] = [
      makeStartMessage(KG_ACTOR_NAME),
      makeStopMessage(KG_ACTOR_NAME),
    ];
    expect(presenceReduce(log)).toBe(false);
  });
});

describe('ToolPresenceService (selector over MessageLogService.log$)', () => {
  let log: MessageLogService;
  let service: ToolPresenceService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, ToolPresenceService],
    });
    log = TestBed.inject(MessageLogService);
    service = TestBed.inject(ToolPresenceService);
  });

  function currentValue(): boolean {
    let v: boolean | undefined;
    const sub = service.hasKnowledgeGraph$.subscribe((x) => (v = x));
    sub.unsubscribe();
    return v as boolean;
  }

  it('(1) initial — hasKnowledgeGraph$ emits false on empty log', () => {
    expect(currentValue()).toBe(false);
  });

  it('(2) KG StartMessage via live append → true', () => {
    log.append(makeStartMessage(KG_ACTOR_NAME));
    expect(currentValue()).toBe(true);
  });

  it('(3) KG StartMessage via appendAll (REST-replay batch) → true', () => {
    log.appendAll([makeStartMessage(KG_ACTOR_NAME)]);
    expect(currentValue()).toBe(true);
  });

  it('(4) irrelevant senders ignored — stays false', () => {
    log.append(makeStartMessage('@Support'));
    log.append(makeStartMessage('#VectorStoreTool'));
    expect(currentValue()).toBe(false);
  });

  it('(5) KG StopMessage flips presence back to false', () => {
    log.append(makeStartMessage(KG_ACTOR_NAME));
    expect(currentValue()).toBe(true);
    log.append(makeStopMessage(KG_ACTOR_NAME));
    expect(currentValue()).toBe(false);
  });

  it('(6) log.reset() flips presence back to false on team switch', () => {
    log.append(makeStartMessage(KG_ACTOR_NAME));
    expect(currentValue()).toBe(true);
    log.reset();
    expect(currentValue()).toBe(false);
  });

  it('(7) redundant StartMessages do not emit true twice (distinctUntilChanged)', () => {
    const emissions: boolean[] = [];
    const sub = service.hasKnowledgeGraph$.subscribe((v) => emissions.push(v));

    log.append(makeStartMessage(KG_ACTOR_NAME));
    log.append(makeStartMessage(KG_ACTOR_NAME));

    // Expected: [false (initial empty log), true (first KG start)].
    // The second KG StartMessage does NOT produce a third emission because
    // the folded value is still `true` and distinctUntilChanged suppresses.
    expect(emissions).toEqual([false, true]);
    sub.unsubscribe();
  });

  it('(AC2) ordered-reduce restart — Start → Stop → Start ends as true', () => {
    // MessageLogService.append() dedupes by msg.id. makeStartMessage uses a
    // deterministic id derived from the sender name, so two raw append(start)
    // calls would drop the second. Assign unique ids per event to model a
    // real restart sequence (Start-1 → Stop-1 → Start-2).
    const start1 = makeStartMessage(KG_ACTOR_NAME);
    start1.id = 'kg-start-1';
    const stop1 = makeStopMessage(KG_ACTOR_NAME);
    stop1.id = 'kg-stop-1';
    const start2 = makeStartMessage(KG_ACTOR_NAME);
    start2.id = 'kg-start-2';

    log.append(start1);
    log.append(stop1);
    log.append(start2);
    expect(currentValue()).toBe(true);
  });

  it('(AC4) late subscriber — current value delivered synchronously on subscribe', () => {
    log.append(makeStartMessage(KG_ACTOR_NAME));

    let received: boolean | undefined;
    const sub = service.hasKnowledgeGraph$.subscribe((v) => (received = v));
    expect(received).toBe(true);
    sub.unsubscribe();
  });
});
