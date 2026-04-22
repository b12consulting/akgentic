import { inject, Injectable } from '@angular/core';

import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import { bufferTime, filter, take } from 'rxjs/operators';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { ConfigService } from './config.service';
import { AkgenticMessage } from '../models/message.types';
import { EventResponse } from '../models/team.interface';

import { ApiService } from '../services/api.service';
import { ChatService } from './chat.service';
import { MessageLogService } from './message-log.service';
import { MessageService } from 'primeng/api';

/**
 * Story 4-10 (AC7): minimum visible duration of the loading spinner.
 * When the first event / WS error / stopped-team replay lands before this
 * floor, the flip to `loadingProcess$.next(false)` is deferred so users
 * never see a sub-perception flash of the spinner before the UI transitions.
 */
const SPINNER_MIN_VISIBLE_MS = 500;

/**
 * `ActorMessageService` — minimal ingestion surface (post Story 6.4):
 *   - REST init replays events into `MessageLogService.log$` and seeds the
 *     two per-agent exception dicts (`stateDict$`, `contextDict$`).
 *   - WS `bufferTime(16)` ingestion appends to the log + per-agent dicts.
 *   - Spinner floor (`loadingProcess$` on `ChatService`, AC7) — UX concern.
 *
 * Two documented exceptions (NFR9 / ADR-005 §Decision 5):
 *   - `stateDict$`  — driven by `StateChangedMessage`
 *   - `contextDict$` — driven by `LlmMessageEvent`
 *
 * Adding a third exception requires a new ADR.
 * `message.service.spec.ts` enforces this invariant by test (Story 6.4 AC5).
 */
@Injectable()
export class ActorMessageService {
  apiService: ApiService = inject(ApiService);
  chatService: ChatService = inject(ChatService);
  messageService: MessageService = inject(MessageService);
  private config: ConfigService = inject(ConfigService);

  /**
   * Story 6.1 (ADR-005 §Decision 1): component-scoped append-only log of
   * every WS + REST-replay message. Story 6.2 migrated KG presence + KG
   * projection to pure selectors (`ToolPresenceService.hasKnowledgeGraph$`,
   * `KGStateReducer.knowledgeGraph$`) — both fold the same log, so the
   * message service no longer injects either of them.
   */
  private log: MessageLogService = inject(MessageLogService);

  webSocket: WebSocketSubject<any> = new WebSocketSubject({ url: '' });

  contextDict$: { [key: string]: BehaviorSubject<any[]> } = {};
  stateDict$: { [key: string]: BehaviorSubject<any> } = {};

  processId: string = '';

  /**
   * Story 4-10 (AC7): timestamp (ms since epoch) of the most recent
   * `loadingProcess$.next(true)` emission in `init()`. Used to compute the
   * elapsed visible duration when scheduling the flip-to-false.
   */
  private spinnerShownAt: number = 0;
  /**
   * Story 4-10 (AC7): handle of a pending `setTimeout` that will flip the
   * spinner to `false` once the 500ms floor is reached. Cleared on re-init
   * so a stale `false` can never clobber a fresh spinner cycle.
   */
  private spinnerFlipTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * Story 8-2 (AC3): deduplication flag — prevents stacking duplicate
   * disconnect toasts when both error and complete fire in sequence.
   */
  private wsDisconnectToastShown = false;
  /** True during ngOnDestroy — suppresses disconnect toast on intentional navigation. */
  private destroying = false;

  /**
   * Story 6.1 (ADR-005 §Decision 3): raw WS inbound stream. Every WS event
   * is `next()`ed onto this Subject at the top of the `webSocket.subscribe`
   * callback so the `bufferTime(16)` batched subscriber (and the `take(1)`
   * spinner side-channel) can consume it without coupling to the per-model
   * dispatch below — which stays intact in PR 1 for parallel populate (AC8).
   */
  private readonly _wsInbound$ = new Subject<AkgenticMessage>();
  /** Frame-batched subscriber (bufferTime 16ms). Held so init()'s (a) step
   *  can dispose it deterministically before (b)-(e) run. */
  private bufferSub: Subscription | null = null;
  /** Spinner-first-event side-channel subscriber (take(1)). Held for
   *  disposal in init()'s (a) step and in ngOnDestroy. */
  private spinnerSub: Subscription | null = null;

