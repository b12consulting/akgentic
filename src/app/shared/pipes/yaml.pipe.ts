import { Pipe, PipeTransform } from '@angular/core';
import * as yaml from 'js-yaml';

@Pipe({
  name: 'yaml',
})
export class YamlPipe implements PipeTransform {
  transform(value: any): string {
    try {
      return yaml.dump(value); // Convert to YAML
    } catch (e) {
      console.error('Error converting to YAML:', e);
      return 'Error converting to YAML';
    }
  }
}
