import {
  Component,
  inject,
  ViewChild,
  ViewChildren,
  QueryList,
  ElementRef,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { BehaviorSubject, firstValueFrom } from 'rxjs';

import { ApiService } from '../services/api.service';
import { TeamContext, isRunning } from '../models/team.interface';
import { NamespaceSummary } from '../models/catalog.interface';

import { CommonModule } from '@angular/common';
import { ConfirmationService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { SelectModule } from 'primeng/select';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';

import { AuthService } from '../services/auth.service';
import { ConfigService } from '../services/config.service';
import { ContextService } from '../services/context.service';

// Story 11.2 — Listed in @Component.imports so Angular's @defer block can
// resolve <app-namespace-panel>. The `@defer (when ...)` block in the
// template ensures the component's compiled code (and its Monaco chunk)
// lives in a deferred chunk that is only loaded on first opening of the
// namespace-editor dialog — the initial home-page bundle stays Monaco-free.
import { NamespacePanelComponent } from '../admin/catalog/namespace-panel/namespace-panel.component';

@Component({
  selector: 'app-home',
  imports: [
    FormsModule,
    TableModule,
    SelectModule,
    ButtonModule,
    TagModule,
    CommonModule,
    DialogModule,
    ConfirmDialogModule,
    InputTextModule,
    NamespacePanelComponent,
  ],
  // First PrimeNG ConfirmDialog in the HomeComponent — scoped locally for
  // the dirty-close guard (Story 11.3 AC 10). The NamespacePanelComponent
  // provides its OWN ConfirmationService for its Cancel-with-dirty-buffer
  // confirm, so they don't collide.
  providers: [ConfirmationService],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent {
  apiService: ApiService = inject(ApiService);
  contextService: ContextService = inject(ContextService);
  router: Router = inject(Router);
  authService: AuthService = inject(AuthService);
  private confirmationService: ConfirmationService = inject(ConfirmationService);
  private config = inject(ConfigService);

  // Catalog namespaces for the team creation dropdown
  namespaces$ = new BehaviorSubject<NamespaceSummary[]>([]);
  selectedNamespace$ = new BehaviorSubject<NamespaceSummary | null>(null);
  isCreatingTeam = false;
  isRefreshing = false;
  stoppingTeams = new Set<string>();
  restoringTeams = new Set<string>();
  editingDescriptionFor: string | null = null;
  descriptionDrafts = new Map<string, string>();

  // Story 11.2 — controls the Namespace Panel dialog visibility. The panel
  // component is mounted lazily via @defer in home.component.html so its
  // Monaco-editor dependency is NOT part of the initial home-page chunk.
  namespacePanelVisible: boolean = false;

  @ViewChildren('descriptionInput') descriptionInputs!: QueryList<ElementRef>;

  // Story 11.3 — @ViewChild on the @defer-rendered panel. The reference is
  // `undefined` until the user opens the dialog (the @defer block only
  // mounts the child when `namespacePanelVisible` flips true), so the close
  // handler MUST null-check. `{ static: false }` is the default for
  // @ViewChild on conditionally-rendered children; no explicit option needed.
  @ViewChild(NamespacePanelComponent)
  namespacePanel?: NamespacePanelComponent;

  // Expose isRunning to template
  isRunning = isRunning;

  async ngOnInit() {
    await this.loadNamespaces();

    // Populate _context$; return value reused for the hideHome branch.
    const teams = await this.contextService.getTeams();

    if (this.config.hideHome) {
      // If no team exists, create one using the first namespace.
      if (!teams || teams.length === 0) {
        const selected = this.selectedNamespace$.value;
        if (selected) {
          await this.contextService.createTeamAndNavigate(selected.namespace);
        }
      }
      // If a team exists, navigate to its process page.
      if (teams && teams.length > 0) {
        const teamId = teams[0].team_id;
        this.router.navigate(['/process', teamId]);
      }
    }

    this.authService.checkAuth().subscribe();
  }

  /**
   * Load catalog namespaces for the dropdown. Extracted so the 2xx save
   * branch (Story 11.3 AC 6) can re-invoke the same code path without
   * duplicating the error handling.
   */
  private async loadNamespaces(): Promise<void> {
    try {
      const namespaces = await this.apiService.getNamespaces();
      this.namespaces$.next(namespaces);
      if (namespaces.length > 0 && !this.selectedNamespace$.value) {
        this.selectedNamespace$.next(namespaces[0]);
      }
    } catch (error) {
      console.error('Failed to load namespaces:', error);
    }
  }

  async createTeam() {
    this.isCreatingTeam = true;
    try {
      const selected = this.selectedNamespace$.value;
      if (!selected) {
        console.warn('No namespace selected');
        return;
      }
      await this.apiService.createTeam(selected.namespace);
      await this.contextService.getTeams();
    } catch (error) {
      console.error('Failed to create team:', error);
    } finally {
      this.isCreatingTeam = false;
    }
  }

  async createTeamAndNavigate() {
    const selected = this.selectedNamespace$.value;
    if (!selected) {
      console.warn('No namespace selected');
      return;
    }
    await this.contextService.createTeamAndNavigate(selected.namespace);
  }

  async deleteTeam(teamId: string) {
    await this.contextService.deleteTeam(teamId);
  }

  async restoreTeam(teamId: string) {
    this.restoringTeams.add(teamId);
    try {
      await this.apiService.restoreTeam(teamId);
      await this.contextService.getTeams();
    } finally {
      this.restoringTeams.delete(teamId);
    }
  }

  isRestoring(teamId: string): boolean {
    return this.restoringTeams.has(teamId);
  }

  async stopTeam(teamId: string) {
    this.stoppingTeams.add(teamId);
    try {
      await this.contextService.stopTeamAndAwait(teamId);
    } catch (error) {
      console.error(`Failed to stop team ${teamId}:`, error);
    } finally {
      this.stoppingTeams.delete(teamId);
    }
  }

  isStopping(teamId: string): boolean {
    return this.stoppingTeams.has(teamId);
  }

  async refreshContext() {
    this.isRefreshing = true;
    try {
      await this.contextService.getTeams();
    } finally {
      this.isRefreshing = false;
    }
  }

  onRowSelect(event: any) {
    const teamId = event.data.team_id;
    this.router.navigate(['/process', teamId]);
  }

  startEditDescription(teamId: string, currentDescription: string | null) {
    this.editingDescriptionFor = teamId;
    this.descriptionDrafts.set(teamId, currentDescription || '');

    // Focus the input field after the view updates
    setTimeout(() => {
      const input = this.descriptionInputs?.first?.nativeElement;
      if (input) {
        input.focus();
        input.select();
      }
    }, 0);
  }

  cancelEditDescription() {
    this.editingDescriptionFor = null;
  }

  async saveDescription(teamId: string) {
    const description = this.descriptionDrafts.get(teamId) || null;
    const trimmed = description?.trim() || null;

    try {
      // Note: updateTeamDescription is a no-op in V2 (no equivalent endpoint).
      // Description changes will not persist. This is a known limitation.
      console.warn(
        'Description editing is not available in V2 -- changes will not persist.'
      );
      await this.apiService.updateTeamDescription(teamId, trimmed);

      // Update local context optimistically (read current list from teams$).
      const teams = await firstValueFrom(this.contextService.teams$);
      const team = teams.find((ctx: TeamContext) => ctx.team_id === teamId);
      if (team) {
        team.description = trimmed;
      }

      this.editingDescriptionFor = null;
    } catch (error) {
      console.error('Failed to update description:', error);
    }
  }

  /**
   * Story 11.3 AC 10 — dialog dirty-close guard.
   *
   * The `p-dialog` binding is split into `[visible]` + `(visibleChange)` so
   * this handler can intercept close attempts. When the user dismisses the
   * dialog and the panel reports unsaved changes, re-assert
   * `namespacePanelVisible = true` and trigger a PrimeNG ConfirmDialog. On
   * confirm, flip visibility to false. On dismiss, leave the dialog open.
   *
   * `this.namespacePanel` may be `undefined` (the panel is mounted lazily
   * via @defer) — when it is not mounted there is nothing to discard, so
   * the close proceeds without a confirm.
   */
  onNamespacePanelVisibleChange(visible: boolean): void {
    if (visible) {
      // The dialog is being opened — the Edit button click already flipped
      // `namespacePanelVisible = true`. No-op.
      return;
    }
    const panel = this.namespacePanel;
    if (!panel || !panel.hasUnsavedChanges()) {
      this.namespacePanelVisible = false;
      return;
    }
    // Dirty panel — re-assert visibility to keep the dialog open while the
    // confirm runs. The confirm's accept callback then truly closes.
    this.namespacePanelVisible = true;
    this.confirmationService.confirm({
      header: 'Unsaved changes',
      icon: 'pi pi-exclamation-triangle',
      message: 'You have unsaved changes. Discard?',
      accept: () => {
        this.namespacePanelVisible = false;
      },
      // `reject` intentionally omitted — dismissing keeps the dialog open
      // (AC 10 dismiss branch).
    });
  }

  /**
   * Story 11.3 AC 6 — `(saved)` output handler. Re-fetches namespaces so
   * any summary-metadata changes (e.g. renamed description) propagate to
   * the dropdown. Shares `loadNamespaces()` with `ngOnInit` to keep the
   * fetch logic in one place.
   */
  async onNamespaceSaved(): Promise<void> {
    await this.loadNamespaces();
  }

  /**
   * Story 11.5 AC 13 — pure derivation of namespace identifiers from the
   * synchronous current value of `namespaces$`. Supplied to the panel via
   * `[existingNamespaces]` for the Clone dialog's pre-flight collision
   * check (panel's AC 4). Getter instead of a dedicated stream because
   * BehaviorSubject exposes `.value` synchronously; no pipe / async needed.
   */
  get namespaceIdentifiers(): string[] {
    return (this.namespaces$.value ?? []).map((n) => n.namespace);
  }

  /**
   * Story 11.7 AC 22, 23 — write-in-flight predicate (FR18). True iff the
   * panel is currently saving OR cloning. Reads (validate / load) are
   * NON-destructive and intentionally excluded so the operator can dismiss
   * the dialog while a Validate request is mid-flight.
   *
   * Used by the dialog's `[closable]` / `[closeOnEscape]` /
   * `[dismissableMask]` bindings to lock all dismissal channels during
   * an in-flight write. Existing destroyed-guard pattern in the panel
   * (Story 11.3 AC 13 + 11.5 AC 16) absorbs late resolutions; this gate
   * just prevents the operator from hitting that race in the first place.
   */
  get isWriteInFlight(): boolean {
    return (
      this.namespacePanel?.saving === true ||
      this.namespacePanel?.cloning === true
    );
  }

  /**
   * Story 11.7 AC 8 — namespace label for the dialog header. Inlined
   * resolution of (selectedNamespace.name ?? selectedNamespace.namespace ??
   * 'Namespace') from the existing async pipe. Wrapped in a getter so the
   * dialog's `<ng-template pTemplate="header">` block can render it
   * alongside the conditional dirty indicator without two async pipes.
   */
  get namespaceLabel(): string {
    const selected = this.selectedNamespace$.value;
    if (selected === null) {
      return 'Namespace';
    }
    return selected.name ?? selected.namespace ?? 'Namespace';
  }

  visible = false;
}
