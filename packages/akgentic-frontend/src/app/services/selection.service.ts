import { inject, Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { AkgentService } from './akgent.service';
import { ApiService } from './api.service';
import { ChatService } from './chat.service';
import { ContextService } from './context.service';

import { SentMessage } from '../models/message.types';
import { ChatMessageInterface, NodeInterface } from '../models/types';

export interface Selectable {
  type: 'tree-node' | 'message' | 'graph-node';
  data: NodeInterface | ChatMessageInterface;
}

@Injectable()
export class SelectionService {
  modalVisible$ = new BehaviorSubject<boolean>(false);
  userRequest$: BehaviorSubject<SentMessage> = new BehaviorSubject<SentMessage>(
    {} as SentMessage
  );

  akgentService: AkgentService = inject(AkgentService);
  apiService: ApiService = inject(ApiService);
  chatService: ChatService = inject(ChatService);
  contextService: ContextService = inject(ContextService);

  handleSelection(selection: Selectable): void {
    if (!selection.data) {
      this.clearSelection();
      return;
    }

    if (selection.data.humanRequests) {
      this.handleHumanRequest(selection.data);
    } else {
      this.clearSelection();
    }

    // Use agent_id for selection (primary) or fallback to name
    const agentId = selection.data.name;
    this.akgentService.select(agentId, selection.data.actorName);
  }

  private handleHumanRequest(node: NodeInterface | ChatMessageInterface): void {
    if (!node.humanRequests || node.humanRequests?.length === 0) {
      this.clearSelection();
      this.akgentService.select(node.name, node.actorName);
    } else {
      // For now, only handle the first human request
      this.userRequest$.next(node.humanRequests[0]);
      this.modalVisible$.next(true);
    }
  }

  private clearSelection(): void {
    this.userRequest$.next({} as SentMessage);
    this.modalVisible$.next(false);
  }

  async onSave(userInput: string, message: SentMessage): Promise<void> {
    const teamId = this.contextService.currentProcessId$.value;
    if (!teamId) {
      console.error('Cannot process human input: no team selected');
      return;
    }
    try {
      // message.id is the SentMessage ID; use it as the message_id for V2 human-input
      await this.apiService.processHumanInput(teamId, userInput, message.id);
    } catch (error) {
      console.error('Failed to process human input:', error);
    } finally {
      this.modalVisible$.next(false);
    }
  }
}
