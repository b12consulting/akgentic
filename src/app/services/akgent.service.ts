import { inject, Injectable } from '@angular/core';
import { ApiService } from '../services/api.service';
import { BehaviorSubject, Subject } from 'rxjs';

export class Akgent {
  name!: string;
  agentId!: string;
}

@Injectable({
  providedIn: 'root',
})
export class AkgentService {
  apiService: ApiService = inject(ApiService);

  selectedAkgent$: BehaviorSubject<Akgent | null> =
    new BehaviorSubject<Akgent | null>(null);

  select(agentId: string, actorName: string): void {
    this.selectedAkgent$.next({
      name: actorName,
      agentId: agentId,
    });
  }

  unselect(): void {
    this.selectedAkgent$.next(null);
  }

  isSavingState: { [key: string]: boolean } = {};

  async sendMessage(
    processId: string,
    agentId: string,
    userInput: string
  ): Promise<void> {
    await this.apiService.sendMessage(processId, userInput, agentId);
  }
}
