import { Pipe, PipeTransform } from '@angular/core';
import { upperFirst } from 'lodash';
import { MarkedOptions, MarkedRenderer } from 'ngx-markdown';
import { combineLatest, Observable, ObservableInput, of } from 'rxjs';

// Open Markdown links in new tab.
export const markedOptionsFactory = (): MarkedOptions => {
  const renderer = new MarkedRenderer();

  renderer.link = ({ href, text }): string => {
    return `<a target="_blank" href="${href}">${text}</a>`;
  };

  return {
    renderer: renderer,
    gfm: true,
    breaks: true,
    pedantic: false,
  };
};

export function emptyableCombineLatest<T>(
  sources?: readonly ObservableInput<T>[]
): Observable<T[]> {
  if (!sources) return of([]);
  else return sources.length === 0 ? of([]) : combineLatest(sources);
}

@Pipe({
  name: 'makeAgentNameUserFriendly',
})
export class MakeAgentNameUserFriendlyPipe implements PipeTransform {
  transform(agentName: string): string {
    return makeAgentNameUserFriendly(agentName);
  }
}

export function makeAgentNameUserFriendly(agentName: string): string {
  // Given an actorName like "NAME-ROLE-BATCH-N-TASK-M"
  // Split the actorName by '-' and take the first part as the name
  const name = agentName.split('-')[0];
  // Split the actorName by '-' and take the second part as the role
  const role: string | undefined = upperFirst(
    agentName.split('-')[1]?.split('_')?.join(' ')
  );
  // Construct the label for the node
  const label = name + (role ? ` [${role}]` : '');
  return label;
}
