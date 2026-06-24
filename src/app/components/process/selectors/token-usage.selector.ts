import { inject, Injectable } from '@angular/core';
import { distinctUntilChanged, map, Observable, shareReplay } from 'rxjs';

import { AgentTokenUsage } from '../event/per-agent-specs';
import { IngestionService } from '../event/ingestion.service';

/** Headline team-wide totals (ADR-022 §Decision 2/4): Σ sent / Σ received. */
export interface TeamTokenTotals {
  totalSent: number;
  totalReceived: number;
}

/** Structural equality of two totals (NFR / OnPush): the summed object is a
 *  fresh reference each frame, so the default reference comparator would never
 *  suppress no-op re-emissions. */
function totalsEqual(a: TeamTokenTotals, b: TeamTokenTotals): boolean {
  return a.totalSent === b.totalSent && a.totalReceived === b.totalReceived;
}

/** Pure Σ over every agent's usage (ADR-022 §Decision 2): the team total is a
 *  DERIVATION, never a separately stored aggregate. An empty map yields zeros. */
function sumTotals(all: ReadonlyMap<string, AgentTokenUsage>): TeamTokenTotals {
  let totalSent = 0;
  let totalReceived = 0;
  for (const usage of all.values()) {
    totalSent += usage.totalSent;
    totalReceived += usage.totalReceived;
  }
  return { totalSent, totalReceived };
}

/**
 * TokenUsageSelector — Epic 26 / Story 26-1 (ADR-022 §Decision 2).
 *
 * Thin read surface over the `tokenUsage` `PerAgentStore` owned by
 * `IngestionService` (registered on the single `PerAgentStoreRegistry` alongside
 * `state` / `context` / `commands` / `systemPrompt`). Holds NO state of its own.
 *
 * - `perAgent$(agentId)` is a façade over `tokenUsage.forAgent(id)`, mirroring
 *   `SystemPromptSelector`. It passes the store's `undefined` (never-run agent)
 *   THROUGH unchanged (ADR-022 §Open Question 2): the data layer stays faithful,
 *   and the member-chat pill (Story 26.2) owns the neutral empty-state render.
 *   `forAgent` already applies a reference `distinctUntilChanged` +
 *   `shareReplay({ bufferSize: 1, refCount: true })` with lazy current-value
 *   replay, so no extra pipeline is needed here.
 * - `teamTotals$` is a PURE sum over `tokenUsage.all$` (Σ every agent's
 *   `totalSent` / `totalReceived`) — NOT a separately stored aggregate. Because
 *   `ProcessComponent` + its scoped registry are destroyed/recreated on every
 *   team switch, `all$` holds exactly the CURRENT team's agents, so the total is
 *   correct by construction with no team-id bookkeeping. The sum is a fresh
 *   reference each frame, so it needs a STRUCTURAL `distinctUntilChanged`
 *   (`totalsEqual`) to suppress no-op frames, plus `shareReplay` for OnPush.
 *
 * Scope: component-scoped (NOT `providedIn: 'root'`) — it injects the
 * component-scoped `IngestionService` from `ProcessComponent.providers`.
 */
@Injectable()
export class TokenUsageSelector {
  private readonly ingestion: IngestionService = inject(IngestionService);

  readonly teamTotals$: Observable<TeamTokenTotals> =
    this.ingestion.tokenUsage.all$.pipe(
      map(sumTotals),
      distinctUntilChanged(totalsEqual),
      shareReplay({ bufferSize: 1, refCount: true }),
    );

  perAgent$(agentId: string): Observable<AgentTokenUsage | undefined> {
    return this.ingestion.tokenUsage.forAgent(agentId);
  }
}
