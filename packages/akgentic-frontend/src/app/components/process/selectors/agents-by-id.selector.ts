import { inject, Injectable } from '@angular/core';
import { distinctUntilChanged, map, Observable } from 'rxjs';

import {
  AkgenticMessage,
  isStartMessage,
} from '../../../protocol/message.types';
import { MessageLogService } from '../event/message-log.service';

/**
 * Display identity for one agent (Epic 23 / ADR-020 §Decision 4). The two
 * fields the member-chip row renders — the agent's `name` (chip label) and its
 * `role` (chip tooltip/subtext). A typed value rather than an inline object
 * shape so the component + spec share one contract.
 */
export interface AgentIdentity {
  name: string;
  role: string;
}

/** `agent_id → { name, role }`. */
export type AgentsById = Record<string, AgentIdentity>;

/**
 * Pure ordered fold over the message log → the `agent_id → { name, role }`
 * identity map (ADR-020 §Decision 4 / FR17, sibling of `presenceReduce` and
 * `workspaceRegistryReduce`).
 *
 * Every `StartMessage.sender` is an `ActorAddress` carrying `agent_id`, `name`,
 * and `role`; each Start records (last-wins) that agent's display identity. A
 * later Start for the same `agent_id` supersedes the earlier identity — uniform
 * with the other fold-the-log selectors. `StopMessage`s are deliberately NOT
 * folded out: this map only resolves DISPLAY identity, while the descriptor
 * fold (`workspaceRegistryReduce`) governs membership; an agent that
 * contributed to a descriptor stays resolvable here even across a Start→Stop.
 *
 * Exported at module scope so tests assert the pure function directly without a
 * `TestBed` harness (faster + clearer coverage).
 */
export function agentsByIdReduce(log: AkgenticMessage[]): AgentsById {
  const byId: AgentsById = {};
  for (const m of log) {
    if (isStartMessage(m)) {
      byId[m.sender.agent_id] = { name: m.sender.name, role: m.sender.role };
    }
  }
  return byId;
}

/**
 * Structural equality of two identity maps (mirror of `descriptorsEqual`). The
 * fold returns a NEW object reference per `log$` emission, so the default
 * reference comparator would never suppress structurally-identical
 * re-emissions; compare the key set plus each entry's `name`/`role`.
 */
function agentsByIdEqual(a: AgentsById, b: AgentsById): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((k) => {
    const o = b[k];
    return o !== undefined && a[k].name === o.name && a[k].role === o.role;
  });
}

/**
 * AgentsByIdService — Story 23-4 (ADR-020 §Decision 4).
 *
 * Publishes `agentsById$` as a pure selector over `MessageLogService.log$`:
 * the `agent_id → { name, role }` identity map folded from every agent's
 * `StartMessage.sender`. Combined in `WorkspaceTabsComponent` with
 * `WorkspaceRegistryService.workspaces$` to resolve each descriptor's
 * `agentIds` to displayable member chips — the descriptor fold is left
 * untouched (no descriptor churn, AC6).
 *
 * Scope: component-scoped (NOT `providedIn: 'root'`) because it injects
 * `MessageLogService`, which is component-scoped on `ProcessComponent`. A team
 * switch destroys the component (and the log), so the map shares that lifecycle
 * and never leaks identities across teams.
 *
 * `distinctUntilChanged` uses a STRUCTURAL comparator (`agentsByIdEqual`): the
 * fold emits a NEW object reference per `log$` emission, so the default
 * reference comparator would never suppress no-op re-emissions.
 */
@Injectable()
export class AgentsByIdService {
  private readonly log: MessageLogService = inject(MessageLogService);

  readonly agentsById$: Observable<AgentsById> = this.log.log$.pipe(
    map(agentsByIdReduce),
    distinctUntilChanged(agentsByIdEqual),
  );
}
