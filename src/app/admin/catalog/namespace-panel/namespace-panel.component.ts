import { CommonModule } from '@angular/common';
import {
  Component,
  DestroyRef,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnInit,
  Output,
  SimpleChanges,
  ViewChild,
  inject,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import { NuMonacoEditorModule } from '@ng-util/monaco-editor';
import { ConfirmationService, MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { TooltipModule } from 'primeng/tooltip';

import { NamespaceValidationReport } from '../../../models/catalog.interface';
import { ApiService } from '../../../services/api.service';
import { HttpError } from '../../../services/fetch.service';
import {
  CloneYamlError,
  extractYamlNamespace,
  rewriteNamespaceInYaml,
} from '../../../services/yaml-clone.helper';
import { ValidationReportComponent } from './validation-report/validation-report.component';

/**
 * NamespacePanelComponent — host-agnostic view of a catalog namespace YAML.
 *
 * Story 11.2 delivered the read-only scaffold. Story 11.3 adds:
 *   - Edit / Cancel / Save action row (Edit replaced by Cancel+Save in edit mode).
 *   - `hasUnsavedChanges()` is now a real buffer-vs-serverYaml comparison.
 *   - Save flow posts directly to `apiService.importNamespace` — NO
 *     pre-save `validate` round-trip (ADR-011 D4).
 *   - 422 → populate `lastValidation` (structured) or `rawSaveError`
 *     (fallback); stay in edit mode, buffer preserved.
 *   - 5xx / 4xx (other than 401/422) → sticky error toast + in-panel Retry
 *     button; stay in edit mode, buffer preserved.
 *   - 401 → no panel-specific behaviour; the FetchService global toast
 *     surfaces the failure, the app-wide handler (if any) runs. Panel only
 *     clears `saving` in `finally`.
 *   - PrimeNG `ConfirmDialog` guards Cancel-with-dirty-buffer.
 *
 * The component remains presentation-only: no `Router`, `ActivatedRoute`,
 * `MAT_DIALOG_DATA`, etc. The dialog wrapper (HomeComponent) and the future
 * deep-link route (Story 11.6) mount it identically.
 *
 * Public surface contract (stable across Epic 11):
 *   - @Input() namespace: string          — required, drives the load flow.
 *   - @Output() closed: EventEmitter<void> — asks the host to dismiss.
 *   - @Output() saved: EventEmitter<void>  — emitted once per successful
 *     import; hosts re-fetch derived lists (e.g. HomeComponent's dropdown).
 *   - hasUnsavedChanges(): boolean         — dirty-state predicate. Returns
 *     true iff `mode === 'edit' && buffer !== serverYaml`. Story 11.6's
 *     `CanDeactivate` guard consumes the same method.
 */
@Component({
  selector: 'app-namespace-panel',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    ConfirmDialogModule,
    DialogModule,
    InputTextModule,
    NuMonacoEditorModule,
    TooltipModule,
    ValidationReportComponent,
  ],
  // ConfirmationService is provided locally per PrimeNG v19 pattern — scopes
  // the confirm dialog to this component (dialog + future route both get
  // their own scoped instance).
  providers: [ConfirmationService],
  templateUrl: './namespace-panel.component.html',
  styleUrls: ['./namespace-panel.component.scss'],
})
export class NamespacePanelComponent implements OnInit, OnChanges {
  @Input() namespace!: string;
  /**
   * Story 11.5 — existing namespace identifiers, supplied by the host, used
   * by the Clone dialog's pre-flight collision check (AC 4). The panel
   * NEVER re-fetches this list itself (NFR7 — the collision check is a UX
   * courtesy, not a correctness guarantee; see ADR-011 D5
   * Collision-on-race).
   */
  @Input() existingNamespaces: string[] = [];
  @Output() closed = new EventEmitter<void>();
  @Output() saved = new EventEmitter<void>();

  private apiService: ApiService = inject(ApiService);
  private messageService: MessageService = inject(MessageService);
  private confirmationService: ConfirmationService = inject(ConfirmationService);
  private destroyRef: DestroyRef = inject(DestroyRef);

  // Internal state — initial values per AC 2.
  serverYaml: string = '';
  buffer: string = '';
  mode: 'view' | 'edit' = 'view';
  lastValidation: NamespaceValidationReport | null = null;
  loading: boolean = false;

  // Story 11.3 — Save flow state.
  /** True while an `importNamespace` request is in flight (AC 5). */
  saving: boolean = false;

  // Story 11.4 — Validate flow state.
  /**
   * True while a `validatePersistedNamespace` or `validateNamespaceBuffer`
   * request is in flight. Independent from `saving` / `loading` — see
   * Story 11.4 Dev Notes "Why three separate flags".
   */
  validating: boolean = false;

  // UX polish: replaces the intrusive "Validation passed" panel on clean
  // reports. When a Validate click returns `ok: true`, the clicked button
  // briefly flips to PrimeNG's `success` severity (green) and then reverts.
  // The report pane stays hidden in this path — findings-only.
  //
  //   - `validationFlashPersisted` — true while the view-mode Validate button
  //     shows the success-flash state.
  //   - `validationFlashBuffer`    — same, for the edit-mode Validate button.
  //   - `flashTimeoutId` — handle so repeated clicks cancel the prior timer
  //     and the destroy hook cleans up any pending callback.
  validationFlashPersisted: boolean = false;
  validationFlashBuffer: boolean = false;
  private flashTimeoutId: ReturnType<typeof setTimeout> | undefined = undefined;

  /**
   * Post-review UX refinement — set while the Edit button's drift check
   * is in flight (one `exportNamespace` call before flipping to edit
   * mode). Gates the Edit button's `[disabled]` + spinner-icon swap so
   * the operator cannot double-click during the async round trip. See
   * `onEditClick()`.
   */
  refreshingForEdit: boolean = false;

  /**
   * Snapshot of the buffer value at the moment a Validate-buffer request
   * returned a clean report. If the user then modifies the buffer, the
   * flash is cancelled via `onBufferChange` — a green "validated" ack must
   * not outlive the exact YAML it validated.
   */
  private validatedBuffer: string | null = null;

  /**
   * Raw (non-structured) server error body surfaced by a 422 whose payload
   * is not a `NamespaceValidationReport` (AC 7 fallback branch).
   * `null` means no unstructured error pending.
   */
  rawSaveError: string | null = null;
  // Story 11.5 — Clone flow state.
  /** Controls visibility of the Clone sub-dialog (AC 2, AC 3). */
  cloneDialogVisible: boolean = false;
  /**
   * Destination namespace name typed into the Clone dialog (AC 3). Two-way
   * bound via `[(ngModel)]`; reset to `''` on dialog dismiss AND on
   * successful clone (AC 3, AC 8, AC 12).
   */
  cloneDestNs: string = '';
  /**
   * True while a clone-import request is in flight (AC 8 + AC 14). Gates
   * the Confirm button (defence in depth alongside the pre-flight
   * validation) and the outer Clone button so the operator cannot
   * double-submit.
   */
  cloning: boolean = false;
  /**
   * Tracks the most recent non-HTTP / non-422 / non-401 clone error. The
   * field is populated on the default failure branch (AC 9) for surfacing
   * the error via a non-sticky toast; no in-panel action-row Retry button
   * — re-clicking Clone Confirm is the natural retry path.
   */
  lastCloneError: Error | null = null;

  // -------------------------------------------------------------------
  // Story 11.7 — UX-polish state (FR14 gate, FR16 a11y, FR17 Clone modal,
  // FR21 inline error)
  // -------------------------------------------------------------------

  /**
   * Story 11.7 FR16 — visually-hidden polite live-region payload. The
   * template renders this string inside a `role="status" aria-live="polite"
   * aria-atomic="true"` <div>; assistive-tech announces every change.
   * Writes happen at FOUR sites: `flashValidated` (Validation passed),
   * `announceValidationOutcome` (Validation found N issues),
   * `onSaveClick` 2xx branch (Namespace saved),
   * `onCloneConfirmClick` 2xx branch (Cloned to namespace 'X').
   */
  a11yAnnouncement: string = '';

  /**
   * Story 11.7 FR17 (AC 21) — inline error text for the Clone modal's
   * unstructured-422 branch. When non-null, rendered as a `role="alert"`
   * <small> immediately below the destination input. Cleared by
   * `onCloneDestNsChange()` (any keystroke) and by `onCloneDialogHide()`.
   */
  cloneInlineError: string | null = null;

  /**
   * Story 11.7 FR17 (AC 19) — outer Clone button reference. Used by
   * `onCloneDialogHide()` to return focus to this button after the modal
   * closes (Cancel / Escape / X / outside click / Confirm).
   */
  @ViewChild('cloneBtn', { read: ElementRef })
  cloneBtnRef?: ElementRef<HTMLButtonElement>;

  /**
   * Story 11.7 FR17 (AC 16) — destination input reference. Used by
   * `onCloneDialogShow()` to autofocus the input on modal open.
   */
  @ViewChild('cloneDestInput', { read: ElementRef })
  cloneDestInputRef?: ElementRef<HTMLInputElement>;

  /**
   * Destroy guard consumed by the async load + save flows so a late-resolving
   * promise cannot write to state on a destroyed component (AC 13).
   */
  private destroyed = false;
  /**
   * Monotonic counter used to discard stale `exportNamespace` responses
   * when `namespace` changes mid-flight (AC 3 re-load contract).
   */
  private loadSeq = 0;

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.destroyed = true;
      if (this.flashTimeoutId !== undefined) {
        clearTimeout(this.flashTimeoutId);
        this.flashTimeoutId = undefined;
      }
    });
  }

  /**
   * Briefly flash the clicked Validate button as `success` (green) — the
   * UX-light alternative to rendering a "Validation passed" panel for clean
   * reports. Repeated clicks during the flash window cancel the pending
   * timer and restart fresh. The destroyed guard prevents a post-destroy
   * write if the timer fires after teardown.
   */
  private flashValidated(target: 'persisted' | 'buffer'): void {
    if (this.flashTimeoutId !== undefined) {
      clearTimeout(this.flashTimeoutId);
    }
    this.validationFlashPersisted = target === 'persisted';
    this.validationFlashBuffer = target === 'buffer';
    // Story 11.7 AC 11 — pair the visual flash with an aria-live
    // announcement so screen-reader users get the same outcome.
    this.a11yAnnouncement = 'Validation passed';
    this.flashTimeoutId = setTimeout(() => {
      this.flashTimeoutId = undefined;
      if (this.destroyed) {
        return;
      }
      this.validationFlashPersisted = false;
      this.validationFlashBuffer = false;
      this.validatedBuffer = null;
    }, 2500);
  }

  /**
   * Story 11.7 AC 12 — write the failing-validation announcement into the
   * live region. Called from THREE sites that populate `lastValidation`
   * with a non-clean report: `onValidatePersistedClick` failing branch,
   * `onValidateBufferClick` failing branch, and the Save / Clone 422
   * structured branches in `handleSaveError` / `handleCloneError`.
   *
   * Plural form is fixed at "issues" (e.g. "Validation found 1 issues") for
   * announcement-text simplicity — assistive-tech audiences tolerate the
   * unidiomatic phrasing better than two announcement variants would
   * tolerate inconsistent rendering. `?? 0` defends against a malformed
   * report missing one of the arrays (test-double sloppiness or a future
   * schema change).
   */
  private announceValidationOutcome(report: NamespaceValidationReport): void {
    const total =
      (report.global_errors?.length ?? 0) +
      (report.entry_issues?.length ?? 0);
    this.a11yAnnouncement = `Validation found ${total} issues`;
  }

  /**
   * Single write path for `buffer` driven from the Monaco editor's
   * `(ngModelChange)`. Writes-through to `this.buffer` AND invalidates the
   * edit-mode Validate-buffer flash when the value diverges from the
   * snapshot captured at the last successful validate. Modifying the YAML
   * after a clean ack makes the ack stale — the button must revert to
   * secondary immediately rather than keep its green state for the full
   * 2500ms window.
   *
   * Direct assignments to `this.buffer` elsewhere in the component (load,
   * save-success, clone-navigate) bypass this handler — the flash flag is
   * already `false` in those paths, so no invalidation is needed.
   */
  onBufferChange(value: string): void {
    this.buffer = value;
    if (
      this.validationFlashBuffer &&
      this.validatedBuffer !== null &&
      value !== this.validatedBuffer
    ) {
      this.validationFlashBuffer = false;
      this.validatedBuffer = null;
      if (this.flashTimeoutId !== undefined) {
        clearTimeout(this.flashTimeoutId);
        this.flashTimeoutId = undefined;
      }
    }
    // Story 11.7 AC 3 — clear stale save error on any keystroke so the
    // FR14 gate auto-lifts. Unconditional (any keystroke is the operator
    // declaring "I am editing — re-evaluate"). The corresponding
    // `lastValidation` clear remains conditional (only when
    // `value !== validatedBuffer`) to preserve Story 11.4 green-flash
    // semantics.
    if (this.rawSaveError !== null) {
      this.rawSaveError = null;
    }
  }

  ngOnInit(): void {
    void this.loadNamespace(this.namespace);
  }

  ngOnChanges(changes: SimpleChanges): void {
    const change = changes['namespace'];
    if (!change || change.firstChange) {
      // ngOnInit owns the first-mount load — avoid a duplicate fetch.
      return;
    }
    if (change.previousValue === change.currentValue) {
      return;
    }
    void this.loadNamespace(this.namespace);
  }

  /**
   * Monaco editor options.
   *
   * MUST be a stable object reference between mode changes. `nu-monaco-editor`
   * receives `[options]` as a signal input (reference-equality semantics) —
   * returning a fresh object every change-detection tick (the previous getter
   * implementation) fires the input on every CD, causes `updateOptions()` to
   * run repeatedly, and drives a CD / layout feedback loop that freezes the
   * tab. The field is reassigned ONLY when `mode` flips (via `setMode`).
   *
   * `automaticLayout: true` is load-bearing for dialog hosting — the editor
   * otherwise renders at 0×0 when its container mounts late.
   */
  editorOptions: Record<string, unknown> = this.buildEditorOptions();

  private buildEditorOptions(): Record<string, unknown> {
    return {
      theme: 'vs',
      language: 'yaml',
      automaticLayout: true,
      readOnly: this.mode === 'view',
    };
  }

  /**
   * Single write path for `mode` — keeps `editorOptions` in lock-step so the
   * Monaco signal input sees exactly one new reference per real mode flip.
   */
  private setMode(next: 'view' | 'edit'): void {
    if (this.mode === next) {
      return;
    }
    this.mode = next;
    this.editorOptions = this.buildEditorOptions();
  }

  /**
   * Dirty-state predicate — single source of truth for confirm-on-close
   * and Save-enabled checks. True iff the panel is in edit mode AND the
   * buffer diverges from the last server snapshot. Method NAME + SIGNATURE
   * are part of Epic 11's stable surface (Story 11.6's `CanDeactivate`
   * guard consumes the same method).
   */
  hasUnsavedChanges(): boolean {
    return this.mode === 'edit' && this.buffer !== this.serverYaml;
  }

  /**
   * Enter edit mode.
   *
   * Post-review refinement — re-fetches the server namespace (single
   * `exportNamespace` call) before flipping to `edit`. If the server has
   * drifted since the initial load, prompts the operator to reload;
   * accept replaces `serverYaml` + `buffer` with the latest and enters
   * edit mode against the fresh state, reject stays in view mode with
   * the stale state visible (operator can re-click Edit or close).
   *
   * On network error the drift check falls through to edit mode with a
   * warning toast — operators shouldn't be blocked by a hiccup from
   * editing their last-known version; the server still validates on
   * save, so a genuinely-stale edit can only land a known-bad bundle.
   *
   * Monaco's `readOnly` flips via the `editorOptions` field (reassigned
   * in `setMode`). Save / Reset / Cancel replace the Edit / Clone pair
   * in the action row.
   */
  async onEditClick(): Promise<void> {
    if (this.refreshingForEdit) {
      return;
    }
    this.refreshingForEdit = true;
    try {
      const latestYaml = await this.apiService.exportNamespace(this.namespace);
      if (this.destroyed) {
        return;
      }
      if (latestYaml === this.serverYaml) {
        this.setMode('edit');
        return;
      }
      // Drift detected — prompt the operator before silently replacing
      // the snapshot the panel is showing.
      this.confirmationService.confirm({
        header: 'Namespace modified',
        icon: 'pi pi-exclamation-triangle',
        message:
          'The namespace was modified on the server since it was loaded. Reload the latest version to edit?',
        accept: () => {
          if (this.destroyed) {
            return;
          }
          this.serverYaml = latestYaml;
          this.buffer = latestYaml;
          this.lastValidation = null;
          this.rawSaveError = null;
          this.validatedBuffer = null;
          this.setMode('edit');
        },
        // reject intentionally omitted — dismissing keeps the operator
        // in view mode with the stale state visible; re-clicking Edit
        // will re-check.
      });
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      // Non-blocking: warn and proceed to edit against last-known state.
      this.messageService.add({
        severity: 'warn',
        summary: 'Could not verify namespace state',
        detail: 'Editing against the last-known version. Save will re-validate on the server.',
      });
      this.setMode('edit');
    } finally {
      if (!this.destroyed) {
        this.refreshingForEdit = false;
      }
    }
  }

  /**
   * Post-review new feature — revert the buffer to `serverYaml` without
   * exiting edit mode. Mirrors Cancel's dirty-state guard (confirm
   * "Discard unsaved changes?") but keeps `mode === 'edit'` on accept
   * so the operator can keep working. Clean buffer path is guarded at
   * the template level (`[disabled]="buffer === serverYaml"`).
   */
  onResetClick(): void {
    if (this.buffer === this.serverYaml) {
      return;
    }
    this.confirmationService.confirm({
      header: 'Unsaved changes',
      icon: 'pi pi-exclamation-triangle',
      message: 'Discard unsaved changes?',
      accept: () => {
        if (this.destroyed) {
          return;
        }
        this.buffer = this.serverYaml;
        this.lastValidation = null;
        this.rawSaveError = null;
        this.validatedBuffer = null;
        // Stay in edit mode — Reset differs from Cancel here.
      },
    });
  }

  /**
   * Cancel the edit. Clean buffer → flip to view directly. Dirty buffer →
   * PrimeNG confirm dialog; on accept revert buffer + flip to view, on
   * reject keep state unchanged (AC 3 + AC 4).
   */
  onCancelClick(): void {
    if (this.buffer === this.serverYaml) {
      this.setMode('view');
      return;
    }
    this.confirmationService.confirm({
      header: 'Unsaved changes',
      icon: 'pi pi-exclamation-triangle',
      message: 'Discard unsaved changes?',
      accept: () => {
        if (this.destroyed) {
          return;
        }
        this.buffer = this.serverYaml;
        this.setMode('view');
      },
      // `reject` intentionally omitted — dismissing the confirm leaves the
      // panel exactly as it was (AC 3 dismiss branch).
    });
  }

  /**
   * Save handler — posts the current buffer directly to
   * `apiService.importNamespace` (NO pre-save validate, per ADR-011 D4).
   *
   * Success (2xx): `serverYaml` snapshots the just-saved buffer, mode flips
   * to view, toast success, `saved` emits so the host refreshes derived
   * lists.
   *
   * 422: populate `lastValidation` when the body is structurally a
   * `NamespaceValidationReport`, else stash the raw body into
   * `rawSaveError` for a verbatim `<pre>` rendering. Stay in edit mode,
   * buffer preserved, saving resets.
   *
   * 401: silent at the panel level. FetchService has already surfaced a
   * global toast; the app-wide 401 handler (if any) runs. The panel only
   * clears `saving` so a subsequent click is not stuck in a spinner.
   *
   * Other (4xx/5xx/network): sticky error toast. The operator retries by
   * clicking the regular Save button again (buffer is still dirty post-
   * failure, so Save remains enabled) — there is no dedicated Retry button
   * in the action row. `importNamespace` is NEVER auto-retried by the
   * panel (AC 8).
   */
  async onSaveClick(): Promise<void> {
    if (!this.hasUnsavedChanges() || this.saving) {
      return;
    }
    // Snapshot the buffer at click time so typing during the request does
    // NOT race the success branch (AC 2.3 guidance).
    const savedBuffer = this.buffer;

    // Post-review guardrail — refuse to Save when the buffer's top-level
    // `namespace:` field has been edited away from the panel's namespace.
    // The server's import endpoint treats the YAML's namespace as
    // authoritative, so saving here would accidentally create or overwrite
    // a DIFFERENT namespace — the operator almost certainly means to Clone.
    // Parse failure returns `null` → skip the guard and let the server
    // reject the bundle with a 422 (handled in the catch branch below).
    const bufferNamespace = extractYamlNamespace(savedBuffer);
    if (bufferNamespace !== null && bufferNamespace !== this.namespace) {
      this.messageService.add({
        severity: 'error',
        summary: 'Cannot change namespace on Save',
        detail: `The buffer's namespace is "${bufferNamespace}" but the panel is editing "${this.namespace}". Use Clone to save under a new namespace name.`,
        sticky: true,
      });
      return;
    }

    this.saving = true;
    this.rawSaveError = null;
    // Clear any stale validation from a prior save attempt so the UI does
    // not display outdated issues while the request is in flight.
    this.lastValidation = null;
    try {
      await this.apiService.importNamespace(savedBuffer);
      if (this.destroyed) {
        return;
      }
      this.serverYaml = savedBuffer;
      this.setMode('view');
      // Story 11.7 AC 13 — pair the success toast with an aria-live
      // announcement so screen-reader users get the same outcome.
      this.a11yAnnouncement = 'Namespace saved';
      this.saved.emit();
      this.messageService.add({
        severity: 'success',
        summary: 'Namespace saved successfully',
      });
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.handleSaveError(err);
    } finally {
      if (!this.destroyed) {
        this.saving = false;
      }
    }
  }

  /**
   * Validate the persisted namespace (view mode). Calls
   * `apiService.validatePersistedNamespace(this.namespace)` exactly once,
   * populates `this.lastValidation` on success, never mutates
   * `serverYaml` / `mode` / `buffer` / `rawSaveError` (Validate is never
   * a Save gate — ADR-011 D3 / D4).
   */
  async onValidatePersistedClick(): Promise<void> {
    if (this.validating) {
      return;
    }
    this.validating = true;
    try {
      const report = await this.apiService.validatePersistedNamespace(
        this.namespace,
      );
      if (this.destroyed) {
        return;
      }
      // Defensive: if the API layer resolved to undefined (e.g. network
      // fallback in FetchService) or an unexpected shape, surface a clear
      // toast instead of throwing a TypeError on `report.ok`.
      if (!this.isValidationReport(report)) {
        throw new Error('Server returned no validation report.');
      }
      // Clean report → keep the pane hidden and flash the button green for a
      // brief ack. Non-clean report → populate `lastValidation` so the
      // ValidationReportComponent renders the findings.
      if (report.ok) {
        this.lastValidation = null;
        this.flashValidated('persisted');
      } else {
        this.lastValidation = report;
        // Story 11.7 AC 12 — announce the failing-validation outcome.
        this.announceValidationOutcome(report);
      }
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.handleValidateError(err);
    } finally {
      if (!this.destroyed) {
        this.validating = false;
      }
    }
  }

  /**
   * Validate the current edit buffer without persisting. Snapshots
   * `this.buffer` at click time (`bufferAtClick`) so Monaco's live
   * `[(ngModel)]` mutation during the in-flight request cannot race the
   * request args (mirrors Story 11.3's `savedBuffer` pattern).
   *
   * Validate never mutates `mode`, `serverYaml`, or `buffer` — the
   * handler touches only `validating` and `lastValidation`.
   */
  async onValidateBufferClick(): Promise<void> {
    if (this.validating) {
      return;
    }
    const bufferAtClick = this.buffer;
    this.validating = true;
    try {
      const report =
        await this.apiService.validateNamespaceBuffer(bufferAtClick);
      if (this.destroyed) {
        return;
      }
      if (!this.isValidationReport(report)) {
        throw new Error('Server returned no validation report.');
      }
      // Clean buffer → flash the edit-mode Validate button. Non-clean →
      // render findings via `lastValidation` as before.
      if (report.ok) {
        this.lastValidation = null;
        this.validatedBuffer = bufferAtClick;
        this.flashValidated('buffer');
      } else {
        this.lastValidation = report;
        // Story 11.7 AC 12 — announce the failing-validation outcome.
        this.announceValidationOutcome(report);
      }
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.handleValidateError(err);
    } finally {
      if (!this.destroyed) {
        this.validating = false;
      }
    }
  }

  /**
   * Parent-owned clear for the `ValidationReportComponent` output. Nulls
   * both `lastValidation` and `rawSaveError` so the sub-component emits
   * no DOM on the next CD tick (branch (a) — AC 8).
   */
  onClearValidationClick(): void {
    this.lastValidation = null;
    this.rawSaveError = null;
  }

  /**
   * Handle a rejected Validate call.
   *
   * - **401** — silent (FetchService / the app-wide handler owns the
   *   surface).
   * - **422 with a structured `NamespaceValidationReport` body** — render
   *   the findings in the panel (same affordance as a 200 with ok:false).
   *   Pydantic-level validation errors from the catalog service commonly
   *   land on 422 even though the operational outcome is the same as a
   *   200 ok:false — the user wants to see the issues, not a toast.
   * - **Other statuses / errors** — non-sticky toast. Re-clicking Validate
   *   is the natural retry, so no Retry action is attached (AC 11).
   *
   * Validate never writes Save's fallback fields (`rawSaveError`) — those
   * belong to the Save flow.
   */
  private handleValidateError(err: unknown): void {
    const status = (err as { status?: number })?.status;
    if (status === 401) {
      // Silent — FetchService already fired a global toast.
      return;
    }
    if (status === 422) {
      const body = (err as HttpError).body;
      if (this.isValidationReport(body)) {
        // Promote the 422 body into the findings pane — matches the
        // ok:false-on-200 rendering path.
        this.lastValidation = body;
        // Story 11.7 AC 12 — announce the failing-validation outcome.
        this.announceValidationOutcome(body);
        return;
      }
      // Fall through to a generic toast when the 422 body isn't a report
      // (e.g. a YAML-parse error surfaced as a plain string / FastAPI
      // detail envelope — not structured enough to render as findings).
    }
    // Non-sticky toast: Validate is idempotent, re-clicking is the retry.
    this.messageService.add({
      severity: 'error',
      summary: 'Validation failed',
      detail: (err as Error)?.message ?? String(err),
    });
  }

  /**
   * Narrows the caught Save error into one of four branches: 401 silent,
   * 422 structured, 422 fallback, other (toast + retry).
   */
  private handleSaveError(err: unknown): void {
    const status = (err as { status?: number })?.status;
    if (status === 401) {
      // Silent at the panel — FetchService already fired a global toast and
      // the app-wide 401 handler (if any) runs. The panel does not mutate
      // `mode` / `buffer` / `lastValidation` on 401 (AC 9).
      return;
    }
    if (status === 422) {
      const body = (err as HttpError).body;
      if (this.isValidationReport(body)) {
        this.lastValidation = body;
        this.rawSaveError = null;
        // Story 11.7 AC 12 — announce the failing-validation outcome.
        this.announceValidationOutcome(body);
      } else {
        this.rawSaveError =
          typeof body === 'string' ? body : JSON.stringify(body, null, 2);
        this.lastValidation = null;
      }
      return;
    }
    // Other (4xx / 5xx / network) — surface a sticky error toast. The
    // regular Save button stays enabled (buffer is still dirty), so clicking
    // it again retries; there is no dedicated action-row Retry button.
    const saveError = err instanceof Error ? err : new Error(String(err));
    this.messageService.add({
      severity: 'error',
      summary: 'Save failed',
      detail: saveError.message,
      sticky: true,
    });
  }

  // -------------------------------------------------------------------
  // Story 11.5 — Clone flow (AC 1–AC 12)
  // -------------------------------------------------------------------

  /**
   * Open the Clone sub-dialog (AC 2). Pure UI flip — does NOT fire a
   * network request, does NOT mutate `buffer` / `serverYaml` / `mode` /
   * `lastValidation` / `rawSaveError`. No-op when `cloning === true`
   * (defence in depth — the outer Clone button is `[disabled]` in that
   * state, so this path is unreachable in practice).
   */
  onCloneClick(): void {
    if (this.cloning) {
      return;
    }
    this.cloneDialogVisible = true;
  }

  /**
   * Cancel the Clone sub-dialog (AC 3). Resets `cloneDestNs`; does NOT
   * mutate `cloning` — an in-flight clone request keeps settling via its
   * own try/catch/finally (Story 11.4's destroy-guard idiom).
   */
  onCloneCancelClick(): void {
    this.cloneDialogVisible = false;
    this.cloneDestNs = '';
  }

  /**
   * `(visibleChange)` handler for the Clone sub-dialog. Mirrors Cancel's
   * reset: when the dialog dismisses (visible = false, e.g. ESC or the
   * close X), we zero `cloneDestNs` so the next open starts clean (AC 3).
   * `cloning` is intentionally unchanged — an in-flight request keeps
   * settling regardless of dialog visibility.
   */
  onCloneDialogVisibleChange(visible: boolean): void {
    this.cloneDialogVisible = visible;
    if (!visible) {
      this.cloneDestNs = '';
    }
  }

  /**
   * Pre-flight predicate for the Clone Confirm button (AC 4). True iff any
   * of: destNs is empty-trimmed, destNs equals the source namespace, destNs
   * already appears in `existingNamespaces` (case-sensitive), or a clone is
   * already in flight.
   */
  get cloneConfirmDisabled(): boolean {
    const trimmed = this.cloneDestNs.trim();
    if (trimmed === '') {
      return true;
    }
    if (trimmed === this.namespace) {
      return true;
    }
    if (this.existingNamespaces.includes(trimmed)) {
      return true;
    }
    if (this.cloning) {
      return true;
    }
    // Story 11.7 AC 7 — the Clone modal Confirm button inherits the
    // FR14 gate so an operator cannot bypass the outer Clone button's
    // disabled state by opening the modal first.
    return this.isCloneGated;
  }

  /**
   * Inline validation message for the Clone dialog (AC 4). Returns `null`
   * while the destNs is an unused, non-colliding value — so the template
   * can gate the error block with `*ngIf`.
   */
  get cloneValidationError(): string | null {
    const trimmed = this.cloneDestNs.trim();
    if (trimmed === '') {
      return 'Destination namespace required';
    }
    if (trimmed === this.namespace) {
      return 'Destination must differ from source namespace';
    }
    if (this.existingNamespaces.includes(trimmed)) {
      return `Namespace '${trimmed}' already exists`;
    }
    return null;
  }

  // -------------------------------------------------------------------
  // Story 11.7 — FR14 Save/Clone gate getters + tooltip helpers
  // -------------------------------------------------------------------

  /**
   * Story 11.7 AC 1, 2, 6 — Save gate predicate. True iff the panel has
   * a known-bad signal: either a structured-422 / 200-with-issues report
   * (`lastValidation.ok === false`) or an unstructured-422 raw-error
   * fallback (`rawSaveError !== null`).
   *
   * Fresh untouched state (`lastValidation === null && rawSaveError ===
   * null`) returns `false` — Save / Clone are enabled by default.
   * Option B (gate-on-known-bad) is the explicit UX choice; see story
   * Dev Notes "Why option B".
   *
   * Two getters (`isSaveGated` + `isCloneGated`) instead of one shared
   * getter so call-site tooltips can be specific (Save's tooltip says
   * "saving"; Clone's says "cloning") without leaking unrelated logic.
   */
  get isSaveGated(): boolean {
    return (
      (this.lastValidation !== null && !this.lastValidation.ok) ||
      this.rawSaveError !== null
    );
  }

  /** Story 11.7 AC 1, 2, 6 — Clone gate predicate (same body as Save). */
  get isCloneGated(): boolean {
    return (
      (this.lastValidation !== null && !this.lastValidation.ok) ||
      this.rawSaveError !== null
    );
  }

  /**
   * Story 11.7 AC 1 — Save tooltip text. Returns the explanatory string
   * ONLY when the gate is the active reason for disabling; returns
   * `undefined` otherwise so PrimeNG's `pTooltip` input (whose type is
   * `string | TemplateRef | undefined`) accepts it under strictTemplates.
   * PrimeNG suppresses the tooltip on `undefined` / empty string, so
   * unrelated disable reasons (clean buffer, in-flight save) get no
   * tooltip — avoiding contradictory messaging.
   */
  get saveTooltip(): string | undefined {
    if (
      this.isSaveGated &&
      !this.saving &&
      this.buffer !== this.serverYaml
    ) {
      return 'Fix validation issues before saving.';
    }
    return undefined;
  }

  /**
   * Story 11.7 AC 1 — Clone tooltip text. Same idiom as `saveTooltip`:
   * returns the gate-explanatory string only when the gate is the active
   * reason for disabling; `undefined` otherwise (pTooltip-type-compatible).
   */
  get cloneTooltip(): string | undefined {
    if (this.isCloneGated && !this.cloning && !this.saving) {
      return 'Fix validation issues before cloning.';
    }
    return undefined;
  }

  // -------------------------------------------------------------------
  // Story 11.7 — Clone modal a11y handlers (FR17 + FR21)
  // -------------------------------------------------------------------

  /**
   * Story 11.7 AC 16 — `(onShow)` handler for the Clone modal. Defers
   * the focus call to the next microtask so PrimeNG has time to mount
   * the input element in its overlay. Idempotent + null-safe.
   */
  onCloneDialogShow(): void {
    setTimeout(() => {
      this.cloneDestInputRef?.nativeElement.focus();
    }, 0);
  }

  /**
   * Story 11.7 AC 18, 19, 21 — `(onHide)` handler for the Clone modal.
   * Runs the same cleanup as `onCloneCancelClick` PLUS clears
   * `cloneInlineError` and returns focus to the outer Clone button.
   * Defers the focus call by one microtask so any post-hide CD settles
   * (and PrimeNG's exit animation completes) before the focus moves.
   */
  onCloneDialogHide(): void {
    this.cloneDestNs = '';
    this.cloneInlineError = null;
    setTimeout(() => {
      this.cloneBtnRef?.nativeElement.focus();
    }, 0);
  }

  /**
   * Story 11.7 AC 21 — destination-input change handler. Clears the
   * unstructured-422 inline error so the operator can edit and retry
   * without first dismissing a stale alert. The existing
   * `cloneValidationError` getter (Story 11.5) is computed-on-getter and
   * does not need a separate clear-on-input handler.
   */
  onCloneDestNsChange(): void {
    if (this.cloneInlineError !== null) {
      this.cloneInlineError = null;
    }
  }

  /**
   * Clone handler — "Save As" semantics (AC 5). Captures the CURRENT buffer
   * (NOT `serverYaml`) so an operator editing mid-view and clicking Clone
   * sees their dirty edits in the rewritten bundle. Rewrites the root
   * `namespace` field via {@link rewriteNamespaceInYaml}, then reuses the
   * Save path (`apiService.importNamespace`) — no new endpoint.
   *
   * Branches (AC 8–AC 12):
   *   - `CloneYamlError` before the network call → toast + dialog stays
   *     open, `cloning` resets, `importNamespace` NEVER called.
   *   - 2xx → dismiss dialog, emit `(saved)`, switch to destNs, re-load via
   *     `loadNamespace(destNs)` (Story 11.2 contract — lands in view mode).
   *   - 401 silent (FetchService surfaces the global toast).
   *   - 422 structured → populate `lastValidation`; dialog stays open.
   *   - 422 unstructured → stash raw body in `rawSaveError`; dialog stays
   *     open.
   *   - Other (4xx / 5xx / network) → sticky error toast + `lastCloneError`;
   *     dialog stays open.
   *
   * In every failure branch: `namespace` / `serverYaml` / `buffer` / `mode`
   * are unchanged (AC 10). Destroy-guard mirrors Story 11.3's `onSaveClick`.
   */
  async onCloneConfirmClick(): Promise<void> {
    if (this.cloneConfirmDisabled) {
      return;
    }
    const destNs = this.cloneDestNs.trim();
    const sourceYaml = this.buffer;
    this.cloning = true;
    this.lastCloneError = null;
    try {
      const rewrittenYaml = rewriteNamespaceInYaml(sourceYaml, destNs);
      await this.apiService.importNamespace(rewrittenYaml);
      if (this.destroyed) {
        return;
      }
      // 2xx happy path: dismiss dialog, emit saved, switch namespace +
      // re-load. Programmatic self-writes to an @Input() field do NOT
      // trigger ngOnChanges, so we invoke loadNamespace explicitly — it
      // re-exports the bundle and flips mode to view (Story 11.2).
      this.cloneDialogVisible = false;
      this.cloneDestNs = '';
      this.cloneInlineError = null;
      this.saved.emit();
      this.namespace = destNs;
      // Fire-and-forget: loadNamespace owns its own loading flag and
      // destroy guard. AC 14 budgets this second fetch explicitly.
      void this.loadNamespace(destNs);
      // Story 11.7 AC 14 — pair the success toast with an aria-live
      // announcement using the captured destNs (avoids racing with
      // any later mutation of `this.cloneDestNs`).
      this.a11yAnnouncement = `Cloned to namespace '${destNs}'`;
      this.messageService.add({
        severity: 'success',
        summary: `Cloned to namespace '${destNs}'`,
      });
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.handleCloneError(err);
    } finally {
      if (!this.destroyed) {
        this.cloning = false;
      }
    }
  }

  /**
   * Narrow the caught Clone error (AC 9 + AC 11). Branch order MUST match
   * AC 11 exactly: `CloneYamlError` FIRST (client-side rewrite failure,
   * `importNamespace` never fired), then 401 silent, then 422 structured /
   * 422 unstructured, then default sticky toast.
   */
  private handleCloneError(err: unknown): void {
    if (err instanceof CloneYamlError) {
      // Client-side rewrite failure — `lastCloneError` is NOT set (that
      // field tracks server errors; the operator can fix the buffer and
      // retry). Non-sticky toast; dialog stays open.
      this.messageService.add({
        severity: 'error',
        summary: 'Clone failed',
        detail: err.message,
      });
      return;
    }
    const status = (err as { status?: number })?.status;
    if (status === 401) {
      // Silent — FetchService already fired a global toast.
      return;
    }
    if (status === 422) {
      const body = (err as HttpError).body;
      if (this.isValidationReport(body)) {
        // Story 11.7 AC 20 — structured 422 closes the Clone modal and
        // surfaces the findings in the parent panel's findings pane (the
        // single canonical findings surface). The outer Clone button is
        // then gated by FR14.
        this.lastValidation = body;
        this.rawSaveError = null;
        this.cloneDialogVisible = false;
        this.cloneDestNs = '';
        this.cloneInlineError = null;
        // AC 12 — announce the failing-validation outcome.
        this.announceValidationOutcome(body);
      } else {
        // Story 11.7 AC 21 — unstructured 422 keeps the modal open with
        // an inline `role="alert"` error. We deliberately do NOT touch
        // `lastValidation` or `rawSaveError` (the parent's FR14 gate is
        // reserved for SAVE failures). The inline alert handles its own
        // assistive-tech announcement, so no live-region write here
        // (avoids duplicate announcements).
        this.cloneInlineError =
          typeof body === 'string' ? body : JSON.stringify(body, null, 2);
      }
      return;
    }
    // Default: 4xx / 5xx / network. Sticky toast + `lastCloneError` so a
    // future story could wire an in-panel Retry-Clone button. This story
    // deliberately does NOT add a Retry button — re-clicking Clone Confirm
    // is the natural retry path, and the action row is already dense.
    this.lastCloneError = err instanceof Error ? err : new Error(String(err));
    this.messageService.add({
      severity: 'error',
      summary: 'Clone failed',
      detail: this.lastCloneError.message,
      sticky: true,
    });
  }

  /** Structural check: does `body` satisfy `NamespaceValidationReport`? */
  private isValidationReport(
    body: unknown,
  ): body is NamespaceValidationReport {
    if (body === null || typeof body !== 'object') {
      return false;
    }
    const shape = body as Record<string, unknown>;
    return (
      'ok' in shape &&
      typeof shape['ok'] === 'boolean' &&
      Array.isArray(shape['global_errors']) &&
      Array.isArray(shape['entry_issues'])
    );
  }

  /**
   * Loads the given namespace YAML via `apiService.exportNamespace`.
   * Uses a monotonic `loadSeq` so a slow response for an old namespace
   * cannot clobber a newer request's state, and a `destroyed` guard so
   * no writes land on a destroyed component.
   */
  private async loadNamespace(namespace: string): Promise<void> {
    const seq = ++this.loadSeq;
    this.loading = true;
    // Reset buffer/serverYaml at the start of each reload so the empty
    // state renders (AC 3 step 1 + AC 5) while the fetch is in flight.
    this.serverYaml = '';
    this.buffer = '';
    try {
      const yaml = await this.apiService.exportNamespace(namespace);
      if (this.destroyed || seq !== this.loadSeq) {
        return;
      }
      this.serverYaml = yaml;
      this.buffer = yaml;
      this.setMode('view');
      this.lastValidation = null;
      this.rawSaveError = null;
    } catch (err) {
      if (this.destroyed || seq !== this.loadSeq) {
        return;
      }
      this.messageService.add({
        severity: 'error',
        summary: `Unable to load namespace '${namespace}'`,
        detail: (err as Error)?.message ?? String(err),
      });
      // Leave serverYaml = '' and buffer = '' — empty-state branch handles it.
    } finally {
      if (this.destroyed || seq !== this.loadSeq) {
        return;
      }
      this.loading = false;
    }
  }
}
