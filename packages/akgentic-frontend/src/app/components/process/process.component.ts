import { AsyncPipe, CommonModule } from '@angular/common';
import { Component, inject, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { isRunning } from '../../core/context/team.interface';
import { AkgentService } from '../../core/ui/akgent.service';
import { ContextService } from '../../core/context/context.service';
import { KGStateReducer } from './selectors/knowledge-graph.selector';
import { MessageLogService } from './event/message-log.service';
import { IngestionService } from './event/ingestion.service';
import { PerAgentStoreRegistry } from './event/per-agent-store';
import { SystemPromptSelector } from './selectors/system-prompt.selector';
import { TokenUsageSelector } from './selectors/token-usage.selector';
import { ToolPresenceService } from './selectors/tool-presence.selector';
import { WorkspaceRegistryService } from './selectors/workspace-registry.selector';
import { AgentsByIdService } from './selectors/agents-by-id.selector';

import { AgentTabsComponent } from './components/agent-tabs/agent-tabs.component';
import { TeamTabsComponent } from './components/team-tabs/team-tabs.component';
import { KnowledgeGraphComponent } from './components/knowledge-graph/knowledge-graph.component';
import { MessageListComponent } from './components/message-list/message-list.component';
import { WorkspaceTabsComponent } from './components/workspace-tabs/workspace-tabs.component';

import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TabsModule } from 'primeng/tabs';
import { BehaviorSubject, combineLatest, Observable, Subscription } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';
import { ChatPanelComponent } from './components/chat/chat-panel.component';
import { ChatService } from './selectors/chat.selector';
import { FeedbackService } from './ui-state/feedback.service';
import { GraphDataService } from './selectors/graph.selector';
import { SelectionService } from './ui-state/selection.service';
import { ViewService } from '../../core/ui/view.service';

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
    WorkspaceTabsComponent,
    TabsModule,
    ButtonModule,
    ChatPanelComponent,
    SelectButtonModule,
    FormsModule,
  ],
  providers: [
    AsyncPipe,
    MessageLogService,
    // Epic 23 (ADR-019): component-scoped registry that folds the message log
    // into the set of WorkspaceDescriptors driving the workspace sub-tabs. Must
    // be provided AFTER MessageLogService (which it injects). Never
    // `providedIn: 'root'` — it shares the team-scoped log lifecycle, so a team
    // switch destroys it and never leaks workspaces across teams.
    WorkspaceRegistryService,
    // Epic 23 (ADR-020): component-scoped identity map that folds the message
    // log into `agent_id -> { name, role }`, combined in WorkspaceTabsComponent
    // with the workspace registry to render each workspace's member chips.
    // Provided AFTER MessageLogService (which it injects); never
    // `providedIn: 'root'` — it shares the team-scoped log lifecycle.
    AgentsByIdService,
    ToolPresenceService,
    KGStateReducer,
    SystemPromptSelector,
    // Epic 17 (ADR-014): component-scoped registry that derives per-agent
    // `state` / `context` from `log$`. Must be provided BEFORE
    // IngestionService (which injects it). Never `providedIn: 'root'` —
    // a team switch destroys this component, destroying the registry and its
    // single `log$` subscription (same lifecycle guarantee as MessageLogService).
    PerAgentStoreRegistry,
    IngestionService,
    // Epic 26 (ADR-022): component-scoped read surface over the `tokenUsage`
    // PerAgentStore. Provided AFTER IngestionService (which it injects); never
    // `providedIn: 'root'` — it shares the team-scoped log lifecycle, so a team
    // switch destroys it and the usage pill always reads THIS team's totals.
    TokenUsageSelector,
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
  ingestionService: IngestionService = inject(IngestionService);
  graphDataService: GraphDataService = inject(GraphDataService);
  viewService: ViewService = inject(ViewService);
  toolPresenceService: ToolPresenceService = inject(ToolPresenceService);
  private readonly workspaceRegistry = inject(WorkspaceRegistryService);

  processId: string = '';

  /**
   * Reactive presence observable for the `#KnowledgeGraphTool` actor.
   * Sourced from `ToolPresenceService.hasKnowledgeGraph$` (Story 5-2).
   * Drives the `<app-knowledge-graph>` `*ngIf` binding via `| async`.
   */
  hasKnowledgeGraph$: Observable<boolean> =
    this.toolPresenceService.hasKnowledgeGraph$;

  /**
   * Reactive workspace presence (ADR-020): the team has at least one workspace
   * iff the registry holds at least one descriptor (i.e. some agent declared a
   * `WorkspaceTool`). Drives both the `Workspaces` tab option and the
   * `<app-workspace-tabs>` `*ngIf` — the whole tab disappears when no workspace
   * exists, mirroring the Knowledge graph tab.
   */
  hasWorkspace$: Observable<boolean> = this.workspaceRegistry.workspaces$.pipe(
    map((ws) => ws.length > 0),
    distinctUntilChanged(),
  );

  visualizationMode$ = new BehaviorSubject<string>('team');

  private readonly allVisualizationOptions: VisualizationOption[] = [
    { label: 'Team', value: 'team', icon: 'pi pi-users' },
    { label: 'Member', value: 'member', icon: 'pi pi-user' },
    {
      label: 'Knowledge graph',
      value: 'knowledge-graph',
      icon: 'pi pi-sitemap',
    },
    { label: 'Workspaces', value: 'workspace', icon: 'pi pi-folder-open' },
    { label: 'Messages', value: 'messages', icon: 'pi pi-envelope' },
  ];

  /**
   * Reactive, filtered list of visualization options. Recomputed whenever
   * `hasKnowledgeGraph$` or `hasWorkspace$` emits — the Knowledge graph and
   * Workspaces tabs each appear only when their tool is present.
   */
  visualizationOptions$: Observable<VisualizationOption[]> = combineLatest([
    this.toolPresenceService.hasKnowledgeGraph$,
    this.hasWorkspace$,
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
  private workspaceSub: Subscription | null = null;

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

    // Same guard for the Workspaces tab: if it disappears (last workspace tool
    // removed) while the user is viewing it, snap back to 'team'.
    this.workspaceSub = this.hasWorkspace$.subscribe((hasWS) => {
      if (!hasWS && this.currentVisualizationMode === 'workspace') {
        this.visualizationMode$.next('team');
      }
    });
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

    await this.ingestionService.init(this.processId, isRunning(currentProcess));
  }

  ngOnDestroy() {
    this.akgentService.unselect();
    this.presenceSub?.unsubscribe();
    this.presenceSub = null;
    this.workspaceSub?.unsubscribe();
    this.workspaceSub = null;
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
