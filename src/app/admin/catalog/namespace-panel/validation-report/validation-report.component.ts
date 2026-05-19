import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { TagModule } from 'primeng/tag';
import { ButtonModule } from 'primeng/button';

import { NamespaceValidationReport } from '../../../../models/catalog.interface';

/**
 * ValidationReportComponent — presentational renderer for the four
 * validation-report states used by `NamespacePanelComponent`:
 *
 *   (a) nothing:  `report === null && rawError === null`  → no DOM.
 *   (b) clean:    `report.ok === true`                    → "Validation passed".
 *   (c) findings: `report.ok === false`                   → header + rows.
 *   (d) fallback: `rawError !== null`                     → <pre> raw body.
 *
 * Branch (d) takes precedence when both `report` and `rawError` are set
 * (parent's responsibility to avoid — the component must not crash).
 *
 * The component MUST NOT mutate its inputs; clearing the results pane is
 * delegated to the parent via the `clearRequested` output. The parent
 * (`NamespacePanelComponent`) owns `lastValidation` / `rawSaveError` state
 * and nulls both fields on its `onClearValidationClick()` handler.
 */
@Component({
  selector: 'app-validation-report',
  standalone: true,
  imports: [CommonModule, TagModule, ButtonModule],
  templateUrl: './validation-report.component.html',
  styleUrls: ['./validation-report.component.scss'],
})
export class ValidationReportComponent {
  /**
   * Structured report. `null` means "nothing to render" and the component
   * emits no DOM (branch (a) — parent's `*ngIf` can also skip mount but
   * the component is safe to mount unconditionally).
   */
  @Input() report: NamespaceValidationReport | null = null;

  /**
   * Unstructured fallback body surfaced by a 422 whose payload is NOT a
   * `NamespaceValidationReport` (reused from Story 11.3's `rawSaveError`
   * flow). Takes precedence over `report` (branch (d) AC 7).
   */
  @Input() rawError: string | null = null;

  /**
   * Fired when the user clicks the "Clear results" button. Parent is
   * responsible for nulling both fields — the component never mutates
   * its own inputs (AC 8).
   */
  @Output() clearRequested = new EventEmitter<void>();
}
