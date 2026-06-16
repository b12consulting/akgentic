import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { AvatarModule } from 'primeng/avatar';
import { ChipModule } from 'primeng/chip';
import { TabsModule } from 'primeng/tabs';
import { TooltipModule } from 'primeng/tooltip';
import { combineLatest, map, Observable } from 'rxjs';

import {
  AgentsById,
  AgentsByIdService,
} from '../../selectors/agents-by-id.selector';
import {
  WorkspaceDescriptor,
  WorkspaceRegistryService,
} from '../../selectors/workspace-registry.selector';
import { WorkspaceExplorerComponent } from '../workspace-explorer/workspace-explorer.component';

/** One resolved member row rendered as a chip (name + role). */
interface MemberRow {
  name: string;
  role: string;
}

/** Combined view-model for the template (workspaces + identity map). */
interface WorkspaceTabsVm {
  workspaces: WorkspaceDescriptor[];
  agentsById: AgentsById;
}

/**
 * Workspace sub-tabs view — Story 23-3 (ADR-019 §Decision 4) + the member-chip
 * header strip from Story 23-4 (ADR-020 §Decisions 1–4).
 *
 * Renders one PrimeNG `<p-tabpanel>` per `WorkspaceDescriptor` emitted by
 * `WorkspaceRegistryService.workspaces$`, each hosting an
 * `<app-workspace-explorer>`. A non-default descriptor binds its
 * `workspaceId`; the default descriptor leaves the explorer's `workspaceId`
 * unset (`undefined`) so it keeps today's team-id-only behaviour.
 *
 * When only the default descriptor is present the view renders a single
 * explorer with NO tab chrome — visually identical to today's single-pane
 * Workspace view (FR13). Tab chrome appears only once at least one named
 * workspace is discovered.
 *
 * Story 23-4 adds a slim header strip ABOVE the explorer in BOTH branches
 * (the strip is NOT a tab bar — FR13 stays intact): a member-chip row built
 * from the descriptor's `agentIds` resolved to `{ name, role }` via
 * `AgentsByIdService.agentsById$`. v1 surfaces MEMBERSHIP only (who) — no
 * read/write access-level indicator. A default descriptor with an empty
 * `agentIds` renders a neutral "Team default — all members" affordance.
 *
 * Neither `WorkspaceRegistryService` nor `AgentsByIdService` is re-provided
 * here — both are provided by `ProcessComponent` so the view shares the
 * component-scoped `MessageLogService` lifecycle.
 */
@Component({
  selector: 'app-workspace-tabs',
  standalone: true,
  imports: [
    CommonModule,
    TabsModule,
    ChipModule,
    AvatarModule,
    TooltipModule,
    WorkspaceExplorerComponent,
  ],
  templateUrl: './workspace-tabs.component.html',
  styleUrl: './workspace-tabs.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorkspaceTabsComponent {
  /**
   * Combined stream of the discovered workspaces and the agent identity map.
   * Both are pure folds over the same component-scoped `MessageLogService.log$`,
   * each with a structural `distinctUntilChanged`, so the combined VM only
   * re-emits when one of them changes structurally (OnPush-safe).
   */
  readonly vm$: Observable<WorkspaceTabsVm> = combineLatest([
    inject(WorkspaceRegistryService).workspaces$,
    inject(AgentsByIdService).agentsById$,
  ]).pipe(map(([workspaces, agentsById]) => ({ workspaces, agentsById })));

  /**
   * Resolve a descriptor's `agentIds` to displayable member rows in the
   * descriptor's `agentIds` order. Pure: no mutation, no side effects. An
   * `agent_id` missing from the identity map (defensive — should not happen
   * for a live descriptor) falls back to the raw `agent_id` as the name with
   * an empty role rather than dropping the chip.
   */
  resolveMembers(d: WorkspaceDescriptor, agentsById: AgentsById): MemberRow[] {
    return d.agentIds.map((id) => {
      const identity = agentsById[id];
      return identity
        ? { name: identity.name, role: identity.role }
        : { name: id, role: '' };
    });
  }
}
