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

import { makeAgentNameUserFriendly } from '../../../../shared/util/util';
import { ConfigService } from '../../../../core/config/config.service';

import { ApiService } from '../../../../core/http/api.service';
import { ChatService } from '../../selectors/chat.selector';
import { ContextService } from '../../../../core/context/context.service';
import { GraphDataService, HUMAN_ROLE } from '../../selectors/graph.selector';
import { IngestionService } from '../../event/ingestion.service';

import { ENTRY_POINT_NAME } from '../../selectors/chat-message.model';
import { CommandDescriptor } from '../../../../protocol/message.types';
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
  ingestionService: IngestionService = inject(IngestionService);
  private config = inject(ConfigService);
  userInput: string = '';
  userInputEnterKeySubmit: boolean = this.config.userInputEnterKeySubmit;

  // Mention configuration
  mentionItems: { name: string; actorName: string; agentId: string }[] = [];

  /**
   * ADR-013: live snapshot of the graph nodes — used to derive the
   * supervisor / entry-point default target for the main chat (Task 2.1).
   */
  private nodes: NodeInterface[] = [];
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
        this.nodes = nodes;
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

  /**
   * Story 19-1 (ADR-016 §Decision 1) — send-origin key source for the just-sent
   * signal. Wrapped as an overridable method so specs can pin a deterministic
   * value. `apiService.sendMessage*` returns no message id synchronously
   * (Open Question 1), so the panel keys the top-anchor by send time and matches
   * the first user-originated (Rule 1) message at/after this timestamp.
   */
  protected nextJustSentKey(): string {
    return String(Date.now());
  }

  async sendMessage() {
    if (!this.contextService.currentTeamRunning$.value || !this.userInput || this.userInput.trim() === '') {
      return;
    }

    const hasSender = this.selectedSender !== null && this.selectedSender !== '';
    const hasRecipients = this.selectedAgents.length > 0;

    // Capture the send-origin key BEFORE dispatch so it precedes the answer's
    // arrival; emitted only after a dispatch actually happens (never on the
    // early-return guards above or the no-candidate-recipient guard below).
    const justSentKey = this.nextJustSentKey();

    if (hasSender && hasRecipients) {
      // Priority 1: explicit sender + explicit recipients
      for (const recipient of this.selectedAgents) {
        await this.apiService.sendMessageFromTo(
          this.processId, this.selectedSender!, recipient, this.userInput,
        );
      }
      this.chatService.emitJustSent(justSentKey);
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
      this.chatService.emitJustSent(justSentKey);
    } else if (hasRecipients) {
      // Priority 3: default sender + explicit recipients (Story 3-1 preserved)
      for (const agentName of this.selectedAgents) {
        await this.apiService.sendMessage(this.processId, this.userInput, agentName);
      }
      this.chatService.emitJustSent(justSentKey);
    } else {
      // Priority 4: broadcast (Story 3-1 preserved)
      await this.apiService.sendMessage(this.processId, this.userInput);
      this.chatService.emitJustSent(justSentKey);
    }

    this.userInput = '';
  }

  selectAgent = (item: any) => {
    return `${item.name} `;
  };

  /**
   * ADR-013 §3 — resolve the SINGLE agent the `/` command list targets in the
   * MAIN chat, by raw actor `name` (the Send-to key); `targetedAgentId()` then
   * maps that name to the agent's `agent_id` for the `store.commands` lookup:
   *   - exactly one "Send to" recipient   → that recipient;
   *   - zero recipients                   → supervisor / entry-point default;
   *   - multiple recipients (broadcast)   → null (no single target, AC-4).
   * Returns null when no single target resolves (the `/` list is then empty).
   */
  private resolveTargetedAgent(): string | null {
    if (this.selectedAgents.length > 1) return null;
    if (this.selectedAgents.length === 1) return this.selectedAgents[0];
    return this.defaultSupervisorTarget();
  }

  /**
   * ADR-013 §3 — supervisor / entry-point default for the main chat when no
   * "Send to" recipient is selected. Derived from existing graph state (no new
   * backend field): the agent whose parent is the `@Human` entry-point node.
   * Falls back to the first non-human agent when the parent link is absent,
   * and to null when no candidate agent exists.
   */
  private defaultSupervisorTarget(): string | null {
    const candidates = this.nodes.filter(
      (n) => n.actorName.startsWith('@') && n.actorName !== ENTRY_POINT_NAME,
    );
    if (candidates.length === 0) return null;
    const entry = this.nodes.find((n) => n.actorName === ENTRY_POINT_NAME);
    if (entry) {
      const child = candidates.find((n) => n.parentId === entry.name);
      if (child) return child.actorName;
    }
    return candidates[0].actorName;
  }

  /**
   * Epic 17 (ADR-014 §2 / ADR-013 §3) — resolve the `/` target to its immutable
   * `agent_id`. `resolveTargetedAgent()` still returns an actor `name`; this maps
   * that name → the matching graph node's `name` field (which IS the `agent_id`
   * UUID, the same value placed in `mentionItems[i].agentId`). A name that does
   * not resolve to a live node yields `null` → empty `/` list (acceptable
   * transient, same posture as the just-hired case, ADR-013 §3). Keying by
   * `agent_id` (not the friendly name) is the ADR-013 keying fix: a display-name
   * reused after a fire/re-hire can never serve the wrong agent's commands.
   */
  private targetedAgentId(): string | null {
    const target = this.resolveTargetedAgent();
    if (!target) return null;
    const node = this.nodes.find((n) => n.actorName === target);
    return node ? node.name : null;
  }

  /**
   * ADR-013 / Epic 17 (ADR-014) — the `/` mention candidate list: the resolved
   * targeted agent's command descriptors (read from `store.commands` by
   * `agent_id`), mapped to dropdown items (`name` + `description` + ordered
   * `args`). Empty when no single target resolves (none/ambiguous, AC-4) or no
   * CommandsAnnouncedEvent has arrived yet for it (AC-6).
   */
  get commandItems(): {
    name: string;
    description: string;
    args: CommandDescriptor['args'];
  }[] {
    const agentId = this.targetedAgentId();
    if (!agentId) return [];
    const descriptors = this.ingestionService.commands.snapshot(agentId) ?? [];
    return descriptors
      // `_`-prefixed commands (e.g. `_expand_media_refs`) are internal, not
      // user-invocable — keep them out of the `/` dropdown.
      .filter((d) => !d.name.startsWith('_'))
      // `angular-mentions` renders a single flat list with no group headers, so
      // the best we can do is keep tool families adjacent: order by provenance
      // (`tool_card`) then command name. `.filter()` above returns a fresh
      // array, so sorting in place does not mutate the stored descriptors.
      .sort(
        (a, b) =>
          a.tool_card.localeCompare(b.tool_card) || a.name.localeCompare(b.name),
      )
      .map((d) => ({
        name: d.name,
        description: d.description,
        args: d.args,
      }));
  }

  /**
   * ADR-013 §2 — multi-trigger `angular-mentions` config. The `@` entry is
   * unchanged (AC-7); the `/` entry lists the targeted agent's commands and
   * inserts `/${name} ` via `selectCommand`. `allowSpace: false` closes the
   * dropdown at the first space (after the command name) so the user types
   * args freely; `maxItems`/`dropUp` mirror the `@` list.
   */
  get mentionConfig() {
    return {
      mentions: [
        {
          triggerChar: '@',
          labelKey: 'name',
          returnTrigger: true,
          allowSpace: true,
          mentionSelect: this.selectAgent,
          dropUp: true,
          maxItems: 10,
          items: this.mentionItems,
        },
        {
          triggerChar: '/',
          labelKey: 'name',
          allowSpace: false,
          mentionSelect: this.selectCommand,
          dropUp: true,
          maxItems: 10,
          // `angular-mentions` re-sorts every list by `labelKey` (here `name`)
          // unless told not to — that would clobber the tool-family ordering
          // `commandItems` builds. Opt out so our `tool_card`-then-name order
          // survives to the dropdown.
          disableSort: true,
          items: this.commandItems,
        },
      ],
    };
  }

  /**
   * ADR-013 §2 — insert `/${name} ` (leading slash + trailing space) for the
   * chosen command so the user keeps typing arguments. Does NOT send; the
   * literal text is sent verbatim on the existing Enter path (AC-5).
   */
  selectCommand = (item: { name: string }) => {
    return `/${item.name} `;
  };

  /**
   * ADR-013 §2 — render an args hint for a command dropdown row, e.g.
   * `<role> [name]`: required args in angle brackets, optional in square
   * brackets, in declared order. Empty string when the command takes no args.
   */
  commandArgsHint(args: CommandDescriptor['args']): string {
    return args
      .map((a) => (a.required ? `<${a.name}>` : `[${a.name}]`))
      .join(' ');
  }
}
