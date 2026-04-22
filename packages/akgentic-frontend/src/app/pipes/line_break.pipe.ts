import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'lineBreak',
  standalone: true,
})
export class LineBreakPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return value.replace(/\n/g, '<br/>');
  }
}
