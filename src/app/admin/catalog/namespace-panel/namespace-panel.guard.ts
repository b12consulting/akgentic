import { inject } from '@angular/core';
import { CanDeactivateFn } from '@angular/router';
import { ConfirmationService } from 'primeng/api';

import { NamespacePanelRouteComponent } from './namespace-panel-route.component';

/**
 * `CanDeactivate` guard for the namespace-panel route presentation.
 *
 * Story 11.6 — protects the deep-link route
 * `/admin/catalog/namespace/:namespace` from losing an operator's unsaved
 * edit buffer during an internal Angular Router navigation (clicks on
 * `routerLink`, programmatic `router.navigateByUrl`, browser back/forward via
 * Angular's `popstate` binding). Does NOT intercept tab close / hard reload /
 * address-bar edits — those are the browser's `beforeunload` territory and
 * explicitly out of scope here (see the story's Dev Notes §"Dirty-state guard
 * vs. browser's native beforeunload").
 *
 * Contract:
 * - `component.panel` undefined (panel not yet mounted / race) → return
 *   `true` synchronously. No buffer state to lose.
 * - `component.panel.hasUnsavedChanges()` false → return `true`
 *   synchronously. No prompt shown.
 * - `component.panel.hasUnsavedChanges()` true → return a
 *   `Promise<boolean>` that resolves via the PrimeNG `ConfirmationService`:
 *     * confirm (accept)  → resolve(true), navigation proceeds.
 *     * reject / dismiss  → resolve(false), navigation is aborted and the
 *       panel's edit buffer is preserved.
 *
 * Prompt wording is the EXACT string `"You have unsaved changes. Discard?"` —
 * identical to the dialog presentation's close confirm (see
 * `home.component.ts` `onNamespacePanelVisibleChange`). UX consistency across
 * the two presentations is a hard requirement of Epic 11.
 *
 * Implementation notes:
 * - Functional guard (not class-based). Matches Angular v16+ idiom and the
 *   rest of this repo's route configuration.
 * - `inject(ConfirmationService)` MUST be called inside the guard body —
 *   functional guards run in a router-owned injection context at navigation
 *   time. Capturing the service via a module-level `inject()` would fail at
 *   module load outside any injection context.
 * - The `ConfirmationService` instance is resolved from the panel's scoped
 *   `providers: [ConfirmationService]` via ancestor-chain DI lookup (the same
 *   `<p-confirmDialog>` the panel already mounts renders the prompt).
 */
export const namespacePanelCanDeactivate: CanDeactivateFn<
  NamespacePanelRouteComponent
> = (component) => {
  const panel = component.panel;
  if (!panel || !panel.hasUnsavedChanges()) {
    return true;
  }
  const confirmation = inject(ConfirmationService);
  return new Promise<boolean>((resolve) => {
    confirmation.confirm({
      header: 'Unsaved changes',
      icon: 'pi pi-exclamation-triangle',
      message: 'You have unsaved changes. Discard?',
      accept: () => resolve(true),
      reject: () => resolve(false),
    });
  });
};
