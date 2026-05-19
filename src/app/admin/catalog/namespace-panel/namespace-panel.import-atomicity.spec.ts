import { CommonModule } from '@angular/common';
import { Component, forwardRef, Input } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import {
  ControlValueAccessor,
  FormsModule,
  NG_VALUE_ACCESSOR,
} from '@angular/forms';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ConfirmationService, MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { TooltipModule } from 'primeng/tooltip';

import { ApiService } from '../../../services/api.service';
import { HttpError } from '../../../services/fetch.service';
import { NamespacePanelComponent } from './namespace-panel.component';
import { ValidationReportComponent } from './validation-report/validation-report.component';

/**
 * Story 11.3 AC 12 — import-atomicity focused spec (NFR5 / ADR-011 D4).
 *
 * This spec verifies the FRONTEND-SIDE contract of the atomicity guarantee:
 * when `importNamespace` rejects with a 422, the panel MUST leave its
 * `serverYaml` snapshot unchanged and keep `buffer` equal to the attempted
 * (invalid) YAML — NO overwrite of the last-known-good server state. A
 * subsequent re-load (via `ngOnChanges` with the same namespace, or a
 * `loadNamespace` call) MUST surface the original baseline.
 *
 * The server-side physical-atomicity guarantee (no partial writes on
 * rejection) is out of scope for a Karma spec — it is indirectly exercised
 * whenever an E2E or integration harness runs against a real server.
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

// A minimal, schema-valid baseline bundle: one namespace, one team entry,
// one agent entry — enough to round-trip through export → import.
const BASELINE_YAML = `namespace: atomicity-test
name: Atomicity Test
description: baseline for NFR5 import-atomicity spec
entries:
  - kind: team
    id: team-1
    model_type: team_v2
    description: a team
    payload:
      members: [agent-1]
  - kind: agent
    id: agent-1
    model_type: agent_v2
    description: an agent
    payload:
      role: assistant
`;

/**
 * INVALID_YAML mutation: remove the required `kind` field on the agent
 * entry. The server's Pydantic schema requires `kind` on every entry, so
 * this bundle fails validation at parse time and triggers a 422.
 */
const INVALID_YAML = `namespace: atomicity-test
name: Atomicity Test
description: baseline for NFR5 import-atomicity spec
entries:
  - kind: team
    id: team-1
    model_type: team_v2
    description: a team
    payload:
      members: [agent-1]
  - id: agent-1
    model_type: agent_v2
    description: an agent
    payload:
      role: assistant
`;

describe('NamespacePanelComponent — import atomicity (NFR5)', () => {
  let fixture: ComponentFixture<NamespacePanelComponent>;
  let component: NamespacePanelComponent;
  let apiSpy: jasmine.SpyObj<ApiService>;
  let messageSpy: jasmine.SpyObj<MessageService>;
  let confirmationSpy: jasmine.SpyObj<ConfirmationService>;

  beforeEach(async () => {
    apiSpy = jasmine.createSpyObj('ApiService', [
      'exportNamespace',
      'importNamespace',
    ]);
    messageSpy = jasmine.createSpyObj('MessageService', ['add']);
    // Real ConfirmationService instance + spy on .confirm — see
    // `namespace-panel.component.spec.ts` for the rationale (PrimeNG's
    // `<p-confirmDialog>` constructor subscribes to `requireConfirmation$`).
    confirmationSpy =
      new ConfirmationService() as jasmine.SpyObj<ConfirmationService>;
    spyOn(confirmationSpy, 'confirm').and.callThrough();

    await TestBed.configureTestingModule({
      imports: [NamespacePanelComponent, NoopAnimationsModule],
      providers: [
        { provide: ApiService, useValue: apiSpy },
        { provide: MessageService, useValue: messageSpy },
      ],
    })
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

  it(
    '(11.3 AC12 NFR5) invalid import → panel preserves server snapshot; reload returns baseline byte-for-byte',
    async () => {
      // FIRST exportNamespace call resolves with the baseline.
      apiSpy.exportNamespace.and.returnValue(Promise.resolve(BASELINE_YAML));

      fixture = TestBed.createComponent(NamespacePanelComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('namespace', 'atomicity-test');
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(component.serverYaml).toBe(BASELINE_YAML);
      expect(component.buffer).toBe(BASELINE_YAML);

      // User flips into edit mode and types in the INVALID bundle.
      await component.onEditClick();
      component.buffer = INVALID_YAML;
      expect(component.mode).toBe('edit');

      // importNamespace rejects with a 422 carrying a structured report.
      apiSpy.importNamespace.and.returnValue(
        Promise.reject(
          new HttpError('Request failed', 422, {
            namespace: 'atomicity-test',
            ok: false,
            global_errors: ['entry missing required field: kind'],
            entry_issues: [],
          }),
        ),
      );

      // Save the invalid bundle.
      await component.onSaveClick();

      // Post-422 assertions:
      //  - mode stays edit,
      //  - buffer is preserved (user's invalid YAML is not lost),
      //  - serverYaml is UNCHANGED — still the pre-save baseline.
      // This is the frontend-testable equivalent of "the server did not
      // persist a partial write".
      expect(component.mode).toBe('edit');
      expect(component.buffer).toBe(INVALID_YAML);
      expect(component.serverYaml).toBe(BASELINE_YAML);

      // Now simulate a reload: the SECOND exportNamespace call returns the
      // original baseline byte-for-byte. The panel's serverYaml must match
      // — confirming the server state is unchanged (from the client's PoV)
      // and the panel wires it cleanly back into its snapshot.
      apiSpy.exportNamespace.and.returnValue(Promise.resolve(BASELINE_YAML));
      fixture.componentRef.setInput('namespace', 'atomicity-test-reload');
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(component.serverYaml).toBe(BASELINE_YAML);
      expect(component.buffer).toBe(BASELINE_YAML);
    },
  );
});
