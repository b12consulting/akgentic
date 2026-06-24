import { CanDeactivateFn } from '@angular/router';

import { NamespacePanelRouteComponent } from './namespace-panel-route.component';

/**
 * `CanDeactivate` guard for the namespace-panel route presentation.
 *
 * Protects the deep-link route `/admin/catalog/namespace/:namespace` from
 * losing an operator's unsaved edit buffer during an internal Angular Router
 * navigation (clicks on `routerLink`, programmatic `router.navigateByUrl`,
 * browser back/forward via Angular's `popstate` binding). Does NOT intercept
 * tab close / hard reload / address-bar edits — those are the browser's
 * `beforeunload` territory and out of scope here.
 *
 * Contract:
 * - `component.panel` undefined (panel not yet mounted / race) → return
 *   `true` synchronously. No buffer state to lose.
 * - `component.panel.hasUnsavedChanges()` false → return `true`
 *   synchronously. No prompt shown.
 * - `component.panel.hasUnsavedChanges()` true → delegate to the panel's
 *   custom confirmation modal via `panel.confirmDiscard()`, which returns a
 *   `Promise<boolean>`:
 *     * Proceed → resolve(true), navigation proceeds.
 *     * Cancel / dismiss (Esc / X) → resolve(false), navigation is aborted and
 *       the panel's edit buffer is preserved.
 *
 * The guard reuses the SAME panel-owned custom modal as the Home config-dialog
 * close confirm (`confirmDiscard()`) (ADR-018).
 *
 * Prompt wording is the EXACT string `"You have unsaved changes. Discard?"`
 * (defined once in `panel.confirmDiscard()`) — identical to the dialog
 * presentation's close confirm, keeping UX consistent across both
 * presentations.
 *
 * Implementation notes:
 * - Functional guard (not class-based). Matches Angular v16+ idiom and the
 *   rest of this repo's route configuration.
 * - No `inject()` needed: the confirm surface lives on the panel instance the
 *   route shell exposes via `@ViewChild`, so the guard reads it directly off
 *   `component.panel`.
 */
export const namespacePanelCanDeactivate: CanDeactivateFn<
  NamespacePanelRouteComponent
> = (component) => {
  const panel = component.panel;
  if (!panel || !panel.hasUnsavedChanges()) {
    return true;
  }
  return panel.confirmDiscard();
};
