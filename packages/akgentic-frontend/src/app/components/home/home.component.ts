import {
  Component,
  HostListener,
  inject,
  ViewChild,
  ViewChildren,
  QueryList,
  ElementRef,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { BehaviorSubject, firstValueFrom, Observable } from 'rxjs';
import { filter, map, take } from 'rxjs/operators';

import { ApiService } from '../../core/http/api.service';
import { TeamContext, isRunning } from '../../core/context/team.interface';
import { NamespaceSummary } from '../../protocol/catalog.interface';

import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';
import { SelectModule } from 'primeng/select';
import { TableModule, TableLazyLoadEvent } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { ToggleSwitchModule } from 'primeng/toggleswitch';

import { AuthService } from '../../core/auth/auth.service';
import { ConfigService } from '../../core/config/config.service';
import { ContextService } from '../../core/context/context.service';

// Listed in @Component.imports so Angular's @defer block can resolve
// <app-namespace-panel>. The `@defer (when ...)` block in the template keeps
// the component's compiled code (and its Monaco chunk) in a deferred chunk
// loaded only on first opening of the namespace-editor dialog — the initial
// home-page bundle stays Monaco-free.
import { NamespacePanelComponent } from '../catalog/namespace-panel/namespace-panel.component';

// Classic team-list page size (Epic 28, ADR-032 §Decision 3). Bound to the
// paginator's [rows] and used as the loadTeamsPage size fallback so no magic
// 250 literal is duplicated. Server clamps size to [1, 500].
const PAGE_SIZE = 250;

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
    InputTextModule,
    ToggleSwitchModule,
    NamespacePanelComponent,
  ],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent {
  apiService: ApiService = inject(ApiService);
  contextService: ContextService = inject(ContextService);
  router: Router = inject(Router);
  authService: AuthService = inject(AuthService);
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

  // Classic paginator state (Epic 28). `rows` feeds [rows]; `first` is the
  // row offset the paginator is parked on; `currentPage` (1-based) is tracked
  // so create/restore/refresh reload the right page. The table's first
  // (onLazyLoad) seeds page 1 — ngOnInit no longer fetches the list (AC #3).
  readonly rows = PAGE_SIZE;
  first = 0;
  currentPage = 1;

  // Flips true after the table's first (onLazyLoad) page-1 load resolves. The
  // hideHome branch awaits this then reads teams$ reactively — so exactly one
  // page-1 fetch happens (the seed), never a second ngOnInit fetch (AC #3).
  private firstPageLoaded$ = new BehaviorSubject<boolean>(false);

  // Controls the Namespace Panel dialog visibility. The panel component is
  // mounted lazily via @defer in home.component.html so its Monaco-editor
  // dependency is NOT part of the initial home-page chunk.
  namespacePanelVisible: boolean = false;

  // Admin-only "show all namespaces" toggle (ADR-028).
  //
  // This is an admin-gated UX affordance, NOT the security boundary. The
  // authoritative "see all" enforcement is the infra unscoping of admin reads
  // (`?all=true` honoured server-side only when the caller's roles include
  // `admin`). A non-admin who forges the flag gets the normal owner+public
  // list back, so the toggle must never be relied upon for enforcement.
  //
  // `showAllNamespaces` is the single source of truth read inside
  // `loadNamespaces()` and forwarded to `getNamespaces`/the panel; it defaults
  // OFF so even an admin starts on the owner+public list (opt-in, never an
  // always-on firehose).
  showAllNamespaces = false;

  // Reactive admin predicate. Derived from `authService.currentUser$` (NOT a
  // one-shot eager read) because `ngOnInit` fires `checkAuth()` which resolves
  // `/auth/me` AFTER first render — reading `currentUserValue` once would miss
  // the late admin resolution. `roles` is read off the verbatim `/auth/me`
  // body (typed `any`); the optional chain yields `false` for the anonymous
  // user (no `roles`). Consumed in the template via the `async` pipe so the
  // toggle appears once the deferred admin user lands.
  isAdmin$: Observable<boolean> = this.authService.currentUser$.pipe(
    map((u) => u?.roles?.includes('admin') === true),
  );

  @ViewChildren('descriptionInput') descriptionInputs!: QueryList<ElementRef>;

  // @ViewChild on the @defer-rendered panel. The reference is `undefined`
  // until the user opens the dialog (the @defer block only mounts the child
  // when `namespacePanelVisible` flips true), so the close handler MUST
  // null-check. `{ static: false }` is the default for @ViewChild on
  // conditionally-rendered children; no explicit option needed.
  @ViewChild(NamespacePanelComponent)
  namespacePanel?: NamespacePanelComponent;

  // Expose isRunning to template
  isRunning = isRunning;

  /**
   * Catalog v2 predicate. Catalog v1 (enterprise) has no namespaces, so the
   * namespace-editor affordances ("Configuration" panel + "Show all
   * namespaces" toggle) are hidden in v1 mode. Defaults to v2 when
   * `catalogVersion` is unset — preserving existing behaviour.
   */
  get isV2(): boolean {
    return this.config.catalogVersion !== 'v1';
  }

  async ngOnInit() {
    await this.loadNamespaces();

    // The list is seeded by the table's first (onLazyLoad) (page 1) — NOT a
    // fetch here, which would double-seed (AC #3). The hideHome branch reads
    // the seeded list reactively below.
    if (this.config.hideHome) {
      await this.handleHideHome();
    }

    this.authService.checkAuth().subscribe();
  }

  /**
   * Classic lazy paginator load (Epic 28, ADR-032 §Decision 3). PrimeNG fires
   * (onLazyLoad) once on init (first: 0) and on every page change. Computes the
   * 1-based page from the row offset and delegates to the 28.1 data layer,
   * which REPLACES teams$ with the fetched page (one page in the DOM) and sets
   * totalCount. Tracks first/currentPage so create/restore/refresh reload the
   * right page.
   */
  async loadPage(event: TableLazyLoadEvent): Promise<void> {
    const size = event.rows ?? PAGE_SIZE;
    this.first = event.first ?? 0;
    this.currentPage = Math.floor(this.first / size) + 1;
    await this.contextService.loadTeamsPage(this.currentPage, size);
    this.firstPageLoaded$.next(true);
  }

  /**
   * hideHome auto-route: once the table's first page-1 seed lands, read the
   * current page reactively from teams$ (no extra fetch) and either create a
   * team (empty) or navigate to the first one. Preserves the master behavior.
   */
  private async handleHideHome(): Promise<void> {
    await firstValueFrom(this.firstPageLoaded$.pipe(filter((done) => done), take(1)));
    const teams = await firstValueFrom(this.contextService.teams$.pipe(take(1)));
    if (!teams || teams.length === 0) {
      const selected = this.selectedNamespace$.value;
      if (selected) {
        await this.contextService.createTeamAndNavigate(selected.namespace);
      }
      return;
    }
    this.router.navigate(['/process', teams[0].team_id]);
  }

  /**
   * Load catalog namespaces for the dropdown. Shared by initial load and the
   * 2xx save branch so the fetch and error handling live in one place.
   *
   * Reconciles the current selection against the freshly-fetched list,
   * comparing on the stable `namespace` identifier (NOT object reference —
   * every fetch returns new objects — and NOT the display `name`, which two
   * summaries may share). If the current selection is no longer present (e.g.
   * it was just deleted), re-select the first remaining namespace, or `null`
   * when the list is empty. If it is still present, leave the subject
   * untouched to avoid a gratuitous dropdown flicker on an unrelated refresh.
   * A `null` current selection is "not present", so a non-empty list still
   * auto-selects `namespaces[0]`.
   */
  private async loadNamespaces(): Promise<void> {
    try {
      // Forward the admin "show all" flag through the single load path so
      // every caller (initial, save, clone, delete, refresh, toggle) stays
      // consistent and the selection reconciliation below runs on every
      // re-fetch. `all=true` is honoured server-side only for admins; for
      // everyone else it is a no-op (normal owner+public list).
      const namespaces = await this.apiService.getNamespaces({
        all: this.showAllNamespaces,
      });
      this.namespaces$.next(namespaces);
      const current = this.selectedNamespace$.value;
      const stillExists =
        current != null && namespaces.some((n) => n.namespace === current.namespace);
      if (!stillExists) {
        this.selectedNamespace$.next(namespaces.length > 0 ? namespaces[0] : null);
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
      // New team is newest under created_at desc → it belongs on page 1.
      // Move the paginator to page 1 and reload (REPLACE — no empty flash).
      this.first = 0;
      this.currentPage = 1;
      await this.contextService.loadTeamsPage(1, PAGE_SIZE);
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
      // Reload the current page (REPLACE — no empty flash); no page jump.
      await this.contextService.loadTeamsPage(this.currentPage, PAGE_SIZE);
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
      // Reload the current page (REPLACE — no empty flash); no page jump.
      await this.contextService.loadTeamsPage(this.currentPage, PAGE_SIZE);
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
   * Dialog dirty-close guard (ADR-018).
   *
   * The `p-dialog` binding is split into `[visible]` + `(visibleChange)` so
   * this handler can intercept close attempts (the X button / dismissable
   * mask; Esc is handled by `onConfigDialogEscape`). When the user dismisses
   * the dialog and the panel reports unsaved changes, re-assert
   * `namespacePanelVisible = true` to keep the dialog open while the panel's
   * `confirmDiscard()` modal runs. On Proceed (resolve `true`) flip visibility
   * to false; on Cancel/dismiss (resolve `false`) leave the dialog open with
   * the buffer intact.
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
    // panel's custom confirm modal runs. Proceed closes; Cancel/dismiss keeps
    // it open.
    this.namespacePanelVisible = true;
    void panel.confirmDiscard().then((discard) => {
      if (discard) {
        this.namespacePanelVisible = false;
      }
    });
  }

  /**
   * Single coordinated Escape handler for the config dialog (ADR-018).
   *
   * All three dialogs (this host config dialog + the panel's Clone + confirm
   * modals) set `[closeOnEscape]="false"`, so PrimeNG's per-dialog
   * document-level Esc listeners never fire and cannot cascade. Instead this
   * ONE document-level handler coordinates Escape while the config dialog is
   * open. A document listener (not a `<p-dialog>`-scoped one) is load-bearing:
   * a secondary modal is teleported to `<body>` as a SIBLING overlay, so its
   * keydown does not bubble to the config dialog element — only a
   * document-level handler sees Escape regardless of which overlay has focus.
   *
   * Exactly ONE action per Escape, in priority order:
   *   1. delegate to `panel.handleSecondaryEscape()` — if a secondary modal
   *      (confirm, then Clone) is open it closes ONLY the topmost one and
   *      returns `true`; we stop there.
   *   2. otherwise (no secondary panel open) run the config panel's own close
   *      flow (`onNamespacePanelVisibleChange(false)`), which routes a dirty
   *      buffer through `confirmDiscard()`.
   *
   * Inactive unless the config dialog is open; a write-in-flight suppresses
   * Escape entirely.
   */
  @HostListener('document:keydown.escape', ['$event'])
  onConfigDialogEscape(event: Event): void {
    if (!this.namespacePanelVisible || this.isWriteInFlight) {
      return;
    }
    const panel = this.namespacePanel;
    if (panel?.handleSecondaryEscape() === true) {
      // A secondary modal consumed the Escape — do not also close the config
      // panel. Prevent the default so nothing else acts on this keystroke.
      event.preventDefault();
      return;
    }
    this.onNamespacePanelVisibleChange(false);
  }

  /**
   * `(saved)` output handler. Re-fetches namespaces so any summary-metadata
   * changes (e.g. renamed description) propagate to the dropdown. Shares
   * `loadNamespaces()` with `ngOnInit` to keep the fetch logic in one place.
   */
  async onNamespaceSaved(): Promise<void> {
    await this.loadNamespaces();
  }

  /**
   * Admin "show all namespaces" toggle handler. Flips the component flag and
   * re-runs the single `loadNamespaces()` path so the `all` flag flows through
   * it (the toggle never calls `getNamespaces` directly — that keeps every
   * load path consistent and preserves the stale-selection reconciliation on
   * the re-fetch). Turning on requests `?all=true` (admin firehose); turning
   * off restores the normal owner+public list.
   */
  async onToggleShowAll(value: boolean): Promise<void> {
    this.showAllNamespaces = value;
    await this.loadNamespaces();
  }

  /**
   * Pure derivation of namespace identifiers from the synchronous current
   * value of `namespaces$`. Supplied to the panel via `[existingNamespaces]`
   * for the Clone dialog's pre-flight collision check. Getter instead of a
   * dedicated stream because BehaviorSubject exposes `.value` synchronously;
   * no pipe / async needed.
   */
  get namespaceIdentifiers(): string[] {
    return (this.namespaces$.value ?? []).map((n) => n.namespace);
  }

  /**
   * Write-in-flight predicate. True iff the panel is currently saving OR
   * cloning. Reads (validate / load) are NON-destructive and intentionally
   * excluded so the operator can dismiss the dialog while a Validate request
   * is mid-flight.
   *
   * Used by the dialog's `[closable]` / `[dismissableMask]` bindings and by
   * the coordinated `onConfigDialogEscape` handler to lock all dismissal
   * channels during an in-flight write. (`[closeOnEscape]` is always `false` —
   * Escape is owned by `onConfigDialogEscape`.) The panel's destroyed-guard
   * absorbs late resolutions; this gate just prevents the operator from
   * hitting that race in the first place.
   */
  get isWriteInFlight(): boolean {
    return (
      this.namespacePanel?.saving === true ||
      this.namespacePanel?.cloning === true
    );
  }

  /**
   * Namespace label for the dialog header. Resolves (selectedNamespace.name ??
   * selectedNamespace.namespace ?? 'Namespace'). Wrapped in a getter so the
   * dialog's `<ng-template pTemplate="header">` block can render it alongside
   * the conditional dirty indicator without two async pipes.
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
