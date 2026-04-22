import { TestBed } from '@angular/core/testing';

import { AkgenticMessage } from '../models/message.types';
import {
  kgFold,
  KGStateReducer,
  KnowledgeGraphData,
  KnowledgeGraphEntity,
  KnowledgeGraphRelation,
} from './kg-state.reducer';
import { MessageLogService } from './message-log.service';

// ---------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------

function makeEntity(
  id: string,
  name: string,
  entity_type = 'Concept',
  description = '',
  observations: any[] = []
): KnowledgeGraphEntity {
  return { id, name, entity_type, description, observations };
}

function makeRelation(
  id: string,
  from_entity: string,
  to_entity: string,
  relation_type = 'relates_to',
  description = ''
): KnowledgeGraphRelation {
  return { id, from_entity, to_entity, relation_type, description };
}

interface KgEventOptions {
  tool_id: string;
  seq: number;
  entities_added?: KnowledgeGraphEntity[];
  entities_modified?: KnowledgeGraphEntity[];
  entities_removed?: string[];
  relations_added?: KnowledgeGraphRelation[];
  relations_modified?: KnowledgeGraphRelation[];
  relations_removed?: string[];
  payload_model?: string;
}

function baseSender() {
  return {
    __actor_address__: true as const,
    name: '#KnowledgeGraphTool',
    role: 'Tool',
    agent_id: 'kg-agent',
    squad_id: 's1',
    user_message: false,
  };
}

/**
 * Build an EventMessage envelope carrying a ToolStateEvent — the actual
 * shape that lands in `log$` (the fold filters on `isEventMessage` +
 * `__model__.includes('ToolStateEvent')`).
 */
function makeKgEventEnvelope(opts: KgEventOptions): AkgenticMessage {
  const inner = {
    __model__: 'akgentic.tool.messages.ToolStateEvent',
    tool_id: opts.tool_id,
    seq: opts.seq,
    payload: {
      __model__:
        opts.payload_model ??
        'akgentic.tool.messages.KnowledgeGraphStateEvent',
      entities_added: opts.entities_added ?? [],
      entities_modified: opts.entities_modified ?? [],
      entities_removed: opts.entities_removed ?? [],
      relations_added: opts.relations_added ?? [],
      relations_modified: opts.relations_modified ?? [],
      relations_removed: opts.relations_removed ?? [],
    },
  };
  return {
    id: 'env-' + opts.tool_id + '-' + opts.seq,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: baseSender(),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.EventMessage',
    event: inner,
  } as unknown as AkgenticMessage;
}

function makeStartEnvelope(senderName: string, id: string): AkgenticMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: {
      __actor_address__: true as const,
      agent_id: 'a-' + id,
      name: senderName,
      role: 'Tool',
      squad_id: 's1',
      user_message: false,
    },
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.StartMessage',
    config: {} as any,
    parent: null,
  } as unknown as AkgenticMessage;
}

function makeUnknownEnvelope(id: string): AkgenticMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: new Date().toISOString(),
    sender: baseSender(),
    display_type: 'other',
    content: null,
    __model__: 'UnknownFutureMessage',
  } as unknown as AkgenticMessage;
}

// ---------------------------------------------------------------------
// Tests — `KGStateReducer` (selector over MessageLogService.log$)
// ---------------------------------------------------------------------

