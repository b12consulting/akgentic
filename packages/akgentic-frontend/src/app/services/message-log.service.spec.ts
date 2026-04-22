import { TestBed } from '@angular/core/testing';

import { AkgenticMessage } from '../models/message.types';
import { MessageLogService, messageListFold } from './message-log.service';

function msg(
  id: string,
  model: string = 'StartMessage',
  senderRole: string = 'Worker',
): AkgenticMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-04-13T00:00:00Z',
    sender: {
      __actor_address__: true,
      name: '@X',
      role: senderRole,
      agent_id: 'a',
      team_id: 'team-1',
      squad_id: 's',
      user_message: false,
    },
    display_type: 'other',
    content: null,
    __model__: `akgentic.core.messages.orchestrator.${model}`,
  } as AkgenticMessage;
}

describe('MessageLogService (Story 6.1)', () => {
  let service: MessageLogService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [MessageLogService] });
    service = TestBed.inject(MessageLogService);
  });

  it('AC1: log$ emits [] on subscribe and snapshot() returns []', () => {
    let observed: AkgenticMessage[] | null = null;
    const sub = service.log$.subscribe((v) => (observed = v));
    expect(observed as AkgenticMessage[] | null).toEqual([]);
    expect(service.snapshot()).toEqual([]);
    sub.unsubscribe();
  });

  it('append: single message produces one new emission and preserves order', () => {
    const emissions: AkgenticMessage[][] = [];
    const sub = service.log$.subscribe((v) => emissions.push(v));
    service.append(msg('1'));
    service.append(msg('2'));
    // Initial [] + two appends = 3 emissions.
    expect(emissions.length).toBe(3);
    expect(emissions[0]).toEqual([]);
    expect(emissions[1].map((m) => m.id)).toEqual(['1']);
    expect(emissions[2].map((m) => m.id)).toEqual(['1', '2']);
    sub.unsubscribe();
  });

  it('appendAll: batch of N produces ONE emission with N messages in arrival order', () => {
    const emissions: AkgenticMessage[][] = [];
    const sub = service.log$.subscribe((v) => emissions.push(v));
    service.appendAll([msg('a'), msg('b'), msg('c')]);
    // Initial [] + one batch append = 2 emissions.
    expect(emissions.length).toBe(2);
    expect(emissions[1].map((m) => m.id)).toEqual(['a', 'b', 'c']);
    sub.unsubscribe();
  });

  it('appendAll: empty batch is a no-op (no emission, no snapshot change)', () => {
    const emissions: AkgenticMessage[][] = [];
    const sub = service.log$.subscribe((v) => emissions.push(v));
    service.appendAll([]);
    expect(emissions.length).toBe(1); // only the initial []
    expect(service.snapshot()).toEqual([]);
    sub.unsubscribe();
  });

  it('appendAll emits a NEW array reference (OnPush-safe, NFR3)', () => {
    const before = service.snapshot();
    service.appendAll([msg('1')]);
    const after = service.snapshot();
    expect(after).not.toBe(before);
  });

  it('reset() empties the log to [] and emits', () => {
    service.appendAll([msg('1'), msg('2')]);
    expect(service.snapshot().length).toBe(2);

    const emissions: AkgenticMessage[][] = [];
    const sub = service.log$.subscribe((v) => emissions.push(v));
    service.reset();
    // Initial ([1,2]) + post-reset ([]) = 2 emissions.
    expect(emissions.length).toBe(2);
    expect(emissions[1]).toEqual([]);
    expect(service.snapshot()).toEqual([]);
    sub.unsubscribe();
  });

  it('snapshot() returns the synchronous current value after appends', () => {
    service.append(msg('1'));
    service.appendAll([msg('2'), msg('3')]);
    expect(service.snapshot().map((m) => m.id)).toEqual(['1', '2', '3']);
  });
});

// ---------------------------------------------------------------------------
// Story 6.4 (AC4) — messageList$ selector
// ---------------------------------------------------------------------------

describe('messageListFold (Story 6.4, AC4)', () => {
  it('empty log → []', () => {
    expect(messageListFold([])).toEqual([]);
  });

  it('filters to SentMessage and ErrorMessage only', () => {
    const log: AkgenticMessage[] = [
      msg('s1', 'SentMessage'),
      msg('st1', 'StartMessage'),
      msg('e1', 'ErrorMessage'),
      msg('sc1', 'StateChangedMessage'),
      msg('ev1', 'EventMessage'),
      msg('r1', 'ReceivedMessage'),
    ];
    const out = messageListFold(log);
    expect(out.map((m) => m.id)).toEqual(['s1', 'e1']);
  });

  it('excludes ActorSystem senders', () => {
    const log: AkgenticMessage[] = [
      msg('s1', 'SentMessage', 'ActorSystem'),
      msg('s2', 'SentMessage', 'Worker'),
      msg('e1', 'ErrorMessage', 'ActorSystem'),
    ];
    const out = messageListFold(log);
    expect(out.map((m) => m.id)).toEqual(['s2']);
  });

  it('FR11 passthrough: messages with missing/unknown __model__ are silently excluded (no throw)', () => {
    const unknown = { ...msg('x1', 'SentMessage'), __model__: undefined as any };
    const empty = { ...msg('x2', 'SentMessage'), __model__: '' as any };
    const good = msg('x3', 'SentMessage');
    expect(() => messageListFold([unknown, empty, good])).not.toThrow();
    const out = messageListFold([unknown, empty, good]);
    expect(out.map((m) => m.id)).toEqual(['x3']);
  });

  it('preserves arrival order across a mixed log', () => {
    const log: AkgenticMessage[] = [
      msg('a', 'SentMessage'),
      msg('b', 'StartMessage'),
      msg('c', 'ErrorMessage'),
      msg('d', 'SentMessage'),
    ];
    expect(messageListFold(log).map((m) => m.id)).toEqual(['a', 'c', 'd']);
  });
});

describe('MessageLogService.messageList$ (Story 6.4, AC4)', () => {
  let service: MessageLogService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [MessageLogService] });
    service = TestBed.inject(MessageLogService);
  });

  it('emits [] on subscribe when log is empty', () => {
    let observed: AkgenticMessage[] | null = null;
    const sub = service.messageList$.subscribe((v) => (observed = v));
    expect(observed as AkgenticMessage[] | null).toEqual([]);
    sub.unsubscribe();
  });

  it('emits the filtered slice when the log changes', () => {
    const emissions: AkgenticMessage[][] = [];
    const sub = service.messageList$.subscribe((v) => emissions.push(v));

    // Initial [] from the seed log.
    expect(emissions.length).toBe(1);
    expect(emissions[0]).toEqual([]);

    // A non-relevant message still triggers a log$ emission; the filter
    // produces a fresh [] (new reference) so distinctUntilChanged passes
    // through (default reference comparison). This is intentional — OnPush
    // consumers rely on a new reference to re-evaluate (NFR3).
    service.append(msg('st1', 'StartMessage'));
    expect(emissions[emissions.length - 1]).toEqual([]);

    // Append a relevant message — slice now contains the SentMessage.
    service.append(msg('s1', 'SentMessage'));
    const last = emissions[emissions.length - 1];
    expect(last.map((m) => m.id)).toEqual(['s1']);

    sub.unsubscribe();
  });
});
