import { inject, Injectable } from '@angular/core';

import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import { bufferTime, filter, take } from 'rxjs/operators';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { ConfigService } from '../../../core/config/config.service';
import {
  AkgenticMessage,
  CommandDescriptor,
} from '../../../protocol/message.types';
import { EventResponse } from '../../../core/context/team.interface';

import { ApiService } from '../../../core/http/api.service';
import { MessageLogService } from './message-log.service';
import {
  PerAgentStore,
  PerAgentStoreRegistry,
} from './per-agent-store';
import {
  AgentStateValue,
  commandsSpec,
  contextSpec,
  stateSpec,
  systemPromptSpec,
  SystemPromptValue,
} from './per-agent-specs';
import { MessageService } from 'primeng/api';

/**
 * Story 4-10 (AC7): minimum visible duration of the loading spinner.
 * When the first event / WS error / stopped-team replay lands before this
 * floor, the flip to `loadingProcess$.next(false)` is deferred so users
 * never see a sub-perception flash of the spinner before the UI transitions.
 */
const SPINNER_MIN_VISIBLE_MS = 500;

/**
 * `IngestionService` — minimal ingestion surface (post Story 6.4 / Epic 17):
 *   - REST init replays events into `MessageLogService.log$`; the registry
 *     folds that replay tail exactly as it folds live WS frames.
 *   - WS `bufferTime(16)` ingestion appends to the log; the registry derives
 *     per-agent `state` / `context` from `log$` (O(Δ), automatic replay/reset).
 *   - Spinner floor (`loadingProcess$`, AC7) — UX concern owned by ingestion.
 *
 * Per-agent state (Epic 17 / ADR-014): `state`, `context`, `commands`, and
 * `systemPrompt` are ALL `PerAgentStore` instances owned by the
 * component-scoped `PerAgentStoreRegistry` (single `log$` subscription, replay +
 * reset for free). Story 17-3 migrated `commands` (driven by
 * `CommandsAnnouncedEvent`, ADR-013) off the bespoke `commandsByAgent$`
 * `BehaviorSubject` and re-keyed it by `sender.agent_id`. Story 17-4 migrated
 * the last per-agent concern — the system-prompt head block — onto the
 * `systemPrompt` instance (custom reducer: latest `LlmSystemPromptEvent` parts
 * win, else first `LlmMessageEvent` system parts) and turned
 * `SystemPromptSelector` into a thin delegating façade. The registry is now the
 * SOLE owner of per-agent maps: adding a new per-agent event is a single
 * `register({...})` call.
 */
@Injectable()
export class IngestionService {
  apiService: ApiService = inject(ApiService);
  messageService: MessageService = inject(MessageService);
  private config: ConfigService = inject(ConfigService);

  /**
   * Story 4-10 (AC7) / Epic 18 (ADR-015 §2): the loading-spinner state.
   * Owned by `IngestionService` (which drives the spinner-floor timing) rather
   * than `ChatService` — the sole reader (`ChatPanelComponent`) reads it from
   * here. Initial value `false`, same as the prior `ChatService` field.
   */
  readonly loadingProcess$: BehaviorSubject<boolean> =
    new BehaviorSubject<boolean>(false);

  /**
   * Story 6.1 (ADR-005 §Decision 1): component-scoped append-only log of
   * every WS + REST-replay message. Story 6.2 migrated KG presence + KG
   * projection to pure selectors (`ToolPresenceService.hasKnowledgeGraph$`,
   * `KGStateReducer.knowledgeGraph$`) — both fold the same log, so the
   * message service no longer injects either of them.
   */
  private log: MessageLogService = inject(MessageLogService);

  /**
   * Epic 17 (ADR-014): component-scoped registry that folds `log$` into the
   * per-agent `state` / `context` maps (single subscription, O(Δ), automatic
   * replay + reset). Provided on `ProcessComponent` alongside
   * `MessageLogService`. Owns the maps the deleted dicts used to hold.
   */
  private registry: PerAgentStoreRegistry = inject(PerAgentStoreRegistry);

  webSocket: WebSocketSubject<any> = new WebSocketSubject({ url: '' });

  /**
   * Epic 17 (ADR-014 §5): per-agent latest `{ schema, state }` derived from
   * `StateChangedMessage`. Replaces the bespoke `stateDict$`. Default key
   * `sender.agent_id`; `schema` is an empty object literal exactly as before
   * (V2 sends an empty schema; raw state rendered as JSON). Read via
   * `state.forAgent(id)`.
   */
  readonly state: PerAgentStore<AgentStateValue> =
    this.registry.register<AgentStateValue>(stateSpec);

