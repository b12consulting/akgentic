import { inject, Injectable } from '@angular/core';
import { map, Observable } from 'rxjs';

import { IngestionService } from '../event/ingestion.service';
import { SystemPromptRow } from '../event/per-agent-specs';

/**
 * Re-export the system-prompt reducer surface from its new home in the
 * `event/` layer (Epic 18 / ADR-015 §3). The reducer + helpers moved into
 * `event/per-agent-specs.ts` to break the `ingestion ↔ system-prompt.selector`
 * circular import; these re-exports preserve the existing import paths for the
 * façade's consumers (`akgent-chat.component.ts`) and the parity specs without
 * a logic change.
 */
export type {
  SystemPromptRow,
  SystemPromptValue,
} from '../event/per-agent-specs';
export {
  systemPromptLabel,
  systemPromptMatch,
  systemPromptReduce,
} from '../event/per-agent-specs';

/**
 * SystemPromptSelector — Story 16-1 (ADR-004 §5b step 1), thin façade since
 * Epic 17 / Story 17-4 (ADR-014).
 *
 * `latestSystemPrompt$(agentId)` now DELEGATES to the `systemPrompt`
 * `PerAgentStore` instance owned by `IngestionService` (registered on the
 * single `PerAgentStoreRegistry` alongside `state` / `context` / `commands`).
 * The selector holds NO per-agent state of its own and no `log$` fold pipeline —
 * the latest-wins + FR2 fallback + row-mapping logic lives in
 * `systemPromptReduce` (now in `event/per-agent-specs.ts`). `forAgent` already
 * applies a reference `distinctUntilChanged` +
 * `shareReplay({ bufferSize: 1, refCount: true })` with a lazy current-value
 * replay, covering the late-subscriber + no-op-suppression + fresh-array-on-change
 * semantics the head block relies on. The façade coalesces the store's
 * `undefined` (agent never folded) to `[]` so the head-block consumer always
 * receives `SystemPromptRow[]`, never `undefined` (AC-4 parity with the old
 * fold's `[]`-for-no-rows contract).
 *
 * Scope: component-scoped (NOT `providedIn: 'root'`) — it injects the
 * component-scoped `IngestionService` from `ProcessComponent.providers`.
 */
@Injectable()
export class SystemPromptSelector {
  private readonly ingestionService: IngestionService =
    inject(IngestionService);

  latestSystemPrompt$(agentId: string): Observable<SystemPromptRow[]> {
    return this.ingestionService.systemPrompt
      .forAgent(agentId)
      .pipe(map((value) => value?.rows ?? []));
  }
}
