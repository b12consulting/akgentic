import { inject, Injectable } from '@angular/core';
import { BehaviorSubject, distinctUntilChanged, map, Observable, shareReplay } from 'rxjs';

import { ENTRY_POINT_NAME } from '../models/chat-message.model';
import {
  AkgenticMessage,
  ErrorMessage,
  isErrorMessage,
  isProcessedMessage,
  isReceivedMessage,
  isSentMessage,
  isStartMessage,
  isStopMessage,
  ProcessedMessage,
  ReceivedMessage,
  SentMessage,
  StartMessage,
  StopMessage,
} from '../models/message.types';
import { EdgeInterface, NodeInterface } from '../models/types';
import { CategoryService } from './category.service';
import { MessageLogService } from './message-log.service';

export const HUMAN_ROLE = 'Human';
export const ORCHESTRATOR_CLASS = 'akgentic.core.orchestrator.Orchestrator';

/**
 * Helper class to build nodes/edges from messages.
 */
export class GraphBuilder {
  message: AkgenticMessage;
  constructor(msg: AkgenticMessage) {
    this.message = msg;
  }

  get source() {
    return this.message.sender?.agent_id || '';
  }

  get target() {
    if (isSentMessage(this.message)) {
      return this.message.recipient?.agent_id || '';
    }
    throw new Error('Message does not have a recipient');
  }

  buildEdge(): EdgeInterface {
    if (!this.message.__model__.includes('SentMessage')) {
      throw new Error('Invalid message type for edge');
    }
    return {
      source: this.source,
      target: this.target,
    };
  }

  isNewEdge(edges: any[]) {
    return !edges.find(
      (e) => e.source === this.source && e.target === this.target
    );
  }

  buildNode(): NodeInterface {
    if (!isStartMessage(this.message)) {
      throw new Error('Invalid message type for node');
    }
    const userProxy = this.message.sender.role === HUMAN_ROLE;
    return {
      name: this.message.sender.agent_id,
      role: this.message.sender.role,
      actorName: this.message.sender.name,
      parentId: this.message.parent?.agent_id ?? '',
      squadId: this.message.sender.squad_id || '',
      userMessage: this.message.sender.user_message || false,
      symbol: userProxy ? 'circle' : 'roundRect',
      category: 0,
    };
  }

  /**
   * Pure variant of `setHumanRequest`: returns a new nodes array with the
   * targeted node replaced by a new object carrying an updated
   * `humanRequests`. Returns the SAME `nodes` reference when no change is
   * required (AC7 reference-equality contract).
   */
  setHumanRequestPure(nodes: NodeInterface[]): NodeInterface[] {
    if (!isSentMessage(this.message)) return nodes;
    if (!this.message.recipient.role.includes(HUMAN_ROLE)) return nodes;
    if (this.message.recipient.name === ENTRY_POINT_NAME) return nodes;
    if (this.message.message.display_type !== 'other') return nodes;

    const senderId = this.message.sender?.agent_id;
    const idx = nodes.findIndex((n) => n.name === senderId);
    if (idx === -1) return nodes;
    const target = nodes[idx];
    const updated: NodeInterface = {
      ...target,
      humanRequests: [...(target.humanRequests || []), this.message],
    };
    return [...nodes.slice(0, idx), updated, ...nodes.slice(idx + 1)];
  }

  /**
   * Pure variant of `unSetHumanRequest`: returns a new nodes array with the
   * targeted node replaced by a new object whose `humanRequests` has the
   * answered entry removed. Returns the SAME `nodes` reference when no change
   * is required (AC7 reference-equality contract).
   */
  unSetHumanRequestPure(nodes: NodeInterface[]): NodeInterface[] {
    if (!isSentMessage(this.message)) return nodes;
    if (this.message.sender?.role !== HUMAN_ROLE) return nodes;
    const recipientId = this.message.recipient?.agent_id;
    const parentMessageId = this.message.message.parent_id;
    const idx = nodes.findIndex((n) => n.name === recipientId);
    if (idx === -1) return nodes;
    const target = nodes[idx];
    if (!target.humanRequests) return nodes;
    const filtered = target.humanRequests.filter(
      (m: SentMessage) => m.message.id !== parentMessageId,
    );
    if (filtered.length === target.humanRequests.length) return nodes;
    const updated: NodeInterface = { ...target, humanRequests: filtered };
    return [...nodes.slice(0, idx), updated, ...nodes.slice(idx + 1)];
  }
}

/**
 * Story 6.3 (ADR-005 §Decision 4, FR6) — pure graph state.
 *
 * Mirrors the three `BehaviorSubject` slices the old imperative
 * `GraphDataService` exposed (nodes$/edges$/categories$). `EMPTY_GRAPH`
 * is the seed for `graphFold` and the late-subscriber initial value.
 */
export interface GraphState {
  nodes: NodeInterface[];
  edges: EdgeInterface[];
  squad: any[];
}

