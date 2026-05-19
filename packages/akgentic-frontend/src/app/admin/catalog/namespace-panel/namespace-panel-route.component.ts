import { CommonModule } from '@angular/common';
import {
  Component,
  DestroyRef,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { ApiService } from '../../../services/api.service';
import { NamespacePanelComponent } from './namespace-panel.component';

/**
 * Route-shell host for `NamespacePanelComponent` on the deep-link route.
 *
 * Story 11.6 — mounts the panel inside a full-page admin layout (header +
 * back-to-home link) for the route `/admin/catalog/namespace/:namespace`.
 * The shell exists to keep `NamespacePanelComponent` host-agnostic (NFR6):
 * the panel itself MUST NOT import `Router`, `ActivatedRoute`, `RouterLink`,
 * etc. — all route-level concerns live here.
 *
 * Responsibilities:
 * - Read the `:namespace` URL param and pass it to the panel's
 *   `@Input() namespace` binding.
 * - Fetch `existingNamespaces` once on mount (fire-and-forget) so the
 *   panel's Clone sub-dialog has a collision list to pre-flight against.
 * - Re-fetch `existingNamespaces` on the panel's `(saved)` emission so a
 *   fresh namespace created via Clone propagates into the collision list
 *   (mirrors `HomeComponent.onNamespaceSaved`).
 * - Expose a `panel` `@ViewChild` reference so the `CanDeactivate` guard can
 *   read `panel.hasUnsavedChanges()`.
 *
 * What the shell does NOT do:
 * - Does NOT wire the panel's `(closed)` output — there is no dialog to
 *   dismiss in the route presentation.
 * - Does NOT embed its own `<p-confirmDialog>` — the panel already renders
 *   one via its scoped `providers: [ConfirmationService]`, and the guard
 *   reuses that scoped service.
 * - Does NOT read panel internals (`loading`, `serverYaml`) — the
 *   back-to-home link is always-present in the header, so the shell never
 *   needs conditional logic based on panel state.
 */
@Component({
  selector: 'app-namespace-panel-route',
  standalone: true,
  imports: [CommonModule, RouterLink, NamespacePanelComponent],
  templateUrl: './namespace-panel-route.component.html',
  styleUrls: ['./namespace-panel-route.component.scss'],
})
export class NamespacePanelRouteComponent implements OnInit {
  private activatedRoute: ActivatedRoute = inject(ActivatedRoute);
  private apiService: ApiService = inject(ApiService);
  private destroyRef: DestroyRef = inject(DestroyRef);

  /**
   * Bound to the panel's `@Input() namespace`. Updated whenever the URL
   * param changes (in-place navigation between two deep-link URLs does
   * NOT re-mount the shell — Angular reuses the same component instance
   * when the matched route is unchanged; the paramMap subscription is the
   * single source of truth).
   */
  currentNamespace: string = '';

  /**
   * Bound to the panel's `@Input() existingNamespaces` (Clone pre-flight
   * collision check — Story 11.5). Populated by a fire-and-forget fetch in
   * `ngOnInit` and refreshed on the panel's `(saved)` emission.
   */
  existingNamespaces: string[] = [];

  /**
   * `@ViewChild` reference consumed by the `CanDeactivate` guard to read
   * `panel.hasUnsavedChanges()` when navigation away is attempted. `?`
   * because the panel is rendered inside the template and may not be
   * available before the first change-detection tick.
   */
  @ViewChild(NamespacePanelComponent) panel?: NamespacePanelComponent;

  constructor() {
    // Subscribe to route params in the constructor so the first emission is
    // synchronous relative to `ngOnInit`'s `getNamespaces()` kick-off.
    // `takeUntilDestroyed` (Angular v16+) ties the subscription's lifetime
    // to the component's destroy lifecycle — no manual `ngOnDestroy` dance.
    this.activatedRoute.paramMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((params) => {
        this.currentNamespace = params.get('namespace') ?? '';
      });
  }

  ngOnInit(): void {
    // Fire-and-forget. Mirrors HomeComponent's `loadNamespaces()` pattern:
    // the panel's own `exportNamespace` runs in parallel, and a rejection
    // here just leaves `existingNamespaces` empty (the Clone pre-flight
    // collision check degrades to "empty-or-source" which is acceptable
    // per ADR-011 D5 — last-writer-wins collision-on-race).
    this.apiService
      .getNamespaces()
      .then((list) => {
        this.existingNamespaces = list.map((n) => n.namespace);
      })
      .catch(() => {
        // Leave `existingNamespaces` as []; the panel's Clone pre-flight
        // still catches destNs === source via `cloneValidationError`.
      });
  }

  /**
   * `(saved)` handler — re-fetches the namespace list so a newly-created
   * namespace (via Clone) populates the Clone sub-dialog's collision check
   * on the next open. Fire-and-forget; on rejection leave the prior list
   * intact so the UX does not flash empty.
   */
  onSaved(): void {
    this.apiService
      .getNamespaces()
      .then((list) => {
        this.existingNamespaces = list.map((n) => n.namespace);
      })
      .catch(() => {
        /* keep prior list on rejection */
      });
  }
}
