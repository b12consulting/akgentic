import { CommonModule } from '@angular/common';
import {
  Component,
  ElementRef,
  EventEmitter,
  forwardRef,
  Input,
  Output,
} from '@angular/core';
import {
  ComponentFixture,
  fakeAsync,
  TestBed,
  tick,
} from '@angular/core/testing';
import {
  ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { By } from '@angular/platform-browser';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { NuMonacoEditorEvent } from '@ng-util/monaco-editor';
import yaml from 'js-yaml';
import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { Dialog, DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { ToggleSwitchModule } from 'primeng/toggleswitch';
import { TooltipModule } from 'primeng/tooltip';

import { NamespaceValidationReport } from '../../../protocol/catalog.interface';
import { ApiService } from '../../../core/http/api.service';
import { AuthService } from '../../../core/auth/auth.service';
import { HttpError } from '../../../core/http/fetch.service';
import { NamespacePanelComponent } from './namespace-panel.component';
import { ValidationReportComponent } from './validation-report/validation-report.component';

/**
 * Stand-in for <nu-monaco-editor> used in component tests. Implements
 * ControlValueAccessor so Angular's NgModel binding resolves cleanly,
 * and accepts the same input shape (`options`, `height`) so the template
 * does not need to change.
 *
 * Keeping the real NuMonacoEditor out of the test graph eliminates the
 * AMD-based script loader it kicks off during `ngAfterViewInit`. That
 * loader's async subscription was registering an `onDestroy` hook on
 * already-destroyed DestroyRefs when fixtures tore down before the load
 * completed (NG0911), bleeding into unrelated specs.
 */
@Component({
  selector: 'nu-monaco-editor',
  standalone: true,
  template: '<textarea data-test="stub-monaco"></textarea>',
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => StubMonacoEditorComponent),
      multi: true,
    },
  ],
})
class StubMonacoEditorComponent implements ControlValueAccessor {
  @Input() options?: Record<string, unknown>;
  @Input() height?: string;
  // Mirror the real component's `(event)` output so the Monaco capture path
  // (`onEditorEvent`) is reachable from the template binding.
  @Output() event = new EventEmitter<NuMonacoEditorEvent>();
  writeValue(_value: string): void {}
  registerOnChange(_fn: (value: string) => void): void {}
  registerOnTouched(_fn: () => void): void {}
  setDisabledState?(_isDisabled: boolean): void {}
}

/**
 * A hand-rolled Monaco editor double whose `addAction` records the descriptors
 * passed to it. Tests drive `component.onEditorEvent({ type: 'init', editor })`
 * with one of these, then locate a descriptor by id and invoke its `run()` to
 * exercise the Monaco capture path.
 */
interface RecordedAction {
  id: string;
  label: string;
  keybindings?: number[];
  run: (editor: unknown, ...args: unknown[]) => void | Promise<void>;
}

function makeEditorStub(): {
  editor: unknown;
  actions: RecordedAction[];
} {
  const actions: RecordedAction[] = [];
  const editor = {
    addAction(descriptor: RecordedAction): { dispose(): void } {
      actions.push(descriptor);
      return { dispose(): void {} };
    },
  };
  return { editor, actions };
}

function makeHttpError(status: number, body: unknown = ''): HttpError {
  return new HttpError('boom', status, body);
}

/**
 * Drain the microtask queue a few times so an awaited async chain (e.g. the
 * Save drift re-export resolving before `confirmationService.confirm` fires)
 * settles before assertions read its side effects.
 */
async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await Promise.resolve();
  }
}

