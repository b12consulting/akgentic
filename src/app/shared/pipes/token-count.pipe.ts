import { Pipe, PipeTransform } from '@angular/core';

/**
 * Compact integer formatter for token counts (Epic 26 / ADR-022). Renders large
 * counts with a one-decimal k / M suffix and small counts (`< 1000`) verbatim:
 *   - `412      → "412"`
 *   - `12_031   → "12.0k"`
 *   - `1_200_000 → "1.2M"`
 *
 * Null / undefined / non-finite inputs render as `"0"`. Negative values are not
 * expected on the wire (token counts are non-negative) and are passed through
 * the same magnitude logic for robustness.
 */
@Pipe({
  name: 'tokenCount',
  standalone: true,
})
export class TokenCountPipe implements PipeTransform {
  transform(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return '0';
    }
    const abs = Math.abs(value);
    if (abs >= 1_000_000) {
      return (value / 1_000_000).toFixed(1) + 'M';
    }
    if (abs >= 1_000) {
      return (value / 1_000).toFixed(1) + 'k';
    }
    return String(value);
  }
}