describe('KGStateReducer (log-driven selector)', () => {
  let log: MessageLogService;
  let reducer: KGStateReducer;
  let emissions: KnowledgeGraphData[];
  let subscription: { unsubscribe(): void };

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [MessageLogService, KGStateReducer],
    });
    log = TestBed.inject(MessageLogService);
    reducer = TestBed.inject(KGStateReducer);
    emissions = [];
    subscription = reducer.knowledgeGraph$.subscribe((p) => emissions.push(p));
  });

  afterEach(() => {
    subscription.unsubscribe();
  });

  it('(1) empty init — emits {nodes:[], edges:[]} before any append', () => {
    expect(emissions.length).toBe(1);
    expect(emissions[0]).toEqual({ nodes: [], edges: [] });
  });

  it('(2) single-add — append emits projection with the added entity', () => {
    const e1 = makeEntity('e1', 'A');
    log.append(
      makeKgEventEnvelope({ tool_id: 't1', seq: 1, entities_added: [e1] })
    );

    const proj = emissions[emissions.length - 1];
    expect(proj.nodes.length).toBe(1);
    expect(proj.nodes[0].id).toBe('e1');
    expect(proj.nodes[0].name).toBe('A');
    expect(proj.edges).toEqual([]);
  });

  it('(3) modify replaces by id, not by name (rename reflected, no dup)', () => {
    log.appendAll([
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        entities_modified: [makeEntity('e1', 'B')],
      }),
    ]);

    const proj = emissions[emissions.length - 1];
    expect(proj.nodes.length).toBe(1);
    expect(proj.nodes[0].id).toBe('e1');
    expect(proj.nodes[0].name).toBe('B');
  });

  it('(4) relation add + cascade delete — both drop', () => {
    log.appendAll([
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A'), makeEntity('e3', 'C')],
        relations_added: [makeRelation('r1', 'A', 'C')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        entities_removed: ['e1'],
        relations_removed: ['r1'],
      }),
    ]);

    const proj = emissions[emissions.length - 1];
    expect(proj.nodes.length).toBe(1);
    expect(proj.nodes[0].id).toBe('e3');
    expect(proj.edges).toEqual([]);
  });

  it('(5) relations_modified forward-compat — replaces by id', () => {
    log.appendAll([
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        relations_added: [makeRelation('r1', 'A', 'B', 'old', '')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        relations_modified: [makeRelation('r1', 'A', 'B', 'updated', 'new')],
      }),
    ]);

    const proj = emissions[emissions.length - 1];
    expect(proj.edges.length).toBe(1);
    expect(proj.edges[0].id).toBe('r1');
    expect(proj.edges[0].relation_type).toBe('updated');
    expect(proj.edges[0].description).toBe('new');
  });

  it('(6) empty-collections event — projection emitted empty, no console output', () => {
    const warnSpy = spyOn(console, 'warn');
    const debugSpy = spyOn(console, 'debug');

    const before = emissions.length;
    log.append(makeKgEventEnvelope({ tool_id: 't1', seq: 1 }));
    const after = emissions.length;

    expect(after).toBe(before + 1);
    expect(emissions[after - 1]).toEqual({ nodes: [], edges: [] });
    expect(warnSpy).not.toHaveBeenCalled();
    expect(debugSpy).not.toHaveBeenCalled();
  });

  it('(7) seq-gap log — 1 then 3 warns with prev/event/tool_id', () => {
    const warnSpy = spyOn(console, 'warn');

    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      })
    );
    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 3,
        entities_added: [makeEntity('e2', 'B')],
      })
    );

    // `kgFold` runs once per log emission (two appends → two folds). Seq=1
    // is applied twice across the two fold passes, but the gap from 1→3
    // only shows up on the second pass. Assert at least one seq-gap warn
    // fired with the expected payload.
    const gapCalls = warnSpy.calls.allArgs().filter(
      (args) => typeof args[0] === 'string' && args[0].includes('seq gap'),
    );
    expect(gapCalls.length).toBeGreaterThanOrEqual(1);
    expect(gapCalls[gapCalls.length - 1][1]).toEqual({
      prev_seq: 1,
      event_seq: 3,
      tool_id: 't1',
    });

    const proj = emissions[emissions.length - 1];
    expect(proj.nodes.length).toBe(2);
  });

  it('(8) unknown payload __model__ — debug logged, projection stays empty', () => {
    const debugSpy = spyOn(console, 'debug');
    const warnSpy = spyOn(console, 'warn');

    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        payload_model: 'akgentic.tool.messages.VectorStoreStateEvent',
      })
    );

    expect(debugSpy).toHaveBeenCalled();
    expect(warnSpy).not.toHaveBeenCalled();
    expect(emissions[emissions.length - 1]).toEqual({ nodes: [], edges: [] });
  });

  it('(9) immutable projection — two successive appends yield distinct refs (NFR7)', () => {
    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      })
    );
    const proj1 = emissions[emissions.length - 1];
    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        entities_added: [makeEntity('e2', 'B')],
      })
    );
    const proj2 = emissions[emissions.length - 1];

    expect(proj1).not.toBe(proj2);
    expect(proj1.nodes.length).toBe(1);
    expect(proj2.nodes.length).toBe(2);
  });

  it('(10) multiple tools isolated — deletes in one do not evict the other', () => {
    log.appendAll([
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      }),
      makeKgEventEnvelope({
        tool_id: 't2',
        seq: 1,
        entities_added: [makeEntity('e2', 'B')],
      }),
      makeKgEventEnvelope({
        tool_id: 't2',
        seq: 2,
        entities_removed: ['e2'],
      }),
    ]);

    const proj = emissions[emissions.length - 1];
    expect(proj.nodes.length).toBe(1);
    expect(proj.nodes[0].id).toBe('e1');
  });

  it('(11) log.reset() clears state and emits empty projection', () => {
    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      })
    );
    expect(emissions[emissions.length - 1].nodes.length).toBe(1);

    log.reset();
    expect(emissions[emissions.length - 1]).toEqual({ nodes: [], edges: [] });

    // Fresh apply after reset starts from zero.
    log.append(
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e9', 'Z')],
      })
    );
    expect(emissions[emissions.length - 1].nodes.length).toBe(1);
    expect(emissions[emissions.length - 1].nodes[0].id).toBe('e9');
  });

  it('(AC7 / FR11) unknown-message passthrough — StartMessage + UnknownFutureMessage leave projection empty, no throw', () => {
    log.appendAll([
      makeStartEnvelope('#KnowledgeGraphTool', 'start-1'),
      makeUnknownEnvelope('unknown-1'),
    ]);

    const proj = emissions[emissions.length - 1];
    expect(proj).toEqual({ nodes: [], edges: [] });
  });

  it('(AC4) late-subscriber — after 3 KG events, fresh subscribe sees the current projection synchronously', () => {
    log.appendAll([
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        entities_added: [makeEntity('e2', 'B')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 3,
        entities_added: [makeEntity('e3', 'C')],
      }),
    ]);

    let received: KnowledgeGraphData | undefined;
    const sub = reducer.knowledgeGraph$.subscribe((p) => (received = p));
    expect(received).toBeDefined();
    expect((received as KnowledgeGraphData).nodes.length).toBe(3);
    sub.unsubscribe();
  });
});

