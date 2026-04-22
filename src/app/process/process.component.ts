import { AsyncPipe, CommonModule } from '@angular/common';
import { Component, inject, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { isRunning } from '../models/team.interface';
import { AkgentService } from '../services/akgent.service';
import { ContextService } from '../services/context.service';
import { KGStateReducer } from '../services/kg-state.reducer';
import { MessageLogService } from '../services/message-log.service';
import { ActorMessageService } from '../services/message.service';
import { ToolPresenceService } from '../services/tool-presence.service';

import { AgentTabsComponent } from './agent-tabs/agent-tabs.component';
import { TeamTabsComponent } from './team-tabs/team-tabs.component';
import { KnowledgeGraphComponent } from './knowledge-graph/knowledge-graph.component';
import { MessageListComponent } from './message-list/message-list.component';
import { WorkspaceExplorerComponent } from './workspace-explorer/workspace-explorer.component';

import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TabsModule } from 'primeng/tabs';
import { BehaviorSubject, combineLatest, Observable, of, Subscription } from 'rxjs';
import { map } from 'rxjs/operators';
import { ChatPanelComponent } from '../chat/chat-panel.component';
import { ChatService } from '../services/chat.service';
import { FeedbackService } from '../services/feedback.service';
import { GraphDataService } from '../services/graph-data.service';
import { SelectionService } from '../services/selection.service';
import { ViewService } from '../view.service';

interface VisualizationOption {
  label: string;
  value: string;
  icon: string;
}

@Component({
  selector: 'app-process',
  imports: [
    CommonModule,
    MessageListComponent,
    AgentTabsComponent,
    TeamTabsComponent,
    KnowledgeGraphComponent,
    WorkspaceExplorerComponent,
    TabsModule,
    ButtonModule,
    ChatPanelComponent,
    SelectButtonModule,
    FormsModule,
  ],
  providers: [
    AsyncPipe,
    MessageLogService,
    ToolPresenceService,
    KGStateReducer,
    ActorMessageService,
    GraphDataService,
    ChatService,
    SelectionService,
    FeedbackService,
  ],
  templateUrl: './process.component.html',
  styleUrl: './process.component.scss',
})
export class ProcessComponent implements OnDestroy {
  route: ActivatedRoute = inject(ActivatedRoute);
  router: Router = inject(Router);

  akgentService: AkgentService = inject(AkgentService);
  contextService: ContextService = inject(ContextService);
  messageService: ActorMessageService = inject(ActorMessageService);
  graphDataService: GraphDataService = inject(GraphDataService);
  viewService: ViewService = inject(ViewService);
  toolPresenceService: ToolPresenceService = inject(ToolPresenceService);

  processId: string = '';
  // Workspace presence is static: every team has a workspace directory by
  // default (backend creates one on team boot). Reactive presence detection
  // for workspace is an explicit future enhancement — out of scope today.
  hasWorkspace: boolean = true;

  /**
   * Reactive presence observable for the `#KnowledgeGraphTool` actor.
   * Sourced from `ToolPresenceService.hasKnowledgeGraph$` (Story 5-2).
   * Drives the `<app-knowledge-graph>` `*ngIf` binding via `| async`.
   */
  hasKnowledgeGraph$: Observable<boolean> =
    this.toolPresenceService.hasKnowledgeGraph$;

  visualizationMode$ = new BehaviorSubject<string>('team');

  private readonly allVisualizationOptions: VisualizationOption[] = [
    { label: 'Team', value: 'team', icon: 'pi pi-users' },
    { label: 'Member', value: 'member', icon: 'pi pi-user' },
    {
      label: 'Knowledge graph',
      value: 'knowledge-graph',
      icon: 'pi pi-sitemap',
    },
    { label: 'Workspace', value: 'workspace', icon: 'pi pi-folder-open' },
    { label: 'Messages', value: 'messages', icon: 'pi pi-envelope' },
  ];

  /**
   * Reactive, filtered list of visualization options. Recomputed whenever
   * `hasKnowledgeGraph$` emits; preserves the `hasWorkspace` static gate so
   * workspace-presence reactivation is a one-line change in the future.
   * (AC3 — reactive derivation)
   */
  visualizationOptions$: Observable<VisualizationOption[]> = combineLatest([
    this.toolPresenceService.hasKnowledgeGraph$,
    of(this.hasWorkspace),
  ]).pipe(
    map(([hasKG, hasWS]) =>
      this.allVisualizationOptions.filter(
        (option) =>
          (option.value !== 'knowledge-graph' || hasKG) &&
          (option.value !== 'workspace' || hasWS),
      ),
    ),
  );

  isRightColumnCollapsed$ =
    this.viewService.isRightColumnCollapsed$.asObservable();

  isLoading$ = this.graphDataService.isLoading$;

  private presenceSub: Subscription | null = null;

  constructor() {
    // Active-mode reset guard (AC3 last clause, AC8): if the user is viewing
    // the KG tab when presence flips to `false`, snap back to 'team' so we
    // never leave the user on a hidden-mode blank panel.
    this.presenceSub = this.toolPresenceService.hasKnowledgeGraph$.subscribe(
      (hasKG) => {
        if (!hasKG && this.currentVisualizationMode === 'knowledge-graph') {
          this.visualizationMode$.next('team');
        }
      },
    );
  }

  async ngOnInit(): Promise<void> {
    this.processId = this.route.snapshot.params['id'];
    this.contextService.currentProcessId$.next(this.processId);

    const useCache = false;
    const currentProcess = await this.contextService.getCurrentTeam(
      this.processId,
      useCache
    );

    // Ensure we always have a visualization mode selected
    if (!this.visualizationMode$.value) {
      this.visualizationMode$.next('team');
    }

    if (currentProcess === null) {
      this.router.navigate(['/']);
      return;
    }

    // KG presence is reactive (Story 5-3 / ADR-004 §Decision 4): the
    // `hasKnowledgeGraph$` observable flips based on `#KnowledgeGraphTool`
    // `StartMessage` / `StopMessage` on the replay + live streams. Workspace
    // presence remains static until a future story reactivates it.

    await this.messageService.init(this.processId, isRunning(currentProcess));
  }

  ngOnDestroy() {
    this.akgentService.unselect();
    this.presenceSub?.unsubscribe();
    this.presenceSub = null;
  }

  setVisualizationMode(mode: string): void {
    this.visualizationMode$.next(mode);
  }

  get currentVisualizationMode(): string {
    return this.visualizationMode$.value || 'team';
  }

  isHidden(mode: string): boolean {
    const currentMode = this.currentVisualizationMode;
    return currentMode !== mode;
  }
}
