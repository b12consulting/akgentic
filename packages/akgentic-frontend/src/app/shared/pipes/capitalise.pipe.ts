import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
  name: 'capitalize',
})
export class CapitalizePipe implements PipeTransform {
  transform(value: string): string {
    if (!value) {
      return value; // Return the value as-is if it's null or empty
    }
    const res = value.charAt(0).toUpperCase() + value.slice(1);
    return res.replace(/_/g, ' ');
  }
}
