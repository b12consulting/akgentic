import { Component, inject, OnInit, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { BehaviorSubject, combineLatest, Subject } from 'rxjs';
import { map, takeUntil } from 'rxjs/operators';

import { TabsModule } from 'primeng/tabs';
import { DropdownModule } from 'primeng/dropdown';

import { AkgentService } from '../../../../core/ui/akgent.service';
import {
  GraphDataService,
  HUMAN_ROLE as HUMAN_PROXY_ROLE,
} from '../../selectors/graph.selector';

import { IngestionService } from '../../event/ingestion.service';

import { AkgentChatComponent } from './akgent-chat/akgent-chat.component';
import { AkgentStateComponent } from './akgent-state/akgent-state.component';

@Component({
  selector: 'app-agent-tabs',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    TabsModule,
    DropdownModule,
    AkgentChatComponent,
    AkgentStateComponent,
  ],
  templateUrl: './agent-tabs.component.html',
  styleUrl: './agent-tabs.component.scss',
})
export class AgentTabsComponent implements OnInit {
  akgentService: AkgentService = inject(AkgentService);
  graphDataService: GraphDataService = inject(GraphDataService);
  messageService: IngestionService = inject(IngestionService);
  private destroyRef = inject(DestroyRef);

  akgentId: string = '';
  akgentName: string = '';
  agentsByCategory: any[] = [];
  selectedAgent: any = null;

  context$: BehaviorSubject<any[]> = new BehaviorSubject<any[]>([]);
  state$: BehaviorSubject<any> = new BehaviorSubject<any>(null);

  // akgentic-agent ADR-007 §4: a NEVER-RUN agent has NO `LlmSystemPromptEvent` (the backend
  // emits no creation event). Its backstory is already on the client as the
  // serialized `AgentState.backstory`, folded by the `state` PerAgentStore
  // (value shape `{ schema, state }`). Project the trimmed backstory string so
  // the chat component can render it as the head-block fallback and so chat-tab
  // visibility can account for it. Emits `''` when there is no backstory.
  backstory$ = this.state$.pipe(map((state) => this.readBackstory(state)));

  // The chat tab is shown when the agent has conversation context OR a non-empty
  // `state.backstory` to display (akgentic-agent ADR-007 §4 never-run case — an empty/whitespace
  // backstory must NOT force the tab open). A running agent always has context;
  // a never-run agent shows `state.backstory`. When visible it occupies slot "0"
  // and the State tab moves to "1".
  chatTabVisible$ = combineLatest([this.context$, this.state$]).pipe(
    map(
      ([context, state]) =>
        (context?.length ?? 0) > 0 || this.readBackstory(state).length > 0,
    ),
  );

  /**
   * akgentic-agent ADR-007 §4 — read the agent's backstory from the `state` PerAgentStore
   * value (`{ schema, state }`, where `state` is the serialized `AgentState`
   * carrying `backstory: str`). Returns the TRIMMED backstory, or `''` when the
   * state, raw state, or backstory is absent/blank. Defensive: never throws on
   * `null`/`undefined`/non-string.
   */
  private readBackstory(stateValue: any): string {
    const raw = stateValue?.state?.backstory;
    return typeof raw === 'string' ? raw.trim() : '';
  }

  // Subject to unsubscribe from agent-specific subscriptions when agent changes
  private agentSubscriptions$ = new Subject<void>();

  ngOnInit(): void {
    this.akgentService.selectedAkgent$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((akgent) => {
        // Unsubscribe from previous agent's subscriptions
        this.agentSubscriptions$.next();

        this.akgentId = akgent?.agentId || '';
        this.akgentName = akgent?.name || '';
        if (akgent) {
          // Epic 17 (ADR-014): source `context` / `state` from the
          // PerAgentStore instances instead of the deleted dicts. `forAgent`'s
          // `shareReplay(1)` delivers the current value on subscribe, so the
          // explicit immediate `.value` push is no longer needed. Map
          // `undefined` → the existing defaults (`[]` / `null`) so the template
          // guards (`(context$ | async)?.length`, `state$ | async`) behave
          // identically.
          this.messageService.context
            .forAgent(akgent.agentId)
            .pipe(
              map((context) => context ?? []),
              takeUntil(this.agentSubscriptions$),
            )
            .subscribe((context) => {
              this.context$.next(context);
            });
          this.messageService.state
            .forAgent(akgent.agentId)
            .pipe(
              map((state) => state ?? null),
              takeUntil(this.agentSubscriptions$),
            )
            .subscribe((state) => {
              this.state$.next(state);
            });
          // Select the agent in the dropdown
          this.set_dropdown_selected_agent(akgent.agentId);
        } else {
          this.context$.next([]);
          this.state$.next(null);
        }
      });

    combineLatest([
      this.graphDataService.nodes$,
      this.graphDataService.categories$,
    ])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(([agents = [], categories = []]: [any[], any[]]) => {
        if (!agents?.length) {
          this.agentsByCategory = [];
          this.selectedAgent = null;
          return;
        }
        // Remove agents with role 'human_proxy'
        const filteredAgents = agents.filter(
          (a: any) => (a.role || '') !== HUMAN_PROXY_ROLE
        );

        // Helper to build dropdown item
        const toDropdownItem = (a: any) => ({
          label: a.actorName,
          value: a.agent_id || a.name, // Use agent_id as the primary value
          agent: a,
        });

        // Build groups for the dropdown
        let dropdownItems: any[] = [];
        if (!categories?.length || categories.length <= 1) {
          dropdownItems = filteredAgents.map(toDropdownItem);
          this.agentsByCategory = [{ label: 'Agents', items: dropdownItems }];
        } else {
          this.agentsByCategory = categories.map((cat: any, idx: number) => {
            const items = filteredAgents
              .filter((a: any) => a.category === idx)
              .map(toDropdownItem);
            return { label: `Team ${idx}`, items };
          });
          dropdownItems = this.agentsByCategory.flatMap((g: any) => g.items);
        }

        // Set selectedAgent to the dropdown item matching the selectedAkgent$ (or first Manager, or first agent)
        const selectedAkgent = this.akgentService.selectedAkgent$.value;
        if (selectedAkgent) {
          this.set_dropdown_selected_agent(selectedAkgent.agentId);
        } else {
          // Try to select first agent with role 'Manager' (case-insensitive)
          const managerItem = dropdownItems.find(
            (item: any) => (item.label || '') === '@Manager'
          );
          this.selectedAgent = managerItem || dropdownItems[0] || null;
          if (this.selectedAgent && this.selectedAgent.agent) {
            this.akgentService.select(
              this.selectedAgent.value,
              this.selectedAgent.label
            );
          }
        }
      });
  }

  set_dropdown_selected_agent(agent_id: string): void {
    const dropdownItems = this.agentsByCategory.flatMap((g: any) => g.items);
    this.selectedAgent =
      dropdownItems.find((item: any) => item.value === agent_id) || null;
  }

  onAgentSelect(event: any): void {
    const agent = event.value;
    if (agent && agent.agent) {
      this.akgentService.select(agent.agent.name, agent.agent.actorName);
    } else {
      this.akgentService.unselect();
    }
  }
}