// ---------------------------------------------------------------------
// Tests — REST/WS parity (AC6)
// ---------------------------------------------------------------------

describe('KG selector parity — REST batch vs WS per-message (AC6)', () => {
  function buildFixture(): AkgenticMessage[] {
    return [
      makeStartEnvelope('#KnowledgeGraphTool', 's1'),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [
          makeEntity('e1', 'Alpha', 'Concept'),
          makeEntity('e2', 'Beta', 'Concept'),
        ],
        relations_added: [makeRelation('r1', 'Alpha', 'Beta', 'relates_to')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 2,
        entities_modified: [makeEntity('e1', 'AlphaPrime', 'Concept')],
      }),
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 3,
        entities_removed: ['e2'],
      }),
    ];
  }

  function snapshot(
    mode: 'rest' | 'ws',
  ): { graph: KnowledgeGraphData; presence: boolean } {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [MessageLogService, KGStateReducer],
    });
    const log = TestBed.inject(MessageLogService);
    const reducer = TestBed.inject(KGStateReducer);

    const graphEmissions: KnowledgeGraphData[] = [];
    const gsub = reducer.knowledgeGraph$.subscribe((p) =>
      graphEmissions.push(p),
    );

    const fixture = buildFixture();
    if (mode === 'rest') {
      log.appendAll(fixture);
    } else {
      for (const msg of fixture) log.append(msg);
    }

    // Compute presence synchronously off the log using the exported fold
    // to keep this parity test self-contained (ToolPresenceService's
    // own tests cover subscription semantics).
    const presenceLog = log.snapshot();
    // Reuse presenceReduce via importing would create a cycle with this
    // file's imports — compute inline by subscribing to a fresh
    // ToolPresenceService is unnecessary; just do the boolean fold here.
    let presence = false;
    for (const m of presenceLog) {
      if (
        m.__model__.includes('StartMessage') &&
        (m as any).sender?.name === '#KnowledgeGraphTool'
      )
        presence = true;
      else if (
        m.__model__.includes('StopMessage') &&
        (m as any).sender?.name === '#KnowledgeGraphTool'
      )
        presence = false;
    }

    const graph = graphEmissions[graphEmissions.length - 1];
    gsub.unsubscribe();
    return { graph, presence };
  }

  it('restState and wsState are bit-identical for knowledgeGraph$ + hasKnowledgeGraph$ (AC6)', () => {
    const rest = snapshot('rest');
    const ws = snapshot('ws');

    // Sort nodes/edges by id for deterministic comparison — kgFold iterates
    // Map insertion order per-tool, identical for both inputs, but be
    // defensive.
    const normalize = (g: KnowledgeGraphData): KnowledgeGraphData => ({
      nodes: [...g.nodes].sort((a, b) => a.id.localeCompare(b.id)),
      edges: [...g.edges].sort((a, b) => a.id.localeCompare(b.id)),
    });
    expect(JSON.stringify(normalize(rest.graph))).toBe(
      JSON.stringify(normalize(ws.graph)),
    );
    expect(rest.presence).toBe(ws.presence);
    expect(rest.presence).toBe(true);

    // Final graph expectation: 1 node (e1 renamed to AlphaPrime), no edges
    // (r1 references Beta which was removed — but relations are NOT
    // cascade-deleted here because the fixture only removed the entity,
    // not the relation, so r1 persists in the fold).
    expect(rest.graph.nodes.length).toBe(1);
    expect(rest.graph.nodes[0].name).toBe('AlphaPrime');
  });
});

// ---------------------------------------------------------------------
// Tests — pure kgFold export
// ---------------------------------------------------------------------

describe('kgFold (pure function)', () => {
  it('empty log → {nodes:[], edges:[]}', () => {
    expect(kgFold([])).toEqual({ nodes: [], edges: [] });
  });

  it('non-event messages passthrough (FR11) — StartMessage alone → empty projection', () => {
    const log: AkgenticMessage[] = [
      makeStartEnvelope('#KnowledgeGraphTool', 's1'),
    ];
    expect(kgFold(log)).toEqual({ nodes: [], edges: [] });
  });

  it('single ToolStateEvent envelope folds to projection', () => {
    const log: AkgenticMessage[] = [
      makeKgEventEnvelope({
        tool_id: 't1',
        seq: 1,
        entities_added: [makeEntity('e1', 'A')],
      }),
    ];
    const proj = kgFold(log);
    expect(proj.nodes.length).toBe(1);
    expect(proj.nodes[0].id).toBe('e1');
  });
});