export const EMPTY_GRAPH: GraphState = { nodes: [], edges: [], squad: [] };

// ---------------------------------------------------------------------------
// Module-scope pure helpers (Task 1.2) — one per domain rule. Each returns the
// SAME state reference for no-op cases (AC7 reference-equality contract) and a
// fresh object with ONLY changed slices replaced for changes.
//
// Path 1 (Task 1.4): `CategoryService` is passed through as a companion DI
// dependency. `squadDict` / `nodes` mutations on the service are documented
// side effects (idempotent — same squadId always maps to the same index).
// External consumers (MessageListComponent, tree/graph components) continue
// to read `categoryService.squadDict` / `.COLORS` / `.nodes` as before.
// ---------------------------------------------------------------------------

function applyStartMessage(
  state: GraphState,
  msg: StartMessage,
  categoryService: CategoryService,
): GraphState {
  if (msg.sender.__actor_type__ === ORCHESTRATOR_CLASS) return state;

  const builder = new GraphBuilder(msg);
  const node = builder.buildNode();
  const nextNodes = [...state.nodes, node];

  const existingCat = state.squad.find((c) => c.squadId === node.squadId);
  let nextSquad = state.squad;
  if (existingCat && node.squadId) {
    node.category = categoryService.squadDict[node.squadId];
  } else if (node.squadId) {
    const newIndex = state.squad.length;
    node.category = newIndex;
    nextSquad = [
      ...state.squad,
      {
        name: `Team ${newIndex}`,
        squadId: node.squadId,
        itemStyle: { color: categoryService.COLORS[newIndex] },
      },
    ];
    categoryService.squadDict[node.squadId] = newIndex;
    const selectedSquad = categoryService.getSelectedCategory();
    if (selectedSquad) {
      selectedSquad.push(true);
      categoryService.setSelectedCategory(selectedSquad);
    }
  }

  // Keep companion `categoryService.nodes` in sync with the fold state so
  // downstream components that read from it (MessageListComponent) stay
  // consistent with `graph$.nodes` (Path 1 — Task 1.5 kept-stateful branch).
  categoryService.nodes = nextNodes;

  return { ...state, nodes: nextNodes, squad: nextSquad };
}

function applySentMessage(state: GraphState, msg: SentMessage): GraphState {
  const builder = new GraphBuilder(msg);
  let nextEdges = state.edges;
  if (builder.isNewEdge(state.edges)) {
    nextEdges = [...state.edges, builder.buildEdge()];
  }
  // Immutable human-request bookkeeping (AC7): return a fresh nodes array
  // with only the targeted node replaced when changes occur; otherwise the
  // same reference. Replaces the former in-place mutation of
  // `node.humanRequests` which silently skipped OnPush change detection.
  let nextNodes = state.nodes;
  nextNodes = builder.setHumanRequestPure(nextNodes);
  nextNodes = builder.unSetHumanRequestPure(nextNodes);

  // Clear error state if the sender recovered (sent a message after an error).
  const senderId = msg.sender?.agent_id;
  if (senderId) {
    const idx = nextNodes.findIndex((n) => n.name === senderId);
    if (idx !== -1 && nextNodes[idx].errorMessage) {
      const { color: _, ...restStyle } = nextNodes[idx].itemStyle || {};
      const updated: NodeInterface = {
        ...nextNodes[idx],
        errorMessage: undefined,
        itemStyle: restStyle,
      };
      nextNodes = [...nextNodes.slice(0, idx), updated, ...nextNodes.slice(idx + 1)];
    }
  }

  if (nextEdges === state.edges && nextNodes === state.nodes) return state;
  return { ...state, edges: nextEdges, nodes: nextNodes };
}

function applyReceivedMessage(
  state: GraphState,
  msg: ReceivedMessage,
): GraphState {
  // Human-role agents (HumanProxy) are waiting for user input, not thinking.
  if (msg.sender?.role === HUMAN_ROLE) return state;
  const idx = state.nodes.findIndex((n) => n.name === msg.sender?.agent_id);
  if (idx === -1) return state;
  const target = state.nodes[idx];
  const updated: NodeInterface = {
    ...target,
    itemStyle: {
      ...(target.itemStyle || {}),
      borderColor: 'darkred',
      borderWidth: 3,
    },
  };
  const nextNodes = [
    ...state.nodes.slice(0, idx),
    updated,
    ...state.nodes.slice(idx + 1),
  ];
  return { ...state, nodes: nextNodes };
}

