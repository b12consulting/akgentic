import { inject, Injectable } from '@angular/core';
import { distinctUntilChanged, map, Observable } from 'rxjs';

import {
  AkgenticMessage,
  isStartMessage,
  isStopMessage,
} from '../models/message.types';
import { MessageLogService } from './message-log.service';

/**
 * Canonical name for the Knowledge Graph tool actor (ADR-004 §Decision 4).
 * Mirrors `KG_ACTOR_NAME` from backend `kg_actor.py`.
 */
export const KG_ACTOR_NAME = '#KnowledgeGraphTool';

/**
 * Ordered-reduce presence fold over the message log (ADR-005 §Decision 4,
 * Epic 6 FR4).
 *
 * Ordered-reduce semantics are LOAD-BEARING: `log.some(isStart) &&
 * !log.some(isStop)` is WRONG on Start→Stop→Start restart sequences (would
 * yield `false` when the correct answer is `true` — the last Start wins).
 *
 * Exported at module scope so tests can assert the pure function directly
 * without a `TestBed` harness (faster + clearer coverage).
 */
export function presenceReduce(log: AkgenticMessage[]): boolean {
  return log.reduce<boolean>(
    (present, m) =>
      isStartMessage(m) && m.sender?.name === KG_ACTOR_NAME
        ? true
        : isStopMessage(m) && m.sender?.name === KG_ACTOR_NAME
          ? false
          : present,
    false,
  );
}

/**
 * ToolPresenceService — Story 6.2 (ADR-005 §Decision 4).
 *
 * Publishes `hasKnowledgeGraph$` as a pure selector over
 * `MessageLogService.log$`. No mutable instance state, no `bindTo()` glue,
 * no `BehaviorSubject`. Late subscribers see the current derived value
 * synchronously on subscribe (AC4) because `log$` is backed by a
 * `BehaviorSubject<AkgenticMessage[]>` that replays its latest value on
 * subscribe, and `map(presenceReduce)` is synchronous.
 *
 * Scope: component-scoped (NOT `providedIn: 'root'`) because it injects
 * `MessageLogService`, which is component-scoped on
 * `ProcessComponent.providers`. Team switches destroy `ProcessComponent`,
 * which destroys the log and the selector — no state leaks between teams.
 */
@Injectable()
export class ToolPresenceService {
  private readonly log: MessageLogService = inject(MessageLogService);

  readonly hasKnowledgeGraph$: Observable<boolean> = this.log.log$.pipe(
    map(presenceReduce),
    distinctUntilChanged(),
  );
}
