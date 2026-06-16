import { inject, Injectable, OnDestroy } from '@angular/core';
import {
  distinctUntilChanged,
  map,
  Observable,
  shareReplay,
  Subject,
  Subscription,
} from 'rxjs';

import { AkgenticMessage } from '../../../protocol/message.types';
import { MessageLogService } from './message-log.service';

/**
 * Generic per-agent derived store — Epic 17 (ADR-014).
 *
 * Generalises the existing "pure fold over `MessageLogService.log$`" pattern
 * (see `system-prompt.selector.ts`) into a single, keyed, O(Δ)-incremental
 * reducer engine. A consumer registers a {@link PerAgentSpec} and gets back a
 * {@link PerAgentStore} exposing `forAgent(id)` / `snapshot(id)` / `all$`,
 * with replay and reset handled automatically — no bespoke per-agent
 * `BehaviorSubject` dict, no manual clear-on-team-switch.
 *
 * APPEND-ONLY-OR-RESET PRECONDITION (NFR5):
 *   The O(Δ) cursor below is correct ONLY because `MessageLogService.log$` is
 *   append-only, with `reset()` (→ a strictly shorter array) as its sole
 *   non-append mutation. The registry advances a `processedCount` cursor and
 *   folds only `log.slice(processedCount)` per frame; a shrink is detected and
 *   triggers a full clear + re-fold. If a future change gives `log$`
 *   in-place / reorder / dedup-rewrite semantics (e.g. a log dedup-semantics
 *   change), THIS precondition is violated and the cursor logic MUST be
 *   revisited. This is documented prose, never a runtime assertion.
 *
 * SCOPING (NFR / ADR-005 parity):
 *   {@link PerAgentStoreRegistry} is COMPONENT-SCOPED — it injects the
 *   component-scoped `MessageLogService` and must be provided on
 *   `ProcessComponent.providers` (NEVER `providedIn: 'root'`). A team switch
 *   destroys the component, which destroys the registry (and its single
 *   `log$` subscription), so no per-agent state leaks across processes. This
 *   story (17-1) ships the class only; consumer wiring is Stories 17-2..17-4.
 */

/** Stable per-agent key. The ADR-013 keying convention is `sender.agent_id`. */
export type AgentId = string;

/**
 * Declarative description of one per-agent derived value `V`.
 *
 * - `name`     — debug label AND the registry key (unique per registry).
 * - `match`    — discriminator (on the outer or an inner `__model__`); only
 *                messages for which it returns `true` are folded by this spec.
 * - `key`      — resolves the {@link AgentId} a message contributes to.
 *                Defaults to `msg.sender?.agent_id`. Returning `undefined`
 *                silently skips the message for this spec (no `undefined` key).
 * - `reduce`   — incremental fold: `(prev, msg) => next`. Returning `undefined`
 *                leaves the agent absent. Reducers should return a FRESH
 *                reference on real change (OnPush safety).
 */
export interface PerAgentSpec<V> {
  name: string;
  match: (msg: AkgenticMessage) => boolean;
  key?: (msg: AkgenticMessage) => AgentId | undefined;
  reduce: (prev: V | undefined, msg: AkgenticMessage) => V | undefined;
}

/** The framework-default key: the outer message's `sender.agent_id`. For
 *  `EventMessage`-wrapped events the emitting agent is the outer sender, so
 *  `msg.sender.agent_id === inner.agent.agent_id` (ADR-014 §Decision 2). */
function defaultKey(msg: AkgenticMessage): AgentId | undefined {
  return msg.sender?.agent_id;
}

// ---------------------------------------------------------------------------
// Reducer factories (ADR-014 §Decision 1). Most real stores become one line.
// ---------------------------------------------------------------------------

/** Latest-wins: each matching message replaces the prior value. */
export function replaceWith<V>(
  project: (msg: AkgenticMessage) => V,
): (prev: V | undefined, msg: AkgenticMessage) => V {
  return (_prev, msg) => project(msg);
}

/** Accumulate: append the projected value to a FRESH array each time (never an
 *  in-place mutation), so OnPush consumers see a new reference. */
export function appendWith<T>(
  project: (msg: AkgenticMessage) => T,
): (prev: T[] | undefined, msg: AkgenticMessage) => T[] {
  return (prev, msg) => [...(prev ?? []), project(msg)];
}

/** First-wins: keep the first projected value; later matches do not overwrite. */
export function firstWith<V>(
  project: (msg: AkgenticMessage) => V,
): (prev: V | undefined, msg: AkgenticMessage) => V {
  return (prev, msg) => prev ?? project(msg);
}

// ---------------------------------------------------------------------------
// Per-spec internal bucket: the map + a per-frame "changed" flag.
// ---------------------------------------------------------------------------

/** Internal per-spec state held by the registry. */
class SpecBucket<V> {
  readonly map = new Map<AgentId, V>();
  /** Notified once per frame in which this bucket's map actually changed. */
  readonly changed$ = new Subject<void>();

  constructor(readonly spec: PerAgentSpec<V>) {}

  /** Fold one message into the map. Returns `true` iff the map changed. */
  apply(msg: AkgenticMessage): boolean {
    if (!this.spec.match(msg)) return false;
    const id = (this.spec.key ?? defaultKey)(msg);
    if (id === undefined) return false;
    const next = this.spec.reduce(this.map.get(id), msg);
    if (next === undefined) return false;
    this.map.set(id, next);
    return true;
  }

