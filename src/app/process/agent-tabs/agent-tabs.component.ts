import { Component, inject, OnInit, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { BehaviorSubject, combineLatest, Subject, Subscription } from 'rxjs';
import { takeUntil } from 'rxjs/operators';

import { TabsModule } from 'primeng/tabs';
import { DropdownModule } from 'primeng/dropdown';
import { ProgressSpinnerModule } from 'primeng/progressspinner';

import { AkgentService } from '../../services/akgent.service';
import {
  GraphDataService,
  HUMAN_ROLE as HUMAN_PROXY_ROLE,
} from '../../services/graph-data.service';

import { ActorMessageService } from '../../services/message.service';

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
    ProgressSpinnerModule,
    AkgentChatComponent,
    AkgentStateComponent,
  ],
  templateUrl: './agent-tabs.component.html',
  styleUrl: './agent-tabs.component.scss',
})
export class AgentTabsComponent implements OnInit {
  akgentService: AkgentService = inject(AkgentService);
  graphDataService: GraphDataService = inject(GraphDataService);
  messageService: ActorMessageService = inject(ActorMessageService);
  private destroyRef = inject(DestroyRef);

  akgentId: string = '';
  akgentName: string = '';
  agentsByCategory: any[] = [];
  selectedAgent: any = null;
  isLoading: boolean = false;

  context$: BehaviorSubject<any[]> = new BehaviorSubject<any[]>([]);
  state$: BehaviorSubject<any> = new BehaviorSubject<any>(null);

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
          this.initDict(this.messageService.contextDict$, akgent.agentId, []);
          this.initDict(this.messageService.stateDict$, akgent.agentId, null);
          // Subscribe to context observable for the selected agent
          // Use takeUntil to properly clean up when agent changes
          this.messageService.contextDict$[akgent.agentId]
            .pipe(takeUntil(this.agentSubscriptions$))
            .subscribe((context) => {
              this.context$.next(context);
            });
          this.messageService.stateDict$[akgent.agentId]
            .pipe(takeUntil(this.agentSubscriptions$))
            .subscribe((state) => {
              this.state$.next(state);
            });
          // Immediately update with the latest context value
          this.context$.next(
            this.messageService.contextDict$[akgent.agentId].value
          );
          this.state$.next(
            this.messageService.stateDict$[akgent.agentId].value
          );
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

  initDict(
    dict: { [key: string]: BehaviorSubject<any[]> },
    key: string,
    defaultValue: any
  ) {
    if (dict[key]) return;
    dict[key] = new BehaviorSubject<any>(defaultValue);
  }

  onAgentSelect(event: any): void {
    this.isLoading = true;
    const agent = event.value;
    if (agent && agent.agent) {
      this.akgentService.select(agent.agent.name, agent.agent.actorName);
    } else {
      this.akgentService.unselect();
    }
  }
}