  async init(processId: string, running: boolean): Promise<void> {
    this.processId = processId;

    // --- ADR-005 §Decision 6 step (a) ---------------------------------
    // Dispose prior WS + bufferTime + spinner subscriptions so a stale
    // team's pipeline cannot deliver events into the fresh cycle.
    // Load-bearing for AC5 (team-switch correctness) and AC7 (no leaks).
    this.disposePriorSubscriptions();

    // Story 6.2 (ADR-005 §Decision 4): KG state + KG presence are now pure
    // selectors over `log$`. `this.log.reset()` below causes both selectors
    // to re-emit their empty-log derivatives automatically — no explicit
    // `resetForTeam()` calls required.

    // --- ADR-005 §Decision 6 step (b) ---------------------------------
    // Reset the log and clear the per-agent exception dicts BEFORE any
    // replay / WS wiring so process-A state cannot leak into process-B.
    this.log.reset();
    this.stateDict$ = {};
    this.contextDict$ = {};

    // Story 8-2: clear any stale toasts from a prior init() cycle
    // so process-A's warnings do not persist into process-B.
    this.messageService.clear();
    this.wsDisconnectToastShown = false;

    // Story 4-10 (AC7): cancel any pending flip from a prior `init()` call
    // (team switch / re-init) before we start a new spinner cycle, otherwise
    // a stale timer could emit `false` against the new cycle.
    if (this.spinnerFlipTimer !== null) {
      clearTimeout(this.spinnerFlipTimer);
      this.spinnerFlipTimer = null;
    }
    this.spinnerShownAt = Date.now();
    this.chatService.loadingProcess$.next(true);

    if (!running) {
      // V2: use getEvents() for stopped teams
      const eventResponses: EventResponse[] =
        await this.apiService.getEvents(processId);

      // --- ADR-005 §Decision 6 step (c) -------------------------------
      // log.appendAll runs FIRST (atomic snapshot of the full replay),
      // BEFORE per-dict seeding. Story 6.1 Task 3.2 documents this choice:
      // selectors in PR 2/3 fold `log$`; the per-agent dicts are imperative
      // side state and are seeded by the loop below in the same synchronous
      // pass.
      const replayMessages: AkgenticMessage[] = eventResponses
        .map((er: EventResponse) => er.event as AkgenticMessage)
        .filter((evt) => !!evt && !!evt.__model__);
      this.log.appendAll(replayMessages);

      // Reconstruct stateDict$ and contextDict$ from persisted events.
      // Events arrive sorted by sequence (ascending) from the API.
      // - StateChangedMessage: later events overwrite earlier (keeps latest state per agent)
      // - LlmMessageEvent: messages appended in chronological order (ordered context)
      const latestStates: { [agentId: string]: any } = {};
      const contextArrays: { [agentId: string]: any[] } = {};

      for (const er of eventResponses) {
        const evt = er.event;
        if (!evt || !evt.__model__) continue;

        if (evt.__model__.includes('StateChangedMessage')) {
          const agentId = evt.sender?.agent_id;
          if (agentId) {
            latestStates[agentId] = evt.state;
          }
        } else if (evt.__model__.includes('EventMessage')) {
          const inner = evt.event;
          if (inner?.__model__?.includes('LlmMessageEvent')) {
            const agentId = evt.sender?.agent_id;
            if (agentId && inner.message) {
              if (!contextArrays[agentId]) contextArrays[agentId] = [];
              contextArrays[agentId].push(inner.message);
            }
          }
          // Story 6.2 (ADR-005 §Decision 4): `ToolStateEvent` is now folded
          // into `knowledgeGraph$` via `kgFold` over `log$` (seeded above by
          // `log.appendAll`). No explicit reducer drive required.
        }
      }

      for (const [agentId, state] of Object.entries(latestStates)) {
        this.initDict(this.stateDict$, agentId, null);
        this.stateDict$[agentId].next({ schema: {}, state });
      }

      for (const [agentId, msgs] of Object.entries(contextArrays)) {
        this.initDict(this.contextDict$, agentId, []);
        this.contextDict$[agentId].next(msgs);
      }

      // Story 6.4 (AC1): `GRAPH_RELEVANT_MODELS` filtering and the
      // `createAgentGraph$` / `messages$` emits below are deleted along with
      // the streams themselves. The agent graph + message list now consume
      // log-derived selectors (`GraphDataService.graph$`, Story 6.3;
      // `MessageLogService.messageList$`, Story 6.4).
      // Story 6.3 (AC9, FR7): thinking-bubble lifecycle is reconstructed by
      // `chatFold` over `log$` (seeded above by `log.appendAll`). The prior
      // imperative replay loop has been deleted.
    }

    // V2: connect directly -- no ticket needed (community tier, AC8)
    const wsProtocol =
      window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const api = this.config.api.replace(/(^\w+:|^)\/\//, '');

    // Story 4-10 (AC2): stopped-team path has already populated replay state
    // via HTTP getEvents() above — flip the spinner off BEFORE wiring up the
    // WS subscription so the user never sees `#emptyState` flash.
    if (!running) {
      this.scheduleSpinnerFlipFalse();
    }

    // Story 4-10 (AC1): running-team path keeps the spinner on until the
    // first WS event actually lands. Closure flag guards against re-emitting
    // `false` for every subsequent event. PR 1 keeps this closure alongside
    // the new `take(1)` spinner side-channel (Task 2.5) — parallel populate
    // per AC8. The second call is a no-op: scheduleSpinnerFlipFalse is
    // idempotent and the guard flag is flipped on first entry.
    let firstEventReceived = false;
    const flipOnFirstEvent = (): void => {
      if (firstEventReceived) return;
      firstEventReceived = true;
      this.scheduleSpinnerFlipFalse();
    };

    // --- ADR-005 §Decision 6 step (d) ---------------------------------
    // Wire the frame-batched subscriber AND the spinner take(1) side-channel
    // BEFORE opening the WebSocket so every first `_wsInbound$.next(...)`
    // fires against a live subscription. Load-bearing for AC3 / AC6.
    this.setupBatchedSubscriber();
    this.setupSpinnerSideChannel();

    try {
      this.webSocket = this.createWebSocket(
        `${wsProtocol}${api}/ws/${this.processId}`,
      );
    } catch (err) {
      // Story 4-10 (AC3): synchronous ctor failure must not leave the UI
      // spinning forever.
      console.error('WebSocket construction failed:', err);
      this.scheduleSpinnerFlipFalse();
      throw err;
    }

    this.webSocket.subscribe({
      next: (data: any) => {
        // Story 4-10 (AC1): first event over the wire ends the loading
        // window. Runs for EVERY event shape (including ones we ignore
        // below) — receiving bytes is proof the replay stream has started.
        flipOnFirstEvent();

        // V2: data is a raw Message with __model__ discriminator
        const event = data;
        if (!event || !event.__model__) return;

        // Story 6.1 (Task 2.6 / AC8): feed the unified log via the frame
        // batched subscriber. The batched path is now the SOLE producer of
        // dict updates and log emissions (Story 6.4 retired the parallel
        // per-__model__ dispatch).
        this._wsInbound$.next(event as AkgenticMessage);

        if (event.__model__.includes('ErrorMessage')) {
          // Story 6.4 (AC1): the toast is dispatched here; the inbound log
          // emission above already feeds every downstream selector. The
          // legacy `this.message$.next(event)` push is deleted (no
          // subscribers remain).
          this.messageService.add({
            severity: 'error',
            summary: 'Error',
            detail: event.exception_value,
            life: 5000,
          });
        }
        // Story 6.4 (AC1): every other branch (StateChangedMessage,
        // EventMessage, fallthrough) is now pure log-feed via
        // `_wsInbound$.next(...)` above. The per-__model__ dispatch + VCR
        // `paused` early-return have been deleted.
      },
      error: (err: any) => {
        // Story 4-10 (AC3): failure before any event landed must not leave
        // the UI spinning forever — flip the flag so the chat panel falls
        // through to `#emptyState` (or the subsequent error affordance)
        // instead of showing the "Loading process..." placeholder for ever.
        flipOnFirstEvent();
        console.error('WebSocket error:', err);
        // Story 8-2 (AC1, AC5): persistent warning toast replaces the
        // transient 5-second error toast. flipOnFirstEvent() is preserved above.
        this.showDisconnectToast();
      },
      complete: () => {
        console.log('webSocket - complete');
        // Story 8-2 (AC2): persistent warning on stream completion.
        this.showDisconnectToast();
      },
    });
  }

  /**
   * Story 4-10: indirection point for WebSocket construction so tests can
   * inject a fake Subject without trying to rewrite the rxjs module
   * namespace (which is frozen under ES modules).
   */
  protected createWebSocket(url: string): WebSocketSubject<any> {
    return webSocket(url);
  }

  /**
   * Story 6.1 (ADR-005 §Decision 6 step (a)): dispose every subscription
   * owned by a previous `init()` cycle in one place. Called from `init()`
   * BEFORE any new state is wired so a stale WS / bufferTime subscription
   * cannot bleed into the fresh team.
   */
  private disposePriorSubscriptions(): void {
    try {
      this.webSocket.unsubscribe();
    } catch {
      /* first-init path: no prior webSocket — ignore */
    }
    if (this.bufferSub) {
      this.bufferSub.unsubscribe();
      this.bufferSub = null;
    }
    if (this.spinnerSub) {
      this.spinnerSub.unsubscribe();
      this.spinnerSub = null;
    }
  }

  /**
   * Story 6.1 (ADR-005 §Decision 3 + §Decision 5): frame-batched consumer
   * of the raw WS inbound stream. `bufferTime(16)` coalesces every message
   * that lands in a single 16ms window into one `log.appendAll` call and
   * one `log$` emission (AC3). Per-agent exception-dict updates
   * (`stateDict$`, `contextDict$`) run in the SAME synchronous pass inside
   * the subscriber callback (AC4).
   */
  private setupBatchedSubscriber(): void {
    this.bufferSub = this._wsInbound$
      .pipe(
        bufferTime(16),
        filter((batch: AkgenticMessage[]) => batch.length > 0),
      )
      .subscribe((batch: AkgenticMessage[]) => {
        this.log.appendAll(batch);
        for (const msg of batch) {
          if (!msg.__model__) continue;
          if (msg.__model__.includes('StateChangedMessage')) {
            this.applyStateChanged(msg as any);
          } else if (msg.__model__.includes('EventMessage')) {
            this.applyEventMessageDicts(msg as any);
          }
        }
      });
  }

  /**
   * Story 6.1 (ADR-005 §Decision 8): spinner first-event flip. `take(1)` on
   * the raw `_wsInbound$` fires once per `init()` cycle and is independent
   * of the batched subscriber (so a tight batch does not delay the flip).
   * `take(1)` (not `first()`) is used so an immediately-completed stream
   * (e.g. unmount before any WS event) doesn't throw `EmptyError`.
   */
  private setupSpinnerSideChannel(): void {
    this.spinnerSub = this._wsInbound$
      .pipe(take(1))
      .subscribe(() => this.scheduleSpinnerFlipFalse());
  }

  /**
   * Story 6.1 (Task 2.4): apply a `StateChangedMessage` to `stateDict$`.
   * Extracted from the bufferSub callback to keep the subscribe body <10
   * LoC (CLAUDE.md ~50-line ceiling).
   */
  private applyStateChanged(event: any): void {
    const agentId = event?.sender?.agent_id;
    if (!agentId) return;
    this.initDict(this.stateDict$, agentId, null);
    this.stateDict$[agentId].next({ schema: {}, state: event.state });
  }

  /**
   * Story 6.1 (Task 2.4): apply an `EventMessage` carrying an
   * `LlmMessageEvent` / `ToolStateEvent` to the exception dicts and to the
   * KG reducer. Story 6.4 made the batched subscriber the SOLE producer of
   * dict updates (the parallel per-message dispatch in `webSocket.subscribe`
   * has been deleted).
   */
  private applyEventMessageDicts(event: any): void {
    const inner = event?.event;
    if (!inner?.__model__) return;
    if (inner.__model__.includes('LlmMessageEvent')) {
      const agentId = event.sender?.agent_id;
      if (agentId && inner.message) {
        this.initDict(this.contextDict$, agentId, []);
        const current = this.contextDict$[agentId].getValue();
        this.contextDict$[agentId].next([...current, inner.message]);
      }
    }
    // Story 6.2 (ADR-005 §Decision 4): `ToolStateEvent` is now covered
    // entirely by `KGStateReducer.knowledgeGraph$` (pure selector over
    // `log$`). The batched subscriber no longer drives the reducer.
  }

  /**
   * Story 4-10 (AC7): flip `loadingProcess$` to `false`, but respect the
   * `SPINNER_MIN_VISIBLE_MS` floor measured from the spinner-on emission
   * time. If the floor has already been reached, flip immediately; otherwise
   * defer via `setTimeout` so the user always sees the spinner for at least
   * half a second.
   *
   * Called from THREE sites (all share the same floor semantics):
   *   - WS first-event path (running=true)
   *   - WS error path (failure-safety)
   *   - stopped-team path (after HTTP replay seeds state)
   *   - synchronous `createWebSocket` throw (failure-safety)
   */
  private scheduleSpinnerFlipFalse(): void {
    // Story 6.1 idempotency: PR 1 keeps both the legacy `flipOnFirstEvent`
    // closure AND the new `take(1)` side-channel (Task 2.5 parallel
    // populate). Both fire on the first WS event, so this method is called
    // twice. Skip the second call if the spinner is already false AND no
    // deferred flip is pending — otherwise the subscriber would see an
    // extra redundant `false` emission, breaking Story 4-10 AC1's "subsequent
    // events do not re-emit false" test.
    if (
      this.chatService.loadingProcess$.value === false &&
      this.spinnerFlipTimer === null
    ) {
      return;
    }
    const elapsed = Date.now() - this.spinnerShownAt;
    if (elapsed >= SPINNER_MIN_VISIBLE_MS) {
      this.chatService.loadingProcess$.next(false);
      return;
    }
    // Clear any pending timer (should normally be null here because the
    // single-shot guard in `flipOnFirstEvent()` prevents double-scheduling,
    // but the stopped-team path and failure paths do not use that guard).
    if (this.spinnerFlipTimer !== null) {
      clearTimeout(this.spinnerFlipTimer);
    }
    this.spinnerFlipTimer = setTimeout(() => {
      this.spinnerFlipTimer = null;
      this.chatService.loadingProcess$.next(false);
    }, SPINNER_MIN_VISIBLE_MS - elapsed);
  }

  /**
   * Story 8-2 (AC1, AC2, AC3): show a persistent, non-closable warning toast
   * when the WebSocket disconnects. The deduplication guard ensures only one
   * toast is visible even if both error and complete fire in sequence.
   */
  private showDisconnectToast(): void {
    if (this.wsDisconnectToastShown || this.destroying) return;
    this.wsDisconnectToastShown = true;
    this.messageService.add({
      severity: 'warn',
      summary: 'Connection Lost',
      detail: 'Real-time connection to the server has been lost. Updates are paused.',
      sticky: true,
      closable: false,
    });
  }

  initDict(
    dict: { [key: string]: BehaviorSubject<any[]> },
    key: string,
    defaultValue: any
  ) {
    if (dict[key]) return;
    dict[key] = new BehaviorSubject<any>(defaultValue);
  }

  ngOnDestroy() {
    // Suppress disconnect toast triggered by the unsubscribe below —
    // this is intentional navigation, not a connection loss.
    this.destroying = true;

    // Story 8-2 (AC4): clear all toasts and reset the flag so
    // navigating away removes warnings and a fresh process view starts clean.
    this.messageService.clear();
    this.wsDisconnectToastShown = false;

    try {
      this.webSocket.unsubscribe();
    } catch {
      /* never-opened WS — ignore */
    }
    // Story 6.1 (Task 4.1 / AC7): dispose the bufferTime + spinner
    // subscriptions and complete the inbound Subject so no leaked listener
    // survives the component teardown. Manual unsubscribe (not
    // takeUntilDestroyed) is chosen for symmetry with `init()`'s (a) step,
    // which must dispose these same subscriptions on re-init WITHOUT
    // tearing the service down.
    this.bufferSub?.unsubscribe();
    this.spinnerSub?.unsubscribe();
    this._wsInbound$.complete();
    if (this.spinnerFlipTimer !== null) {
      clearTimeout(this.spinnerFlipTimer);
      this.spinnerFlipTimer = null;
    }
  }
}
