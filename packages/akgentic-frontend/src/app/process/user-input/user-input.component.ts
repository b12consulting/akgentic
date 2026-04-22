import { CommonModule } from '@angular/common';
import { Component, inject, Input, OnInit, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';

import { ButtonModule } from 'primeng/button';
import { DropdownModule } from 'primeng/dropdown';
import { FloatLabelModule } from 'primeng/floatlabel';
import { MultiSelectModule } from 'primeng/multiselect';
import { TextareaModule } from 'primeng/textarea';
import { MentionModule } from 'angular-mentions';

import { makeAgentNameUserFriendly } from '../../lib/util';
import { ConfigService } from '../../services/config.service';

import { ApiService } from '../../services/api.service';
import { ChatService } from '../../services/chat.service';
import { ContextService } from '../../services/context.service';
import { GraphDataService, HUMAN_ROLE } from '../../services/graph-data.service';

import { ENTRY_POINT_NAME } from '../../models/chat-message.model';
import { NodeInterface } from '../../models/types';

@Component({
  selector: 'app-user-input',
  imports: [
    CommonModule,
    FormsModule,
    TextareaModule,
    FloatLabelModule,
    ButtonModule,
    DropdownModule,
    MultiSelectModule,
    MentionModule,
  ],
  templateUrl: './user-input.component.html',
  styleUrl: './user-input.component.scss',
})
export class ProcessUserInputComponent implements OnInit {
  @Input() processId!: string;

  apiService: ApiService = inject(ApiService);
  chatService: ChatService = inject(ChatService);
  contextService: ContextService = inject(ContextService);
  graphDataService: GraphDataService = inject(GraphDataService);
  private config = inject(ConfigService);
  userInput: string = '';
  userInputEnterKeySubmit: boolean = this.config.userInputEnterKeySubmit;

  // Mention configuration
  mentionItems: { name: string; actorName: string; agentId: string }[] = [];
  // Dropdown configuration
  dropdownAgents: { label: string; value: string }[] = [];
  selectedAgents: string[] = [];
  // "Send as" human selector state (Story 7-1)
  humanAgents: NodeInterface[] = [];
  humanAgentOptions: { label: string; value: string }[] = [];
  selectedSender: string | null = null;
  private destroyRef = inject(DestroyRef);

  ngOnInit() {
    // Subscribe to nodes to populate mention items and dropdown agents
    this.graphDataService.nodes$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((nodes) => {
        const agents = nodes.filter(
          (n) => n.actorName.startsWith('@') && n.actorName !== ENTRY_POINT_NAME,
        );

        this.mentionItems = agents.map((node) => ({
          name: makeAgentNameUserFriendly(node.actorName),
          actorName: node.actorName,
          agentId: node.name,
        }));

        this.dropdownAgents = agents.map((node) => ({
          label: makeAgentNameUserFriendly(node.actorName),
          value: node.actorName,
        }));

        // Remove fired agents from selection
        this.selectedAgents = this.selectedAgents.filter((a) =>
          agents.some((n) => n.actorName === a),
        );

        // "Send as" human selector (Story 7-1, revised Story 7-3): populate
        // humanAgents / humanAgentOptions from the same emission. Visibility is
        // template-driven by humanAgents.length > 1. Routing wiring is
        // Story 7-2. Story 7-3 drops the actorName !== ENTRY_POINT_NAME clause
        // so @Human is a first-class selectable sender (ADR-007 Revision
        // 2026-04-15, FR2 amendment).
        this.humanAgents = nodes.filter((n) => n.role === HUMAN_ROLE);
        this.humanAgentOptions = this.humanAgents.map((n) => ({
          label: makeAgentNameUserFriendly(n.actorName),
          value: n.actorName,
        }));

        // NFR1 / Story 7-2: keep selectedSender coherent with the live roster.
        // Clear the selection when the chosen sender is fired, OR when the
        // non-entry-point human count drops below 2 (dropdown hidden — selection
        // would be invisible and stale).
        if (
          this.selectedSender !== null &&
          (this.humanAgents.length < 2 ||
            !this.humanAgents.some((n) => n.actorName === this.selectedSender))
        ) {
          this.selectedSender = null;
        }
      });
  }

  /**
   * Comma-joined friendly labels of the current dropdown selection, e.g.
   * `@AgentA, @AgentB`. Used by the Send-to echo indicator in the template.
   * Empty string when no agent is selected (broadcast case).
   */
  get selectedAgentsDisplay(): string {
    return this.selectedAgents.map(makeAgentNameUserFriendly).join(', ');
  }

  /**
   * Redundant convenience for clearing the Send-to dropdown selection.
   * The p-multiSelect's own `[showClear]` chip remains the canonical clear
   * control; this method backs the `×` button on the Send-to echo indicator.
   */
  clearSendTo(): void {
    this.selectedAgents = [];
  }

  async sendMessage() {
    if (!this.contextService.currentTeamRunning$.value || !this.userInput || this.userInput.trim() === '') {
      return;
    }

    const hasSender = this.selectedSender !== null && this.selectedSender !== '';
    const hasRecipients = this.selectedAgents.length > 0;

    if (hasSender && hasRecipients) {
      // Priority 1: explicit sender + explicit recipients
      for (const recipient of this.selectedAgents) {
        await this.apiService.sendMessageFromTo(
          this.processId, this.selectedSender!, recipient, this.userInput,
        );
      }
    } else if (hasSender && !hasRecipients) {
      // Priority 2: explicit sender, no recipient -> first dropdown agent
      const defaultRecipient = this.dropdownAgents[0]?.value;
      if (!defaultRecipient) {
        // AC #3: no candidate recipient exists -> do not send, preserve input
        return;
      }
      await this.apiService.sendMessageFromTo(
        this.processId, this.selectedSender!, defaultRecipient, this.userInput,
      );
    } else if (hasRecipients) {
      // Priority 3: default sender + explicit recipients (Story 3-1 preserved)
      for (const agentName of this.selectedAgents) {
        await this.apiService.sendMessage(this.processId, this.userInput, agentName);
      }
    } else {
      // Priority 4: broadcast (Story 3-1 preserved)
      await this.apiService.sendMessage(this.processId, this.userInput);
    }

    this.userInput = '';
  }

  selectAgent = (item: any) => {
    return `${item.name} `;
  };
}
