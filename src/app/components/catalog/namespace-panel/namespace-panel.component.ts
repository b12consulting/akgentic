import { CommonModule } from '@angular/common';
import {
  AfterViewInit,
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

import {
  NuMonacoEditorEvent,
  NuMonacoEditorModule,
} from '@ng-util/monaco-editor';
import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { ToggleSwitchModule } from 'primeng/toggleswitch';
import { TooltipModule } from 'primeng/tooltip';

import { NamespaceValidationReport } from '../../../protocol/catalog.interface';
import { ApiService } from '../../../core/http/api.service';
import { AuthService } from '../../../core/auth/auth.service';
import { HttpError } from '../../../core/http/fetch.service';
import {
  CloneYamlError,
  extractYamlName,
  extractYamlNamespace,
  extractYamlPublic,
  extractYamlShareable,
  extractYamlUserId,
  rewriteNamespaceInYaml,
  suggestDestName,
  suggestDestNamespace,
} from '../yaml-clone.helper';
import { ValidationReportComponent } from './validation-report/validation-report.component';

/**
 * Typed shape for the panel's custom confirmation modal (ADR-018). One
 * interface, every call site — never an inline `Record`/object literal
 * scattered across handlers. `variant` selects the action-row button layout:
 *   - `'reset'` / `'delete'` / `'discard'` → `[Cancel] [Proceed]` (Proceed is
 *     destructive — dark-red — for `'delete'` only). `'discard'` is the
 *     dirty-close / dirty-nav variant the host config dialog (Home) and the
 *     route `CanDeactivate` guard reuse via the public `confirmDiscard()`
 *     method.
 *   - `'drift'` → `[Reload] [Overwrite]` (two explicit named buttons, not a
 *     binary accept/reject).
 */
interface ConfirmRequest {
  header: string;
  message: string;
  variant: 'reset' | 'drift' | 'delete' | 'discard';
}

/**
 * NamespacePanelComponent — host-agnostic editor for a catalog namespace YAML.
 *
 * The panel has no view/edit mode axis: it is driven by a single **dirty**
 * signal (`buffer !== serverYaml`) and is **always editable** — Monaco mounts
 * permanently writable, there is no Edit-click barrier, and the entire action
 * row is driven by one boolean (ADR-017):
 *
 *   - Monaco mounts writable (`readOnly: false`); `editorOptions` is a single
 *     stable object built once and never reassigned.
 *   - `hasUnsavedChanges()` is `buffer !== serverYaml`; the route
 *     `CanDeactivate` guard and the home dirty-close guard consume it.
 *   - Action row: Validate (always) · Save (dirty) · Reset (dirty) ·
 *     Clone (clean) · Delete (always).
 *   - Save flow posts directly to `apiService.importNamespace` — no pre-save
 *     `validate` round-trip — preceded by a one-shot drift check (re-export;
 *     on divergence prompt reload-and-rebase vs overwrite).
 *   - 422 → populate `lastValidation` (structured) or `rawSaveError`
 *     (fallback); buffer preserved.
 *   - 5xx / 4xx (other than 401/422) → sticky error toast; buffer preserved.
 *     Re-clicking Save retries.
 *   - 401 → no panel-specific behaviour; the FetchService global toast
 *     surfaces the failure, the app-wide handler (if any) runs. Panel only
 *     clears `saving` in `finally`.
 *   - A panel-owned custom confirmation modal — same `<p-dialog>` shell as the
 *     Clone modal — guards Reset-with-dirty-buffer, the Save drift
 *     reload-vs-overwrite choice, and Delete. The primary button holds focus on
 *     open so Enter activates it and Esc cancels.
 *
 * The component is presentation-only: no `Router`, `ActivatedRoute`,
 * `MAT_DIALOG_DATA`, etc. The dialog wrapper (HomeComponent) and the
 * deep-link route mount it identically.
 *
 * Public surface contract:
 *   - @Input() namespace: string          — required, drives the load flow.
 *   - @Output() closed: EventEmitter<void> — asks the host to dismiss.
 *   - @Output() saved: EventEmitter<void>  — emitted once per successful
 *     import; hosts re-fetch derived lists (e.g. HomeComponent's dropdown).
 *   - hasUnsavedChanges(): boolean         — dirty-state predicate. True iff
 *     `buffer !== serverYaml`. The route `CanDeactivate` guard and the home
 *     dirty-close guard consume the same method.
 */
@Component({
  selector: 'app-namespace-panel',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    NuMonacoEditorModule,
    ToggleSwitchModule,
    TooltipModule,
    ValidationReportComponent,
  ],
  templateUrl: './namespace-panel.component.html',
  styleUrls: ['./namespace-panel.component.scss'],
})
export class NamespacePanelComponent
  implements OnInit, OnChanges, AfterViewInit
{
  @Input() namespace!: string;
  /**
   * Existing namespace identifiers, supplied by the host, used by the Clone
   * dialog's pre-flight collision check. The panel NEVER re-fetches this list
   * itself — the collision check is a UX courtesy, not a correctness
   * guarantee (the server is authoritative on collision-on-race).
   */
  @Input() existingNamespaces: string[] = [];
  /**
   * Admin "show all" context, propagated from the home toggle
   * (`showAllNamespaces`). When `true`, the panel's entry-read
   * (`exportNamespace`) carries `?all=true` so an admin can open a
   * foreign-owned namespace surfaced by the "show all" list. `all=true` is
   * honoured server-side only for admins (the `/admin/catalog/*` mount
   * unscopes admin GETs); for a non-admin (or when the host toggle is off) it
   * is the normal owner-scoped read. Default `false` keeps every caller on the
   * owner-scoped path.
   */
  @Input() showAll: boolean = false;
  @Output() closed = new EventEmitter<void>();
  @Output() saved = new EventEmitter<void>();

  private apiService: ApiService = inject(ApiService);
  private messageService: MessageService = inject(MessageService);
  private authService: AuthService = inject(AuthService);
  private destroyRef: DestroyRef = inject(DestroyRef);
  private elementRef: ElementRef<HTMLElement> = inject(ElementRef);

  // Internal state. The panel is always editable — there is no `mode` axis.
  // The single signal that drives the action row is `buffer !== serverYaml`.
  serverYaml: string = '';
  buffer: string = '';
  lastValidation: NamespaceValidationReport | null = null;
  loading: boolean = false;

  // Save flow state.
  /** True while an `importNamespace` request is in flight. */
  saving: boolean = false;

  // Validate flow state.
  /**
   * True while a `validateNamespaceBuffer` request is in flight. Independent
   * from `saving` / `loading` so the three flows can be in flight separately.
   */
  validating: boolean = false;

  // UX polish: replaces the intrusive "Validation passed" panel on clean
  // reports. When a Validate click returns `ok: true`, the Validate button
  // briefly flips to PrimeNG's `success` severity (green) and then reverts.
  // The report pane stays hidden in this path — findings-only.
  //
  //   - `validationFlashBuffer` — true while the Validate button shows the
  //     success-flash state. There is one Validate button, over the buffer.
  //   - `flashTimeoutId` — handle so repeated clicks cancel the prior timer
  //     and the destroy hook cleans up any pending callback.
  validationFlashBuffer: boolean = false;
  private flashTimeoutId: ReturnType<typeof setTimeout> | undefined = undefined;

  /**
   * Snapshot of the buffer value at the moment a Validate-buffer request
   * returned a clean report. If the user then modifies the buffer, the
   * flash is cancelled via `onBufferChange` — a green "validated" ack must
   * not outlive the exact YAML it validated.
   */
  private validatedBuffer: string | null = null;

  /**
   * Raw (non-structured) server error body surfaced by a 422 whose payload
   * is not a `NamespaceValidationReport` (fallback branch).
   * `null` means no unstructured error pending.
   */
  rawSaveError: string | null = null;
  // Clone flow state.
  /** Controls visibility of the Clone sub-dialog. */
  cloneDialogVisible: boolean = false;
  /**
   * Destination namespace name typed into the Clone dialog. Two-way bound via
   * `[(ngModel)]`; reset to `''` on dialog dismiss AND on successful clone.
   */
  cloneDestNs: string = '';
  /**
   * Destination display name typed into the Clone dialog — rewritten into
   * the bundle's root `name:` field (the meta header surfaced in the
   * home-page dropdown). Pre-filled with a `<source>_copy` suggestion when
   * the modal opens; reset to `''` on dialog dismiss AND on successful
   * clone, mirroring `cloneDestNs`.
   */
  cloneDestName: string = '';
  /**
   * Clone visibility/sharing toggles, two-way bound via `[(ngModel)]` to the
   * modal's `p-toggleswitch` controls. The two flags are ORTHOGONAL:
   *   - `cloneShareable` → root `shareable`: other namespaces may reference
   *     entries in this one cross-namespace (referenceability).
   *   - `clonePublic`    → root `public`: non-owner users may list, read, and
   *     clone this namespace (listability/cloneability).
   *
   * Both pre-fill from the source buffer on `onCloneClick` and follow the
   * SAME reset-parity contract as `cloneDestName`: wherever
   * `cloneDestName = ''` resets on a dismiss/success path, both toggles reset
   * to `false` alongside it (`onCloneCancelClick`,
   * `onCloneDialogVisibleChange(false)`, `onCloneDialogHide`, and the 2xx
   * success branch of `onCloneConfirmClick`).
   */
  cloneShareable: boolean = false;
  clonePublic: boolean = false;
  /**
   * True while a clone-import request is in flight. Gates the Confirm button
   * (defence in depth alongside the pre-flight validation) and the outer Clone
   * button so the operator cannot double-submit.
   */
  cloning: boolean = false;
  /**
   * Tracks the most recent non-HTTP / non-422 / non-401 clone error. The
   * field is populated on the default failure branch for surfacing the error
   * via a non-sticky toast; no in-panel action-row Retry button — re-clicking
   * Clone Confirm is the natural retry path.
   */
  lastCloneError: Error | null = null;

  // -------------------------------------------------------------------
  // Delete-namespace flow (frontend leg).
  // -------------------------------------------------------------------

  /**
   * True while a `deleteNamespace` request is in flight. Mirrors the `cloning`
   * gate: disables the Delete button (`[disabled]="deleting"`) and makes both
   * `onDeleteClick()` and `onDeleteConfirm()` no-ops, guarding against
   * double-submit. Cleared in a `finally` block guarded by the `destroyed`
   * idiom.
   */
  deleting: boolean = false;

  // -------------------------------------------------------------------
  // Custom confirmation modal (ADR-018). One shared shell drives the three
  // confirm flows from component state (a visibility flag + a typed
  // `ConfirmRequest`) rather than an imperative service call.
  // -------------------------------------------------------------------

  /** Controls visibility of the custom confirmation modal. */
  confirmDialogVisible: boolean = false;

  /**
   * The active confirm request describing the modal's header/message/variant.
   * `null` when no confirm is open; nulled on close so a stale request cannot
   * leak into the next open.
   */
  confirmRequest: ConfirmRequest | null = null;

  /**
   * Per-flow accept callback captured when the confirm opens. The Proceed
   * (reset/delete) and Overwrite (drift) buttons invoke this; a private closure
   * so each flow runs its own effect. `null` while no confirm is open.
   */
  private confirmAccept: (() => void) | null = null;

  /**
   * The drift variant's "Reload" (safe / focused-default) callback. Only set
   * for the `'drift'` variant; Reset/Delete leave it `null` (their safe branch
   * is a bare cancel). Invoked by the Reload button.
   */
  private confirmReload: (() => void) | null = null;

  /**
   * Pending "resolve the drift `Promise<boolean>` to the SAFE branch (false)"
   * thunk for an Esc / Cancel / X dismissal of the drift modal. Set by
   * `checkSaveDrift` while the drift modal is open; invoked by
   * `onConfirmDialogHide` so a dismissal that bypasses a button still settles
   * the awaited promise (and does so as the non-overwrite branch). `null` for
   * the reset/delete variants (their dismissal is a bare cancel with no
   * pending promise).
   */
  private confirmDriftResolve: (() => void) | null = null;

  /**
   * Pending "resolve the dirty-discard `Promise<boolean>` to the SAFE branch
   * (false — keep edits)" thunk for an Esc / Cancel / X dismissal of the
   * `'discard'` modal. Set by `confirmDiscard()` while the discard modal is
   * open; invoked by `onConfirmDialogHide` so a dismissal that bypasses a
   * button still settles the awaited promise as "do not discard". `null` for
   * the reset/delete/drift variants.
   */
  private confirmDiscardResolve: (() => void) | null = null;

  /**
   * Primary-button reference for the confirmation modal. On open
   * `onConfirmDialogShow()` autofocuses the PRIMARY/proceed button so **Enter
   * activates the main action**: the Proceed button for the
   * reset/delete/discard variants, the Reload (recommended primary) button for
   * the drift variant. Esc still cancels/dismisses. Mirrors the Clone modal's
   * `cloneDestInputRef` focus plumbing.
   */
  @ViewChild('confirmPrimaryBtn', { read: ElementRef })
  confirmPrimaryBtnRef?: ElementRef<HTMLButtonElement>;

  // -------------------------------------------------------------------
  // UX-polish state (validation gate, a11y live region, Clone modal,
  // inline error)
  // -------------------------------------------------------------------

  /**
   * Visually-hidden polite live-region payload. The template renders this
   * string inside a `role="status" aria-live="polite" aria-atomic="true"`
   * <div>; assistive-tech announces every change. Writes happen at FOUR sites:
   * `flashValidated` (Validation passed), `announceValidationOutcome`
   * (Validation found N issues), `onSaveClick` 2xx branch (Namespace saved),
   * `onCloneConfirmClick` 2xx branch (Cloned to namespace 'X').
   */
  a11yAnnouncement: string = '';

  /**
   * Inline error text for the Clone modal's unstructured-422 branch. When
   * non-null, rendered as a `role="alert"` <small> immediately below the
   * destination input. Cleared by `onCloneDestNsChange()` (any keystroke) and
   * by `onCloneDialogHide()`.
   */
  cloneInlineError: string | null = null;

  /**
   * Outer Clone button reference. Used by `onCloneDialogHide()` to return focus
   * to this button after the modal closes (Cancel / Escape / X / outside
   * click / Confirm).
   */
  @ViewChild('cloneBtn', { read: ElementRef })
  cloneBtnRef?: ElementRef<HTMLButtonElement>;

  /**
   * Destination input reference. Used by `onCloneDialogShow()` to autofocus the
   * input on modal open.
   */
  @ViewChild('cloneDestInput', { read: ElementRef })
  cloneDestInputRef?: ElementRef<HTMLInputElement>;

  /**
   * Destroy guard consumed by the async load + save flows so a late-resolving
   * promise cannot write to state on a destroyed component.
   */
  private destroyed = false;
  /**
   * Monotonic counter used to discard stale `exportNamespace` responses
   * when `namespace` changes mid-flight.
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
   * Briefly flash the Validate button as `success` (green) — the UX-light
   * alternative to rendering a "Validation passed" panel for clean reports.
   * Repeated clicks during the flash window cancel the pending timer and
   * restart fresh. The destroyed guard prevents a post-destroy write if the
   * timer fires after teardown.
   */
  private flashValidated(): void {
    if (this.flashTimeoutId !== undefined) {
      clearTimeout(this.flashTimeoutId);
    }
    this.validationFlashBuffer = true;
    // Pair the visual flash with an aria-live announcement so screen-reader
    // users get the same outcome.
    this.a11yAnnouncement = 'Validation passed';
    this.flashTimeoutId = setTimeout(() => {
      this.flashTimeoutId = undefined;
      if (this.destroyed) {
        return;
      }
      this.validationFlashBuffer = false;
      this.validatedBuffer = null;
    }, 2500);
  }

  /**
   * Write the failing-validation announcement into the live region. Called
   * from the sites that populate `lastValidation` with a non-clean report: the
   * `onValidateBufferClick` failing branch and the Save / Clone / Delete 422
   * structured branches in `handleSaveError` / `handleCloneError` /
   * `handleDeleteError`.
   *
   * Plural form is fixed at "issues" (e.g. "Validation found 1 issues") for
   * announcement-text simplicity — assistive-tech audiences tolerate the
   * unidiomatic phrasing better than two announcement variants would
   * tolerate inconsistent rendering. `?? 0` defends against a malformed
   * report missing one of the arrays.
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
   * Validate success-flash when the value diverges from the
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
    // Clear stale save error on any keystroke so the validation gate
    // auto-lifts. Unconditional (any keystroke is the operator declaring "I am
    // editing — re-evaluate"). The corresponding `lastValidation` clear remains
    // conditional (only when `value !== validatedBuffer`) to preserve the
    // green-flash semantics.
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
   * Monaco editor options — a single stable object reference built once and
   * NEVER reassigned. The editor is permanently writable (`readOnly: false`)
   * since the panel is always editable.
   *
   * `nu-monaco-editor` receives `[options]` as a signal input
   * (reference-equality semantics): a stable reference is load-bearing so
   * Monaco's `updateOptions()` does not re-run on every change-detection tick.
   *
   * `automaticLayout: true` is load-bearing for dialog hosting — the editor
   * otherwise renders at 0×0 when its container mounts late.
   */
  readonly editorOptions: Record<string, unknown> = {
    theme: 'vs',
    language: 'yaml',
    automaticLayout: true,
    readOnly: false,
  };

  /**
   * Dirty-state predicate — single source of truth for the confirm-on-close
   * guards and the Save/Reset/Clone enablement. True iff the buffer diverges
   * from the last server snapshot. The route `CanDeactivate` guard and the home
   * dirty-close guard consume the same method.
   */
  hasUnsavedChanges(): boolean {
    return this.buffer !== this.serverYaml;
  }

  // -------------------------------------------------------------------
  // Keyboard shortcuts (ADR-017).
  //
  // The five action shortcuts are captured in TWO places — a Monaco
  // `editor.addAction` (in-editor focus; Monaco swallows keystrokes) and a
  // capture-phase host keydown listener (focus outside the editor, e.g. on a
  // button). Both paths funnel through ONE shared dispatch surface: the
  // `can*` enablement getters below (which mirror the button `[disabled]`
  // expressions) and the `triggerX()` entry points. The button template binds
  // the SAME `can*` getters so the enablement predicate cannot drift between
  // the click path and the shortcut path.
  // -------------------------------------------------------------------

  /**
   * Save enablement — mirrors the Save button's `[disabled]` expression
   * (`buffer === serverYaml || saving || isSaveGated`). `buffer !== serverYaml`
   * is exactly `hasUnsavedChanges()`, used here so intent is explicit. ⌥S
   * fires Save iff this is true.
   */
  get canSave(): boolean {
    return this.hasUnsavedChanges() && !this.saving && !this.isSaveGated;
  }

  /**
   * Validate enablement — mirrors the Validate button's `[disabled]`
   * (`saving || validating`). NEVER gated by the validation gate; ⌥V fires in
   * both clean and dirty states.
   */
  get canValidate(): boolean {
    return !this.validating && !this.saving;
  }

  /**
   * Reset enablement — mirrors the Reset button's `[disabled]`
   * (`buffer === serverYaml || saving`). ⌥R is a no-op while clean; when dirty
   * it routes through the discard confirm.
   */
  get canReset(): boolean {
    return this.hasUnsavedChanges() && !this.saving;
  }

  /**
   * Clone enablement — mirrors the Clone button's `[disabled]`
   * (`buffer !== serverYaml || cloning || isCloneGated`). ⌥⇧C is a no-op while
   * dirty; it opens the Clone modal only when clean.
   */
  get canClone(): boolean {
    return !this.hasUnsavedChanges() && !this.cloning && !this.isCloneGated;
  }

  /**
   * Delete enablement — mirrors the Delete button's `[disabled]`
   * (`deleting`). ⌥D still routes through the Delete confirm dialog.
   */
  get canDelete(): boolean {
    return !this.deleting;
  }

  /**
   * Shared Save trigger. Consults `canSave` then delegates to the handler body
   * — never duplicates it. A true no-op when Save is disabled (the keystroke is
   * still `preventDefault`ed upstream).
   */
  private triggerSave(): void {
    if (this.canSave) {
      void this.onSaveClick();
    }
  }

  /** Shared Validate trigger. */
  private triggerValidate(): void {
    if (this.canValidate) {
      void this.onValidateBufferClick();
    }
  }

  /** Shared Reset trigger. */
  private triggerReset(): void {
    if (this.canReset) {
      this.onResetClick();
    }
  }

  /** Shared Clone trigger. */
  private triggerClone(): void {
    if (this.canClone) {
      this.onCloneClick();
    }
  }

  /** Shared Delete trigger — routes through the Delete confirm. */
  private triggerDelete(): void {
    if (this.canDelete) {
      this.onDeleteClick();
    }
  }

  /**
   * Single keyboard matcher consumed by BOTH capture sites.
   *
   * - Returns immediately (unhandled, NO `preventDefault`) when `!altKey`.
   * - Keys off `event.code` (NOT `event.key`) so macOS Option dead-keys
   *   (⌥S → `ß`, ⌥V → `√`, …) still match.
   * - `KeyC` → Clone ONLY when `event.shiftKey` (Alt+KeyC without Shift is
   *   unhandled). The S/V/R/D branches are keyed by their own codes, so
   *   Alt+Shift+KeyC fires ONLY Clone (no double-fire).
   * - On a recognised combo, `preventDefault()` is called ALWAYS — even when
   *   the matching `triggerX()` is a no-op by enablement — so the dead-key
   *   glyph / Alt-mnemonic is suppressed for keystrokes that are "ours".
   *
   * macOS dead-key / IME composition robustness (ADR-018). On some macOS
   * keyboard layouts an ⌥-chord initiates a dead-key composition: the browser
   * delivers the `keydown` flagged as composing (`isComposing === true`,
   * `keyCode === 229`, `key === 'Dead'`). This matcher MUST NOT early-return on
   * those flags — it keys off `event.altKey` + `event.code` ONLY and never
   * reads `event.key`, so a composing event with `code: 'KeyV'` still matches
   * and fires Validate. The `preventDefault()` on a handled combo also
   * suppresses the OS dead-key composition so no glyph is inserted. NEVER add
   * an `isComposing` / `keyCode === 229` / `key === 'Dead'` guard ahead of this
   * matcher for these chords. (Karma cannot reproduce the real OS composition
   * layer — synthetic `KeyboardEvent`s bypass it — so the real-world fix is
   * manually verified on macOS.)
   */
  private matchShortcut(event: KeyboardEvent): void {
    if (!event.altKey) {
      return; // not ours — let it propagate, no preventDefault.
    }
    switch (event.code) {
      case 'KeyS':
        event.preventDefault();
        this.triggerSave();
        return;
      case 'KeyV':
        event.preventDefault();
        this.triggerValidate();
        return;
      case 'KeyR':
        event.preventDefault();
        this.triggerReset();
        return;
      case 'KeyD':
        event.preventDefault();
        this.triggerDelete();
        return;
      case 'KeyC':
        if (event.shiftKey) {
          event.preventDefault();
          this.triggerClone();
        }
        // Alt+KeyC without Shift is unhandled — no preventDefault.
        return;
      default:
        // Alt + a non-bound code — unhandled, propagates.
        return;
    }
  }

  /**
   * Component-level keydown capture — the AUTHORITATIVE shortcut path
   * (ADR-018). Delegates to the shared `matchShortcut`.
   *
   * Registered as a **capture-phase** listener on the host element (see
   * `ngAfterViewInit`). The capture phase runs the host handler BEFORE the
   * event reaches Monaco's internal keydown handling, so even when focus is
   * inside the editor (Monaco may `stopPropagation` a keydown before it would
   * bubble back to the host) the host still sees — and acts on — the chord. The
   * Monaco `editor.addAction` keybindings registered in
   * `registerEditorShortcuts` are a best-effort in-editor convenience ONLY;
   * correctness does not depend on them. Single-fire-per-keystroke is
   * preserved: there is exactly ONE host dispatch site (this listener) routing
   * through the idempotent `triggerX()` surface, so no second uncoordinated
   * host dispatch can open two confirms / submit two imports.
   */
  onKeydown(event: KeyboardEvent): void {
    this.matchShortcut(event);
  }

  /**
   * Bound capture-phase keydown handler reference. Stored so the exact same
   * function identity can be passed to both `addEventListener` and
   * `removeEventListener` (an inline arrow would not be removable).
   */
  private readonly captureKeydown = (event: KeyboardEvent): void => {
    this.onKeydown(event);
  };

  /**
   * Register the authoritative capture-phase keydown listener on the host
   * element. Capture phase is load-bearing: it lets the host see the chord
   * BEFORE Monaco can swallow it when the editor has focus. Cleaned up via the
   * `destroyRef` hook so no listener leaks after the panel is torn down.
   */
  ngAfterViewInit(): void {
    const host = this.elementRef.nativeElement;
    host.addEventListener('keydown', this.captureKeydown, { capture: true });
    this.destroyRef.onDestroy(() => {
      host.removeEventListener('keydown', this.captureKeydown, {
        capture: true,
      });
    });
  }

  /**
   * `(event)` handler for `<nu-monaco-editor>`. On the `'init'` event the
   * editor instance is available; register the five chord actions via
   * `editor.addAction(...)`, each `run` delegating to the SAME `triggerX()`
   * entry point as the host capture path. Guarded against re-registration on a
   * `'re-init'` event via `monacoActionsRegistered`.
   */
  onEditorEvent(e: NuMonacoEditorEvent): void {
    if (e.type !== 'init' || !e.editor) {
      return;
    }
    if (this.monacoActionsRegistered) {
      return;
    }
    const editor = e.editor as monaco.editor.IStandaloneCodeEditor;
    this.registerEditorShortcuts(editor);
    this.monacoActionsRegistered = true;
  }

  /**
   * Registers the five ⌥-key chord actions on the Monaco editor. Each `run`
   * delegates to the shared `triggerX()` dispatch surface so enablement parity
   * holds identically for the Monaco and host capture sites. The chord
   * constants come from the ambient `monaco` namespace (`monaco.KeyMod` /
   * `monaco.KeyCode`) — no `monaco-editor` import.
   */
  private registerEditorShortcuts(
    editor: monaco.editor.IStandaloneCodeEditor,
  ): void {
    editor.addAction({
      id: 'namespace-panel.save',
      label: 'Save namespace',
      keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.KeyS],
      run: () => this.triggerSave(),
    });
    editor.addAction({
      id: 'namespace-panel.validate',
      label: 'Validate namespace',
      keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.KeyV],
      run: () => this.triggerValidate(),
    });
    editor.addAction({
      id: 'namespace-panel.reset',
      label: 'Reset namespace buffer',
      keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.KeyR],
      run: () => this.triggerReset(),
    });
    editor.addAction({
      id: 'namespace-panel.clone',
      label: 'Clone namespace',
      keybindings: [monaco.KeyMod.Alt | monaco.KeyMod.Shift | monaco.KeyCode.KeyC],
      run: () => this.triggerClone(),
    });
    editor.addAction({
      id: 'namespace-panel.delete',
      label: 'Delete namespace',
      keybindings: [monaco.KeyMod.Alt | monaco.KeyCode.KeyD],
      run: () => this.triggerDelete(),
    });
  }

  /**
   * Guards `onEditorEvent` against double-registering the Monaco actions if a
   * `'re-init'` event follows the initial `'init'`.
   */
  private monacoActionsRegistered = false;

  // -------------------------------------------------------------------
  // Custom confirmation modal (ADR-018). One shared open/close surface reused
  // by Reset-discard, Save-drift, and Delete; the primary button autofocuses on
  // show so Enter activates it and Esc cancels.
  // -------------------------------------------------------------------

  /**
   * Open the custom confirmation modal. Captures the typed request + the
   * per-flow callbacks the template buttons invoke, then flips the visibility
   * flag. For the `'drift'` variant `onReload` is the safe/focused Reload branch
   * and `onAccept` is the Overwrite branch; for `'reset'` / `'delete'` only
   * `onAccept` (Proceed) is supplied and `onReload` is omitted.
   */
  private openConfirm(
    request: ConfirmRequest,
    onAccept: () => void,
    onReload?: () => void,
  ): void {
    this.confirmRequest = request;
    this.confirmAccept = onAccept;
    this.confirmReload = onReload ?? null;
    this.confirmDialogVisible = true;
  }

  /**
   * Close the confirmation modal and null the request + callbacks so a stale
   * request cannot leak into the next open. Idempotent. The pending drift
   * resolver (`confirmDriftResolve`) is intentionally NOT cleared here —
   * `onConfirmDialogHide` owns resolving it to the safe branch before this runs.
   */
  private closeConfirm(): void {
    this.confirmDialogVisible = false;
    this.confirmRequest = null;
    this.confirmAccept = null;
    this.confirmReload = null;
  }

  /**
   * Public predicate — true iff ANY secondary panel (the Clone modal OR the
   * confirmation modal) is open. The host (`HomeComponent` config dialog, and
   * any route-shell that binds `closeOnEscape`) reads this synchronously to
   * suppress its own `closeOnEscape` while a secondary panel is open, so Esc
   * closes only the topmost secondary panel (ADR-018).
   */
  get hasSecondaryPanelOpen(): boolean {
    return this.cloneDialogVisible || this.confirmDialogVisible;
  }

  /**
   * Coordinated Escape for the panel's secondary modals (ADR-018). Closes ONLY
   * the topmost open secondary panel and reports whether it consumed the
   * Escape, so EXACTLY ONE action happens per keystroke (no cascade):
   *
   *   1. confirmation modal open → cancel/close ONLY it (settling any pending
   *      drift/discard promise via `onConfirmDialogHide`), return `true`.
   *   2. else Clone modal open → close ONLY it, return `true`.
   *   3. else no secondary panel open → return `false` (the host then runs the
   *      configuration panel's own close flow).
   *
   * PrimeNG attaches `closeOnEscape` as a DOCUMENT-level listener per dialog, so
   * a child `stopPropagation()` cannot stop the host config dialog's listener —
   * that is why ALL THREE dialogs set `[closeOnEscape]="false"` and a single
   * coordinator (the host's capture-phase keydown) delegates here first. Order
   * is deterministic: confirm is always stacked above Clone, so it wins.
   */
  handleSecondaryEscape(): boolean {
    if (this.confirmDialogVisible) {
      // Route through onConfirmDialogHide so a pending drift/discard promise
      // settles to its safe branch (mirrors a Cancel/X dismissal).
      this.onConfirmDialogHide();
      return true;
    }
    if (this.cloneDialogVisible) {
      this.onCloneDialogVisibleChange(false);
      return true;
    }
    return false;
  }

  /**
   * PUBLIC dirty-discard confirm (ADR-018). Opens the panel-owned custom
   * confirmation modal with the `'discard'` variant ("Unsaved changes" / "You
   * have unsaved changes. Discard?") and returns a `Promise<boolean>` that
   * resolves:
   *   - `true`  → the operator chose **Proceed**. The panel buffer is REALLY
   *               discarded here (`revertBufferToServer()` — buffer reset to the
   *               server snapshot, dirty-derived state cleared) BEFORE the
   *               promise resolves, then the host closes the panel. Because the
   *               panel instance is kept alive across a dialog visibility toggle
   *               (not destroyed), resetting on this path is what stops the
   *               abandoned edits from leaking into the next open — reopening
   *               shows the clean server YAML.
   *   - `false` → the operator **Cancelled** / dismissed (Esc / X / mask) — the
   *               buffer is left UNTOUCHED (still dirty) and the panel stays open.
   *
   * Reused by BOTH dirty-state hosts:
   *   - the Home config-dialog close paths (`onNamespacePanelVisibleChange` and
   *     the Esc/X/mask close), and
   *   - the deep-link route `CanDeactivate` guard (`namespacePanelCanDeactivate`).
   *
   * A dismissal that bypasses the buttons (Esc / X / mask) settles `false` via
   * `confirmDiscardResolve` in `onConfirmDialogHide` — the safe branch (keep the
   * edits), mirroring the drift modal's safe-dismissal contract.
   */
  confirmDiscard(): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      let settled = false;
      const settle = (value: boolean): void => {
        if (settled) {
          return;
        }
        settled = true;
        this.confirmDiscardResolve = null;
        resolve(value);
      };
      // Esc / Cancel / X dismissal resolves the SAFE branch (false — keep edits).
      this.confirmDiscardResolve = () => settle(false);
      this.openConfirm(
        {
          header: 'Unsaved changes',
          message: 'You have unsaved changes. Discard?',
          variant: 'discard',
        },
        // Proceed → REALLY discard (revert the buffer to the server snapshot,
        // clear dirty-derived state) then allow the close-or-navigation. The
        // reset happens on BOTH the Esc and the X/mask close paths because they
        // all route Proceed through this same accept callback.
        () => {
          this.revertBufferToServer();
          settle(true);
        },
      );
    });
  }

  /**
   * The action-row label for the confirmation modal's affirmative button. Every
   * single-affirmative variant — reset, delete, and discard — uses
   * **"Proceed"** (the dark-red `__danger` class is what marks the `delete`
   * variant as destructive, NOT a distinct label). The drift variant renders
   * its own two explicit Reload / Overwrite buttons, so this label is never
   * shown for it. Returned as a getter so the template need not branch on the
   * variant.
   */
  get confirmProceedLabel(): string {
    return 'Proceed';
  }

  /**
   * Proceed (reset/delete/discard) button handler. Runs the captured accept
   * callback then closes. For `'delete'` this fires `onDeleteConfirm()`; for
   * `'reset'` the revert effect; for `'discard'` it resolves the public
   * `confirmDiscard()` promise `true` (the accept callback settles + clears the
   * pending `confirmDiscardResolve`, so the resulting onHide is a no-op).
   */
  onConfirmProceedClick(): void {
    const accept = this.confirmAccept;
    this.closeConfirm();
    accept?.();
  }

  /**
   * Overwrite (drift) button handler. The destructive drift branch: resolves
   * the drift promise `true` (proceed to import the operator buffer) via the
   * captured accept callback, then closes.
   */
  onConfirmOverwriteClick(): void {
    const accept = this.confirmAccept;
    // The drift promise is settled by `accept`; clear the dismissal resolver
    // first so the resulting onHide does not re-settle it.
    this.confirmDriftResolve = null;
    this.closeConfirm();
    accept?.();
  }

  /**
   * Reload (drift) button handler — the safe / focused default. Runs the rebase
   * effect and resolves the drift promise `false` (skip the import) via the
   * captured reload callback, then closes.
   */
  onConfirmReloadClick(): void {
    const reload = this.confirmReload;
    this.confirmDriftResolve = null;
    this.closeConfirm();
    reload?.();
  }

  /**
   * Cancel (reset/delete/discard) button handler. The safe branch: closes
   * without running any accept callback — no destructive/lossy effect occurs.
   * For the `'discard'` variant it settles the public `confirmDiscard()` promise
   * to `false` (keep edits) directly, so the awaited promise resolves
   * deterministically on a button click without depending on the PrimeNG
   * `(onHide)` round-trip; the resolver is cleared so the subsequent onHide is a
   * no-op.
   */
  onConfirmCancelClick(): void {
    const discardResolve = this.confirmDiscardResolve;
    this.confirmDiscardResolve = null;
    this.closeConfirm();
    discardResolve?.();
  }

  /**
   * `(onShow)` handler for the confirmation modal. Mirrors `onCloneDialogShow`:
   * defers the focus call by a microtask so PrimeNG has mounted the overlay,
   * then focuses the PRIMARY/proceed button (Proceed for reset/delete/discard,
   * Reload for drift) so **Enter activates the main action**. Esc still cancels.
   * Null-safe via the `@ViewChild` ref.
   */
  onConfirmDialogShow(): void {
    setTimeout(() => {
      this.confirmPrimaryBtnRef?.nativeElement.focus();
    }, 0);
  }

  /**
   * `(onHide)` handler for the confirmation modal (ADR-018). Resolves a
   * still-pending dismissal to the SAFE branch — the drift dismissal to `false`
   * (no blind overwrite) and the dirty-discard dismissal to `false` (keep the
   * edits) — then resets the request + callbacks so nothing leaks into the next
   * open. Both resolvers are invoked BEFORE `closeConfirm` so the awaited
   * `onSaveClick` / `confirmDiscard()` promise settles even on an Esc/X
   * dismissal.
   */
  onConfirmDialogHide(): void {
    const driftResolve = this.confirmDriftResolve;
    const discardResolve = this.confirmDiscardResolve;
    this.confirmDriftResolve = null;
    this.confirmDiscardResolve = null;
    driftResolve?.();
    discardResolve?.();
    this.closeConfirm();
  }

  /**
   * Revert the edit buffer to the last server snapshot and clear every
   * dirty-derived signal (`lastValidation` / `rawSaveError` / `validatedBuffer`)
   * so the panel lands fully clean. Shared by the Reset button (`onResetClick`)
   * and the dirty-CLOSE discard (`confirmDiscard` Proceed). Load-bearing for the
   * close path: the panel instance is kept alive across a dialog visibility
   * toggle (not destroyed), so without this reset the abandoned edits would leak
   * into the next open. Destroy-guarded.
   */
  private revertBufferToServer(): void {
    if (this.destroyed) {
      return;
    }
    this.buffer = this.serverYaml;
    this.lastValidation = null;
    this.rawSaveError = null;
    this.validatedBuffer = null;
  }

  /**
   * Revert the buffer to `serverYaml` — the undo affordance. Guarded by the
   * custom "Discard unsaved changes?" confirm; a no-op when clean (the Reset
   * button is also disabled via `[disabled]="buffer === serverYaml"`).
   * Reverts-and-STAYS (does not close the panel) — distinct from the
   * dirty-CLOSE discard which reverts-and-closes.
   */
  onResetClick(): void {
    if (this.buffer === this.serverYaml) {
      return;
    }
    this.openConfirm(
      {
        header: 'Unsaved changes',
        message: 'Discard unsaved changes?',
        variant: 'reset',
      },
      () => this.revertBufferToServer(),
    );
  }

  /**
   * Save handler — posts the current buffer directly to
   * `apiService.importNamespace` (NO pre-save validate), preceded by a one-shot
   * **drift check** (ADR-017).
   *
   * Gated on `hasUnsavedChanges()` (and `saving`).
   *
   * Drift check: before importing, re-`exportNamespace` and, if the returned
   * YAML diverges from `serverYaml`, prompt the operator (reload-and-rebase
   * vs overwrite) instead of importing blindly. The check is one-shot per Save.
   *
   * Success (2xx): `serverYaml` snapshots the just-saved buffer, toast
   * success, `saved` emits so the host refreshes derived lists. The buffer is
   * unchanged, so the panel is now clean (Save/Reset disable, Clone enables).
   *
   * 422: populate `lastValidation` when the body is structurally a
   * `NamespaceValidationReport`, else stash the raw body into `rawSaveError`
   * for a verbatim `<pre>` rendering. Buffer preserved, saving resets.
   *
   * 401: silent at the panel level. FetchService has already surfaced a
   * global toast; the app-wide 401 handler (if any) runs. The panel only
   * clears `saving` so a subsequent click is not stuck in a spinner.
   *
   * Other (4xx/5xx/network): sticky error toast. The operator retries by
   * clicking the regular Save button again (buffer is still dirty post-
   * failure, so Save remains enabled) — there is no dedicated Retry button
   * in the action row. `importNamespace` is NEVER auto-retried by the panel.
   */
  async onSaveClick(): Promise<void> {
    if (!this.hasUnsavedChanges() || this.saving) {
      return;
    }
    // Snapshot the buffer at click time so typing during the request does
    // NOT race the success branch.
    const savedBuffer = this.buffer;

    // Guardrail — refuse to Save when the buffer's top-level `namespace:`
    // field has been edited away from the panel's namespace.
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

    // Advisory pre-flight (NOT the security boundary — the infra import gate
    // is). Mirror the namespace-change guard above: block obviously-doomed
    // Saves with a clear message instead of letting the server bounce a 403.
    // This check lives in the browser and can be bypassed (devtools, direct
    // API call), so it must NEVER be relied upon for enforcement — the
    // server's owner-or-admin import gate is authoritative. Admin is checked
    // first; an unresolvable owner (null) falls through to the server.
    const me = this.authService.currentUserValue;
    const namespaceOwner = extractYamlUserId(savedBuffer);
    const isAdmin = me?.roles?.includes('admin') === true;
    const isOwner = namespaceOwner !== null && namespaceOwner === me?.user_id;
    if (!isAdmin && !isOwner && namespaceOwner !== null) {
      this.messageService.add({
        severity: 'error',
        summary: 'Cannot save changes to this namespace',
        detail: 'You can only save changes to namespaces you own.',
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
      // One-shot drift check before importing (ADR-017). Returns false when
      // the operator chose reload-and-rebase (import is skipped this click);
      // true when the import should proceed (no drift, or overwrite).
      const proceed = await this.checkSaveDrift(savedBuffer);
      if (this.destroyed || !proceed) {
        return;
      }
      await this.performSaveImport(savedBuffer);
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
   * One-shot drift check folded into Save (ADR-017). Re-`exportNamespace`s
   * (carrying `{ all: this.showAll }`) and, if the returned YAML diverges from
   * `serverYaml`, prompts the operator with a reload-and-rebase vs overwrite
   * choice. Returns:
   *   - `true`  → proceed with the import (no drift detected, OR the operator
   *               chose **overwrite**).
   *   - `false` → skip the import this click (the operator chose
   *               **reload-and-rebase**: `serverYaml`, and `buffer` if it
   *               equalled the old `serverYaml`, are replaced with the latest
   *               server YAML so the operator can re-Save against the fresh
   *               base).
   *
   * The choice is rendered as **two explicit named buttons** in the custom
   * confirmation modal (Reload + Overwrite), not a binary accept/reject. Reload
   * (the safe, focused default) resolves `false`; Overwrite resolves `true`.
   * Dismissing the modal (Esc / Cancel / X) resolves `false` — the safe branch
   * — so an accidental dismissal never lands a blind overwrite. A network error
   * during the re-export falls through to `true` (proceed) — the server still
   * validates on import, so a genuinely-stale edit can only land a known-bad
   * bundle, and operators should not be blocked by a hiccup.
   */
  private async checkSaveDrift(savedBuffer: string): Promise<boolean> {
    let latestYaml: string;
    try {
      latestYaml = await this.apiService.exportNamespace(this.namespace, {
        all: this.showAll,
      });
    } catch {
      // Non-blocking: proceed to import against last-known server state.
      return true;
    }
    if (this.destroyed) {
      return false;
    }
    if (latestYaml === this.serverYaml) {
      return true;
    }
    return new Promise<boolean>((resolve) => {
      // Guard against double-resolution: a button click resolves and closes;
      // the resulting onHide must NOT resolve a second time. `settle` clears
      // the pending resolver so `onConfirmDialogHide` becomes a no-op once a
      // button has chosen.
      let settled = false;
      const settle = (value: boolean): void => {
        if (settled) {
          return;
        }
        settled = true;
        this.confirmDriftResolve = null;
        resolve(value);
      };
      // Esc / Cancel / X dismissal resolves the SAFE branch (false). Registered
      // so `onConfirmDialogHide` can settle a dismissal that bypasses a button.
      this.confirmDriftResolve = () => settle(false);
      this.openConfirm(
        {
          header: 'Namespace modified',
          message:
            'The namespace was modified on the server since it was loaded. ' +
            'Reload the latest version (discarding this Save), or overwrite it ' +
            'with your changes?',
          variant: 'drift',
        },
        // Overwrite (the destructive branch) → proceed with the operator buffer.
        () => settle(true),
        // Reload (the safe, focused default) → rebase + skip this import.
        () => {
          if (!this.destroyed) {
            // Rebase onto the fresh server YAML; only move the buffer if it
            // was still pinned to the old snapshot (no local edits to lose).
            if (this.buffer === this.serverYaml) {
              this.buffer = latestYaml;
            }
            this.serverYaml = latestYaml;
          }
          settle(false);
        },
      );
    });
  }

  /**
   * Issues the import and runs the success branch. Extracted from
   * `onSaveClick` so the handler stays within the method-length budget. The
   * caller owns `saving` / the destroy guard / the catch branch.
   */
  private async performSaveImport(savedBuffer: string): Promise<void> {
    await this.apiService.importNamespace(savedBuffer);
    if (this.destroyed) {
      return;
    }
    this.serverYaml = savedBuffer;
    // Pair the success toast with an aria-live announcement so screen-reader
    // users get the same outcome.
    this.a11yAnnouncement = 'Namespace saved';
    this.saved.emit();
    this.messageService.add({
      severity: 'success',
      summary: 'Namespace saved successfully',
    });
  }

  /**
   * The Validate handler (ADR-017). Validates the current buffer without
   * persisting via `apiService.validateNamespaceBuffer(buffer)`. Snapshots
   * `this.buffer` at click time (`bufferAtClick`) so Monaco's live
   * `[(ngModel)]` mutation during the in-flight request cannot race the
   * request args.
   *
   * When the buffer is clean (`buffer === serverYaml`), this is identical to
   * validating the persisted namespace. Validate never mutates `serverYaml`
   * or `buffer` — the handler touches only `validating` and `lastValidation`.
   * It is never disabled by the validation gate.
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
      // Clean buffer → flash the Validate button. Non-clean → render
      // findings via `lastValidation`.
      if (report.ok) {
        this.lastValidation = null;
        this.validatedBuffer = bufferAtClick;
        this.flashValidated();
      } else {
        this.lastValidation = report;
        // Announce the failing-validation outcome.
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
   * no DOM on the next CD tick.
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
   *   is the natural retry, so no Retry action is attached.
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
        // Announce the failing-validation outcome.
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
      // `buffer` / `lastValidation` on 401.
      return;
    }
    if (status === 422) {
      const body = (err as HttpError).body;
      if (this.isValidationReport(body)) {
        this.lastValidation = body;
        this.rawSaveError = null;
        // Announce the failing-validation outcome.
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
  // Clone flow
  // -------------------------------------------------------------------

  /**
   * Open the Clone sub-dialog. Pure UI flip — does NOT fire a network request,
   * does NOT mutate `buffer` / `serverYaml` / `lastValidation` / `rawSaveError`.
   * No-op when `cloning === true` (defence in depth — the outer Clone button is
   * `[disabled]` in that state, so this path is unreachable in practice).
   */
  onCloneClick(): void {
    if (this.cloning) {
      return;
    }
    // Pre-fill the modal inputs from the current buffer. Source values are
    // best-effort: `extractYamlName` returns null on a header-less bundle,
    // in which case the name suggestion falls back to the namespace string
    // (rare path — meta header is required by the v17.5+ wire format).
    const srcNs = extractYamlNamespace(this.buffer) ?? this.namespace;
    const srcName = extractYamlName(this.buffer) ?? srcNs;
    this.cloneDestNs = suggestDestNamespace(srcNs);
    this.cloneDestName = suggestDestName(srcName);
    // Pre-fill the visibility/sharing toggles from the same buffer. Absent /
    // non-boolean / unparseable → default to false.
    this.cloneShareable = extractYamlShareable(this.buffer) ?? false;
    this.clonePublic = extractYamlPublic(this.buffer) ?? false;
    this.cloneDialogVisible = true;
  }

  /**
   * Cancel the Clone sub-dialog. Resets `cloneDestNs`; does NOT mutate
   * `cloning` — an in-flight clone request keeps settling via its own
   * try/catch/finally (the destroy-guard idiom).
   */
  onCloneCancelClick(): void {
    this.cloneDialogVisible = false;
    this.cloneDestNs = '';
    this.cloneDestName = '';
    this.cloneShareable = false;
    this.clonePublic = false;
  }

  /**
   * `(visibleChange)` handler for the Clone sub-dialog. Mirrors Cancel's
   * reset: when the dialog dismisses (visible = false, e.g. ESC or the
   * close X), we zero `cloneDestNs` so the next open starts clean. `cloning`
   * is intentionally unchanged — an in-flight request keeps settling
   * regardless of dialog visibility.
   */
  onCloneDialogVisibleChange(visible: boolean): void {
    this.cloneDialogVisible = visible;
    if (!visible) {
      this.cloneDestNs = '';
      this.cloneDestName = '';
      this.cloneShareable = false;
      this.clonePublic = false;
    }
  }

  /**
   * Pre-flight predicate for the Clone Confirm button. True iff any of: destNs
   * is empty-trimmed, destNs equals the source namespace, destNs already
   * appears in `existingNamespaces` (case-sensitive), or a clone is already in
   * flight.
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
    if (this.cloneDestName.trim() === '') {
      return true;
    }
    if (this.cloning) {
      return true;
    }
    // The Clone modal Confirm button inherits the validation gate so an
    // operator cannot bypass the outer Clone button's disabled state by opening
    // the modal first.
    return this.isCloneGated;
  }

  /**
   * Inline validation message for the Clone dialog. Returns `null` while the
   * destNs is an unused, non-colliding value — so the template can gate the
   * error block with `*ngIf`.
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
    if (this.cloneDestName.trim() === '') {
      return 'Destination name required';
    }
    return null;
  }

  // -------------------------------------------------------------------
  // Save/Clone validation-gate getters + tooltip helpers
  // -------------------------------------------------------------------

  /**
   * Save gate predicate. True iff the panel has a known-bad signal: either a
   * structured-422 / 200-with-issues report (`lastValidation.ok === false`) or
   * an unstructured-422 raw-error fallback (`rawSaveError !== null`).
   *
   * Fresh untouched state (`lastValidation === null && rawSaveError === null`)
   * returns `false` — Save / Clone are enabled by default. The gate fires only
   * on a known-bad signal.
   *
   * Two getters (`isSaveGated` + `isCloneGated`) instead of one shared getter
   * so call-site tooltips can be specific (Save's tooltip says "saving";
   * Clone's says "cloning") without leaking unrelated logic.
   */
  get isSaveGated(): boolean {
    return (
      (this.lastValidation !== null && !this.lastValidation.ok) ||
      this.rawSaveError !== null
    );
  }

  /** Clone gate predicate (same body as Save). */
  get isCloneGated(): boolean {
    return (
      (this.lastValidation !== null && !this.lastValidation.ok) ||
      this.rawSaveError !== null
    );
  }

  /**
   * Save tooltip text. Returns the explanatory string ONLY when the gate is the
   * active reason for disabling; returns `undefined` otherwise so PrimeNG's
   * `pTooltip` input (whose type is `string | TemplateRef | undefined`) accepts
   * it under strictTemplates. PrimeNG suppresses the tooltip on `undefined` /
   * empty string, so unrelated disable reasons (clean buffer, in-flight save)
   * get no tooltip — avoiding contradictory messaging.
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
   * Clone tooltip text. Same idiom as `saveTooltip`: returns the
   * gate-explanatory string only when the gate is the active reason for
   * disabling; `undefined` otherwise (pTooltip-type-compatible).
   */
  get cloneTooltip(): string | undefined {
    if (this.isCloneGated && !this.cloning && !this.saving) {
      return 'Fix validation issues before cloning.';
    }
    return undefined;
  }

  // -------------------------------------------------------------------
  // Clone modal a11y handlers
  // -------------------------------------------------------------------

  /**
   * `(onShow)` handler for the Clone modal. Defers the focus call to the next
   * microtask so PrimeNG has time to mount the input element in its overlay.
   * Idempotent + null-safe.
   */
  onCloneDialogShow(): void {
    setTimeout(() => {
      this.cloneDestInputRef?.nativeElement.focus();
    }, 0);
  }

  /**
   * `(onHide)` handler for the Clone modal. Runs the same cleanup as
   * `onCloneCancelClick` PLUS clears `cloneInlineError` and returns focus to the
   * outer Clone button. Defers the focus call by one microtask so any post-hide
   * CD settles (and PrimeNG's exit animation completes) before the focus moves.
   */
  onCloneDialogHide(): void {
    this.cloneDestNs = '';
    this.cloneDestName = '';
    this.cloneShareable = false;
    this.clonePublic = false;
    this.cloneInlineError = null;
    setTimeout(() => {
      this.cloneBtnRef?.nativeElement.focus();
    }, 0);
  }

  /**
   * Destination-input change handler. Clears the unstructured-422 inline error
   * so the operator can edit and retry without first dismissing a stale alert.
   * The `cloneValidationError` getter is computed-on-getter and does not need a
   * separate clear-on-input handler.
   */
  onCloneDestNsChange(): void {
    if (this.cloneInlineError !== null) {
      this.cloneInlineError = null;
    }
  }

  /**
   * Clone handler — "duplicate the saved bundle" semantics (ADR-017). The outer
   * Clone button is gated on a **clean** panel (`buffer === serverYaml`), so the
   * captured buffer is the persisted bundle, never a half-edited draft.
   * Rewrites the root `namespace` field via {@link rewriteNamespaceInYaml},
   * then reuses the Save path (`apiService.importNamespace`) — no new endpoint.
   *
   * Branches:
   *   - `CloneYamlError` before the network call → toast + dialog stays
   *     open, `cloning` resets, `importNamespace` NEVER called.
   *   - 2xx → dismiss dialog, emit `(saved)`, switch to destNs, re-load via
   *     `loadNamespace(destNs)` — lands clean.
   *   - 401 silent (FetchService surfaces the global toast).
   *   - 422 structured → populate `lastValidation`; dialog stays open.
   *   - 422 unstructured → stash raw body in `rawSaveError`; dialog stays
   *     open.
   *   - Other (4xx / 5xx / network) → sticky error toast + `lastCloneError`;
   *     dialog stays open.
   *
   * In every failure branch: `namespace` / `serverYaml` / `buffer` are
   * unchanged. Destroy-guard mirrors `onSaveClick`.
   */
  async onCloneConfirmClick(): Promise<void> {
    if (this.cloneConfirmDisabled) {
      return;
    }
    const destNs = this.cloneDestNs.trim();
    const destName = this.cloneDestName.trim();
    // Capture the toggle values once at the top (mirroring the destNs /
    // destName trim-once capture) so they are stable across the async boundary.
    // `isPublic` avoids the reserved-ish local name `public`.
    const shareable = this.cloneShareable;
    const isPublic = this.clonePublic;
    const sourceYaml = this.buffer;
    this.cloning = true;
    this.lastCloneError = null;
    try {
      const rewrittenYaml = rewriteNamespaceInYaml(
        sourceYaml,
        destNs,
        destName,
        shareable,
        isPublic,
      );
      await this.apiService.importNamespace(rewrittenYaml);
      if (this.destroyed) {
        return;
      }
      // 2xx happy path: dismiss dialog, emit saved, switch namespace +
      // re-load. Programmatic self-writes to an @Input() field do NOT
      // trigger ngOnChanges, so we invoke loadNamespace explicitly — it
      // re-exports the bundle and lands the panel clean.
      this.cloneDialogVisible = false;
      this.cloneDestNs = '';
      this.cloneDestName = '';
      this.cloneShareable = false;
      this.clonePublic = false;
      this.cloneInlineError = null;
      this.saved.emit();
      this.namespace = destNs;
      // Fire-and-forget: loadNamespace owns its own loading flag and
      // destroy guard.
      void this.loadNamespace(destNs);
      // Pair the success toast with an aria-live announcement using the
      // captured destNs (avoids racing with any later mutation of
      // `this.cloneDestNs`).
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
   * Narrow the caught Clone error. Branch order: `CloneYamlError` FIRST
   * (client-side rewrite failure, `importNamespace` never fired), then 401
   * silent, then 422 structured / 422 unstructured, then default sticky toast.
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
        // Structured 422 closes the Clone modal and surfaces the findings in
        // the parent panel's findings pane (the single canonical findings
        // surface). The outer Clone button is then gated by validation.
        this.lastValidation = body;
        this.rawSaveError = null;
        this.cloneDialogVisible = false;
        this.cloneDestNs = '';
        this.cloneInlineError = null;
        // Announce the failing-validation outcome.
        this.announceValidationOutcome(body);
      } else {
        // Unstructured 422 keeps the modal open with an inline `role="alert"`
        // error. We deliberately do NOT touch `lastValidation` or
        // `rawSaveError` (the parent's validation gate is reserved for SAVE
        // failures). The inline alert handles its own assistive-tech
        // announcement, so no live-region write here (avoids duplicate
        // announcements).
        this.cloneInlineError =
          typeof body === 'string' ? body : JSON.stringify(body, null, 2);
      }
      return;
    }
    // Default: 4xx / 5xx / network. Sticky toast + `lastCloneError`. There is
    // no in-panel Retry-Clone button — re-clicking Clone Confirm is the natural
    // retry path, and the action row is already dense.
    this.lastCloneError = err instanceof Error ? err : new Error(String(err));
    this.messageService.add({
      severity: 'error',
      summary: 'Clone failed',
      detail: this.lastCloneError.message,
      sticky: true,
    });
  }

  // -------------------------------------------------------------------
  // Delete flow (frontend leg). The backend authorizes the request
  // (owner-or-admin); this client only issues the DELETE and reacts to the
  // status code — no role/ownership logic lives here.
  // -------------------------------------------------------------------

  /**
   * Delete handler. Always available (only gated by an in-flight `deleting`).
   * No-op while a delete is in flight (defence in depth alongside
   * `[disabled]="deleting"`). Opens the custom confirmation modal naming the
   * current namespace; the Proceed (destructive) button runs `onDeleteConfirm()`.
   * Cancelling is a no-op (panel unchanged, no network call).
   */
  onDeleteClick(): void {
    if (this.deleting) {
      return;
    }
    this.openConfirm(
      {
        header: 'Delete namespace',
        message: `Delete namespace "${this.namespace}" and all its entries? This cannot be undone.`,
        variant: 'delete',
      },
      () => {
        void this.onDeleteConfirm();
      },
    );
  }

  /**
   * Issues the DELETE and runs the result paths. No-op while a delete is in
   * flight. Success (`204`): emit `saved` (host refreshes the namespace list)
   * AND `closed` (Home dialog dismisses; route-shell ignores `closed`
   * harmlessly). Failure branches delegate to `handleDeleteError`; in every
   * failure branch `namespace`, `serverYaml`, and `buffer` are unchanged. The
   * `deleting` flag is always cleared in `finally`, guarded by the `destroyed`
   * idiom.
   */
  async onDeleteConfirm(): Promise<void> {
    if (this.deleting) {
      return;
    }
    this.deleting = true;
    try {
      await this.apiService.deleteNamespace(this.namespace);
      if (this.destroyed) {
        return;
      }
      // Success path mirrors the Clone-success UX.
      this.a11yAnnouncement = `Namespace '${this.namespace}' deleted`;
      this.saved.emit();
      this.closed.emit();
      this.messageService.add({
        severity: 'success',
        summary: `Namespace '${this.namespace}' deleted`,
      });
    } catch (err) {
      if (this.destroyed) {
        return;
      }
      this.handleDeleteError(err);
    } finally {
      if (!this.destroyed) {
        this.deleting = false;
      }
    }
  }

  /**
   * Narrow the caught Delete error. Branches:
   *   - `401` → silent return (FetchService already fired the global toast;
   *     the panel does not mutate `buffer` / `serverYaml`).
   *   - `403` → non-sticky not-authorized toast; panel stays open. NOT
   *     retried. (Community never returns 403; gated tiers do.)
   *   - `409` / `422` → inbound-reference blocker. If the body is a
   *     structured `NamespaceValidationReport`, route it through the findings
   *     pane (`lastValidation` + `announceValidationOutcome`); otherwise
   *     surface the raw detail via a non-sticky error toast. Panel stays open.
   *   - default (other 4xx / 5xx / network) → non-sticky error toast.
   */
  private handleDeleteError(err: unknown): void {
    const status = (err as { status?: number })?.status;
    if (status === 401) {
      // Silent — FetchService already fired a global toast.
      return;
    }
    if (status === 403) {
      this.messageService.add({
        severity: 'error',
        summary: 'You are not authorized to delete this namespace',
      });
      return;
    }
    if (status === 409 || status === 422) {
      const body = (err as HttpError).body;
      if (this.isValidationReport(body)) {
        this.lastValidation = body;
        this.rawSaveError = null;
        this.announceValidationOutcome(body);
        return;
      }
      this.messageService.add({
        severity: 'error',
        summary: 'Cannot delete namespace',
        detail:
          typeof body === 'string'
            ? body
            : ((err as Error)?.message ?? String(err)),
      });
      return;
    }
    // Default: other 4xx / 5xx / network.
    this.messageService.add({
      severity: 'error',
      summary: 'Delete failed',
      detail: (err as Error)?.message ?? String(err),
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
    // state renders while the fetch is in flight.
    this.serverYaml = '';
    this.buffer = '';
    try {
      // Carry the admin "show all" flag so an admin opening a foreign-owned
      // namespace from the home "show all" list can read it (the export GET is
      // unscoped server-side for admins when all=true).
      const yaml = await this.apiService.exportNamespace(namespace, {
        all: this.showAll,
      });
      if (this.destroyed || seq !== this.loadSeq) {
        return;
      }
      this.serverYaml = yaml;
      this.buffer = yaml;
      // buffer === serverYaml ⇒ the panel lands clean (Save/Reset disabled,
      // Clone enabled). The editor is always writable.
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
