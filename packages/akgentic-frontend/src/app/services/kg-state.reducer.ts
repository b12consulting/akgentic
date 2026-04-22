import { inject, Injectable } from '@angular/core';
import { map, Observable, shareReplay } from 'rxjs';

import { AkgenticMessage, isEventMessage } from '../models/message.types';
import { MessageLogService } from './message-log.service';

/**
 * V2 Knowledge Graph wire types.
 *
 * These mirror the Pydantic models on the backend (ADR-024):
 * - Entity: keyed by `id` (uuid string), carries display fields (`name`,
 *   `entity_type`, `description`, `observations`) and an optional `is_root`
 *   flag the backend only sends when `True`.
 * - Relation: keyed by `id` (uuid string), carries `from_entity` / `to_entity`
 *   names (not ids — the existing `KnowledgeGraphComponent` keys ECharts
 *   source/target on names), a `relation_type`, and an optional `description`.
 */
export interface KnowledgeGraphEntity {
  id: string;
  name?: string;
  entity_type?: string;
  description?: string;
  observations?: any[];
  is_root?: boolean;
}

export interface KnowledgeGraphRelation {
  id: string;
  from_entity?: string;
  to_entity?: string;
  relation_type?: string;
  description?: string;
}

export interface KnowledgeGraphData {
  nodes: KnowledgeGraphEntity[];
  edges: KnowledgeGraphRelation[];
}

/**
 * Per-`tool_id` reducer state. `entities` and `relations` are id-keyed Maps
 * so every delta (add / modify / remove) is O(1). `lastSeq` starts at 0 and
 * tracks the last successfully-applied `ToolStateEvent.seq`.
 */
interface KGPerToolState {
  entities: Map<string, KnowledgeGraphEntity>;
  relations: Map<string, KnowledgeGraphRelation>;
  lastSeq: number;
}

// ---------------------------------------------------------------------------
// Module-scope pure helpers (ADR-005 §Decision 4).
// kgFold operates on a locally-allocated Map; no instance state.
// ---------------------------------------------------------------------------

function getOrCreateState(
  stateByTool: Map<string, KGPerToolState>,
  tool_id: string,
): KGPerToolState {
  let state = stateByTool.get(tool_id);
  if (!state) {
    state = {
      entities: new Map<string, KnowledgeGraphEntity>(),
      relations: new Map<string, KnowledgeGraphRelation>(),
      lastSeq: 0,
    };
    stateByTool.set(tool_id, state);
  }
  return state;
}

function applyEntityDeltas(state: KGPerToolState, payload: any): void {
  const added: KnowledgeGraphEntity[] = payload.entities_added ?? [];
  const modified: KnowledgeGraphEntity[] = payload.entities_modified ?? [];
  const removed: string[] = payload.entities_removed ?? [];

  for (const entity of added) {
    state.entities.set(entity.id, entity);
  }
  for (const entity of modified) {
    state.entities.set(entity.id, entity);
  }
  for (const id of removed) {
    state.entities.delete(id);
  }
}

function applyRelationDeltas(state: KGPerToolState, payload: any): void {
  const added: KnowledgeGraphRelation[] = payload.relations_added ?? [];
  // `relations_modified` is always empty on the wire today (ADR-024 §1b —
  // `ManageGraph` has no relation-update op). Code path retained for
  // forward-compat; exercised by a unit test so it does not rot.
  const modified: KnowledgeGraphRelation[] = payload.relations_modified ?? [];
  const removed: string[] = payload.relations_removed ?? [];

  for (const relation of added) {
    state.relations.set(relation.id, relation);
  }
  for (const relation of modified) {
    state.relations.set(relation.id, relation);
  }
  for (const id of removed) {
    state.relations.delete(id);
  }
}

function applyToolStateEvent(
  stateByTool: Map<string, KGPerToolState>,
  inner: any,
): void {
  const tool_id: string | undefined = inner?.tool_id;
  const seq: number = inner?.seq ?? 0;
  const payload = inner?.payload;

  // FR11 payload dispatch — unknown payloads are debug-logged.
  if (!payload?.__model__?.includes('KnowledgeGraphStateEvent')) {
    console.debug('[KGStateReducer] unknown payload', {
      tool_id,
      model: payload?.__model__,
    });
    return;
  }

  if (!tool_id) {
    console.debug('[KGStateReducer] missing tool_id', { seq });
    return;
  }

  const state = getOrCreateState(stateByTool, tool_id);

  // FR10 seq-gap log (still APPLIES the event — ADR-004 §Decision 6).
  const prev = state.lastSeq;
  if (prev !== 0 && seq !== prev + 1) {
    console.warn('[KGStateReducer] seq gap', {
      prev_seq: prev,
      event_seq: seq,
      tool_id,
    });
  }

  applyEntityDeltas(state, payload);
  applyRelationDeltas(state, payload);
  state.lastSeq = seq;
}

/**
 * Pure projection from the per-tool state Map into a flattened
 * `{nodes, edges}` object. Allocates a fresh object so downstream `OnPush`
 * consumers (NFR3) always see a new reference.
 */
function projectionFromState(
  stateByTool: Map<string, KGPerToolState>,
): KnowledgeGraphData {
  const nodes: KnowledgeGraphEntity[] = [];
  const edges: KnowledgeGraphRelation[] = [];
  for (const state of stateByTool.values()) {
    for (const entity of state.entities.values()) {
      nodes.push(entity);
    }
    for (const relation of state.relations.values()) {
      edges.push(relation);
    }
  }
  return { nodes, edges };
}

/**
 * Pure fold over the message log producing the current
 * `KnowledgeGraphData` projection. Filters `EventMessage` envelopes
 * carrying `ToolStateEvent` payloads (FR11: other messages pass through
 * unchanged), applies the existing ADR-004 delta logic per-`tool_id`,
 * then flattens into `{nodes, edges}`.
 *
 * Exported so tests can import it directly (faster; no TestBed required).
 */
export function kgFold(log: AkgenticMessage[]): KnowledgeGraphData {
  const stateByTool = new Map<string, KGPerToolState>();
  for (const msg of log) {
    if (!isEventMessage(msg)) continue; // FR11 passthrough
    const inner = (msg as any).event;
    if (!inner?.__model__?.includes('ToolStateEvent')) continue;
    applyToolStateEvent(stateByTool, inner);
  }
  return projectionFromState(stateByTool);
}

/**
 * KGStateReducer — Story 6.2 (ADR-005 §Decision 4).
 *
 * Exposes `knowledgeGraph$` as a pure selector over
 * `MessageLogService.log$`. `shareReplay(1)` is load-bearing for AC4
 * (late-subscriber parity) and for avoiding redundant fold work across
 * multiple subscribers.
 *
 * Scope: component-scoped (NOT `providedIn: 'root'`) because it injects
 * `MessageLogService`, which is component-scoped on
 * `ProcessComponent.providers`.
 */
@Injectable()
export class KGStateReducer {
  private readonly log: MessageLogService = inject(MessageLogService);

  readonly knowledgeGraph$: Observable<KnowledgeGraphData> = this.log.log$.pipe(
    map(kgFold),
    shareReplay(1),
  );
}
