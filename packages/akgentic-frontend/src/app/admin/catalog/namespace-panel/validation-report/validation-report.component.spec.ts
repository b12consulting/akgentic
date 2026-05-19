import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';

import { NamespaceValidationReport } from '../../../../models/catalog.interface';
import { ValidationReportComponent } from './validation-report.component';

/**
 * Isolated unit specs for `ValidationReportComponent`. The sub-component
 * is pure presentational — no `ApiService` / `MessageService` /
 * `ConfirmationService` injection needed. Tests drive the two inputs and
 * assert DOM output + clearRequested emissions.
 */
describe('ValidationReportComponent', () => {
  let fixture: ComponentFixture<ValidationReportComponent>;
  let component: ValidationReportComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ValidationReportComponent, NoopAnimationsModule],
    }).compileComponents();
  });

  function build(): void {
    fixture = TestBed.createComponent(ValidationReportComponent);
    component = fixture.componentInstance;
  }

  // -------------------------------------------------------------------
  // Branch (a): nothing — both inputs null → zero DOM.
  // -------------------------------------------------------------------
  it('(11.4 AC7 branch a) nothing to render — zero DOM when both inputs null', () => {
    build();
    fixture.componentRef.setInput('report', null);
    fixture.componentRef.setInput('rawError', null);
    fixture.detectChanges();

    // No [data-test="validation-report"] element in the DOM.
    const root = fixture.nativeElement.querySelector(
      '[data-test="validation-report"]',
    );
    expect(root).toBeNull();

    // No Clear button either.
    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    );
    expect(clearBtn).toBeNull();
  });

  // -------------------------------------------------------------------
  // Branch (b): clean pass — report.ok === true.
  // -------------------------------------------------------------------
  it('(11.4 AC7 branch b) clean pass — renders "Validation passed" + Clear button, no findings', () => {
    build();
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: true,
      global_errors: [],
      entry_issues: [],
    };
    fixture.componentRef.setInput('report', report);
    fixture.componentRef.setInput('rawError', null);
    fixture.detectChanges();

    const root = fixture.nativeElement.querySelector(
      '[data-test="validation-report"]',
    ) as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.classList.contains('validation-report--ok')).toBeTrue();
    expect(root!.textContent).toContain('Validation passed');

    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    );
    expect(clearBtn).not.toBeNull();

    // No findings lists.
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="validation-report-global"]',
      ),
    ).toBeNull();
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="validation-report-entries"]',
      ),
    ).toBeNull();
  });

  // -------------------------------------------------------------------
  // Branch (c): report with findings.
  // -------------------------------------------------------------------
  it('(11.4 AC7 branch c) findings — renders header, global + entry rows, Clear button', () => {
    build();
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: false,
      global_errors: ['bad-ref'],
      entry_issues: [
        { entry_id: 'a', kind: 'agent', errors: ['missing-field'] },
      ],
    };
    fixture.componentRef.setInput('report', report);
    fixture.componentRef.setInput('rawError', null);
    fixture.detectChanges();

    const root = fixture.nativeElement.querySelector(
      '[data-test="validation-report"]',
    ) as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.classList.contains('validation-report--issues')).toBeTrue();
    expect(root!.textContent).toContain('Validation report (issues)');
    expect(root!.textContent).toContain('bad-ref');
    expect(root!.textContent).toContain('a');
    expect(root!.textContent).toContain('missing-field');

    const globalList = fixture.nativeElement.querySelector(
      '[data-test="validation-report-global"]',
    );
    expect(globalList).not.toBeNull();
    expect(globalList!.querySelectorAll('li').length).toBe(1);

    const entryList = fixture.nativeElement.querySelector(
      '[data-test="validation-report-entries"]',
    );
    expect(entryList).not.toBeNull();
    // Outer <li> for the issue + nested <li> for each error.
    expect(entryList!.querySelectorAll(':scope > li').length).toBe(1);

    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    );
    expect(clearBtn).not.toBeNull();
  });

  // -------------------------------------------------------------------
  // Branch (d): unstructured fallback.
  // -------------------------------------------------------------------
  it('(11.4 AC7 branch d) unstructured — renders <pre> raw text + Clear, no findings', () => {
    build();
    fixture.componentRef.setInput('report', null);
    fixture.componentRef.setInput(
      'rawError',
      'FastAPI detail: something broke',
    );
    fixture.detectChanges();

    const root = fixture.nativeElement.querySelector(
      '[data-test="validation-report"]',
    ) as HTMLElement | null;
    expect(root).not.toBeNull();
    expect(root!.classList.contains('validation-report--raw')).toBeTrue();

    const pre = fixture.nativeElement.querySelector(
      '[data-test="validation-report-raw"]',
    ) as HTMLElement | null;
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('FastAPI detail: something broke');

    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    );
    expect(clearBtn).not.toBeNull();

    // No findings lists in fallback branch.
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="validation-report-global"]',
      ),
    ).toBeNull();
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="validation-report-entries"]',
      ),
    ).toBeNull();
  });

  // -------------------------------------------------------------------
  // Precedence: rawError wins over report when both are non-null.
  // -------------------------------------------------------------------
  it('(11.4 AC7 precedence) rawError wins when both inputs non-null', () => {
    build();
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: false,
      global_errors: ['bad-ref'],
      entry_issues: [],
    };
    fixture.componentRef.setInput('report', report);
    fixture.componentRef.setInput('rawError', 'verbatim raw body');
    fixture.detectChanges();

    const root = fixture.nativeElement.querySelector(
      '[data-test="validation-report"]',
    ) as HTMLElement | null;
    expect(root).not.toBeNull();
    // Fallback branch (--raw) is active; no findings-branch classes.
    expect(root!.classList.contains('validation-report--raw')).toBeTrue();
    expect(root!.classList.contains('validation-report--issues')).toBeFalse();
    expect(root!.textContent).toContain('verbatim raw body');

    // The findings lists MUST NOT render in fallback mode.
    expect(
      fixture.nativeElement.querySelector(
        '[data-test="validation-report-global"]',
      ),
    ).toBeNull();
  });

  // -------------------------------------------------------------------
  // clearRequested output fires on Clear button click, no input mutation.
  // -------------------------------------------------------------------
  // -------------------------------------------------------------------
  // Story 11.7 AC 25 — findings-pane bounded height (NFR10)
  // -------------------------------------------------------------------
  it('(11.7 AC25) findings pane caps height at min(30vh, 240px) with internal overflow-y: auto', () => {
    build();
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: false,
      // 50 global errors — the findings list is far longer than the cap.
      global_errors: Array.from({ length: 50 }, (_, i) => `error ${i}`),
      entry_issues: [],
    };
    fixture.componentRef.setInput('report', report);
    fixture.componentRef.setInput('rawError', null);
    fixture.detectChanges();

    // Attach the host to the live document body so getComputedStyle and
    // getBoundingClientRect return real layout dimensions.
    const host = fixture.nativeElement as HTMLElement;
    document.body.appendChild(host);
    try {
      const styles = window.getComputedStyle(host);
      expect(styles.overflowY).toBe('auto');
      const rect = host.getBoundingClientRect();
      // The host is capped at min(30vh, 240px); under any viewport the
      // upper bound is 240. Allow a 1-px tolerance for rounding.
      expect(rect.height).toBeLessThanOrEqual(241);
    } finally {
      // Karma keeps the document around between tests — clean up so the
      // next test isn't polluted by a stray host element.
      if (host.parentElement === document.body) {
        document.body.removeChild(host);
      }
    }
  });

  it('(11.4 AC8) Clear button emits clearRequested and never mutates inputs', () => {
    build();
    const report: NamespaceValidationReport = {
      namespace: 'foo',
      ok: true,
      global_errors: [],
      entry_issues: [],
    };
    fixture.componentRef.setInput('report', report);
    fixture.componentRef.setInput('rawError', null);
    fixture.detectChanges();

    const emitSpy = spyOn(component.clearRequested, 'emit');

    const clearBtn = fixture.nativeElement.querySelector(
      '[data-test="clear-results-btn"]',
    ) as HTMLButtonElement;
    clearBtn.click();

    expect(emitSpy).toHaveBeenCalledTimes(1);
    // Inputs unchanged — component never mutates its own inputs.
    expect(component.report).toBe(report);
    expect(component.rawError).toBeNull();
  });
});