  /** Clear the map (reset path). Returns `true` iff it held anything. */
  clear(): boolean {
    if (this.map.size === 0) return false;
    this.map.clear();
    return true;
  }
}

/**
 * Public per-spec read surface, returned from `register`. Derives both stream
 * shapes (`forAgent` / `all$`) and the synchronous `snapshot` from one bucket.
 */
export class PerAgentStore<V> {
  /** @internal — constructed by {@link PerAgentStoreRegistry}. */
  constructor(private readonly bucket: SpecBucket<V>) {}

  /**
   * Per-agent stream: the current reduced value for `id`, re-emitting only on
   * real change. `distinctUntilChanged` (reference) suppresses no-op frames;
   * `shareReplay({ bufferSize: 1, refCount: true })` gives late subscribers the
   * current value and releases the shared source when the last unsubscribes.
   */
  forAgent(id: AgentId): Observable<V | undefined> {
    return this.bucket.changed$.pipe(
      map(() => this.bucket.map.get(id)),
      startWithCurrent(() => this.bucket.map.get(id)),
      distinctUntilChanged(),
      shareReplay({ bufferSize: 1, refCount: true }),
    );
  }

  /** Synchronous read of the current value for `id` (mirrors
   *  `MessageLogService.snapshot()` ergonomics). */
  snapshot(id: AgentId): V | undefined {
    return this.bucket.map.get(id);
  }

  /**
   * Whole-store stream: ONE coalesced emission per frame in which this store's
   * map changed (not one per changed agent). Delivers a fresh read-only view of
   * the map so OnPush consumers re-evaluate; a no-change frame does not re-emit.
   */
  get all$(): Observable<ReadonlyMap<AgentId, V>> {
    return this.bucket.changed$.pipe(
      map(() => new Map(this.bucket.map) as ReadonlyMap<AgentId, V>),
      startWithCurrent(
        () => new Map(this.bucket.map) as ReadonlyMap<AgentId, V>,
      ),
      distinctUntilChanged(),
      shareReplay({ bufferSize: 1, refCount: true }),
    );
  }
}

/**
 * Prepend the current value at subscribe time so late subscribers see state
 * that already exists, while subsequent emissions flow from `changed$`. Unlike
 * a captured-once `startWith`, the value is read lazily per subscription.
 */
function startWithCurrent<T>(read: () => T) {
  return (source: Observable<T>): Observable<T> =>
    new Observable<T>((subscriber) => {
      subscriber.next(read());
      return source.subscribe(subscriber);
    });
}

/**
 * The registry: subscribes to `MessageLogService.log$` EXACTLY ONCE, holds a
 * `processedCount` cursor, and folds each frame's new tail into every
 * registered spec's map (O(Δ)). Reset and replay share one code path.
 *
 * Component-scoped (see module docstring). Provide on `ProcessComponent`'s
 * `providers`; never `providedIn: 'root'`.
 */
@Injectable()
export class PerAgentStoreRegistry implements OnDestroy {
  private readonly log: MessageLogService = inject(MessageLogService);
  private readonly buckets: SpecBucket<unknown>[] = [];
  private processedCount = 0;
  private logSub: Subscription | null = null;

  /** Register a spec and obtain its read surface. The single `log$`
   *  subscription is started on first registration. */
  register<V>(spec: PerAgentSpec<V>): PerAgentStore<V> {
    const bucket = new SpecBucket<V>(spec);
    this.buckets.push(bucket as SpecBucket<unknown>);
    this.ensureSubscribed();
    return new PerAgentStore<V>(bucket);
  }

  /** Test/diagnostic accessor: how many log entries have been folded. */
  get cursor(): number {
    return this.processedCount;
  }

  ngOnDestroy(): void {
    this.logSub?.unsubscribe();
    this.logSub = null;
    for (const bucket of this.buckets) bucket.changed$.complete();
  }

  /** Start the single shared `log$` subscription (idempotent). */
  private ensureSubscribed(): void {
    if (this.logSub) return;
    this.logSub = this.log.log$.subscribe((log) => this.onFrame(log));
  }

  /**
   * Process one `log$` emission. A shrink (`log.length < processedCount`) means
   * `reset()` ran: clear every map and rewind the cursor to 0 so the now-smaller
   * log is re-folded from the start (reset == replay, one code path). Each
   * bucket that changed in this frame — whether by clearing or by folding new
   * messages — emits EXACTLY ONCE (coalesced; `all$` / `forAgent` then suppress
   * no-op values via `distinctUntilChanged`).
   */
  private onFrame(log: AkgenticMessage[]): void {
    const changedThisFrame = new Set<SpecBucket<unknown>>();

    if (log.length < this.processedCount) {
      this.processedCount = 0;
      for (const bucket of this.buckets) {
        if (bucket.clear()) changedThisFrame.add(bucket);
      }
    }

    for (let i = this.processedCount; i < log.length; i++) {
      const msg = log[i];
      for (const bucket of this.buckets) {
        if (bucket.apply(msg)) changedThisFrame.add(bucket);
      }
    }
    this.processedCount = log.length;

    for (const bucket of changedThisFrame) bucket.changed$.next();
  }
}