  /**
   * Epic 17 (ADR-014 §5): per-agent ordered conversation array derived by
   * appending each `LlmMessageEvent` envelope's inner `message`. Replaces the
   * bespoke `contextDict$`. Default key `sender.agent_id`; the append is
   * O(Δ)/frame (the registry walks only `log.slice(processedCount)` and
   * `appendWith` concats once per new message). Read via `context.forAgent(id)`.
   */
  readonly context: PerAgentStore<unknown[]> =
    this.registry.register<unknown[]>(contextSpec);

  /**
   * Epic 17 (ADR-014 §5): per-agent slash-command store derived from
   * `CommandsAnnouncedEvent` riding the `EventMessage` passthrough. Replaces
   * the bespoke `commandsByAgent$`. Default key `sender.agent_id` (ADR-013
   * keying fix — the emitting agent is the outer sender, so
   * `sender.agent_id === inner.agent.agent_id`, ADR-014 §2), so a fired/re-hired
   * display-name reuse can never serve the wrong agent's commands. `replaceWith`
   * gives the same replace-on-re-announce semantics the backend relies on (the
   * full list is re-emitted on change). Read via `commands.forAgent(id)` /
   * `commands.snapshot(id)` by the `/` mention consumers.
   */
  readonly commands: PerAgentStore<CommandDescriptor[]> =
    this.registry.register<CommandDescriptor[]>(commandsSpec);

  /**
   * Epic 17 (ADR-014 §5): per-agent system-prompt head block derived from
   * `LlmSystemPromptEvent` (primary, latest-wins, FR1) with a first
   * `LlmMessageEvent` system-part fallback (FR2). Replaces the bespoke
   * `SystemPromptSelector` `log$` fold — the selector is now a thin façade that
   * delegates to `systemPrompt.forAgent(id)`. The reducer is a custom one
   * (`systemPromptReduce`) because the precedence is "latest primary OR first
   * fallback", not a stock factory; `match` (`systemPromptMatch`) admits BOTH
   * `LlmSystemPromptEvent` and `LlmMessageEvent` inners so both reach the
   * reducer. Default key `sender.agent_id`. Read via the façade or directly via
   * `systemPrompt.forAgent(id)` (value `{ rows, hasPrimary }`; the façade
   * projects `.rows`).
   */
  readonly systemPrompt: PerAgentStore<SystemPromptValue> =
    this.registry.register<SystemPromptValue>(systemPromptSpec);

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
    // Reset the log BEFORE any replay / WS wiring so process-A state cannot
    // leak into process-B. Epic 17 (ADR-014 §Decision 3): the `state` /
    // `context` / `commands` registry detects this log shrink, clears its maps,
    // and rewinds its cursor automatically — no bespoke per-store reset needed.
    this.log.reset();

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
    this.loadingProcess$.next(true);

    if (!running) {
      // V2: use getEvents() for stopped teams
      const eventResponses: EventResponse[] =
        await this.apiService.getEvents(processId);

      // --- ADR-005 §Decision 6 step (c) -------------------------------
      // log.appendAll is the ONLY replay seeding now. Epic 17 (ADR-014
      // §Decision 3): the registry folds this replay tail exactly as it folds
      // live WS frames, so `state` / `context` / `commands` are reconstructed
      // for free — replay is just another `log$` tail. The bespoke
      // `latestStates` / `contextArrays` / `commandsByAgent` reconstruction
      // loops are deleted (Story 17-2 / 17-3).
      const replayMessages: AkgenticMessage[] = eventResponses
        .map((er: EventResponse) => er.event as AkgenticMessage)
        .filter((evt) => !!evt && !!evt.__model__);
      this.log.appendAll(replayMessages);

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
   * one `log$` emission (AC3). Epic 17 (ADR-014): `state` / `context` /
   * `commands` are all folded off `log$` by the registry, so there is no
   * remaining per-message dispatch — the batched subscriber only feeds the log.
   */
  private setupBatchedSubscriber(): void {
    this.bufferSub = this._wsInbound$
      .pipe(
        bufferTime(16),
        filter((batch: AkgenticMessage[]) => batch.length > 0),
      )
      .subscribe((batch: AkgenticMessage[]) => {
        this.log.appendAll(batch);
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
      this.loadingProcess$.value === false &&
      this.spinnerFlipTimer === null
    ) {
      return;
    }
    const elapsed = Date.now() - this.spinnerShownAt;
    if (elapsed >= SPINNER_MIN_VISIBLE_MS) {
      this.loadingProcess$.next(false);
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
      this.loadingProcess$.next(false);
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
    // Epic 17 (ADR-014): the `commands` store is owned by the registry; its
    // single `log$` subscription is torn down by `PerAgentStoreRegistry`'s own
    // ngOnDestroy — no per-store completion needed here.
    if (this.spinnerFlipTimer !== null) {
      clearTimeout(this.spinnerFlipTimer);
      this.spinnerFlipTimer = null;
    }
  }
}
