import { CommonModule } from '@angular/common';
import { Component, forwardRef, Input } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import {
  ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { RouterTestingModule } from '@angular/router/testing';
import yaml from 'js-yaml';
import { BehaviorSubject } from 'rxjs';

import { ConfirmationService, MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { TooltipModule } from 'primeng/tooltip';

import {
  NamespaceSummary,
  NamespaceValidationReport,
} from '../../../models/catalog.interface';
import { ApiService } from '../../../services/api.service';
import { HttpError } from '../../../services/fetch.service';
import { NamespacePanelComponent } from './namespace-panel.component';
import { NamespacePanelRouteComponent } from './namespace-panel-route.component';
import { ValidationReportComponent } from './validation-report/validation-report.component';

/**
 * Story 11.6 — route-shell component tests.
 *
 * Uses `RouterTestingModule` + a stubbed `ActivatedRoute.paramMap` (not
 * `RouterTestingHarness`) because every test mounts the shell directly
 * via `TestBed.createComponent` — we do not need the router to actually
 * navigate for these assertions. The separate `app.routes.spec.ts` covers
 * the route-registration / URL-parsing contract (AC 1, AC 15).
 *
 * The inner `<nu-monaco-editor>` is swapped with `StubMonacoEditorComponent`
 * via `TestBed.overrideComponent(NamespacePanelComponent, ...)` so the real
 * Monaco bundle does NOT load — mirrors the panel's own spec pattern.
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
  writeValue(_value: string): void {}
  registerOnChange(_fn: (value: string) => void): void {}
  registerOnTouched(_fn: () => void): void {}
  setDisabledState?(_isDisabled: boolean): void {}
}

function makeHttpError(status: number, body: unknown = ''): HttpError {
  return new HttpError('boom', status, body);
}

function summary(ns: string): NamespaceSummary {
  return { namespace: ns, name: ns, description: '' };
}

function cleanReport(namespace = 'foo'): NamespaceValidationReport {
  return {
    namespace,
    ok: true,
    global_errors: [],
    entry_issues: [],
  };
}

describe('NamespacePanelRouteComponent (Story 11.6)', () => {
  let fixture: ComponentFixture<NamespacePanelRouteComponent>;
  let component: NamespacePanelRouteComponent;
  let apiSpy: jasmine.SpyObj<ApiService>;
  let messageSpy: jasmine.SpyObj<MessageService>;
  let confirmationSpy: jasmine.SpyObj<ConfirmationService>;
  let paramMap$: BehaviorSubject<ReturnType<typeof convertToParamMap>>;

  async function buildFixture(namespace: string): Promise<void> {
    paramMap$.next(convertToParamMap({ namespace }));
    fixture = TestBed.createComponent(NamespacePanelRouteComponent);
    component = fixture.componentInstance;
  }

  beforeEach(async () => {
    apiSpy = jasmine.createSpyObj('ApiService', [
      'exportNamespace',
      'importNamespace',
      'validateNamespaceBuffer',
      'validatePersistedNamespace',
      'getNamespaces',
    ]);
    // Sensible defaults — individual tests override as needed.
    apiSpy.exportNamespace.and.returnValue(Promise.resolve(''));
    apiSpy.getNamespaces.and.returnValue(Promise.resolve([summary('foo')]));
    messageSpy = jasmine.createSpyObj('MessageService', ['add']);

    confirmationSpy =
      new ConfirmationService() as jasmine.SpyObj<ConfirmationService>;
    spyOn(confirmationSpy, 'confirm').and.callThrough();

    paramMap$ = new BehaviorSubject(convertToParamMap({ namespace: 'foo' }));

    await TestBed.configureTestingModule({
      imports: [
        NamespacePanelRouteComponent,
        NoopAnimationsModule,
        RouterTestingModule,
      ],
      providers: [
        { provide: ApiService, useValue: apiSpy },
        { provide: MessageService, useValue: messageSpy },
        {
          provide: ActivatedRoute,
          useValue: { paramMap: paramMap$.asObservable() },
        },
      ],
    })
      // Swap real Monaco for the stub — the panel is nested inside the shell.
      .overrideComponent(NamespacePanelComponent, {
        set: {
          imports: [
            CommonModule,
            FormsModule,
            ButtonModule,
            ConfirmDialogModule,
            DialogModule,
            InputTextModule,
            TooltipModule,
            StubMonacoEditorComponent,
            ValidationReportComponent,
          ],
          providers: [
            { provide: ConfirmationService, useValue: confirmationSpy },
          ],
        },
      })
      .compileComponents();
  });

  // ---------------------------------------------------------------------
  // AC 19 route-shell spec — six required tests
  // ---------------------------------------------------------------------

  it('(AC2) route activation with :namespace="foo" binds currentNamespace and mounts panel', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.currentNamespace).toBe('foo');
    expect(component.panel).toBeDefined();
    expect(component.panel!.namespace).toBe('foo');
    // Header text echoes the URL param.
    const header = fixture.nativeElement.querySelector(
      '.namespace-panel-route__title',
    ) as HTMLElement;
    expect(header.textContent).toContain('foo');
  });

  it('(AC3) existingNamespaces populated from apiService.getNamespaces on mount', async () => {
    apiSpy.getNamespaces.and.returnValue(
      Promise.resolve([summary('foo'), summary('bar')]),
    );

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(1);
    expect(component.existingNamespaces).toEqual(['foo', 'bar']);
    expect(component.panel!.existingNamespaces).toEqual(['foo', 'bar']);
  });

  it('(AC3) getNamespaces rejection leaves existingNamespaces empty, no throw', async () => {
    apiSpy.getNamespaces.and.returnValue(Promise.reject(new Error('boom')));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.existingNamespaces).toEqual([]);
  });

  it('(AC3) (saved) event triggers re-fetch of getNamespaces and refreshes list', async () => {
    apiSpy.getNamespaces.and.returnValue(Promise.resolve([summary('foo')]));
    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(1);

    // Second call returns a fresh list (a newly-cloned namespace appended).
    apiSpy.getNamespaces.and.returnValue(
      Promise.resolve([summary('foo'), summary('dst')]),
    );
    component.onSaved();
    await fixture.whenStable();

    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(2);
    expect(component.existingNamespaces).toEqual(['foo', 'dst']);
  });

  it('(AC6) 404 empty state — exportNamespace rejects → empty-state + back-to-home link in DOM', async () => {
    apiSpy.exportNamespace.and.returnValue(
      Promise.reject(makeHttpError(404, '')),
    );

    await buildFixture('nonexistent');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain("Unable to load namespace 'nonexistent'");
    const backLink = fixture.nativeElement.querySelector(
      'a[data-test="namespace-panel-route-back-home"]',
    ) as HTMLAnchorElement | null;
    expect(backLink).not.toBeNull();
    // RouterLink renders `href="/"` — the directive resolves the router
    // link to an anchor href at CD time.
    expect(backLink!.getAttribute('href')).toBe('/');
  });

  // ----- AC 10 parity smoke test ------------------------------------------------
  it('(AC10) parity smoke test — load → edit → save → validate-persisted → clone → edit → cancel', async () => {
    const srcYaml = `namespace: foo
user_id: null
entries:
  team-1:
    kind: team
    model_type: BaseTeamModel
    payload:
      description: "src ns"
`;
    apiSpy.exportNamespace.and.callFake((ns: string) => {
      if (ns === 'foo') {
        return Promise.resolve(srcYaml);
      }
      return Promise.resolve('namespace: new-ns\nuser_id: null\nentries: {}\n');
    });
    apiSpy.getNamespaces.and.returnValue(
      Promise.resolve([summary('foo'), summary('bar')]),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.validatePersistedNamespace.and.returnValue(
      Promise.resolve(cleanReport('foo')),
    );

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    expect(panel.serverYaml).toBe(srcYaml);
    expect(panel.mode).toBe('view');

    // --- Edit → Save ---
    await panel.onEditClick();
    panel.buffer = srcYaml + '# edited\n';
    await panel.onSaveClick();
    expect(panel.mode).toBe('view');
    expect(panel.serverYaml).toBe(srcYaml + '# edited\n');

    // --- Validate persisted ---
    // Clean reports now flash the view-mode Validate button instead of
    // populating the "Validation passed" pane — lastValidation stays null.
    await panel.onValidatePersistedClick();
    expect(panel.lastValidation).toBeNull();
    expect(panel.validationFlashPersisted).toBeTrue();

    // --- Clone (valid bundle yaml in buffer) ---
    // Set buffer to a clean bundle root mapping so rewriteNamespaceInYaml
    // succeeds, then invoke the Clone confirm path.
    panel.onCloneClick();
    panel.cloneDestNs = 'new-ns';
    await panel.onCloneConfirmClick();
    await fixture.whenStable();
    await fixture.whenStable();
    expect(panel.namespace).toBe('new-ns');
    expect(panel.mode).toBe('view');

    // --- Edit → Cancel (dirty) with ConfirmationService.accept ---
    confirmationSpy.confirm.and.callFake((cfg) => {
      cfg.accept!();
      return confirmationSpy;
    });
    await panel.onEditClick();
    panel.buffer = panel.serverYaml + '# dirty again\n';
    panel.onCancelClick();
    expect(panel.buffer).toBe(panel.serverYaml);
    expect(panel.mode).toBe('view');

    // Verify the parity bundle of YAML round-trips the rewritten namespace
    // field (belts-and-braces — ensures Clone really rewrote the bundle).
    const clonedPayload = apiSpy.importNamespace.calls
      .mostRecent()
      .args[0] as string;
    const parsed = yaml.load(clonedPayload) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('new-ns');
  });

  // ----- AC 11 NFR7 REST call budget --------------------------------------------
  it('(AC11) NFR7 REST budget — mount=2, +validate=+1, +save=+1, +clone=+3 (total 7)', async () => {
    const srcYaml = `namespace: foo
user_id: null
entries: {}
`;
    apiSpy.exportNamespace.and.callFake((ns: string) =>
      Promise.resolve(
        ns === 'foo'
          ? srcYaml
          : 'namespace: ' + ns + '\nuser_id: null\nentries: {}\n',
      ),
    );
    apiSpy.getNamespaces.and.returnValue(
      Promise.resolve([summary('foo')]),
    );
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));
    apiSpy.validatePersistedNamespace.and.returnValue(
      Promise.resolve(cleanReport('foo')),
    );

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    // Mount = 2 (exportNamespace + getNamespaces).
    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(1);
    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(1);

    const panel = component.panel!;

    // +Validate persisted (+1).
    await panel.onValidatePersistedClick();
    expect(apiSpy.validatePersistedNamespace).toHaveBeenCalledTimes(1);

    // +Save (+1 import).
    await panel.onEditClick();
    panel.buffer = srcYaml + '# edit\n';
    await panel.onSaveClick();
    await fixture.whenStable();
    // (saved) emits → shell re-fetches getNamespaces (expected +1).
    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(1);
    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(2);

    // +Clone (+1 import + 1 export on the destNs re-load + 1 getNamespaces
    // re-fetch from the shell's (saved) handler).
    panel.onCloneClick();
    panel.cloneDestNs = 'dst';
    await panel.onCloneConfirmClick();
    await fixture.whenStable();
    await fixture.whenStable();

    expect(apiSpy.importNamespace).toHaveBeenCalledTimes(2);
    // foo-mount + foo-drift-check-on-edit + dst-clone-reload = 3.
    // Post-review UX refinement added the drift-check export on Edit.
    expect(apiSpy.exportNamespace).toHaveBeenCalledTimes(3);
    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(3); // mount + (saved)×2

    // Tally — 2 (mount) + 1 (validate) + 1 (edit-drift-check) + 1 (save-import)
    //         + 1 (save-refresh-ns) + 1 (clone-import) + 1 (clone-reload-export)
    //         + 1 (clone-refresh-ns) = 9 total calls across all four spies.
    // AC 11's original milestone-grouped total was 7 (save refresh counted as
    // part of save, clone refresh counted as part of clone). The drift-check
    // on Edit adds +1 → 10 by that accounting, 9 at the spy level.
    const totalCalls =
      apiSpy.exportNamespace.calls.count() +
      apiSpy.getNamespaces.calls.count() +
      apiSpy.importNamespace.calls.count() +
      apiSpy.validatePersistedNamespace.calls.count() +
      apiSpy.validateNamespaceBuffer.calls.count();
    expect(totalCalls).toBe(9);
    // No polling / heartbeat / background fetches.
    expect(apiSpy.validateNamespaceBuffer).not.toHaveBeenCalled();
  });

  // ----- AC 8 clean buffer → no prompt on programmatic navigation ---------------
  it('(AC8) clean buffer — panel.hasUnsavedChanges returns false; guard would not prompt', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    expect(panel.hasUnsavedChanges()).toBeFalse();
    // The functional guard short-circuits on this path before any
    // ConfirmationService interaction — covered exhaustively in
    // namespace-panel.guard.spec.ts.
    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
  });

  // ----- AC 7 dirty buffer → guard would prompt (component-level check) ---------
  it('(AC7) dirty buffer — panel.hasUnsavedChanges returns true; ready for guard prompt', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    await panel.onEditClick();
    panel.buffer = 'key: modified\n';
    expect(panel.hasUnsavedChanges()).toBeTrue();
  });

  // ---------------------------------------------------------------------
  // Story 11.7 AC 8, 9, 10 — route-shell dirty indicator (FR15)
  // ---------------------------------------------------------------------

  it('(11.7 AC8, AC9) route shell renders dirty indicator with aria-label when panel is dirty', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    await panel.onEditClick();
    panel.buffer = 'key: modified\n';
    fixture.detectChanges();

    const indicator = fixture.nativeElement.querySelector(
      '[data-test="dirty-indicator-route"]',
    ) as HTMLElement | null;
    expect(indicator).not.toBeNull();
    expect(indicator!.getAttribute('aria-label')).toBe('Unsaved changes');
    expect(indicator!.textContent?.trim()).toBe('●');
  });

  it('(11.7 AC10) route shell dirty indicator absent when panel is clean', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    expect(panel.hasUnsavedChanges()).toBeFalse();

    const indicator = fixture.nativeElement.querySelector(
      '[data-test="dirty-indicator-route"]',
    );
    expect(indicator).toBeNull();
  });

  it('(11.7 AC10) route shell dirty indicator removed after Save 2xx (panel flips back to view)', async () => {
    apiSpy.exportNamespace.and.returnValue(Promise.resolve('key: value\n'));
    apiSpy.importNamespace.and.returnValue(Promise.resolve([]));

    await buildFixture('foo');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panel = component.panel!;
    await panel.onEditClick();
    panel.buffer = 'key: modified\n';
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="dirty-indicator-route"]',
      ),
    ).not.toBeNull();

    await panel.onSaveClick();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(panel.mode).toBe('view');
    expect(panel.hasUnsavedChanges()).toBeFalse();
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="dirty-indicator-route"]',
      ),
    ).toBeNull();
  });
});