function applyProcessedMessage(
  state: GraphState,
  msg: ProcessedMessage,
): GraphState {
  const idx = state.nodes.findIndex((n) => n.name === msg.sender?.agent_id);
  if (idx === -1) return state;
  const target = state.nodes[idx];
  if (!target.itemStyle) return state;
  // Strip thinking-border properties without mutating the existing itemStyle.
  const { borderColor: _bc, borderWidth: _bw, ...restStyle } = target.itemStyle;
  // No-op if neither border property was set — preserve slice identity.
  if (_bc === undefined && _bw === undefined) return state;
  const updated: NodeInterface = { ...target, itemStyle: restStyle };
  const nextNodes = [
    ...state.nodes.slice(0, idx),
    updated,
    ...state.nodes.slice(idx + 1),
  ];
  return { ...state, nodes: nextNodes };
}

function applyStopMessage(state: GraphState, msg: StopMessage): GraphState {
  const idx = state.nodes.findIndex((n) => n.name === msg.sender?.agent_id);
  if (idx === -1) return state;
  const nextNodes = [...state.nodes.slice(0, idx), ...state.nodes.slice(idx + 1)];
  return { ...state, nodes: nextNodes };
}

function applyErrorMessage(state: GraphState, msg: ErrorMessage): GraphState {
  const idx = state.nodes.findIndex((n) => n.name === msg.sender?.agent_id);
  if (idx === -1) return state;
  const target = state.nodes[idx];
  const updated: NodeInterface = {
    ...target,
    errorMessage: msg.exception_value || msg.exception_type || 'Error',
    itemStyle: { ...(target.itemStyle || {}), color: 'darkred' },
  };
  const nextNodes = [
    ...state.nodes.slice(0, idx),
    updated,
    ...state.nodes.slice(idx + 1),
  ];
  return { ...state, nodes: nextNodes };
}

/**
 * Pure per-message transition (Task 1.3). Discriminates on `__model__` and
 * delegates to a helper. Returns `state` unchanged for unhandled
 * discriminants (FR11 passthrough — AC6).
 */
export function graphStep(
  state: GraphState,
  msg: AkgenticMessage,
  categoryService: CategoryService,
): GraphState {
  if (!msg?.__model__) return state;
  const kind = msg.__model__.split('.').pop();
  switch (kind) {
    case 'StartMessage':
      return isStartMessage(msg)
        ? applyStartMessage(state, msg, categoryService)
        : state;
    case 'SentMessage':
      return isSentMessage(msg) ? applySentMessage(state, msg) : state;
    case 'ReceivedMessage':
      return isReceivedMessage(msg) ? applyReceivedMessage(state, msg) : state;
    case 'ProcessedMessage':
      return isProcessedMessage(msg)
        ? applyProcessedMessage(state, msg)
        : state;
    case 'StopMessage':
      return isStopMessage(msg) ? applyStopMessage(state, msg) : state;
    case 'ErrorMessage':
      return isErrorMessage(msg) ? applyErrorMessage(state, msg) : state;
    default:
      return state;
  }
}

/**
 * Pure fold over the full log (Task 1.4). `categoryService` is an injected
 * companion dependency (Path 1 — kept-stateful `squadDict` mutation). The
 * fold is pure w.r.t. the `(log, categoryService)` pair.
 */
export function graphFold(
  log: AkgenticMessage[],
  categoryService: CategoryService,
): GraphState {
  return log.reduce(
    (s, m) => graphStep(s, m, categoryService),
    EMPTY_GRAPH,
  );
}

/**
 * GraphDataService — Story 6.3 (ADR-005 §Decision 4).
 *
 * Exposes `graph$` as a pure selector over `MessageLogService.log$`. The
 * three legacy observables `nodes$` / `edges$` / `categories$` are re-derived
 * as sliced projections for downstream compatibility. Imperative state
 * (`isLoading$`) is preserved — it reflects UX concerns, not message state
 * (AC10; NFR9 "two exceptions" invariant is unaffected because it lives on
 * `GraphDataService`, not `ActorMessageService`).
 */
@Injectable()
export class GraphDataService {
  categoryService: CategoryService = inject(CategoryService);
  private readonly log: MessageLogService = inject(MessageLogService);

  readonly graph$: Observable<GraphState> = this.log.log$.pipe(
    map((log) => graphFold(log, this.categoryService)),
    shareReplay(1),
  );

  readonly nodes$: Observable<NodeInterface[]> = this.graph$.pipe(
    map((s) => s.nodes),
    distinctUntilChanged(),
  );
  readonly edges$: Observable<EdgeInterface[]> = this.graph$.pipe(
    map((s) => s.edges),
    distinctUntilChanged(),
  );
  readonly categories$: Observable<any[]> = this.graph$.pipe(
    map((s) => s.squad),
    distinctUntilChanged(),
  );

  /**
   * AC10 — intentionally imperative UX state (external async loading
   * indicator). NOT one of ADR-005's two exceptions — those live on
   * `ActorMessageService`, so NFR9's invariant is unaffected.
   */
  isLoading$: BehaviorSubject<boolean> = new BehaviorSubject<boolean>(false);

  set isLoading(value: boolean) {
    this.isLoading$.next(value);
  }
}
