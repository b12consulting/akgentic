import { CommonModule } from '@angular/common';
import {
  AfterViewChecked,
  Component,
  ElementRef,
  HostListener,
  inject,
  Input,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core';
import { combineLatest, Subscription } from 'rxjs';

import { ChatMessage } from '../models/chat-message.model';
import { ActorAddress } from '../models/message.types';
import { ApiService } from '../services/api.service';
import { ChatService, ThinkingState } from '../services/chat.service';
import { Selectable, SelectionService } from '../services/selection.service';
import {
  AnsweredRequest,
  ChatHumanModalComponent,
  HumanModalReply,
} from './chat-human-modal.component';
import { ChatMessageComponent } from './chat-message.component';
import { ChatThinkingComponent } from './chat-thinking.component';
import { ProcessUserInputComponent } from '../process/user-input/user-input.component';

/** Discriminated-union item rendered inline in the chat panel. */
export type DisplayItem =
  | { kind: 'message'; data: ChatMessage }
  | { kind: 'thinking'; data: ThinkingState };

@Component({
  selector: 'app-chat-panel',
  standalone: true,
  imports: [
    CommonModule,
    ChatMessageComponent,
    ChatThinkingComponent,
    ChatHumanModalComponent,
    ProcessUserInputComponent,
  ],
  templateUrl: './chat-panel.component.html',
  styleUrl: './chat-panel.component.scss',
})
export class ChatPanelComponent implements OnInit, OnDestroy, AfterViewChecked {
  @Input() processId!: string;

  @ViewChild('scrollContainer', { static: false })
  private scrollContainer!: ElementRef;

  chatService: ChatService = inject(ChatService);
  selectionService: SelectionService = inject(SelectionService);
  apiService: ApiService = inject(ApiService);

  chatMessages: ChatMessage[] = [];
  thinkingStates: ThinkingState[] = [];
  displayItems: DisplayItem[] = [];
  loadingProcess$ = this.chatService.loadingProcess$;

  // Modal state for Rule 3 notification dialog
  modalVisible = false;
  modalAgentPair: { sender: ActorAddress; recipient: ActorAddress } | null = null;
  modalPendingMessages: ChatMessage[] = [];
  modalAnsweredMessages: AnsweredRequest[] = [];

  private subscription!: Subscription;
  private shouldScrollToBottom = true;
  private lastScrollHeight = 0;
  // Story 4.4: hover-aware auto-scroll lock. `isHovered` short-circuits the
  // scroll in ngAfterViewChecked while the user's pointer is over the panel.
  // `pendingCatchUpScroll` is set ONLY from the messages$ subscription path
  // (new-message arrival) — never from ngAfterViewChecked height changes —
  // so collapse/expand toggles during hover do NOT queue a spurious catch-up.
  private isHovered = false;
  private pendingCatchUpScroll = false;
  private expandedMessageIds = new Set<string>();
  /** Story 4-8: per-bubble expansion state (mirrors expandedMessageIds
   *  pattern; persists across thinkingAgents$ re-emissions). */
  private thinkingExpanded = new Set<string>();
  selectedMessageId: string | null = null;
  private notificationSubscription!: Subscription;
  pendingNotifications: Set<string> = new Set();

  ngOnInit(): void {
    this.notificationSubscription = this.chatService.pendingNotifications$.subscribe(
      (pending) => {
        this.pendingNotifications = pending;
        this.recomputeModalInputs();
      }
    );
    // Story 4-8: merge chat messages + thinking states into a single sorted
    // displayItems array. Either stream re-triggers rebuild.
    // Story 6.4 (AC3): subscription migrated from the deleted
    // `messageService.messages$` onto `chatService.messages$` — classification
    // is owned by `chatFold` (Story 6.3); the component is a presenter.
    this.subscription = combineLatest([
      this.chatService.messages$,
      this.chatService.thinkingAgents$,
    ]).subscribe(([classified, thinkingStates]) => {
      this.checkShouldAutoScroll();

      const previousLength = this.chatMessages.length;
      // Preserve per-user expand state across re-emissions (Rule 3 / Rule 4).
      for (const chatMsg of classified) {
        if (
          (chatMsg.rule === 3 || chatMsg.rule === 4) &&
          this.expandedMessageIds.has(chatMsg.id)
        ) {
          chatMsg.collapsed = false;
        }
      }

      this.chatMessages = classified;
      this.thinkingStates = thinkingStates;
      this.displayItems = this.buildDisplayItems(classified, thinkingStates);
      // Story 4.4: if a new message arrived while the user is hovering the
      // panel, remember that a catch-up scroll is owed on mouseleave.
      if (this.isHovered && classified.length > previousLength) {
        this.pendingCatchUpScroll = true;
      }
      this.recomputeModalInputs();
    });
  }

  onToggleCollapse(chatMsg: ChatMessage): void {
    if (chatMsg.rule !== 3 && chatMsg.rule !== 4) return;
    chatMsg.collapsed = !chatMsg.collapsed;
    if (chatMsg.collapsed) {
      this.expandedMessageIds.delete(chatMsg.id);
    } else {
      this.expandedMessageIds.add(chatMsg.id);
    }
  }

  ngAfterViewChecked(): void {
    // Always evaluate hasContentChanged() so lastScrollHeight stays fresh —
    // even while hovered — otherwise a collapse/expand during hover would
    // leave a stale lastScrollHeight that spuriously fires a scroll later.
    const contentChanged = this.hasContentChanged();
    if (this.isHovered) return; // Story 4.4: suspended while hovering
    if (this.shouldScrollToBottom && contentChanged) {
      this.scrollToBottom();
    }
  }

  onMouseEnter(): void {
    this.isHovered = true;
  }

  onMouseLeave(): void {
    this.isHovered = false;
    // Catch-up only if a new message arrived during hover AND the user was
    // near the bottom at arrival time (preserve pre-existing user-intent).
    const shouldCatchUp = this.pendingCatchUpScroll && this.shouldScrollToBottom;
    this.pendingCatchUpScroll = false;
    if (shouldCatchUp) {
      // queueMicrotask defers the scroll until after the current event-loop
      // tick so Angular can finish any change detection triggered by the
      // isHovered = false assignment before we read scrollHeight.
      queueMicrotask(() => this.scrollToBottom());
    }
  }

  hasNotification(chatMsg: ChatMessage): boolean {
    // `pendingNotifications` holds inner `BaseMessage.id` values so that
    // `reply.parent_id` (also inner) clears the correct entry. Using the
    // outer envelope `chatMsg.id` here would never match, which was the
    // root cause of the "hand icon never disappears in chat" regression.
    return chatMsg.rule === 3 && this.pendingNotifications.has(chatMsg.message_id);
  }

  ngOnDestroy(): void {
    if (this.subscription) {
      this.subscription.unsubscribe();
    }
    if (this.notificationSubscription) {
      this.notificationSubscription.unsubscribe();
    }
  }

  /**
   * Bubble click updates the visual selection highlight only. Routing is
   * driven exclusively by the Send-to dropdown in `user-input.component`
   * (see Story 4-11 / ADR-002 Revision Log 2026-04-13 — Decision 3
   * superseded). The former `chatService.setReplyContext(...)` call was
   * retired because it silently overrode the dropdown selection.
   */
  onBubbleClicked(chatMsg: ChatMessage): void {
    this.selectedMessageId = chatMsg.id;
  }

  /**
   * Open the Rule 3 modal with every still-unanswered message from the same
   * agent pair as the clicked bubble. Story 4-7 will replace the
   * last-message fallback (modal emits one reply per send) with per-request
   * reply inputs; this story only changes the notification tracking data
   * model from `Map<pairKey, ChatMessage[]>` to `Set<id>`.
   */
  onRule3Clicked(chatMsg: ChatMessage): void {
    const pair = { sender: chatMsg.sender, recipient: chatMsg.recipient };
    const pendingForPair = this.computePendingForPair(pair);
    const answeredForPair = this.computeAnsweredForPair(pair);

    if (pendingForPair.length === 0 && answeredForPair.length === 0) return;

    this.modalAgentPair = pair;
    this.modalPendingMessages = pendingForPair;
    this.modalAnsweredMessages = answeredForPair;
    this.modalVisible = true;
  }

  onModalReply(reply: HumanModalReply): void {
    // Modal stays open across replies (Story 4-7 AC3) — the next messages$
    // emission will reclassify pending/answered via recomputeModalInputs().
    this.apiService
      .processHumanInput(this.processId, reply.content, reply.messageId)
      .catch((err) => console.error('Failed to send human input:', err));
  }

  onModalVisibleChange(visible: boolean): void {
    this.modalVisible = visible;
    if (!visible) {
      this.modalAgentPair = null;
      this.modalPendingMessages = [];
      this.modalAnsweredMessages = [];
    }
  }

  /**
   * Re-derive modalPendingMessages and modalAnsweredMessages from the current
   * chatMessages + pendingNotifications when the modal is open. Auto-closes
   * the modal when both lists become empty (clean-exit condition).
   */
  private recomputeModalInputs(): void {
    if (this.modalAgentPair === null) return;
    const pendingForPair = this.computePendingForPair(this.modalAgentPair);
    const answeredForPair = this.computeAnsweredForPair(this.modalAgentPair);
    this.modalPendingMessages = pendingForPair;
    this.modalAnsweredMessages = answeredForPair;
    if (pendingForPair.length === 0 && answeredForPair.length === 0) {
      this.modalVisible = false;
      this.modalAgentPair = null;
    }
  }

  private computePendingForPair(
    pair: { sender: ActorAddress; recipient: ActorAddress },
  ): ChatMessage[] {
    return this.chatMessages.filter(
      (m) =>
        m.rule === 3 &&
        m.sender.name === pair.sender.name &&
        m.recipient.name === pair.recipient.name &&
        this.pendingNotifications.has(m.message_id),
    );
  }

  private computeAnsweredForPair(
    pair: { sender: ActorAddress; recipient: ActorAddress },
  ): AnsweredRequest[] {
    return this.chatMessages
      .filter(
        (m) =>
          m.rule === 3 &&
          m.sender.name === pair.sender.name &&
          m.recipient.name === pair.recipient.name &&
          !this.pendingNotifications.has(m.message_id),
      )
      .map((request) => {
        // Reply's `parent_id` is the original's INNER id (`message_id`),
        // not its outer envelope `id`.
        const reply =
          this.chatMessages.find((r) => r.parent_id === request.message_id) ?? null;
        return reply ? { request, reply } : null;
      })
      .filter((x): x is AnsweredRequest => x !== null);
  }

  onBackgroundClick(): void {
    this.selectedMessageId = null;
  }

  @HostListener('document:keydown.escape')
  onEscapePress(): void {
    this.selectedMessageId = null;
  }

  onMessageSelected(chatMsg: ChatMessage): void {
    const selectable: Selectable = {
      type: 'message',
      data: {
        name: chatMsg.sender.agent_id,
        actorName: chatMsg.sender.name,
      },
    };
    this.selectionService.handleSelection(selectable);
  }

  trackById(_index: number, item: ChatMessage): string {
    return item.id;
  }

  /**
   * Story 4-8: Stable tracking key for the mixed display list — preserves
   * DOM nodes across ephemeral → persistent transitions of a thinking
   * bubble (the `anchor_message_id` is constant across the finalise flip).
   */
  trackByDisplayItem(_: number, item: DisplayItem): string {
    return (
      item.kind +
      ':' +
      (item.kind === 'message' ? item.data.id : item.data.anchor_message_id)
    );
  }

  isThinkingExpanded(state: ThinkingState): boolean {
    return this.thinkingExpanded.has(state.anchor_message_id);
  }

  onToggleThinkingExpanded(anchorId: string): void {
    if (this.thinkingExpanded.has(anchorId)) {
      this.thinkingExpanded.delete(anchorId);
    } else {
      this.thinkingExpanded.add(anchorId);
    }
  }

  /**
   * Story 4-8 (AC5): Merge chat messages and thinking states into a single
   * chronologically sorted list.
   *   - Primary key: timestamp / start_time (ms).
   *   - Tie-break (same ms): message BEFORE thinking (so the triggering
   *     ReceivedMessage visually precedes its own bubble).
   */
  private buildDisplayItems(
    messages: ChatMessage[],
    thinking: ThinkingState[],
  ): DisplayItem[] {
    const items: DisplayItem[] = [];
    for (const m of messages) items.push({ kind: 'message', data: m });
    for (const t of thinking) items.push({ kind: 'thinking', data: t });
    items.sort((a, b) => {
      const ta =
        a.kind === 'message' ? a.data.timestamp.getTime() : a.data.start_time.getTime();
      const tb =
        b.kind === 'message' ? b.data.timestamp.getTime() : b.data.start_time.getTime();
      if (ta !== tb) return ta - tb;
      // Tie-break: messages before thinking bubbles.
      if (a.kind === b.kind) return 0;
      return a.kind === 'message' ? -1 : 1;
    });
    return items;
  }

  private checkShouldAutoScroll(): void {
    if (!this.scrollContainer) {
      this.shouldScrollToBottom = true;
      return;
    }
    const el = this.scrollContainer.nativeElement;
    const threshold = 100;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    this.shouldScrollToBottom =
      distanceFromBottom <= threshold || this.lastScrollHeight === 0;
  }

  private hasContentChanged(): boolean {
    if (!this.scrollContainer) return false;
    const currentScrollHeight = this.scrollContainer.nativeElement.scrollHeight;
    const changed = currentScrollHeight !== this.lastScrollHeight;
    this.lastScrollHeight = currentScrollHeight;
    return changed;
  }

  private scrollToBottom(): void {
    if (!this.scrollContainer) return;
    try {
      const el = this.scrollContainer.nativeElement;
      el.scrollTop = el.scrollHeight;
    } catch (err) {
      console.warn('Could not scroll to bottom:', err);
    }
  }
}