describe('NamespacePanelComponent', () => {
  let fixture: ComponentFixture<NamespacePanelComponent>;
  let component: NamespacePanelComponent;
  let apiSpy: jasmine.SpyObj<ApiService>;
  let messageSpy: jasmine.SpyObj<MessageService>;
  // Minimal AuthService stub exposing only a settable `currentUserValue`
  // (the sole surface the panel's advisory owner pre-check reads). Defaults
  // to the anonymous user so Save tests whose buffers carry `user_id: null`
  // (unresolvable owner → defer to server) are unaffected.
  let authStub: { currentUserValue: { user_id: string; roles?: string[] } };

  // The real Monaco library (and its `window.monaco` global carrying
  // `KeyMod`/`KeyCode`) is NOT loaded in Karma (the AMD loader is stubbed out
  // via StubMonacoEditorComponent). The component's `registerEditorShortcuts`
  // reads `monaco.KeyMod`/`monaco.KeyCode` when it builds the chord
  // keybindings, so install a minimal runtime stub. Values are
  // arbitrary-but-stable distinct bits — the assertions only require that the
  // production code and the test compute the SAME chord number from the SAME
  // stub (the real bit values live in the browser-loaded library).
  let prevMonaco: unknown;
  beforeAll(() => {
    const w = globalThis as unknown as { monaco?: unknown };
    prevMonaco = w.monaco;
    if (w.monaco === undefined) {
      w.monaco = {
        KeyMod: { Alt: 1 << 9, Shift: 1 << 10, CtrlCmd: 1 << 11, WinCtrl: 1 << 8 },
        KeyCode: { KeyS: 49, KeyV: 52, KeyR: 48, KeyC: 33, KeyD: 34 },
      };
    }
  });
  afterAll(() => {
    (globalThis as unknown as { monaco?: unknown }).monaco = prevMonaco;
  });

  async function buildFixture(namespace: string) {
    fixture = TestBed.createComponent(NamespacePanelComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('namespace', namespace);
  }

  beforeEach(async () => {
    apiSpy = jasmine.createSpyObj('ApiService', [
      'exportNamespace',
      'importNamespace',
      'validateNamespaceBuffer',
      'validatePersistedNamespace',
      'deleteNamespace',
    ]);
    messageSpy = jasmine.createSpyObj('MessageService', ['add']);
    authStub = { currentUserValue: { user_id: 'anonymous' } };

    await TestBed.configureTestingModule({
      imports: [NamespacePanelComponent, NoopAnimationsModule],
      providers: [
        { provide: ApiService, useValue: apiSpy },
        { provide: MessageService, useValue: messageSpy },
        { provide: AuthService, useValue: authStub },
      ],
    })
      // Swap the real NuMonacoEditor for a lightweight stub that honours
      // the same template surface (selector + `options` + `ControlValueAccessor`)
      // but does not kick off Monaco's AMD loader — which would otherwise leak
      // late `onDestroy` registrations across tests (NG0911).
      //
      // The panel does not use `ConfirmationService` / `<p-confirmDialog>`; the
      // confirm flows run through the panel-owned custom modal driven by
      // component state. Confirm assertions read `confirmDialogVisible` /
      // `confirmRequest` and exercise the modal's button handlers directly.
      .overrideComponent(NamespacePanelComponent, {
        set: {
          imports: [
            CommonModule,
            FormsModule,
            ButtonModule,
            DialogModule,
            InputTextModule,
            ToggleSwitchModule,
            TooltipModule,
            StubMonacoEditorComponent,
            ValidationReportComponent,
          ],
        },
      })
      .compileComponents();
  });

  // ---------------------------------------------------------------------
  // Custom confirmation modal helpers. A confirm is "open" when
  // `confirmDialogVisible` is true and `confirmRequest` carries the flow's
  // header/message/variant; Proceed / Cancel / Reload / Overwrite are
  // exercised via the component's button handlers.
  // ---------------------------------------------------------------------

  /** True iff the custom confirmation modal is open. */
  function confirmOpen(): boolean {
    return component.confirmDialogVisible && component.confirmRequest !== null;
  }

  /** Run the Proceed (reset/delete) button effect, then close. */
  function clickConfirmProceed(): void {
    component.onConfirmProceedClick();
  }

  /** Run the Cancel (reset/delete) button effect, then close. */
  function clickConfirmCancel(): void {
    component.onConfirmCancelClick();
  }

  /** Run the Reload (drift safe-default) button effect, then close. */
  function clickConfirmReload(): void {
    component.onConfirmReloadClick();
  }

  /** Run the Overwrite (drift destructive) button effect, then close. */
  function clickConfirmOverwrite(): void {
    component.onConfirmOverwriteClick();
  }

  // ---------------------------------------------------------------------
  // Shared load helper — drives ngOnInit → loadNamespace → stable view.
  // The panel lands clean (buffer === serverYaml) and always editable.
  // ---------------------------------------------------------------------

  async function loaded(yaml = 'foo: 1\n'): Promise<void> {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve(yaml));
    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  // ---------------------------------------------------------------------
  // Load / view tests (no `mode` axis).
  // ---------------------------------------------------------------------

  it('(AC13) load flow (happy path) — populates serverYaml/buffer and clears loading', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges(); // triggers ngOnInit
    await fixture.whenStable();
    fixture.detectChanges();

    // exportNamespace carries the admin "show all" flag ({ all: showAll });
    // default showAll=false ⇒ owner-scoped read.
    expect(apiSpy.exportNamespace).toHaveBeenCalledOnceWith('foo', {
      all: false,
    });
    expect(component.serverYaml).toBe('key: value\n');
    expect(component.buffer).toBe('key: value\n');
    expect(component.lastValidation).toBeNull();
    expect(component.loading).toBe(false);
    // Lands clean — Save/Reset disabled, Clone enabled.
    expect(component.hasUnsavedChanges()).toBe(false);
  });

  it('(14.4 AC8, AC17) admin foreign-open — showAll=true threads all:true into the entry read', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foreign-ns');
    fixture.componentRef.setInput('showAll', true);
    fixture.detectChanges(); // triggers ngOnInit / loadNamespace
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiSpy.exportNamespace).toHaveBeenCalledOnceWith('foreign-ns', {
      all: true,
    });
    expect(component.serverYaml).toBe('key: value\n');
  });

  it('(14.4 AC8) toggle off — showAll=false keeps the normal owner-scoped entry read', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('owned-ns');
    fixture.componentRef.setInput('showAll', false);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiSpy.exportNamespace).toHaveBeenCalledOnceWith('owned-ns', {
      all: false,
    });
  });

  it('(AC13) load flow (error) — leaves buffers empty, toasts, and renders empty state', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.reject(new Error('boom')));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.serverYaml).toBe('');
    expect(component.buffer).toBe('');
    expect(component.loading).toBe(false);
    expect(messageSpy.add).toHaveBeenCalledTimes(1);

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Unable to load');
  });

  it('(AC13) input change — re-fetches and resets state for the new namespace', async () => {
    let resolveFirst!: (value: string) => void;
    let resolveSecond!: (value: string) => void;

    apiSpy.exportNamespace.and.callFake((ns: string) => {
      if (ns === 'foo') {
        return new Promise<string>((r) => {
          resolveFirst = r;
        });
      }
      return new Promise<string>((r) => {
        resolveSecond = r;
      });
    });

    await buildFixture('foo');
    fixture.detectChanges();
    resolveFirst('foo: yaml\n');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.serverYaml).toBe('foo: yaml\n');

    fixture.componentRef.setInput('namespace', 'bar');
    fixture.detectChanges();

    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(2);
    expect(apiSpy.exportNamespace.calls.mostRecent().args).toEqual([
      'bar',
      { all: false },
    ]);
    expect(component.loading).toBe(true);
    expect(component.serverYaml).toBe('');
    expect(component.buffer).toBe('');

    resolveSecond('bar: yaml\n');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.serverYaml).toBe('bar: yaml\n');
    expect(component.buffer).toBe('bar: yaml\n');
    expect(component.loading).toBe(false);
  });

  // ---------------------------------------------------------------------
  // Monaco always writable; editorOptions stable constant (ADR-017).
  // ---------------------------------------------------------------------

  it('(22.1 AC1/AC2) editorOptions.readOnly === false and the object preserves theme/language/automaticLayout', async () => {
    await loaded('foo: 1\n');

    const options = component.editorOptions;
    expect(options['readOnly']).toBe(false);
    expect(options['language']).toBe('yaml');
    expect(options['automaticLayout']).toBe(true);
    expect(options['theme']).toBe('vs');
  });

  it('(22.1 AC1) editorOptions is a single stable object reference — never reassigned', async () => {
    await loaded('foo: 1\n');
    const first = component.editorOptions;

    // Dirtying the buffer (no Edit click exists) does not churn the reference.
    component.buffer = 'foo: 2\n';
    fixture.detectChanges();
    expect(component.editorOptions).toBe(first);

    // Saving and resetting also leave the reference untouched.
    component.onResetClick();
    fixture.detectChanges();
    expect(component.editorOptions).toBe(first);
  });

  it('(AC13) public surface — namespace @Input, closed/saved @Output, hasUnsavedChanges', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve(''));
    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();

    expect(component.namespace).toBe('foo');
    expect(typeof component.closed.emit).toBe('function');
    expect(typeof component.saved.emit).toBe('function');
    expect(typeof component.hasUnsavedChanges).toBe('function');
  });

  it('(AC12) destroy safety (load) — late-resolving export does not write to state', async () => {
    let resolveLate!: (value: string) => void;
    apiSpy.exportNamespace.and.returnValue(
      new Promise<string>((r) => {
        resolveLate = r;
      }),
    );

    await buildFixture('foo');
    fixture.detectChanges();
    expect(component.loading).toBe(true);

    const consoleErrorSpy = spyOn(console, 'error');
    fixture.destroy();

    resolveLate('late: yaml\n');
    await Promise.resolve();
    await Promise.resolve();

    expect(component.serverYaml).toBe('');
    expect(component.buffer).toBe('');
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it('(AC7 NFR6) no host-specific types referenced on the component instance', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve(''));
    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();

    expect((component as unknown as { router?: unknown })?.router).toBeUndefined();
    expect(
      (component as unknown as { activatedRoute?: unknown })?.activatedRoute,
    ).toBeUndefined();
  });

  // ---------------------------------------------------------------------
  // hasUnsavedChanges() collapse (ADR-017).
  // ---------------------------------------------------------------------

  it('(22.1 AC4/AC5) hasUnsavedChanges() === (buffer !== serverYaml) — no mode axis', async () => {
    await loaded('foo: 1\n');

    // Clean buffer.
    expect(component.buffer).toBe(component.serverYaml);
    expect(component.hasUnsavedChanges()).toBe(false);

    // Dirty buffer — true with no Edit click (the editor is always writable).
    component.buffer = 'foo: 2\n';
    expect(component.hasUnsavedChanges()).toBe(true);

    // Back to clean.
    component.buffer = component.serverYaml;
    expect(component.hasUnsavedChanges()).toBe(false);
  });

  // ---------------------------------------------------------------------
  // Action row renders five buttons whenever there is content (ADR-017).
  // ---------------------------------------------------------------------

  it('(22.1 AC3/AC6) all five action buttons render whenever the panel has content (no per-mode *ngIf)', async () => {
    await loaded('foo: 1\n');

    const row = fixture.nativeElement as HTMLElement;
    expect(row.querySelector('button[data-test="validate-btn"]')).not.toBeNull();
    expect(row.querySelector('button[data-test="save-btn"]')).not.toBeNull();
    expect(row.querySelector('button[data-test="reset-btn"]')).not.toBeNull();
    expect(row.querySelector('button[data-test="clone-btn"]')).not.toBeNull();
    expect(
      row.querySelector('button[data-test="delete-ns-btn"]'),
    ).not.toBeNull();

    // The edit/cancel/per-mode validate buttons are not rendered.
    expect(row.querySelector('button[data-test="edit-btn"]')).toBeNull();
    expect(row.querySelector('button[data-test="cancel-btn"]')).toBeNull();
    expect(
      row.querySelector('button[data-test="validate-persisted-btn"]'),
    ).toBeNull();
    expect(
      row.querySelector('button[data-test="validate-buffer-btn"]'),
    ).toBeNull();
  });

  it('(22.1 AC3) action row + editor are hidden while loading and in the empty state', async () => {
    // Loading: pending export keeps loading true.
    apiSpy.exportNamespace.and.returnValue(new Promise<string>(() => {}));
    await buildFixture('foo');
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('button[data-test="save-btn"]'),
    ).toBeNull();
    fixture.destroy();

    // Empty / error state: serverYaml === ''.
    apiSpy.exportNamespace.and.returnValue(Promise.reject(new Error('boom')));
    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('button[data-test="save-btn"]'),
    ).toBeNull();
  });

  // ----- Save enablement -----

  it('(22.1 AC7) Save disabled when clean, enabled when dirty', async () => {
    await loaded('foo: 1\n');

    let saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeTrue();

    component.buffer = 'foo: 2\n';
    fixture.detectChanges();
    saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeFalse();
  });

  it('(22.1 AC7) Save disabled while saving and while FR14-gated even when dirty', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    component.saving = true;
    fixture.detectChanges();
    let saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeTrue();

    component.saving = false;
    component.lastValidation = failingReport(1);
    fixture.detectChanges();
    saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeTrue();
  });

  // ----- Validate enablement -----

  it('(22.1 AC8) Validate enabled in both clean and dirty states, and never gated by FR14', async () => {
    await loaded('foo: 1\n');

    // Clean — enabled.
    let validateBtn = fixture.nativeElement.querySelector(
      'button[data-test="validate-btn"]',
    ) as HTMLButtonElement;
    expect(validateBtn.disabled).toBeFalse();

    // Dirty AND FR14-gated (a known-bad report is pending) — STILL enabled.
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(2);
    component.rawSaveError = 'raw';
    expect(component.isSaveGated).toBeTrue();
    fixture.detectChanges();

    validateBtn = fixture.nativeElement.querySelector(
      'button[data-test="validate-btn"]',
    ) as HTMLButtonElement;
    expect(validateBtn.disabled).toBeFalse();

    // Only an in-flight validating / saving disables it.
    component.validating = true;
    fixture.detectChanges();
    validateBtn = fixture.nativeElement.querySelector(
      'button[data-test="validate-btn"]',
    ) as HTMLButtonElement;
    expect(validateBtn.disabled).toBeTrue();
  });

  // ----- Reset enablement -----

  it('(22.1 AC9) Reset disabled when clean, enabled when dirty, disabled while saving', async () => {
    await loaded('foo: 1\n');

    let resetBtn = fixture.nativeElement.querySelector(
      'button[data-test="reset-btn"]',
    ) as HTMLButtonElement;
    expect(resetBtn.disabled).toBeTrue();

    component.buffer = 'foo: 2\n';
    fixture.detectChanges();
    resetBtn = fixture.nativeElement.querySelector(
      'button[data-test="reset-btn"]',
    ) as HTMLButtonElement;
    expect(resetBtn.disabled).toBeFalse();

    component.saving = true;
    fixture.detectChanges();
    resetBtn = fixture.nativeElement.querySelector(
      'button[data-test="reset-btn"]',
    ) as HTMLButtonElement;
    expect(resetBtn.disabled).toBeTrue();
  });

  // ----- Clone enablement -----

  it('(22.1 AC10) Clone enabled when clean, disabled when dirty', async () => {
    await loaded('foo: 1\n');

    let cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(cloneBtn.disabled).toBeFalse();

    component.buffer = 'foo: 2\n';
    fixture.detectChanges();
    cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(cloneBtn.disabled).toBeTrue();
  });

  it('(22.1 AC10) Clone disabled while cloning and while FR14-gated (clean state)', async () => {
    await loaded('foo: 1\n');

    component.cloning = true;
    fixture.detectChanges();
    let cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(cloneBtn.disabled).toBeTrue();

    component.cloning = false;
    component.lastValidation = failingReport(1);
    fixture.detectChanges();
    cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(cloneBtn.disabled).toBeTrue();
  });

  // ----- Delete enablement -----

  it('(22.1 AC11) Delete enabled by default, disabled only while deleting', async () => {
    await loaded('foo: 1\n');

    let deleteBtn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    ) as HTMLButtonElement;
    expect(deleteBtn.disabled).toBeFalse();

    component.deleting = true;
    fixture.detectChanges();
    deleteBtn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    ) as HTMLButtonElement;
    expect(deleteBtn.disabled).toBeTrue();
  });

  // ---------------------------------------------------------------------
  // Save flow (gate, drift check, success, errors) (ADR-017).
  // ---------------------------------------------------------------------

  it('(22.1 AC12) onSaveClick is a no-op when clean (gated on hasUnsavedChanges)', async () => {
    await loaded('foo: 1\n');
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
  });

  it('(22.1 AC12) Save posts buffer directly to importNamespace — validateNamespaceBuffer NOT called', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    // Drift re-export returns the same server YAML → no prompt.
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));

    await component.onSaveClick();

    expect(apiSpy.validateNamespaceBuffer).not.toHaveBeenCalled();
    expect(apiSpy.importNamespace).toHaveBeenCalledOnceWith('foo: 2\n');
  });

  it('(22.1 AC13) Save 2xx — serverYaml updates, saved.emit + success toast + a11y, panel becomes clean', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    const savedEmit = spyOn(component.saved, 'emit');

    await component.onSaveClick();

    expect(component.serverYaml).toBe('foo: 2\n');
    expect(component.saving).toBe(false);
    expect(component.hasUnsavedChanges()).toBe(false);
    expect(component.a11yAnnouncement).toBe('Namespace saved');
    expect(savedEmit).toHaveBeenCalledTimes(1);
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('success');

    // Now clean → Save/Reset disabled, Clone enabled.
    fixture.detectChanges();
    const saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    const cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeTrue();
    expect(cloneBtn.disabled).toBeFalse();
  });

  it('(22.1 AC13) Save 422 structured — lastValidation populated, buffer preserved', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: false,
      global_errors: ['bad: missing field'],
      entry_issues: [],
    };
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, report)),
    );

    await component.onSaveClick();

    expect(component.buffer).toBe('foo: 2\n');
    expect(component.saving).toBe(false);
    expect(component.lastValidation).toEqual(report);
    expect(component.rawSaveError).toBeNull();
  });

  it('(22.1 AC13) Save 422 unstructured — rawSaveError populated, lastValidation null', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    const rawBody = { detail: [{ msg: 'field required', loc: ['body'] }] };
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, rawBody)),
    );

    await component.onSaveClick();

    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toContain('field required');
    expect(component.buffer).toBe('foo: 2\n');
  });

  it('(22.1 AC12) Save 5xx — sticky toast; re-clicking Save retries with the same buffer', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(Promise.reject(makeHttpError(500)));

    await component.onSaveClick();

    expect(component.buffer).toBe('foo: 2\n');
    expect(component.saving).toBe(false);
    const errorToast = messageSpy.add.calls.mostRecent().args[0];
    expect(errorToast.severity).toBe('error');
    expect(errorToast.sticky).toBeTrue();

    // No dedicated Retry button renders.
    fixture.detectChanges();
    const retryBtn = fixture.nativeElement.querySelector(
      'button[data-test="retry-save-btn"]',
    ) as HTMLButtonElement | null;
    expect(retryBtn).toBeNull();

    // Save still enabled (dirty, not saving).
    const saveBtn = fixture.nativeElement.querySelector(
      'button[data-test="save-btn"]',
    ) as HTMLButtonElement;
    expect(saveBtn.disabled).toBeFalse();

    // Second click — 2xx — retries with the same buffer.
    apiSpy.importNamespace.calls.reset();
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    await component.onSaveClick();
    expect(apiSpy.importNamespace).toHaveBeenCalledOnceWith('foo: 2\n');
  });

  it('(22.1 AC12) Save 500 does NOT auto-invoke importNamespace a second time', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(Promise.reject(makeHttpError(500)));

    await component.onSaveClick();
    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
  });

  it('(22.1 AC9) Save 401 — no panel toast, buffer preserved, saving resets', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(Promise.reject(makeHttpError(401)));

    await component.onSaveClick();

    expect(component.buffer).toBe('foo: 2\n');
    expect(component.saving).toBe(false);
    expect(messageSpy.add).not.toHaveBeenCalled();
  });

  // ----- Save buffer-namespace guard -----

  it('(22.1 AC12) Save refuses when buffer namespace has been edited — error toast, no import, no drift export', async () => {
    await loaded('namespace: foo\nuser_id: null\nentries: {}\n');
    component.buffer = 'namespace: bar\nuser_id: null\nentries: {}\n';
    apiSpy.exportNamespace.calls.reset();

    await component.onSaveClick();

    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    // The guard fires before the drift re-export.
    expect(apiSpy.exportNamespace).not.toHaveBeenCalled();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
    expect(toast.sticky).toBeTrue();
    expect(toast.detail).toContain('Clone');
  });

  it('(22.1 AC12) Save proceeds when the buffer namespace matches (guard is a no-op)', async () => {
    await loaded('namespace: foo\nuser_id: null\nentries: {}\n');
    component.buffer = 'namespace: foo\nuser_id: null\nentries:\n  x: 1\n';
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: foo\nuser_id: null\nentries: {}\n'),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
  });

  it('(22.1 AC12) Save falls through the guard when the buffer is unparseable — server decides', async () => {
    await loaded('namespace: foo\nuser_id: null\nentries: {}\n');
    component.buffer = 'namespace: foo\nentries: [\n  unclosed\n';
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: foo\nuser_id: null\nentries: {}\n'),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
  });

  // ----- Advisory owner-or-admin pre-flight -----

  it('(22.1 AC12 / 14.3) owner can Save — import fires, no owner-block toast', async () => {
    authStub.currentUserValue = { user_id: 'alice', roles: [] };
    await loaded('namespace: foo\nuser_id: alice\nentries: {}\n');
    component.buffer = 'namespace: foo\nuser_id: alice\nentries:\n  x: 1\n';
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: foo\nuser_id: alice\nentries: {}\n'),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    const ownerBlocked = messageSpy.add.calls
      .allArgs()
      .some((args) => args[0]?.summary === 'Cannot save changes to this namespace');
    expect(ownerBlocked).toBeFalse();
  });

  it('(22.1 AC12 / 14.3) non-owner non-admin is blocked — no import, sticky toast', async () => {
    authStub.currentUserValue = { user_id: 'bob', roles: [] };
    await loaded('namespace: foo\nuser_id: alice\nentries: {}\n');
    component.buffer = 'namespace: foo\nuser_id: alice\nentries:\n  x: 1\n';
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
    expect(toast.sticky).toBeTrue();
    expect(toast.summary).toBe('Cannot save changes to this namespace');
    expect(component.saving).toBeFalse();
  });

  it('(22.1 AC12 / 14.3) admin can Save another user\'s namespace — import fires', async () => {
    authStub.currentUserValue = { user_id: 'bob', roles: ['admin'] };
    await loaded('namespace: foo\nuser_id: alice\nentries: {}\n');
    component.buffer = 'namespace: foo\nuser_id: alice\nentries:\n  x: 1\n';
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: foo\nuser_id: alice\nentries: {}\n'),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
  });

  it('(22.1 AC12 / 14.3) namespace-change guard still wins over ownership guard', async () => {
    authStub.currentUserValue = { user_id: 'bob', roles: [] };
    await loaded('namespace: foo\nuser_id: alice\nentries: {}\n');
    component.buffer = 'namespace: bar\nuser_id: alice\nentries: {}\n';

    await component.onSaveClick();

    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.summary).toBe('Cannot change namespace on Save');
  });

  // ----- Save drift check -----

  it('(22.1 AC20/AC21) Save with a matching server export imports without a drift prompt', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    // Drift re-export ran (one extra export beyond the mount load) but no
    // confirm modal was shown.
    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(2);
    expect(confirmOpen()).toBeFalse();
    expect(apiSpy.importNamespace).toHaveBeenCalledOnceWith('foo: 2\n');
  });

  it('(22.3 AC7/AC8) Save with diverged server export opens the drift modal with Reload + Overwrite; Reload skips the import and rebases', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    // Server has drifted since load.
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    const savePromise = component.onSaveClick();
    // Let the drift re-export resolve so the modal opens.
    await flushMicrotasks();
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('drift');
    expect(component.confirmRequest!.header).toBe('Namespace modified');

    // Reload = reload-and-rebase (safe / focused default).
    clickConfirmReload();
    await savePromise;

    // Modal closed; import was NOT performed this click.
    expect(confirmOpen()).toBeFalse();
    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    // serverYaml rebased to the latest; buffer kept the operator's edit
    // (it had diverged from the old serverYaml, so it is preserved).
    expect(component.serverYaml).toBe('foo: server\n');
    expect(component.buffer).toBe('foo: 2\n');
    expect(component.saving).toBeFalse();
  });

  it('(22.3 AC7) drift Reload moves the buffer when it still equalled the old serverYaml', async () => {
    await loaded('foo: 1\n');
    // Force a dirty gate so onSaveClick runs, but keep buffer === old
    // serverYaml at the moment the rebase fires by reverting after dirtying.
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));

    const savePromise = component.onSaveClick();
    await flushMicrotasks();
    expect(component.confirmRequest!.variant).toBe('drift');
    // Simulate buffer back at the old serverYaml right before Reload.
    component.buffer = 'foo: 1\n';
    clickConfirmReload();
    await savePromise;

    expect(component.serverYaml).toBe('foo: server\n');
    // buffer equalled the OLD serverYaml → moved to the fresh server YAML.
    expect(component.buffer).toBe('foo: server\n');
  });

  it('(22.3 AC7) Save with diverged server export — Overwrite proceeds with the operator buffer', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    const savePromise = component.onSaveClick();
    await flushMicrotasks();
    expect(component.confirmRequest!.variant).toBe('drift');
    // Overwrite → import proceeds with the operator's buffer.
    clickConfirmOverwrite();
    await savePromise;

    expect(apiSpy.importNamespace).toHaveBeenCalledOnceWith('foo: 2\n');
    expect(component.serverYaml).toBe('foo: 2\n');
  });

  it('(22.3 AC8) drift modal dismissal (onHide) resolves the safe branch — no import this click', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    const savePromise = component.onSaveClick();
    await flushMicrotasks();
    expect(component.confirmRequest!.variant).toBe('drift');

    // Esc / Cancel / X — the dialog (onHide) fires without a button choice.
    component.onConfirmDialogHide();
    await savePromise;

    // Safe branch: no blind overwrite, import skipped this click.
    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    expect(confirmOpen()).toBeFalse();
    expect(component.saving).toBeFalse();
  });

  it('(22.1 AC21) drift export network error falls through to import (non-blocking)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.reject(new Error('net')));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(confirmOpen()).toBeFalse();
    expect(apiSpy.importNamespace).toHaveBeenCalledOnceWith('foo: 2\n');
  });

  // ----- Destroy-during-save -----

  it('(22.1) destroy-during-save — late-resolving import does not write state', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    let resolveLate!: (value: unknown) => void;
    apiSpy.importNamespace.and.returnValue(
      new Promise<unknown>((r) => {
        resolveLate = r;
      }) as unknown as Promise<never>,
    );

    const consoleErrorSpy = spyOn(console, 'error');
    const savePromise = component.onSaveClick();
    // Drain the drift re-export microtask so importNamespace is reached.
    await Promise.resolve();
    await Promise.resolve();
    expect(component.saving).toBe(true);

    fixture.destroy();

    resolveLate([]);
    await savePromise;
    await Promise.resolve();

    expect(component.serverYaml).toBe('foo: 1\n');
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // Reset is the only undo (ADR-017).
  // ---------------------------------------------------------------------

  it('(22.1 AC14) onCancelClick is removed from the component', async () => {
    await loaded('foo: 1\n');
    expect(
      (component as unknown as { onCancelClick?: unknown }).onCancelClick,
    ).toBeUndefined();
  });

  it('(22.1 AC15 / 22.3 AC4) onResetClick reverts behind the custom confirm, clearing validation state', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);
    component.rawSaveError = 'raw';

    component.onResetClick();
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('reset');
    expect(component.confirmRequest!.message).toContain('Discard unsaved changes');

    clickConfirmProceed();
    expect(confirmOpen()).toBeFalse();
    expect(component.buffer).toBe('foo: 1\n');
    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toBeNull();
    expect(component.hasUnsavedChanges()).toBe(false);
  });

  it('(22.3 AC4) Reset Cancel leaves all state untouched', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);
    component.rawSaveError = 'raw';

    component.onResetClick();
    expect(confirmOpen()).toBeTrue();

    clickConfirmCancel();
    expect(confirmOpen()).toBeFalse();
    // Cancel runs no revert — buffer + validation state are unchanged.
    expect(component.buffer).toBe('foo: 2\n');
    expect(component.lastValidation).not.toBeNull();
    expect(component.rawSaveError).toBe('raw');
    expect(component.hasUnsavedChanges()).toBe(true);
  });

  it('(22.1 AC15) onResetClick is a no-op when clean (no confirm)', async () => {
    await loaded('foo: 1\n');

    component.onResetClick();

    expect(confirmOpen()).toBeFalse();
  });

  // ---------------------------------------------------------------------
  // Single Validate handler (ADR-017).
  // ---------------------------------------------------------------------

  function cleanReport(namespace = 'foo'): NamespaceValidationReport {
    return {
      namespace,
      ok: true,
      global_errors: [],
      entry_issues: [],
    };
  }

  it('(22.1 AC17) onValidatePersistedClick is removed; onValidateBufferClick is the single handler', async () => {
    await loaded('foo: 1\n');
    expect(
      (component as unknown as { onValidatePersistedClick?: unknown })
        .onValidatePersistedClick,
    ).toBeUndefined();
    expect(typeof component.onValidateBufferClick).toBe('function');
  });

  it('(22.1 AC17) Validate validates the buffer via validateNamespaceBuffer; clean state equals persisted', async () => {
    await loaded('foo: 1\n');
    // Clean: buffer === serverYaml, so a buffer-validate equals a
    // persisted-validate.
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(cleanReport()),
    );

    await component.onValidateBufferClick();

    expect(apiSpy.validateNamespaceBuffer).toHaveBeenCalledOnceWith('foo: 1\n');
    expect(apiSpy.validatePersistedNamespace).not.toHaveBeenCalled();
  });

  it('(22.1 AC17) Validate passes snapshot-at-click buffer; live edits post-click do NOT change args', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    let resolveValidate!: (v: NamespaceValidationReport) => void;
    apiSpy.validateNamespaceBuffer.and.returnValue(
      new Promise<NamespaceValidationReport>((r) => {
        resolveValidate = r;
      }),
    );

    const validatePromise = component.onValidateBufferClick();
    component.buffer = 'foo: 99\n';
    resolveValidate(cleanReport());
    await validatePromise;

    expect(apiSpy.validateNamespaceBuffer).toHaveBeenCalledOnceWith('foo: 2\n');
    expect(component.lastValidation).toBeNull();
    expect(component.validationFlashBuffer).toBeTrue();
    expect(component.buffer).toBe('foo: 99\n');
  });

  it('(22.1 AC18) clean report flashes the Validate button green and announces; pane stays hidden', async () => {
    await loaded('foo: 1\n');
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(cleanReport()),
    );

    await component.onValidateBufferClick();
    fixture.detectChanges();

    expect(component.lastValidation).toBeNull();
    expect(component.validationFlashBuffer).toBeTrue();
    expect(component.a11yAnnouncement).toBe('Validation passed');
    const validateBtn = fixture.nativeElement.querySelector(
      'button[data-test="validate-btn"]',
    ) as HTMLButtonElement;
    expect(validateBtn.classList.contains('p-button-success')).toBeTrue();
  });

  it('(22.1 AC18) non-clean report populates lastValidation (findings pane renders)', async () => {
    await loaded('foo: 1\n');
    const report = failingReport(1, 0);
    apiSpy.validateNamespaceBuffer.and.returnValue(Promise.resolve(report));

    await component.onValidateBufferClick();

    expect(component.lastValidation).toEqual(report);
    expect(component.validationFlashBuffer).toBeFalse();
  });

  it('(22.1 AC18) clean-report flash auto-reverts after 2500ms', fakeAsync(() => {
    void buildFixture('foo');
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(cleanReport()),
    );
    fixture.detectChanges();
    tick();

    void component.onValidateBufferClick();
    tick();
    expect(component.validationFlashBuffer).toBeTrue();
    tick(1000);
    expect(component.validationFlashBuffer).toBeTrue();
    tick(1600);
    expect(component.validationFlashBuffer).toBeFalse();
  }));

  it('(22.1 AC18) editing the buffer after a clean Validate clears the flash immediately', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(cleanReport()),
    );

    await component.onValidateBufferClick();
    expect(component.validationFlashBuffer).toBeTrue();

    component.onBufferChange('foo: 2\n# edited\n');

    expect(component.buffer).toBe('foo: 2\n# edited\n');
    expect(component.validationFlashBuffer).toBeFalse();
  });

  it('(22.1 AC17) Validate does NOT mutate serverYaml/buffer or call importNamespace', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(cleanReport()),
    );

    await component.onValidateBufferClick();

    expect(component.buffer).toBe('foo: 2\n');
    expect(component.serverYaml).toBe('foo: 1\n');
    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    expect(component.rawSaveError).toBeNull();
  });

  it('(22.1 AC18) Validate 422 structured → findings pane, no toast', async () => {
    await loaded('foo: 1\n');
    const report = failingReport(1, 0);
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.reject(makeHttpError(422, report)),
    );

    await component.onValidateBufferClick();

    expect(component.lastValidation).toEqual(report);
    expect(messageSpy.add).not.toHaveBeenCalled();
  });

  it('(22.1 AC18) Validate 422 non-report → generic toast', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'garbage: [\n';
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.reject(makeHttpError(422, 'yaml parse error at line 5')),
    );

    await component.onValidateBufferClick();

    expect(component.lastValidation).toBeNull();
    expect(messageSpy.add).toHaveBeenCalled();
  });

  it('(22.1 AC18) Validate resolved to undefined → clear toast, not TypeError', async () => {
    await loaded('foo: 1\n');
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(undefined as unknown as NamespaceValidationReport),
    );

    await component.onValidateBufferClick();

    const call = messageSpy.add.calls.mostRecent().args[0];
    expect(call.summary).toBe('Validation failed');
    expect(call.detail).not.toContain("reading 'ok'");
  });

  it('(22.1 AC18) Validate 5xx — error toast, lastValidation unchanged, validating resets', async () => {
    await loaded('foo: 1\n');
    const prior = cleanReport();
    component.lastValidation = prior;
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.reject(makeHttpError(500)),
    );

    await component.onValidateBufferClick();

    expect(component.lastValidation).toBe(prior);
    expect(component.validating).toBe(false);
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
    expect(toast.summary).toBe('Validation failed');
    expect(toast.sticky).toBeFalsy();
  });

  it('(22.1 AC18) Validate 401 — no panel toast, state unchanged, validating resets', async () => {
    await loaded('foo: 1\n');
    const prior = cleanReport();
    component.lastValidation = prior;
    messageSpy.add.calls.reset();
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.reject(makeHttpError(401)),
    );

    await component.onValidateBufferClick();

    expect(component.lastValidation).toBe(prior);
    expect(component.validating).toBe(false);
    expect(messageSpy.add).not.toHaveBeenCalled();
  });

  it('(22.1) destroy-during-validate — late-resolving validate does not write state', async () => {
    await loaded('foo: 1\n');
    let resolveLate!: (v: NamespaceValidationReport) => void;
    apiSpy.validateNamespaceBuffer.and.returnValue(
      new Promise<NamespaceValidationReport>((r) => {
        resolveLate = r;
      }),
    );

    const consoleErrorSpy = spyOn(console, 'error');
    const validatePromise = component.onValidateBufferClick();
    expect(component.validating).toBe(true);

    fixture.destroy();

    resolveLate(failingReport(1));
    await validatePromise;
    await Promise.resolve();

    expect(component.lastValidation).toBeNull();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  // ----- onClearValidationClick nulls both validation fields -----

  it('(11.4 AC8) onClearValidationClick nulls lastValidation AND rawSaveError', async () => {
    await loaded('foo: 1\n');
    component.lastValidation = cleanReport();
    component.rawSaveError = 'some raw';

    component.onClearValidationClick();

    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toBeNull();
  });

  it('(11.4 AC8) ValidationReportComponent clearRequested output triggers onClearValidationClick', async () => {
    await loaded('foo: 1\n');
    component.lastValidation = cleanReport();
    component.rawSaveError = null;
    fixture.detectChanges();

    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    ) as HTMLButtonElement;
    expect(clearBtn).not.toBeNull();
    clearBtn.click();
    fixture.detectChanges();

    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toBeNull();
  });

  // ---------------------------------------------------------------------
  // Save/Clone gate retained, orthogonal to Validate (ADR-017).
  // ---------------------------------------------------------------------

  /** A non-clean validation report with a known finding count. */
  function failingReport(
    globalCount = 1,
    entryCount = 0,
  ): NamespaceValidationReport {
    return {
      namespace: 'foo',
      ok: false,
      global_errors: Array.from(
        { length: globalCount },
        (_, i) => `global error ${i}`,
      ),
      entry_issues: Array.from({ length: entryCount }, (_, i) => ({
        entry_id: `entry-${i}`,
        kind: 'agent',
        errors: ['bad'],
      })),
    };
  }

  it('(22.1 AC19) isSaveGated / isCloneGated retain their bodies (known-bad report or rawSaveError)', async () => {
    await loaded('foo: 1\n');

    expect(component.isSaveGated).toBeFalse();
    expect(component.isCloneGated).toBeFalse();

    component.lastValidation = failingReport(2, 0);
    expect(component.isSaveGated).toBeTrue();
    expect(component.isCloneGated).toBeTrue();

    component.lastValidation = null;
    component.rawSaveError = 'raw';
    expect(component.isSaveGated).toBeTrue();
    expect(component.isCloneGated).toBeTrue();
  });

  it('(22.1 AC19) onBufferChange clears rawSaveError unconditionally (gate auto-lifts)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = null;
    component.rawSaveError = 'stale raw error';
    expect(component.isSaveGated).toBeTrue();

    component.onBufferChange('foo: 3\n');

    expect(component.rawSaveError).toBeNull();
    expect(component.isSaveGated).toBeFalse();
    expect(component.isCloneGated).toBeFalse();
  });

  it('(22.1 AC19) onClearValidationClick lifts the gate', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);
    component.rawSaveError = 'raw';
    expect(component.isSaveGated).toBeTrue();

    component.onClearValidationClick();

    expect(component.isSaveGated).toBeFalse();
    expect(component.isCloneGated).toBeFalse();
  });

  it('(22.1 AC19) a successful Save (2xx) lifts the gate', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await component.onSaveClick();

    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toBeNull();
    expect(component.serverYaml).toBe('foo: 2\n');
    expect(component.isSaveGated).toBeFalse();
  });

  it('(22.1 AC19) saveTooltip/cloneTooltip describe the gate only when it is the active disable reason', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);

    expect(component.saveTooltip).toBe('Fix validation issues before saving.');

    // Clone tooltip surfaces in a clean state (Clone enabled only when clean).
    component.buffer = component.serverYaml;
    expect(component.cloneTooltip).toBe('Fix validation issues before cloning.');
  });

  it('(22.1 AC19) saveTooltip returns undefined when the gate is not the active disable reason', async () => {
    await loaded('foo: 1\n');
    component.buffer = component.serverYaml; // clean — disabled by dirtiness
    component.lastValidation = null;
    component.rawSaveError = null;

    expect(component.saveTooltip).toBeUndefined();
  });

  // ---------------------------------------------------------------------
  // Clone flow — modal gated on a clean buffer (ADR-017).
  // ---------------------------------------------------------------------

  const cloneSrcYaml = `namespace: src
user_id: null
entries:
  team-1:
    kind: team
    model_type: BaseTeamModel
    payload:
      description: "configured for src namespace"
`;

  async function loadedWithCloneSrc(
    existing: string[] = ['src'],
  ): Promise<void> {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve(cloneSrcYaml));
    await buildFixture('src');
    fixture.componentRef.setInput('existingNamespaces', existing);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  }

  it('(11.5 AC1) Clone button is present in the loaded (clean) state', async () => {
    await loadedWithCloneSrc();
    const btn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBeFalse();
  });

  it('(22.1 AC16) onCloneClick opens the dialog from a clean state without a network call', async () => {
    await loadedWithCloneSrc();
    const bufferBefore = component.buffer;
    const serverYamlBefore = component.serverYaml;
    apiSpy.importNamespace.calls.reset();

    component.onCloneClick();
    fixture.detectChanges();

    expect(component.cloneDialogVisible).toBeTrue();
    expect(component.buffer).toBe(bufferBefore);
    expect(component.serverYaml).toBe(serverYamlBefore);
    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
  });

  it('(22.1 AC16) the outer Clone button is disabled while dirty', async () => {
    await loadedWithCloneSrc();
    component.buffer = cloneSrcYaml + '# dirty\n';
    fixture.detectChanges();

    const btn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    expect(btn.disabled).toBeTrue();
  });

  it('(11.5 AC2) onCloneClick is a no-op when cloning === true', async () => {
    await loadedWithCloneSrc();
    component.cloning = true;
    component.cloneDialogVisible = false;

    component.onCloneClick();

    expect(component.cloneDialogVisible).toBeFalse();
  });

  // ----- Clone modal pre-flight validation -----

  it('(11.5 AC4) Confirm disabled — empty destNs', async () => {
    await loadedWithCloneSrc();
    component.onCloneClick();
    component.cloneDestNs = '';
    fixture.detectChanges();

    expect(component.cloneConfirmDisabled).toBeTrue();
    expect(component.cloneValidationError).toBe(
      'Destination namespace required',
    );
  });

  it('(11.5 AC4) Confirm disabled — destNs equals source namespace', async () => {
    await loadedWithCloneSrc();
    component.onCloneClick();
    component.cloneDestNs = 'src';

    expect(component.cloneConfirmDisabled).toBeTrue();
    expect(component.cloneValidationError).toBe(
      'Destination must differ from source namespace',
    );
  });

  it('(11.5 AC4) Confirm disabled — destNs collides with existingNamespaces', async () => {
    await loadedWithCloneSrc(['src', 'already-there']);
    component.onCloneClick();
    component.cloneDestNs = 'already-there';

    expect(component.cloneConfirmDisabled).toBeTrue();
    expect(component.cloneValidationError).toBe(
      "Namespace 'already-there' already exists",
    );
  });

  it('(11.5 AC4) Confirm disabled while cloning === true', async () => {
    await loadedWithCloneSrc();
    component.onCloneClick();
    component.cloneDestNs = 'dst';
    component.cloning = true;

    expect(component.cloneConfirmDisabled).toBeTrue();
  });

  it('(11.5 AC4) Confirm enabled when all checks pass', async () => {
    await loadedWithCloneSrc();
    component.onCloneClick();
    component.cloneDestNs = 'dst';

    expect(component.cloneConfirmDisabled).toBeFalse();
    expect(component.cloneValidationError).toBeNull();
  });

  // ----- Clone Confirm happy path -----

  it('(11.5 AC8+12+14) Clone Confirm happy path — import then re-export, land on destNs clean', async () => {
    await loadedWithCloneSrc(['src']);
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    const exportedFresh = 'namespace: dst\nuser_id: null\nentries: {}\n';
    apiSpy.exportNamespace.and.callFake((ns: string) =>
      Promise.resolve(ns === 'dst' ? exportedFresh : cloneSrcYaml),
    );
    const savedEmit = spyOn(component.saved, 'emit');

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();
    await fixture.whenStable();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(2);
    expect(apiSpy.exportNamespace.calls.mostRecent().args).toEqual([
      'dst',
      { all: false },
    ]);
    expect(component.namespace).toBe('dst');
    expect(component.hasUnsavedChanges()).toBe(false);
    expect(component.cloneDialogVisible).toBeFalse();
    expect(component.cloneDestNs).toBe('');
    expect(component.cloning).toBeFalse();
    expect(savedEmit).toHaveBeenCalledTimes(1);
    const toasts = messageSpy.add.calls.allArgs().map((a) => a[0]);
    const success = toasts.find((t) => t.severity === 'success');
    expect(success).toBeDefined();
    expect(success!.summary).toContain("'dst'");
  });

  it('(11.5 AC5) onCloneConfirmClick captures the buffer (== server bundle when clean) as the clone source', async () => {
    await loadedWithCloneSrc();
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: dst\nuser_id: null\nentries: {}\n'),
    );

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();
    await fixture.whenStable();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    const sent = apiSpy.importNamespace.calls.mostRecent().args[0] as string;
    const parsed = yaml.load(sent) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('dst');
    const entries = parsed['entries'] as Record<string, unknown>;
    expect(entries['team-1']).toBeDefined();
  });

  it('(11.5 AC9 + 11.7 AC20) Clone 422 structured — lastValidation populated, modal closes, source intact', async () => {
    await loadedWithCloneSrc();
    const report = failingReport(1, 0);
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, report)),
    );

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();

    expect(component.lastValidation).toEqual(report);
    expect(component.rawSaveError).toBeNull();
    expect(component.cloneDialogVisible).toBeFalse();
    expect(component.cloneDestNs).toBe('');
    expect(component.namespace).toBe('src');
    expect(component.buffer).toBe(cloneSrcYaml);
    expect(component.serverYaml).toBe(cloneSrcYaml);
    expect(component.cloning).toBeFalse();
  });

  it('(11.5 AC9 + 11.7 AC21) Clone 422 unstructured — cloneInlineError populated, modal stays open', async () => {
    await loadedWithCloneSrc();
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, 'FastAPI detail string')),
    );

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();

    expect(component.cloneInlineError).toBe('FastAPI detail string');
    expect(component.rawSaveError).toBeNull();
    expect(component.lastValidation).toBeNull();
    expect(component.cloneDialogVisible).toBeTrue();
    expect(component.cloning).toBeFalse();
  });

  it('(11.5 AC9+10) Clone 5xx — sticky toast, lastCloneError populated, source intact', async () => {
    await loadedWithCloneSrc();
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(500, '')),
    );
    messageSpy.add.calls.reset();

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();

    expect(component.lastCloneError).not.toBeNull();
    expect(component.cloning).toBeFalse();
    expect(component.cloneDialogVisible).toBeTrue();
    expect(component.namespace).toBe('src');
    expect(component.serverYaml).toBe(cloneSrcYaml);
    const lastToast = messageSpy.add.calls.mostRecent().args[0];
    expect(lastToast.severity).toBe('error');
    expect(lastToast.sticky).toBeTrue();
  });

  it('(11.5 AC9) Clone 401 — no panel toast, dialog stays open, cloning resets', async () => {
    await loadedWithCloneSrc();
    apiSpy.importNamespace.and.returnValue(Promise.reject(makeHttpError(401)));
    messageSpy.add.calls.reset();

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();

    expect(messageSpy.add).not.toHaveBeenCalled();
    expect(component.cloning).toBeFalse();
    expect(component.cloneDialogVisible).toBeTrue();
    expect(component.lastCloneError).toBeNull();
  });

  it('(11.5 AC11) CloneYamlError — no import call, toast fired, cloning resets, lastCloneError NOT set', async () => {
    await loadedWithCloneSrc();
    // A buffer that is not a valid bundle root mapping forces
    // rewriteNamespaceInYaml to throw CloneYamlError before the network call.
    // Set it directly (Clone reads this.buffer); the gate is bypassed by
    // calling onCloneConfirmClick after opening the modal.
    component.onCloneClick();
    component.buffer = '- not a mapping\n';
    component.cloneDestNs = 'dst';
    apiSpy.importNamespace.calls.reset();
    messageSpy.add.calls.reset();

    await component.onCloneConfirmClick();

    expect(apiSpy.importNamespace).not.toHaveBeenCalled();
    expect(component.cloning).toBeFalse();
    expect(component.lastCloneError).toBeNull();
    expect(messageSpy.add).toHaveBeenCalledTimes(1);
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
  });

  it('(11.5 AC14) NFR7 — clone happy path = 1 import + 2 exports total, no validate calls', async () => {
    await loadedWithCloneSrc();
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.exportNamespace.and.callFake((ns: string) =>
      Promise.resolve(`namespace: ${ns}\nuser_id: null\nentries: {}\n`),
    );

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    await component.onCloneConfirmClick();
    await fixture.whenStable();
    await fixture.whenStable();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(2);
    expect(apiSpy.validateNamespaceBuffer).not.toHaveBeenCalled();
    expect(apiSpy.validatePersistedNamespace).not.toHaveBeenCalled();
  });

  it('(11.5 AC16) destroy-during-clone — late-resolving import does not write state', async () => {
    await loadedWithCloneSrc();
    let resolveLate!: (value: unknown) => void;
    apiSpy.importNamespace.and.returnValue(
      new Promise<unknown>((r) => {
        resolveLate = r;
      }) as unknown as Promise<never>,
    );
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: dst\nuser_id: null\nentries: {}\n'),
    );

    const consoleErrorSpy = spyOn(console, 'error');
    component.onCloneClick();
    component.cloneDestNs = 'dst';
    const clonePromise = component.onCloneConfirmClick();
    expect(component.cloning).toBeTrue();

    fixture.destroy();

    resolveLate([]);
    await clonePromise;
    await Promise.resolve();

    expect(component.namespace).toBe('src');
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  it('(11.7 AC7) Clone modal Confirm button is gated by isCloneGated', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    component.cloneDestNs = 'valid-new-ns';
    expect(component.cloneConfirmDisabled).toBeFalse();

    component.lastValidation = failingReport(1);
    expect(component.cloneConfirmDisabled).toBeTrue();
  });

  // ----- Clone modal a11y handlers -----

  it('(11.7 AC18) onCloneDialogHide clears cloneDestNs and cloneInlineError', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    component.cloneDestNs = 'something';
    component.cloneInlineError = 'pending error';

    component.onCloneDialogHide();

    expect(component.cloneDestNs).toBe('');
    expect(component.cloneInlineError).toBeNull();
  });

  it('(11.7 AC19) onCloneDialogHide returns focus to the outer Clone button', fakeAsync(async () => {
    await loadedWithCloneSrc(['src']);
    fixture.detectChanges();

    const cloneBtn = fixture.nativeElement.querySelector(
      'button[data-test="clone-btn"]',
    ) as HTMLButtonElement;
    component.cloneBtnRef = {
      nativeElement: cloneBtn,
    } as ElementRef<HTMLButtonElement>;

    component.onCloneDialogHide();
    tick(0);

    expect(document.activeElement).toBe(cloneBtn);
  }));

  it('(11.7 AC16) onCloneDialogShow focuses the destination input via cloneDestInputRef', fakeAsync(async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    fixture.detectChanges();

    const stub = document.createElement('input');
    document.body.appendChild(stub);
    component.cloneDestInputRef = {
      nativeElement: stub,
    } as ElementRef<HTMLInputElement>;

    component.onCloneDialogShow();
    tick(0);

    expect(document.activeElement).toBe(stub);
    document.body.removeChild(stub);
  }));

  it('(11.7 AC21) onCloneDestNsChange clears cloneInlineError', async () => {
    await loadedWithCloneSrc(['src']);
    component.cloneInlineError = 'stale error';

    component.onCloneDestNsChange();

    expect(component.cloneInlineError).toBeNull();
  });

  // ---------------------------------------------------------------------
  // a11y live region.
  // ---------------------------------------------------------------------

  it('(11.7 AC12) failing Validate sets a11yAnnouncement to "Validation found N issues"', async () => {
    await loaded('foo: 1\n');
    apiSpy.validateNamespaceBuffer.and.returnValue(
      Promise.resolve(failingReport(3, 2)),
    );

    await component.onValidateBufferClick();
    fixture.detectChanges();

    expect(component.a11yAnnouncement).toBe('Validation found 5 issues');
    const liveRegion = fixture.nativeElement.querySelector(
      '[data-test="a11y-live-region"]',
    ) as HTMLElement;
    expect(liveRegion.textContent?.trim()).toBe('Validation found 5 issues');
  });

  it('(11.7 AC12) Save 422 structured branch announces "Validation found N issues"', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: 1\n'));
    apiSpy.importNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, failingReport(1, 0))),
    );

    await component.onSaveClick();

    expect(component.a11yAnnouncement).toBe('Validation found 1 issues');
  });

  it('(11.7 AC14) Clone Confirm 2xx sets a11yAnnouncement to "Cloned to namespace \'X\'"', async () => {
    await loadedWithCloneSrc(['src']);
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve('namespace: new-ns\nuser_id: null\nentries: {}\n'),
    );
    component.onCloneClick();
    component.cloneDestNs = 'new-ns';

    await component.onCloneConfirmClick();
    await fixture.whenStable();

    expect(component.a11yAnnouncement).toBe("Cloned to namespace 'new-ns'");
  });

  it('(11.7 AC15) live region is visually-hidden but present in the DOM', async () => {
    await loaded('foo: 1\n');
    fixture.detectChanges();

    const liveRegion = fixture.nativeElement.querySelector(
      '[data-test="a11y-live-region"]',
    ) as HTMLElement;
    expect(liveRegion).not.toBeNull();
    expect(liveRegion.getAttribute('role')).toBe('status');
    expect(liveRegion.getAttribute('aria-live')).toBe('polite');
    expect(liveRegion.getAttribute('aria-atomic')).toBe('true');

    const styles = window.getComputedStyle(liveRegion);
    expect(styles.position).toBe('absolute');
    expect(styles.width).toBe('1px');
    expect(styles.height).toBe('1px');
    expect(styles.overflow).toBe('hidden');
  });

  // ---------------------------------------------------------------------
  // Delete flow — always available (ADR-028).
  // ---------------------------------------------------------------------

  it('(14.1 AC11) Delete button renders whenever the panel has content', async () => {
    await loaded('foo: 1\n');
    const btn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    );
    expect(btn).not.toBeNull();
  });

  it('(14.1 AC11) Delete button is present even when dirty (orthogonal to dirty)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    fixture.detectChanges();
    const btn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    ) as HTMLButtonElement;
    expect(btn).not.toBeNull();
    expect(btn.disabled).toBeFalse();
  });

  it('(14.1 AC12 / 22.3 AC5) clicking Delete opens the custom confirm naming the namespace; Proceed calls deleteNamespace once', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());

    component.onDeleteClick();
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('delete');
    expect(component.confirmRequest!.header).toBe('Delete namespace');
    expect(component.confirmRequest!.message).toContain('foo');

    clickConfirmProceed();
    await fixture.whenStable();

    expect(confirmOpen()).toBeFalse();
    expect(apiSpy.deleteNamespace).toHaveBeenCalledOnceWith('foo');
  });

  it('(22.3 AC5) Delete Cancel fires no network request and leaves the panel unchanged', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());

    component.onDeleteClick();
    expect(confirmOpen()).toBeTrue();

    clickConfirmCancel();

    expect(confirmOpen()).toBeFalse();
    expect(apiSpy.deleteNamespace).not.toHaveBeenCalled();
    expect(component.serverYaml).toBe('foo: 1\n');
    expect(component.buffer).toBe('foo: 1\n');
  });

  it('(14.1 AC13) 204 success — emits saved + closed and shows a success toast', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());
    const savedEmit = spyOn(component.saved, 'emit');
    const closedEmit = spyOn(component.closed, 'emit');

    await component.onDeleteConfirm();

    expect(apiSpy.deleteNamespace).toHaveBeenCalledOnceWith('foo');
    expect(savedEmit).toHaveBeenCalledTimes(1);
    expect(closedEmit).toHaveBeenCalledTimes(1);
    expect(component.deleting).toBeFalse();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('success');
    expect(component.a11yAnnouncement).toBe("Namespace 'foo' deleted");
  });

  it('(14.1 AC14) 403 — not-authorized toast, panel open, state unchanged, single call', async () => {
    await loaded('foo: 1\n');
    const savedEmit = spyOn(component.saved, 'emit');
    apiSpy.deleteNamespace.and.returnValue(Promise.reject(makeHttpError(403)));
    messageSpy.add.calls.reset();

    await component.onDeleteConfirm();

    expect(apiSpy.deleteNamespace).toHaveBeenCalledTimes(1);
    expect(savedEmit).not.toHaveBeenCalled();
    expect(component.serverYaml).toBe('foo: 1\n');
    expect(component.buffer).toBe('foo: 1\n');
    expect(component.deleting).toBeFalse();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
    expect(toast.summary).toContain('not authorized');
    expect(toast.sticky).toBeFalsy();
  });

  it('(14.1 AC15) 422 structured NamespaceValidationReport — findings surfaced, panel open', async () => {
    await loaded('foo: 1\n');
    const report = failingReport(1, 0);
    apiSpy.deleteNamespace.and.returnValue(
      Promise.reject(makeHttpError(422, report)),
    );

    await component.onDeleteConfirm();

    expect(component.lastValidation).toEqual(report);
    expect(component.rawSaveError).toBeNull();
    expect(component.serverYaml).toBe('foo: 1\n');
    expect(component.a11yAnnouncement).toBe('Validation found 1 issues');
  });

  it('(14.1 AC15) 409 with unstructured body — error toast surfaces the detail, panel open', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(
      Promise.reject(makeHttpError(409, 'referenced by namespace bar')),
    );
    messageSpy.add.calls.reset();

    await component.onDeleteConfirm();

    expect(component.lastValidation).toBeNull();
    const toast = messageSpy.add.calls.mostRecent().args[0];
    expect(toast.severity).toBe('error');
    expect(toast.detail).toContain('referenced by namespace bar');
  });

  it('(14.1 AC9) Delete 401 — no panel toast, state unchanged', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.reject(makeHttpError(401)));
    messageSpy.add.calls.reset();

    await component.onDeleteConfirm();

    expect(messageSpy.add).not.toHaveBeenCalled();
    expect(component.serverYaml).toBe('foo: 1\n');
    expect(component.deleting).toBeFalse();
  });

  it('(14.1 AC16) in-flight guard — button disabled while deleting; second confirm does not fire a second call', async () => {
    await loaded('foo: 1\n');
    let resolveDelete!: () => void;
    apiSpy.deleteNamespace.and.returnValue(
      new Promise<void>((r) => {
        resolveDelete = r;
      }),
    );

    const firstCall = component.onDeleteConfirm();
    expect(component.deleting).toBeTrue();
    fixture.detectChanges();

    const btn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    ) as HTMLButtonElement;
    expect(btn.disabled).toBeTrue();

    void component.onDeleteConfirm();
    component.onDeleteClick();
    expect(confirmOpen()).toBeFalse();

    resolveDelete();
    await firstCall;

    expect(apiSpy.deleteNamespace).toHaveBeenCalledTimes(1);
    expect(component.deleting).toBeFalse();
  });

  it('(14.1 AC10) onDeleteClick is a no-op while a delete is in flight', async () => {
    await loaded('foo: 1\n');
    component.deleting = true;

    component.onDeleteClick();

    expect(confirmOpen()).toBeFalse();
  });

  it('(14.1 AC10) destroy-during-delete — late resolution does not write state', async () => {
    await loaded('foo: 1\n');
    let resolveLate!: () => void;
    apiSpy.deleteNamespace.and.returnValue(
      new Promise<void>((r) => {
        resolveLate = r;
      }),
    );
    const savedEmit = spyOn(component.saved, 'emit');
    const consoleErrorSpy = spyOn(console, 'error');

    const deletePromise = component.onDeleteConfirm();
    expect(component.deleting).toBeTrue();

    fixture.destroy();
    resolveLate();
    await deletePromise;
    await Promise.resolve();

    expect(savedEmit).not.toHaveBeenCalled();
    expect(consoleErrorSpy).not.toHaveBeenCalled();
  });

  // ---------------------------------------------------------------------
  // Clone modal shareable / public toggles.
  // ---------------------------------------------------------------------

  const cloneSrcYamlWithFlags = `namespace: src
name: Src
shareable: true
public: false
entries: {}
`;

  it('(12.2 AC7) cloneShareable / clonePublic initialize to false', async () => {
    await loadedWithCloneSrc();
    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC7) onCloneCancelClick resets both toggles to false', async () => {
    await loadedWithCloneSrc();
    component.cloneShareable = true;
    component.clonePublic = true;

    component.onCloneCancelClick();

    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC7) onCloneDialogVisibleChange(false) resets both toggles to false', async () => {
    await loadedWithCloneSrc();
    component.cloneShareable = true;
    component.clonePublic = true;

    component.onCloneDialogVisibleChange(false);

    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC7) onCloneDialogHide resets both toggles to false', async () => {
    await loadedWithCloneSrc();
    component.cloneShareable = true;
    component.clonePublic = true;

    component.onCloneDialogHide();

    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC8) onCloneClick pre-fills both toggles from the buffer flags', async () => {
    apiSpy.exportNamespace.and.returnValue(
      Promise.resolve(cloneSrcYamlWithFlags),
    );
    await buildFixture('src');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    component.onCloneClick();

    expect(component.cloneShareable).toBeTrue();
    expect(component.clonePublic).toBeFalse();
    expect(component.cloneDestNs).toMatch(/^src_/);
    expect(component.cloneDestName).toBe('Src_copy');
  });

  it('(12.2 AC8) onCloneClick defaults both toggles to false when the buffer omits the flags', async () => {
    await loadedWithCloneSrc();
    component.cloneShareable = true;
    component.clonePublic = true;

    component.onCloneClick();

    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC9) onCloneConfirmClick passes both toggle values into the import YAML', async () => {
    await loadedWithCloneSrc(['src']);
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.exportNamespace.and.callFake((ns: string) =>
      Promise.resolve(`namespace: ${ns}\nuser_id: null\nentries: {}\n`),
    );

    component.onCloneClick();
    component.cloneDestNs = 'dst';
    component.cloneDestName = 'Dest';
    component.cloneShareable = true;
    component.clonePublic = false;

    await component.onCloneConfirmClick();
    await fixture.whenStable();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    const sent = apiSpy.importNamespace.calls.mostRecent().args[0] as string;
    const parsed = yaml.load(sent) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(true);
    expect(parsed['public']).toBe(false);
    expect(parsed['namespace']).toBe('dst');
    expect(parsed['name']).toBe('Dest');
    expect(component.cloneShareable).toBeFalse();
    expect(component.clonePublic).toBeFalse();
  });

  it('(12.2 AC10) toggle combinations never change cloneConfirmDisabled or cloneValidationError', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    component.cloneDestNs = 'dst';
    component.cloneDestName = 'Dest';

    const combos: [boolean, boolean][] = [
      [true, true],
      [true, false],
      [false, true],
      [false, false],
    ];
    for (const [s, p] of combos) {
      component.cloneShareable = s;
      component.clonePublic = p;
      expect(component.cloneConfirmDisabled).toBeFalse();
      expect(component.cloneValidationError).toBeNull();
    }
  });

  it('(12.2 AC11) both toggles render after the inputs with distinguishing hints', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    fixture.detectChanges();

    const root = fixture.nativeElement as HTMLElement;
    const shareable = root.querySelector(
      '[data-test="clone-shareable-toggle"]',
    );
    const isPublic = root.querySelector('[data-test="clone-public-toggle"]');
    expect(shareable).not.toBeNull();
    expect(isPublic).not.toBeNull();

    const ordered = Array.from(
      root.querySelectorAll(
        '[data-test="clone-destname-input"],' +
          '[data-test="clone-destns-input"],' +
          '[data-test="clone-shareable-toggle"],' +
          '[data-test="clone-public-toggle"]',
      ),
    ).map((el) => el.getAttribute('data-test'));
    expect(ordered).toEqual([
      'clone-destname-input',
      'clone-destns-input',
      'clone-shareable-toggle',
      'clone-public-toggle',
    ]);

    expect(
      root.querySelector('label[for="clone-shareable-toggle"]'),
    ).not.toBeNull();
    expect(
      root.querySelector('label[for="clone-public-toggle"]'),
    ).not.toBeNull();

    const shareableHint =
      root.querySelector('#clone-shareable-hint')?.textContent?.toLowerCase() ??
      '';
    const publicHint =
      root.querySelector('#clone-public-hint')?.textContent?.toLowerCase() ??
      '';
    expect(shareableHint).toContain('reference');
    expect(shareableHint).not.toContain('clone');
    expect(publicHint).toContain('clone');
    expect(publicHint).toMatch(/list|read/);
  });

  // ---------------------------------------------------------------------
  // Action-row keyboard shortcuts (ADR-017).
  // ⌥S Save · ⌥V Validate · ⌥R Reset · ⌥⇧C Clone · ⌥D Delete.
  // Match on altKey + code (NOT key); preventDefault on handled combos;
  // two capture sites (HostListener + Monaco addAction) over one shared
  // dispatch surface honouring each button's enablement.
  // ---------------------------------------------------------------------

  /**
   * Build a KeyboardEvent with explicit code/altKey/shiftKey/key.
   * `cancelable: true` so `event.defaultPrevented` reflects a
   * `preventDefault()` call even when the event is invoked directly (not
   * dispatched) — a non-cancelable synthetic event ignores preventDefault.
   */
  function keyEvent(
    code: string,
    init: Partial<KeyboardEventInit> = {},
  ): KeyboardEvent {
    return new KeyboardEvent('keydown', {
      altKey: true,
      cancelable: true,
      code,
      ...init,
    });
  }

  // ----- can* enablement getters mirror the button [disabled] -----

  it('(22.2 AC6) can* getters mirror the button [disabled] expressions', async () => {
    await loaded('foo: 1\n');

    // Clean, idle: Save/Reset disabled, Validate/Clone/Delete enabled.
    expect(component.canSave).toBeFalse();
    expect(component.canReset).toBeFalse();
    expect(component.canValidate).toBeTrue();
    expect(component.canClone).toBeTrue();
    expect(component.canDelete).toBeTrue();

    // Dirty: Save/Reset enable, Clone disables.
    component.buffer = 'foo: 2\n';
    expect(component.canSave).toBeTrue();
    expect(component.canReset).toBeTrue();
    expect(component.canClone).toBeFalse();

    // FR14-gated: Save off, Clone off (when clean), Validate STILL on.
    component.buffer = component.serverYaml;
    component.lastValidation = failingReport(1);
    expect(component.canSave).toBeFalse();
    expect(component.canClone).toBeFalse();
    expect(component.canValidate).toBeTrue();

    // In-flight flags.
    component.lastValidation = null;
    component.saving = true;
    expect(component.canValidate).toBeFalse();
    component.saving = false;
    component.validating = true;
    expect(component.canValidate).toBeFalse();
    component.validating = false;
    component.cloning = true;
    expect(component.canClone).toBeFalse();
    component.cloning = false;
    component.deleting = true;
    expect(component.canDelete).toBeFalse();
  });

  it('(22.2 AC6) the button [disabled] bindings consume the can* getters (single source of truth)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    fixture.detectChanges();

    const dis = (test: string): boolean =>
      (
        fixture.nativeElement.querySelector(
          `button[data-test="${test}"]`,
        ) as HTMLButtonElement
      ).disabled;

    // Dirty state: Save/Reset enabled, Clone disabled — matches can*.
    expect(dis('save-btn')).toBe(!component.canSave);
    expect(dis('reset-btn')).toBe(!component.canReset);
    expect(dis('clone-btn')).toBe(!component.canClone);
    expect(dis('validate-btn')).toBe(!component.canValidate);
    expect(dis('delete-ns-btn')).toBe(!component.canDelete);
  });

  // ----- HostListener path: per-action dispatch + enablement -----

  it('(22.2 AC1/AC6) ⌥S fires Save when dirty+enabled; no-op while clean', async () => {
    await loaded('foo: 1\n');
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    // Clean → no-op.
    component.onKeydown(keyEvent('KeyS'));
    expect(saveSpy).not.toHaveBeenCalled();

    // Dirty → fires.
    component.buffer = 'foo: 2\n';
    component.onKeydown(keyEvent('KeyS'));
    expect(saveSpy).toHaveBeenCalledTimes(1);
  });

  it('(22.2 AC1/AC6) ⌥S is a no-op while saving or FR14-gated even when dirty', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    component.saving = true;
    component.onKeydown(keyEvent('KeyS'));
    expect(saveSpy).not.toHaveBeenCalled();

    component.saving = false;
    component.lastValidation = failingReport(1);
    component.onKeydown(keyEvent('KeyS'));
    expect(saveSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC2/AC6) ⌥V fires Validate in clean AND dirty states; never FR14-suppressed', async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );

    // Clean.
    component.onKeydown(keyEvent('KeyV'));
    expect(validateSpy).toHaveBeenCalledTimes(1);

    // Dirty + FR14-gated → still fires.
    component.buffer = 'foo: 2\n';
    component.lastValidation = failingReport(1);
    component.rawSaveError = 'raw';
    expect(component.isSaveGated).toBeTrue();
    component.onKeydown(keyEvent('KeyV'));
    expect(validateSpy).toHaveBeenCalledTimes(2);
  });

  it('(22.2 AC2/AC6) ⌥V is a no-op while validating or saving', async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );

    component.validating = true;
    component.onKeydown(keyEvent('KeyV'));
    expect(validateSpy).not.toHaveBeenCalled();

    component.validating = false;
    component.saving = true;
    component.onKeydown(keyEvent('KeyV'));
    expect(validateSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC3/AC6) ⌥R fires Reset when dirty; no-op while clean; routes through the confirm', async () => {
    await loaded('foo: 1\n');
    const resetSpy = spyOn(component, 'onResetClick').and.callThrough();

    // Clean → no-op (and no confirm).
    component.onKeydown(keyEvent('KeyR'));
    expect(resetSpy).not.toHaveBeenCalled();
    expect(confirmOpen()).toBeFalse();

    // Dirty → fires onResetClick, which opens the discard confirm.
    component.buffer = 'foo: 2\n';
    component.onKeydown(keyEvent('KeyR'));
    expect(resetSpy).toHaveBeenCalledTimes(1);
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('reset');
    expect(component.confirmRequest!.message).toContain('Discard unsaved changes');
  });

  it('(22.2 AC3/AC6) ⌥R is a no-op while saving even when dirty', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.saving = true;
    const resetSpy = spyOn(component, 'onResetClick');

    component.onKeydown(keyEvent('KeyR'));

    expect(resetSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC4/AC6/AC8) ⌥⇧C fires Clone when clean; no-op while dirty', async () => {
    await loaded('foo: 1\n');
    const cloneSpy = spyOn(component, 'onCloneClick').and.callThrough();

    // Clean + Shift → opens the modal.
    component.onKeydown(keyEvent('KeyC', { shiftKey: true }));
    expect(cloneSpy).toHaveBeenCalledTimes(1);
    expect(component.cloneDialogVisible).toBeTrue();

    // Dirty → no-op even with Shift.
    cloneSpy.calls.reset();
    component.cloneDialogVisible = false;
    component.buffer = 'foo: 2\n';
    component.onKeydown(keyEvent('KeyC', { shiftKey: true }));
    expect(cloneSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC4/AC6) ⌥⇧C is a no-op while cloning or FR14-gated', async () => {
    await loaded('foo: 1\n');
    const cloneSpy = spyOn(component, 'onCloneClick');

    component.cloning = true;
    component.onKeydown(keyEvent('KeyC', { shiftKey: true }));
    expect(cloneSpy).not.toHaveBeenCalled();

    component.cloning = false;
    component.lastValidation = failingReport(1);
    component.onKeydown(keyEvent('KeyC', { shiftKey: true }));
    expect(cloneSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC5) ⌥D opens the Delete confirm; does NOT call deleteNamespace until Proceed', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());
    const deleteSpy = spyOn(component, 'onDeleteClick').and.callThrough();

    component.onKeydown(keyEvent('KeyD'));

    expect(deleteSpy).toHaveBeenCalledTimes(1);
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('delete');
    expect(component.confirmRequest!.header).toBe('Delete namespace');
    // No direct DELETE before Proceed.
    expect(apiSpy.deleteNamespace).not.toHaveBeenCalled();

    clickConfirmProceed();
    await fixture.whenStable();
    expect(apiSpy.deleteNamespace).toHaveBeenCalledOnceWith('foo');
  });

  it('(22.2 AC5/AC6) ⌥D is a no-op while a delete is in flight', async () => {
    await loaded('foo: 1\n');
    component.deleting = true;
    const deleteSpy = spyOn(component, 'onDeleteClick').and.callThrough();

    component.onKeydown(keyEvent('KeyD'));

    expect(deleteSpy).not.toHaveBeenCalled();
    expect(confirmOpen()).toBeFalse();
  });

  // ----- Match on code, NOT key — macOS dead-key survival -----

  it('(22.2 AC7) ⌥S still fires when key carries the dead-key glyph ß (matcher ignores event.key)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    component.onKeydown(keyEvent('KeyS', { key: 'ß' }));

    expect(saveSpy).toHaveBeenCalledTimes(1);
  });

  it('(22.2 AC7) ⌥V still fires when key carries the dead-key glyph √', async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );

    component.onKeydown(keyEvent('KeyV', { key: '√' }));

    expect(validateSpy).toHaveBeenCalledTimes(1);
  });

  // ----- Shift gating for Clone -----

  it('(22.2 AC8) Alt+KeyC WITHOUT Shift does not trigger Clone and does not preventDefault', async () => {
    await loaded('foo: 1\n');
    const cloneSpy = spyOn(component, 'onCloneClick');
    const evt = keyEvent('KeyC', { shiftKey: false });

    component.onKeydown(evt);

    expect(cloneSpy).not.toHaveBeenCalled();
    expect(evt.defaultPrevented).toBeFalse();
  });

  it('(22.2 AC8) Alt+Shift+KeyC triggers ONLY Clone — no S/V/R/D handler fires', async () => {
    await loaded('foo: 1\n');
    const cloneSpy = spyOn(component, 'onCloneClick').and.callThrough();
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );
    const resetSpy = spyOn(component, 'onResetClick');
    const deleteSpy = spyOn(component, 'onDeleteClick');

    component.onKeydown(keyEvent('KeyC', { shiftKey: true }));

    expect(cloneSpy).toHaveBeenCalledTimes(1);
    expect(saveSpy).not.toHaveBeenCalled();
    expect(validateSpy).not.toHaveBeenCalled();
    expect(resetSpy).not.toHaveBeenCalled();
    expect(deleteSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC8) S/V/R/D match regardless of shiftKey state', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    // Alt+Shift+KeyS still fires Save (Shift is irrelevant for S/V/R/D).
    component.onKeydown(keyEvent('KeyS', { shiftKey: true }));

    expect(saveSpy).toHaveBeenCalledTimes(1);
  });

  // ----- preventDefault on handled combos only -----

  it('(22.2 AC9) preventDefault is called on a handled combo even when the action is a no-op (⌥S while clean)', async () => {
    await loaded('foo: 1\n');
    // Clean → triggerSave is a no-op, but the keystroke is "ours".
    const evt = keyEvent('KeyS');
    const pd = spyOn(evt, 'preventDefault').and.callThrough();
    const saveSpy = spyOn(component, 'onSaveClick');

    component.onKeydown(evt);

    expect(saveSpy).not.toHaveBeenCalled();
    expect(pd).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBeTrue();
  });

  it('(22.2 AC9) preventDefault is NOT called for an event without Alt', async () => {
    await loaded('foo: 1\n');
    const evt = new KeyboardEvent('keydown', {
      altKey: false,
      cancelable: true,
      code: 'KeyS',
    });
    const pd = spyOn(evt, 'preventDefault').and.callThrough();

    component.onKeydown(evt);

    expect(pd).not.toHaveBeenCalled();
    expect(evt.defaultPrevented).toBeFalse();
  });

  it('(22.2 AC9) preventDefault is NOT called for Alt + a non-bound code', async () => {
    await loaded('foo: 1\n');
    const evt = keyEvent('KeyX');
    const pd = spyOn(evt, 'preventDefault').and.callThrough();

    component.onKeydown(evt);

    expect(pd).not.toHaveBeenCalled();
  });

  it('(22.2 AC9) preventDefault IS called on a handled+enabled combo (⌥S while dirty)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    spyOn(component, 'onSaveClick').and.returnValue(Promise.resolve());
    const evt = keyEvent('KeyS');
    const pd = spyOn(evt, 'preventDefault').and.callThrough();

    component.onKeydown(evt);

    expect(pd).toHaveBeenCalledTimes(1);
  });

  // ----- HostListener binding wired end-to-end via dispatchEvent -----

  it('(22.2 AC11) the @HostListener binding catches a dispatched keydown on the host element', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    const evt = keyEvent('KeyS');
    fixture.nativeElement.dispatchEvent(evt);

    expect(saveSpy).toHaveBeenCalledTimes(1);
  });

  // ----- Monaco capture path via editor.addAction -----

  function findAction(actions: RecordedAction[], id: string): RecordedAction {
    const action = actions.find((a) => a.id.endsWith(id));
    expect(action).withContext(`addAction for ${id}`).toBeDefined();
    return action!;
  }

  it('(22.2 AC10) onEditorEvent("init") registers five chord actions with the expected keybindings', async () => {
    await loaded('foo: 1\n');
    const { editor, actions } = makeEditorStub();

    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });

    expect(actions.length).toBe(5);
    const save = findAction(actions, 'save');
    const validate = findAction(actions, 'validate');
    const reset = findAction(actions, 'reset');
    const clone = findAction(actions, 'clone');
    const del = findAction(actions, 'delete');

    // Alt = bit, Shift = bit, codes resolve to distinct chord numbers.
    expect(save.keybindings![0]).toBe(monaco.KeyMod.Alt | monaco.KeyCode.KeyS);
    expect(validate.keybindings![0]).toBe(
      monaco.KeyMod.Alt | monaco.KeyCode.KeyV,
    );
    expect(reset.keybindings![0]).toBe(monaco.KeyMod.Alt | monaco.KeyCode.KeyR);
    expect(clone.keybindings![0]).toBe(
      monaco.KeyMod.Alt | monaco.KeyMod.Shift | monaco.KeyCode.KeyC,
    );
    expect(del.keybindings![0]).toBe(monaco.KeyMod.Alt | monaco.KeyCode.KeyD);
  });

  it('(22.2 AC10) a non-init event (or missing editor) registers nothing', async () => {
    await loaded('foo: 1\n');
    const { editor, actions } = makeEditorStub();

    component.onEditorEvent({ type: 're-init' });
    component.onEditorEvent({ type: 'resize' });
    component.onEditorEvent({
      type: 'init',
      editor: undefined,
    });
    expect(actions.length).toBe(0);

    // A real init registers; a following re-init does NOT double-register.
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });
    expect(actions.length).toBe(5);
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });
    expect(actions.length).toBe(5);
  });

  it('(22.2 AC10/AC12) Monaco run() routes through the same dispatch + enablement as the HostListener', async () => {
    await loaded('foo: 1\n');
    const { editor, actions } = makeEditorStub();
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });

    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );
    const save = findAction(actions, 'save');

    // Clean → run() is a no-op (enablement parity with the HostListener).
    save.run(editor);
    expect(saveSpy).not.toHaveBeenCalled();

    // Dirty → run() fires Save.
    component.buffer = 'foo: 2\n';
    save.run(editor);
    expect(saveSpy).toHaveBeenCalledTimes(1);
  });

  it('(22.2 AC12) Monaco path and HostListener path reach the same handler under the same state', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const { editor, actions } = makeEditorStub();
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    // HostListener path.
    component.onKeydown(keyEvent('KeyS'));
    // Monaco path.
    findAction(actions, 'save').run(editor);

    expect(saveSpy).toHaveBeenCalledTimes(2);
  });

  it('(22.2 AC10/AC12) Monaco Clone run() honours the clean-only enablement', async () => {
    await loadedWithCloneSrc(['src']);
    const { editor, actions } = makeEditorStub();
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });
    const cloneSpy = spyOn(component, 'onCloneClick').and.callThrough();
    const clone = findAction(actions, 'clone');

    // Clean → opens the modal.
    clone.run(editor);
    expect(cloneSpy).toHaveBeenCalledTimes(1);

    // Dirty → no-op.
    cloneSpy.calls.reset();
    component.cloneDialogVisible = false;
    component.buffer = cloneSrcYaml + '# dirty\n';
    clone.run(editor);
    expect(cloneSpy).not.toHaveBeenCalled();
  });

  it('(22.2 AC10) the template wires nu-monaco-editor (event) → onEditorEvent', async () => {
    await loaded('foo: 1\n');
    const onEditorEventSpy = spyOn(component, 'onEditorEvent').and.callThrough();
    const { editor } = makeEditorStub();

    const stub = fixture.debugElement.query(By.css('nu-monaco-editor'));
    expect(stub).withContext('nu-monaco-editor present').not.toBeNull();
    (stub.componentInstance as StubMonacoEditorComponent).event.emit({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });

    expect(onEditorEventSpy).toHaveBeenCalledTimes(1);
    const arg = onEditorEventSpy.calls.mostRecent().args[0];
    expect(arg.type).toBe('init');
  });

  // ---------------------------------------------------------------------
  // macOS Option-shortcut robustness (dead-key / IME composition) (ADR-018).
  // On some macOS layouts an ⌥-chord initiates a dead-key composition: the
  // keydown arrives flagged composing (isComposing:true, keyCode:229,
  // key:'Dead'). The matcher MUST still fire (it keys off altKey+code, never
  // key) AND preventDefault to suppress the composition. The host capture-phase
  // listener is the AUTHORITATIVE path.
  //
  // NOTE: synthetic KeyboardEvents bypass the OS composition layer, so these
  // specs prove the matcher WOULD handle a composing event (guarding a future
  // composing-skip regression) but CANNOT reproduce the real macOS chord —
  // that remains a manual-verification gate left for the operator.
  // ---------------------------------------------------------------------

  /**
   * Build a composing (dead-key / IME) keydown — the macOS failure shape:
   * `isComposing:true`, `keyCode:229`, `key:'Dead'`. Merges any extra init
   * (e.g. `{ shiftKey: true }` for ⌥⇧C). Reuses the `keyEvent` helper
   * (altKey:true + cancelable:true), so `evt.defaultPrevented` is observable
   * after a direct `component.onKeydown(evt)` call.
   */
  function composingKeyEvent(
    code: string,
    init: Partial<KeyboardEventInit> = {},
  ): KeyboardEvent {
    return keyEvent(code, {
      isComposing: true,
      keyCode: 229,
      key: 'Dead',
      ...init,
    });
  }

  // ----- composing-event survival + preventDefault, uniform -----

  interface ComposingChordCase {
    name: string;
    code: string;
    extraInit: Partial<KeyboardEventInit>;
    handler:
      | 'onSaveClick'
      | 'onValidateBufferClick'
      | 'onResetClick'
      | 'onCloneClick'
      | 'onDeleteClick';
    /** Make the chord's `canX` enablement true before dispatching. */
    enable: () => void;
  }

  const composingChordCases: ComposingChordCase[] = [
    {
      name: '⌥S Save',
      code: 'KeyS',
      extraInit: {},
      handler: 'onSaveClick',
      enable: (): void => {
        component.buffer = 'foo: 2\n'; // dirty → canSave
      },
    },
    {
      name: '⌥V Validate',
      code: 'KeyV',
      extraInit: {},
      handler: 'onValidateBufferClick',
      enable: (): void => {
        // Validate is enabled by default (clean or dirty) — no setup needed.
      },
    },
    {
      name: '⌥R Reset',
      code: 'KeyR',
      extraInit: {},
      handler: 'onResetClick',
      enable: (): void => {
        component.buffer = 'foo: 2\n'; // dirty → canReset
      },
    },
    {
      name: '⌥⇧C Clone',
      code: 'KeyC',
      extraInit: { shiftKey: true },
      handler: 'onCloneClick',
      enable: (): void => {
        // Clean (default loaded state) → canClone is already true.
      },
    },
    {
      name: '⌥D Delete',
      code: 'KeyD',
      extraInit: {},
      handler: 'onDeleteClick',
      enable: (): void => {
        // Delete is enabled by default (only gated by an in-flight delete).
      },
    },
  ];

  composingChordCases.forEach((c) => {
    it(`(22.4 AC1/AC3) ${c.name} fires under a composing event (keyCode:229/isComposing/key:'Dead') and preventDefaults`, async () => {
      await loaded('foo: 1\n');
      // Keep handlers inert so no real network / modal side effects run; we
      // only assert the matcher reached the handler under a composing event.
      const handlerSpy = spyOn(component, c.handler).and.returnValue(
        undefined as never,
      );
      c.enable();

      const evt = composingKeyEvent(c.code, c.extraInit);
      component.onKeydown(evt);

      expect(handlerSpy)
        .withContext(`${c.name} handler under composing event`)
        .toHaveBeenCalledTimes(1);
      expect(evt.defaultPrevented)
        .withContext(`${c.name} preventDefault under composing event`)
        .toBeTrue();
    });
  });

  it("(22.4 AC1) the matcher keys off event.code, never event.key — a composing ⌥V with key:'Dead' still validates", async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );

    // key:'Dead' is irrelevant — only altKey + code drive the match.
    const evt = composingKeyEvent('KeyV');
    expect(evt.key).toBe('Dead');
    component.onKeydown(evt);

    expect(validateSpy).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBeTrue();
  });

  it('(22.4 AC2) preventDefault fires under a composing + no-op-by-enablement combo (⌥S while clean)', async () => {
    await loaded('foo: 1\n');
    // Clean → triggerSave is a no-op, but the keystroke is "ours" and the
    // composition MUST still be suppressed (parity with the non-composing path).
    const saveSpy = spyOn(component, 'onSaveClick');
    const evt = composingKeyEvent('KeyS');

    component.onKeydown(evt);

    expect(saveSpy).not.toHaveBeenCalled();
    expect(evt.defaultPrevented).toBeTrue();
  });

  it('(22.4 AC3) per-action enablement is honoured under composing events (clean ⌥S/⌥R no-op; ⌥V/⌥⇧C/⌥D still fire)', async () => {
    await loaded('foo: 1\n');
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );
    const resetSpy = spyOn(component, 'onResetClick');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );
    const cloneSpy = spyOn(component, 'onCloneClick');
    const deleteSpy = spyOn(component, 'onDeleteClick');

    // Clean state: Save/Reset are no-ops; Validate/Clone/Delete fire.
    component.onKeydown(composingKeyEvent('KeyS'));
    component.onKeydown(composingKeyEvent('KeyR'));
    component.onKeydown(composingKeyEvent('KeyV'));
    component.onKeydown(composingKeyEvent('KeyC', { shiftKey: true }));
    component.onKeydown(composingKeyEvent('KeyD'));

    expect(saveSpy).not.toHaveBeenCalled();
    expect(resetSpy).not.toHaveBeenCalled();
    expect(validateSpy).toHaveBeenCalledTimes(1);
    expect(cloneSpy).toHaveBeenCalledTimes(1);
    expect(deleteSpy).toHaveBeenCalledTimes(1);
  });

  it("(22.4 AC1) a composing ⌥⇧C (Clone) still requires Shift — composing Alt+KeyC without Shift is unhandled", async () => {
    await loaded('foo: 1\n');
    const cloneSpy = spyOn(component, 'onCloneClick');

    const noShift = composingKeyEvent('KeyC', { shiftKey: false });
    component.onKeydown(noShift);
    expect(cloneSpy).not.toHaveBeenCalled();
    expect(noShift.defaultPrevented).toBeFalse();

    const withShift = composingKeyEvent('KeyC', { shiftKey: true });
    component.onKeydown(withShift);
    expect(cloneSpy).toHaveBeenCalledTimes(1);
    expect(withShift.defaultPrevented).toBeTrue();
  });

  // ----- host capture-phase listener is the authoritative path -----

  it('(22.4 AC4) a composing keydown dispatched on the host element fires the action via the capture-phase listener', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );

    // Dispatch (not a direct method call) so the registered capture-phase host
    // listener — the authoritative path — is what catches the composing chord.
    const evt = composingKeyEvent('KeyS');
    fixture.nativeElement.dispatchEvent(evt);

    expect(saveSpy).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBeTrue();
  });

  it('(22.4 AC4) the host capture listener is removed on destroy — a post-teardown keydown does not dispatch', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const saveSpy = spyOn(component, 'onSaveClick').and.returnValue(
      Promise.resolve(),
    );
    const host = fixture.nativeElement as HTMLElement;

    fixture.destroy();
    host.dispatchEvent(composingKeyEvent('KeyS'));

    expect(saveSpy).not.toHaveBeenCalled();
  });

  // ----- single dispatch per keystroke (no double-fire) -----

  it('(22.4 AC5) a composing ⌥R opens EXACTLY ONE reset confirm via the host path', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    const openSpy = spyOn(component, 'onResetClick').and.callThrough();

    component.onKeydown(composingKeyEvent('KeyR'));

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('reset');
  });

  it('(22.4 AC5) a composing ⌥D from the authoritative host path opens ONE delete confirm; Proceeding once issues exactly ONE DELETE', async () => {
    await loaded('foo: 1\n');
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());
    const openSpy = spyOn(component, 'onDeleteClick').and.callThrough();

    // ONE physical keystroke reaches the ONE authoritative host capture site
    // (real macOS delivers a single keystroke to a single listener). It opens a
    // single delete confirm via the idempotent triggerDelete() surface — there
    // is exactly one host dispatch per keystroke.
    component.onKeydown(composingKeyEvent('KeyD'));

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('delete');
    expect(apiSpy.deleteNamespace).not.toHaveBeenCalled();

    // Proceed once → exactly one DELETE issued (no double-submit per keystroke).
    clickConfirmProceed();
    await fixture.whenStable();
    expect(apiSpy.deleteNamespace).toHaveBeenCalledTimes(1);
  });

  it('(22.4 AC5/AC4) the Monaco best-effort run() routes through the SAME triggerDelete() surface as the host path (parity, not a second uncoordinated dispatch)', async () => {
    await loaded('foo: 1\n');
    const { editor, actions } = makeEditorStub();
    component.onEditorEvent({
      type: 'init',
      editor: editor as unknown as NuMonacoEditorEvent['editor'],
    });
    const openSpy = spyOn(component, 'onDeleteClick').and.callThrough();

    // The Monaco descriptor's run() reaches the SAME triggerDelete() →
    // onDeleteClick surface, honouring the same enablement as the host path.
    // It is best-effort convenience only; correctness rides on the host
    // capture listener (AC 4). Invoking it opens the same single delete confirm.
    findAction(actions, 'delete').run(editor);

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('delete');
  });

  // ----- non-composing regression guard (non-composing path intact) -----

  it('(22.4 AC6) the non-composing path is unchanged — ⌥V (no composing flags) still validates and preventDefaults', async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );

    const evt = keyEvent('KeyV'); // no isComposing / keyCode 229 / key:'Dead'
    component.onKeydown(evt);

    expect(validateSpy).toHaveBeenCalledTimes(1);
    expect(evt.defaultPrevented).toBeTrue();
  });

  it('(22.4 AC6) a composing event WITHOUT Alt is still unhandled — no preventDefault, propagates', async () => {
    await loaded('foo: 1\n');
    const validateSpy = spyOn(component, 'onValidateBufferClick').and.returnValue(
      Promise.resolve(),
    );
    // altKey:false overrides the keyEvent default → not ours.
    const evt = composingKeyEvent('KeyV', { altKey: false });

    component.onKeydown(evt);

    expect(validateSpy).not.toHaveBeenCalled();
    expect(evt.defaultPrevented).toBeFalse();
  });

  // ---------------------------------------------------------------------
  // Custom confirmation modal + secondary-modal interaction contract
  // (ADR-018): no <p-confirmDialog>; Clone-idiom custom modal; focused
  // proceed button / Esc cancels; hasSecondaryPanelOpen predicate; no
  // backdrop; shared dark-red destructive style.
  // ---------------------------------------------------------------------

  // ----- <p-confirmDialog> removed; custom modal renders -----

  it('(22.3 AC1) the rendered panel contains no <p-confirmDialog>', async () => {
    await loaded('foo: 1\n');
    expect(
      fixture.nativeElement.querySelector('p-confirmDialog'),
    ).toBeNull();
  });

  it('(22.3 AC1/AC2) opening a confirm renders a <p-dialog> with the Clone-modal body/action classes and the supplied header/message', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    component.onResetClick();
    fixture.detectChanges();

    // The confirm dialog teleports its content to document.body; query the
    // whole document for the body/action classes + message.
    const body = document.querySelector(
      '[data-test="confirm-dialog"] .namespace-panel__clone-body',
    );
    const actions = document.querySelector(
      '[data-test="confirm-dialog"] .namespace-panel__clone-actions',
    );
    expect(body).withContext('confirm body uses clone-body class').not.toBeNull();
    expect(actions)
      .withContext('confirm action row uses clone-actions class')
      .not.toBeNull();

    const message = document.querySelector(
      '[data-test="confirm-message"]',
    ) as HTMLElement | null;
    expect(message?.textContent).toContain('Discard unsaved changes');
  });

  it('(22.3 AC3) the same modal surface is reused across reset / drift / delete variants', async () => {
    await loaded('foo: 1\n');

    // Reset variant.
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    expect(component.confirmRequest!.variant).toBe('reset');
    component.onConfirmDialogHide();
    expect(confirmOpen()).toBeFalse();

    // Delete variant — same `confirmDialogVisible` / `confirmRequest` surface.
    component.onDeleteClick();
    expect(component.confirmRequest!.variant).toBe('delete');
    component.onConfirmDialogHide();
    expect(confirmOpen()).toBeFalse();
  });

  // ----- focus the PRIMARY/proceed button; Esc cancels (ADR-018) -----

  it('(ADR-018 §e) onConfirmDialogShow focuses the primary/proceed button (reset/delete/discard)', fakeAsync(async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();

    const primaryBtn = document.createElement('button');
    document.body.appendChild(primaryBtn);
    component.confirmPrimaryBtnRef = {
      nativeElement: primaryBtn,
    } as ElementRef<HTMLButtonElement>;

    component.onConfirmDialogShow();
    tick(0);

    expect(document.activeElement).toBe(primaryBtn);
    document.body.removeChild(primaryBtn);
  }));

  it('(ADR-018 §e) drift variant focuses the Reload (recommended primary) button on show', fakeAsync(async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));

    void component.onSaveClick();
    tick();
    expect(component.confirmRequest!.variant).toBe('drift');

    const reloadBtn = document.createElement('button');
    document.body.appendChild(reloadBtn);
    component.confirmPrimaryBtnRef = {
      nativeElement: reloadBtn,
    } as ElementRef<HTMLButtonElement>;

    component.onConfirmDialogShow();
    tick(0);

    expect(document.activeElement).toBe(reloadBtn);
    document.body.removeChild(reloadBtn);
    // Settle the pending drift promise so no async leaks into later specs.
    component.onConfirmDialogHide();
    tick();
  }));

  it('(22.3 AC10) activating the focused Cancel button runs the cancel path — accept callback does NOT fire (no destructive effect)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onDeleteClick();
    apiSpy.deleteNamespace.and.returnValue(Promise.resolve());

    // Enter on the focused Cancel button activates it natively → cancel path.
    clickConfirmCancel();

    expect(apiSpy.deleteNamespace).not.toHaveBeenCalled();
    expect(confirmOpen()).toBeFalse();
  });

  // ----- confirm-request state cleared on hide -----

  it('(22.3 AC19) onConfirmDialogHide clears confirmRequest so a stale request cannot leak', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    expect(component.confirmRequest).not.toBeNull();

    component.onConfirmDialogHide();

    expect(component.confirmRequest).toBeNull();
    expect(component.confirmDialogVisible).toBeFalse();
  });

  // ----- hasSecondaryPanelOpen predicate -----

  it('(22.3 AC12) hasSecondaryPanelOpen is true when the Clone modal is open, false otherwise', async () => {
    await loadedWithCloneSrc(['src']);
    expect(component.hasSecondaryPanelOpen).toBeFalse();

    component.onCloneClick();
    expect(component.hasSecondaryPanelOpen).toBeTrue();

    component.onCloneDialogVisibleChange(false);
    expect(component.hasSecondaryPanelOpen).toBeFalse();
  });

  it('(22.3 AC12) hasSecondaryPanelOpen is true when the confirmation modal is open, false otherwise', async () => {
    await loaded('foo: 1\n');
    expect(component.hasSecondaryPanelOpen).toBeFalse();

    component.buffer = 'foo: 2\n';
    component.onResetClick();
    expect(component.hasSecondaryPanelOpen).toBeTrue();

    component.onConfirmDialogHide();
    expect(component.hasSecondaryPanelOpen).toBeFalse();
  });

  // ----- transparent-mask modals (ADR-018) -----
  //
  // The secondary panels stay `[modal]="true"` but paint NO dimming backdrop.
  // PrimeNG v19 always renders a `.p-dialog-mask` wrapper; the no-dimming is
  // achieved by tagging that wrapper with `np-secondary-modal-mask` (whose
  // background the component SCSS forces transparent). In modal mode the mask
  // keeps `pointer-events: auto`, so it ABSORBS clicks (no click-through). The
  // deterministic, headless-stable contract is the mask class + modal pointer
  // behaviour (computed background transparency is a real-browser concern).

  it('(ADR-018 §a) the Clone <p-dialog> mask carries the transparent np-secondary-modal-mask class and stays modal (pointer-events auto)', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const mask = document.querySelector('.p-dialog-mask') as HTMLElement | null;
    expect(mask).withContext('Clone dialog mask wrapper present').not.toBeNull();
    expect(
      mask!.classList.contains('np-secondary-modal-mask'),
    ).withContext('transparent mask class applied').toBeTrue();
    // Modal mode → the mask absorbs clicks (no click-through to the config panel).
    expect(mask!.style.pointerEvents).toBe('auto');
  });

  it('(ADR-018 §a) the confirmation <p-dialog> mask carries the transparent np-secondary-modal-mask class and stays modal (pointer-events auto)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const mask = document.querySelector('.p-dialog-mask') as HTMLElement | null;
    expect(mask).withContext('confirm dialog mask wrapper present').not.toBeNull();
    expect(
      mask!.classList.contains('np-secondary-modal-mask'),
    ).withContext('transparent mask class applied').toBeTrue();
    expect(mask!.style.pointerEvents).toBe('auto');
  });

  // ----- shared dark-red destructive class -----

  it('(22.3 AC16) the action-row Delete button carries namespace-panel__danger and drops severity="danger"', async () => {
    await loaded('foo: 1\n');

    const deleteBtn = fixture.nativeElement.querySelector(
      'button[data-test="delete-ns-btn"]',
    ) as HTMLButtonElement;
    expect(deleteBtn.classList.contains('namespace-panel__danger')).toBeTrue();
    // The bright PrimeNG danger severity is gone (no p-button-danger class and
    // no severity attribute set to danger).
    expect(deleteBtn.getAttribute('severity')).not.toBe('danger');
  });

  it('(22.3 AC16) the Delete-confirm Proceed button carries the shared namespace-panel__danger class', async () => {
    await loaded('foo: 1\n');
    component.onDeleteClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const proceedBtn = document.querySelector(
      '[data-test="confirm-proceed-btn"]',
    ) as HTMLButtonElement | null;
    expect(proceedBtn).withContext('Delete Proceed button rendered').not.toBeNull();
    expect(
      proceedBtn!.classList.contains('namespace-panel__danger'),
    ).toBeTrue();
  });

  it('(22.3 AC18) the Reset-confirm Proceed button is NOT destructive (no danger class)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const proceedBtn = document.querySelector(
      '[data-test="confirm-proceed-btn"]',
    ) as HTMLButtonElement | null;
    expect(proceedBtn).not.toBeNull();
    expect(
      proceedBtn!.classList.contains('namespace-panel__danger'),
    ).toBeFalse();
  });

  it('(22.3 AC18) the drift Reload / Overwrite buttons are NOT destructive (no danger class)', fakeAsync(async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('foo: server\n'));

    void component.onSaveClick();
    tick();
    fixture.detectChanges();
    expect(component.confirmRequest!.variant).toBe('drift');

    const reloadBtn = document.querySelector(
      '[data-test="confirm-reload-btn"]',
    ) as HTMLButtonElement | null;
    const overwriteBtn = document.querySelector(
      '[data-test="confirm-overwrite-btn"]',
    ) as HTMLButtonElement | null;
    expect(reloadBtn).not.toBeNull();
    expect(overwriteBtn).not.toBeNull();
    expect(
      reloadBtn!.classList.contains('namespace-panel__danger'),
    ).toBeFalse();
    expect(
      overwriteBtn!.classList.contains('namespace-panel__danger'),
    ).toBeFalse();

    // Settle the pending drift promise so no async leaks into later specs.
    component.onConfirmDialogHide();
    tick();
  }));

  // ---------------------------------------------------------------------
  // Public confirmDiscard() for dirty-close / dirty-nav (ADR-018).
  // ---------------------------------------------------------------------

  it('(ADR-018 §c) confirmDiscard opens the discard variant with the canonical header/message', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    void component.confirmDiscard();

    expect(confirmOpen()).toBeTrue();
    expect(component.confirmRequest!.variant).toBe('discard');
    expect(component.confirmRequest!.header).toBe('Unsaved changes');
    expect(component.confirmRequest!.message).toBe(
      'You have unsaved changes. Discard?',
    );
    // Settle so no async leaks into later specs.
    component.onConfirmDialogHide();
  });

  it('(ADR-018 §c) confirmDiscard Proceed resolves true AND really discards (buffer reset to serverYaml)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    expect(component.hasUnsavedChanges()).toBeTrue();

    const promise = component.confirmDiscard();
    clickConfirmProceed();

    await expectAsync(promise).toBeResolvedTo(true);
    expect(confirmOpen()).toBeFalse();
    // Real discard: the buffer is reverted to the server snapshot → clean.
    expect(component.buffer).toBe('foo: 1\n');
    expect(component.buffer).toBe(component.serverYaml);
    expect(component.hasUnsavedChanges()).toBeFalse();
  });

  it('(ADR-018 §c) confirmDiscard Proceed clears dirty-derived state (lastValidation / rawSaveError)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.lastValidation = {
      namespace: 'foo',
      ok: false,
      global_errors: ['bad'],
      entry_issues: [],
    };
    component.rawSaveError = 'boom';

    const promise = component.confirmDiscard();
    clickConfirmProceed();
    await promise;

    expect(component.lastValidation).toBeNull();
    expect(component.rawSaveError).toBeNull();
  });

  it('(ADR-018 §c) confirmDiscard Cancel resolves false AND leaves the buffer dirty (no reset)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    const promise = component.confirmDiscard();
    clickConfirmCancel();

    await expectAsync(promise).toBeResolvedTo(false);
    expect(confirmOpen()).toBeFalse();
    // Cancel = stay: the edits are kept intact.
    expect(component.buffer).toBe('foo: 2\n');
    expect(component.hasUnsavedChanges()).toBeTrue();
  });

  it('(ADR-018 §c) confirmDiscard Esc/X dismissal resolves false AND leaves the buffer dirty', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    const promise = component.confirmDiscard();
    component.onConfirmDialogHide();

    await expectAsync(promise).toBeResolvedTo(false);
    expect(component.buffer).toBe('foo: 2\n');
    expect(component.hasUnsavedChanges()).toBeTrue();
  });

  it('(ADR-018 §d) the discard-variant Proceed button is NOT destructive (no danger class)', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';

    void component.confirmDiscard();
    fixture.detectChanges();
    await fixture.whenStable();

    const proceedBtn = document.querySelector(
      '[data-test="confirm-proceed-btn"]',
    ) as HTMLButtonElement | null;
    expect(proceedBtn).not.toBeNull();
    expect(
      proceedBtn!.classList.contains('namespace-panel__danger'),
    ).toBeFalse();
    component.onConfirmDialogHide();
  });

  // ---------------------------------------------------------------------
  // Affirmative label is "Proceed" for every single-affirmative variant
  // (reset, delete, discard); dark-red is class-only (ADR-018).
  // ---------------------------------------------------------------------

  it('(ADR-018 §d) confirmProceedLabel is "Proceed" for reset / delete / discard', async () => {
    await loaded('foo: 1\n');

    component.buffer = 'foo: 2\n';
    component.onResetClick();
    expect(component.confirmProceedLabel).toBe('Proceed');
    component.onConfirmDialogHide();

    component.onDeleteClick();
    expect(component.confirmProceedLabel).toBe('Proceed');
    component.onConfirmDialogHide();

    void component.confirmDiscard();
    expect(component.confirmProceedLabel).toBe('Proceed');
    component.onConfirmDialogHide();
  });

  it('(ADR-018 §d) the delete-variant Proceed button shows "Proceed" but keeps the dark-red danger class', async () => {
    await loaded('foo: 1\n');
    component.onDeleteClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const proceedBtn = document.querySelector(
      '[data-test="confirm-proceed-btn"]',
    ) as HTMLButtonElement | null;
    expect(proceedBtn).not.toBeNull();
    expect(proceedBtn!.textContent).toContain('Proceed');
    expect(
      proceedBtn!.classList.contains('namespace-panel__danger'),
    ).toBeTrue();
    component.onConfirmDialogHide();
  });

  // ---------------------------------------------------------------------
  // handleSecondaryEscape: only the topmost secondary modal closes;
  // exactly one action per Escape (ADR-018).
  // ---------------------------------------------------------------------

  it('(ADR-018 §b) handleSecondaryEscape closes ONLY the confirm modal (topmost) and returns true', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    expect(component.confirmDialogVisible).toBeTrue();

    const consumed = component.handleSecondaryEscape();

    expect(consumed).toBeTrue();
    expect(component.confirmDialogVisible).toBeFalse();
  });

  it('(ADR-018 §b) handleSecondaryEscape closes ONLY the Clone modal when no confirm is open and returns true', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    expect(component.cloneDialogVisible).toBeTrue();
    expect(component.confirmDialogVisible).toBeFalse();

    const consumed = component.handleSecondaryEscape();

    expect(consumed).toBeTrue();
    expect(component.cloneDialogVisible).toBeFalse();
  });

  it('(ADR-018 §b) handleSecondaryEscape returns false when no secondary modal is open (host then closes the config panel)', async () => {
    await loaded('foo: 1\n');
    expect(component.hasSecondaryPanelOpen).toBeFalse();

    expect(component.handleSecondaryEscape()).toBeFalse();
  });

  it('(ADR-018 §b) confirm wins over Clone when both flags are set — only the confirm closes (no cascade)', async () => {
    await loadedWithCloneSrc(['src']);
    // Force both secondary flags on (e.g. a confirm stacked over Clone).
    component.cloneDialogVisible = true;
    component.confirmRequest = {
      header: 'Unsaved changes',
      message: 'Discard unsaved changes?',
      variant: 'reset',
    };
    component.confirmDialogVisible = true;

    const consumed = component.handleSecondaryEscape();

    expect(consumed).toBeTrue();
    // Only the confirm closed; the Clone modal is still open (no cascade).
    expect(component.confirmDialogVisible).toBeFalse();
    expect(component.cloneDialogVisible).toBeTrue();
  });

  // ---------------------------------------------------------------------
  // Secondary modals are modal but NON-draggable (ADR-018). Read the PrimeNG
  // Dialog inputs back off the component instance so the test FAILS if someone
  // flips `draggable` on or drops `modal`.
  // ---------------------------------------------------------------------

  /**
   * Resolve a PrimeNG `Dialog` directive instance by its host `data-test`
   * attribute. `modal` / `draggable` are `booleanAttribute`-transformed inputs,
   * so reading them back yields the resolved boolean the template bound.
   */
  function dialogInstanceByTestId(testId: string): Dialog {
    const debugEl = fixture.debugElement
      .queryAll(By.directive(Dialog))
      .find(
        (de) =>
          (de.nativeElement as HTMLElement).getAttribute('data-test') ===
          testId,
      );
    expect(debugEl)
      .withContext(`p-dialog[data-test="${testId}"] present`)
      .toBeTruthy();
    return debugEl!.componentInstance as Dialog;
  }

  it('(ADR-018 §a) the confirmation <p-dialog> is modal and NON-draggable', async () => {
    await loaded('foo: 1\n');
    component.buffer = 'foo: 2\n';
    component.onResetClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const confirmDialog = dialogInstanceByTestId('confirm-dialog');
    expect(confirmDialog.modal).toBe(true);
    expect(confirmDialog.draggable).toBe(false);
    component.onConfirmDialogHide();
  });

  it('(ADR-018 §a) the Clone <p-dialog> is modal and NON-draggable', async () => {
    await loadedWithCloneSrc(['src']);
    component.onCloneClick();
    fixture.detectChanges();
    await fixture.whenStable();

    const cloneDialog = dialogInstanceByTestId('clone-dialog');
    expect(cloneDialog.modal).toBe(true);
    expect(cloneDialog.draggable).toBe(false);
  });
});
