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

import { ChatMessage, ENTRY_POINT_NAME } from '../../selectors/chat-message.model';
import { ActorAddress } from '../../../../protocol/message.types';
import { ApiService } from '../../../../core/http/api.service';
import { ChatService, ThinkingState } from '../../selectors/chat.selector';
import { IngestionService } from '../../event/ingestion.service';
import { ContextService } from '../../../../core/context/context.service';
import { Selectable, SelectionService } from '../../ui-state/selection.service';
import {
  AnsweredRequest,
  ChatHumanModalComponent,
  HumanModalReply,
} from './chat-human-modal.component';
import { ChatMessageComponent } from './chat-message.component';
import { ChatThinkingComponent } from './chat-thinking.component';
import { ProcessUserInputComponent } from '../user-input/user-input.component';

/** Discriminated-union item rendered inline in the chat panel. */
export type DisplayItem =
  | { kind: 'message'; data: ChatMessage }
  | { kind: 'thinking'; data: ThinkingState };

/**
 * Chat panel scroll model (ADR-016, simplified rewrite).
 *
 * Two behaviors, kept deliberately small and position-based (no programmatic
 * scroll flags, no hover lock, no state-machine enum):
 *
 *  1. ANCHOR ON SEND — when the user submits a message, the panel pins THAT
 *     message to the top of the viewport (regardless of the prior scroll
 *     position) and reserves a trailing spacer so the agent's reply streams into
 *     the space below it. The submit is signalled by `chatService.justSent$`; the
 *     echoed message is matched by identity (the newest human-authored message
 *     that appeared after the send). The spacer shrinks as the reply grows, so
 *     the message holds its top position without any further scrolling.
 *
 *  2. FOLLOW ON DEMAND — while a reply streams in below the fold, a
 *     "New messages" indicator is shown. Clicking it scrolls to the bottom and
 *     enters FOLLOW mode (auto-scroll to the bottom on every new message). Any
 *     manual upward scroll exits follow mode. A new send also exits follow and
 *     re-anchors.
 *
 * Programmatic scrolls are INSTANT, so a scroll event is never confused with a
 * user scroll: follow mode exits only when the position moves measurably away
 * from the bottom, which a "scroll to bottom" write never does.
 *
 * Entry-to-page (mount) scrolling is intentionally OUT of scope here.
 *
 * The per-agent `akgent-chat` trace keeps its own stick-to-bottom autoscroll —
 * the two surfaces diverge on purpose (see `akgent-chat.component.ts#scroll()`).
 */
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
  private scrollContainer!: ElementRef<HTMLElement>;

  /** The trailing spacer element that reserves room below the anchored message. */
  @ViewChild('spacer', { static: false })
  private spacerRef?: ElementRef<HTMLElement>;

  chatService: ChatService = inject(ChatService);
  ingestionService: IngestionService = inject(IngestionService);
  selectionService: SelectionService = inject(SelectionService);
  apiService: ApiService = inject(ApiService);
  private contextService: ContextService = inject(ContextService);

  chatMessages: ChatMessage[] = [];
  thinkingStates: ThinkingState[] = [];
  displayItems: DisplayItem[] = [];
  // Epic 18 (ADR-015 §2): spinner state now lives on IngestionService.
  loadingProcess$ = this.ingestionService.loadingProcess$;

  // Modal state for Rule 3 notification dialog
  modalVisible = false;
  modalAgentPair: { sender: ActorAddress; recipient: ActorAddress } | null = null;
  modalPendingMessages: ChatMessage[] = [];
  modalAnsweredMessages: AnsweredRequest[] = [];

  private subscription!: Subscription;
  private notificationSubscription!: Subscription;
  private justSentSubscription!: Subscription;
  private runningSubscription!: Subscription;

  private expandedMessageIds = new Set<string>();
  /** Story 4-8: per-bubble expansion state. */
  private thinkingExpanded = new Set<string>();
  selectedMessageId: string | null = null;
  pendingNotifications: Set<string> = new Set();

  // --- scroll state -----------------------------------------------------------
  /** Reserved height (px) of the trailing spacer; written to the DOM imperatively. */
  private spacerHeight = 0;
  /** Status-pill label above the input (template-bound), or null when hidden.
   *  One of: 'Auto scrolling' | 'New messages' | 'Messages'. */
  indicatorLabel: string | null = null;
  /** FOLLOW mode: auto-scroll to the bottom on every new message. */
  private following = false;
  /** A new message arrived that the user hasn't caught up to yet — the only
   *  difference between "New messages" and "Messages". Set on growth while
   *  not following; cleared once the newest message is back on screen. */
  private unseen = false;
  /** Message count last seen, to detect a genuinely new message. */
  private prevCount = 0;
  /** True once the initial history has loaded — so the first batch isn't counted
   *  as "new" (it's pre-existing, hence "Messages", not "New messages"). */
  private loaded = false;

  /** Outer id of the message currently pinned to the top (null when none). */
  private anchorId: string | null = null;
  /** Whether the one-time scroll-to-top for `anchorId` has run. */
  private anchorPinned = false;

  /** Between a send and the arrival of its echo. */
  private awaitingEcho = false;
  /** Newest entry-point (@Human) message id at send time — tells the echo from a
   *  pre-existing one. */
  private sendBaselineId: string | null = null;

  private lastScrollHeight = 0;
  /** Last observed scrollTop — used to tell a user scroll-UP from our own smooth
   *  scroll (which only ever moves DOWN toward the bottom). */
  private lastScrollTop = 0;

  /** Small top inset so the pinned message isn't flush against the edge. */
  private readonly TOP_PAD = 8;

  ngOnInit(): void {
    this.notificationSubscription = this.chatService.pendingNotifications$.subscribe(
      (pending) => {
        this.pendingNotifications = pending;
        this.recomputeModalInputs();
      },
    );

    // A submit arms anchoring and exits follow mode. The echo is matched on the
    // next emission (see onMessages); the baseline distinguishes it from a
    // pre-existing human message (e.g. restored history).
    this.justSentSubscription = this.chatService.justSent$.subscribe(() => {
      this.sendBaselineId = this.latestEntryPointId();
      this.awaitingEcho = true;
      this.following = false;
      this.anchorId = null;
      this.anchorPinned = false;
      this.unseen = false;
      this.indicatorLabel = null;
      this.clearSpacer();
    });

    // "Auto scrolling" only applies to a RUNNING process — a stopped team
    // produces no messages to follow. Exit follow + refresh the pill on change.
    this.runningSubscription = this.contextService.currentTeamRunning$.subscribe(
      (running) => {
        if (!running) this.following = false;
        this.updateIndicator();
      },
    );

    this.subscription = combineLatest([
      this.chatService.messages$,
      this.chatService.thinkingAgents$,
    ]).subscribe(([classified, thinking]) => this.onMessages(classified, thinking));
  }

  ngOnDestroy(): void {
    this.subscription?.unsubscribe();
    this.notificationSubscription?.unsubscribe();
    this.justSentSubscription?.unsubscribe();
    this.runningSubscription?.unsubscribe();
  }

  // ---------------------------------------------------------------------------
  // Message stream
  // ---------------------------------------------------------------------------

  private onMessages(classified: ChatMessage[], thinking: ThinkingState[]): void {
    const grew = classified.length > this.prevCount;
    this.prevCount = classified.length;

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
    this.thinkingStates = thinking;
    this.displayItems = this.buildDisplayItems(classified, thinking);

    // The user's just-sent message arrived → pin it to the top (done in
    // ngAfterViewChecked once it has rendered). The baseline distinguishes the
    // echo from a pre-existing @Human message (restored history).
    if (this.awaitingEcho) {
      const echoId = this.latestEntryPointId(classified);
      if (echoId && echoId !== this.sendBaselineId) {
        this.anchorId = echoId;
        this.anchorPinned = false;
        this.awaitingEcho = false;
        this.following = false;
        this.unseen = false;
        this.indicatorLabel = null;
      }
    }

    // A genuinely new message the user isn't watching live ⇒ "New messages"
    // (vs "Messages" when merely scrolled up). The initial history load
    // does not count as "new". The label itself is derived from layout and
    // applied in ngAfterViewChecked.
    if (grew && this.loaded && !this.following) this.unseen = true;
    this.loaded = this.loaded || classified.length > 0;
    this.recomputeModalInputs();
  }

  /**
   * Post-layout scrolling. Runs after the view is updated so `offsetTop` /
   * `scrollHeight` are measured against the rendered DOM.
   */
  ngAfterViewChecked(): void {
    if (this.anchorId && !this.anchorPinned) {
      // First chance to pin the just-sent message to the top. Wait until BOTH the
      // message element and the spacer have rendered — pinning without the spacer
      // would reserve no room and the scroll would clamp short.
      const el = this.findMessageEl(this.anchorId);
      if (el && this.spacerRef) {
        this.pinToTop(el);
        this.anchorPinned = true;
      }
    } else if (this.anchorId && this.anchorPinned) {
      // Keep the reserved room accurate as the reply streams in. The spacer
      // shrinks by exactly what the reply grows, so scrollHeight stays constant
      // and the pinned message holds its top position with no further scrolling.
      const el = this.findMessageEl(this.anchorId);
      if (el) this.reserveSpacer(el);
      // Once the reply fills the viewport the spacer is gone — stop managing.
      if (this.spacerHeight === 0) this.anchorId = null;
    }

    // FOLLOW mode: tail to the bottom whenever new content arrived.
    if (this.following && this.contentGrewSinceLastCheck()) {
      this.scrollToBottom();
    }

    // Refresh the status pill from post-layout geometry (this is what makes it
    // appear as replies stream in below an anchored turn, when no scroll event
    // fires). Deferred to a microtask, and only when it would actually change, so
    // it can't trip Angular's "expression changed after it was checked" guard or
    // loop.
    if (this.computeIndicatorLabel() !== this.indicatorLabel) {
      queueMicrotask(() => this.updateIndicator());
    }
  }

  /** Scroll handler (template `(scroll)` binding). Pure position logic — no
   *  programmatic-scroll flag needed: our smooth scrolls only ever move DOWN
   *  toward the bottom, so a measurable UPWARD move is unambiguously the user. */
  onScroll(): void {
    const c = this.scrollContainer?.nativeElement;
    if (!c) return;
    const movedUp = c.scrollTop < this.lastScrollTop - 2;
    this.lastScrollTop = c.scrollTop;
    // Reaching the bottom turns auto-scroll ON; scrolling up turns it off
    // ("follow until the user scrolls"). The `anchorId` guard keeps a just-sent
    // anchored turn (message parked at top, spacer below) from counting as
    // "at the bottom".
    if (this.anchorId === null && !this.newestMessageBelowFold()) {
      this.following = true;
    } else if (this.following && movedUp) {
      this.following = false;
    }
    // Re-derive the pill from the new position (safe directly — outside CD).
    this.updateIndicator();
  }

  /** Pill click — jump to the bottom and start auto-scrolling. */
  onJumpToLatest(): void {
    this.following = true;
    this.unseen = false;
    this.indicatorLabel = 'Auto scrolling';
    this.anchorId = null;
    this.clearSpacer();
    this.scrollToBottom();
  }

  // ---------------------------------------------------------------------------
  // Scroll primitives (all INSTANT — see class docstring)
  // ---------------------------------------------------------------------------

  /** Pin `anchorEl` to the top: reserve the spacer (synchronously, so the
   *  container has room) then scroll the message's top to the viewport top. */
  private pinToTop(anchorEl: HTMLElement): void {
    const c = this.scrollContainer?.nativeElement;
    if (!c) return;
    this.reserveSpacer(anchorEl);
    // `offsetTop` is relative to the nearest positioned ancestor (`.chat-container`),
    // which the message and the scroll container (`.message-list`) share — so
    // subtract the container's own `offsetTop` to get a position within the
    // scroll content.
    const top = Math.max(0, anchorEl.offsetTop - (c.offsetTop ?? 0) - this.TOP_PAD);
    c.scrollTo({ top, behavior: this.scrollBehavior() });
  }

  private scrollToBottom(): void {
    const c = this.scrollContainer?.nativeElement;
    if (c) c.scrollTo({ top: c.scrollHeight, behavior: this.scrollBehavior() });
  }

  /** Smooth by default; instant only when the user requests reduced motion. */
  private scrollBehavior(): ScrollBehavior {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
      ? 'auto'
      : 'smooth';
  }

  /** Recompute + write the spacer min-height to the DOM synchronously. Real
   *  content below the anchor = `spacer.offsetTop - anchor.offsetTop` (the spacer
   *  is the last child, so its offsetTop marks the end of the messages). This is
   *  immune to `scrollHeight` being clamped up to `clientHeight` on short
   *  conversations — the case where a scrollHeight-based formula under-reserves
   *  and the message can never reach the top.
   *
   *  We reserve only enough that the PINNED position (anchor at `TOP_PAD`) is the
   *  bottom-most scroll: subtracting the `TOP_PAD` inset and the list's own
   *  bottom padding makes `maxScrollTop === anchorTop - TOP_PAD` exactly, so
   *  scrolling down can't push the anchored bubble above its top inset and no
   *  surplus empty space is left below the reply. The flex gap before the spacer
   *  is part of `realBelow` and cancels out, so the inset is exact regardless of
   *  the gap/padding values. */
  private reserveSpacer(anchorEl: HTMLElement): void {
    const c = this.scrollContainer?.nativeElement;
    const spacer = this.spacerRef?.nativeElement;
    if (!c || !spacer) {
      this.spacerHeight = 0;
      return;
    }
    const realBelow = spacer.offsetTop - anchorEl.offsetTop;
    const padBottom = parseFloat(getComputedStyle(c).paddingBottom) || 0;
    const reserve = c.clientHeight - realBelow - this.TOP_PAD - padBottom;
    this.spacerHeight = Math.max(0, Math.min(c.clientHeight, reserve));
    spacer.style.minHeight = this.spacerHeight + 'px';
  }

  private clearSpacer(): void {
    this.spacerHeight = 0;
    const spacer = this.spacerRef?.nativeElement;
    if (spacer) spacer.style.minHeight = '0px';
  }

  private findMessageEl(id: string): HTMLElement | null {
    return (
      this.scrollContainer?.nativeElement.querySelector<HTMLElement>(
        '[data-message-id="' + id + '"]',
      ) ?? null
    );
  }

  /**
   * The "is there a new message to show?" criterion (the indicator's trigger):
   * the END of the message list is below the visible viewport — i.e. the newest
   * message is not on screen. The spacer is the list's last child, so its
   * POSITION (`offsetTop`, not its reserved height) marks where the messages end;
   * using it keeps the check correct even while a turn is anchored (the reserved
   * spacer height would otherwise make a scrollHeight-based check think we're at
   * the bottom).
   */
  private newestMessageBelowFold(): boolean {
    const c = this.scrollContainer?.nativeElement;
    const spacer = this.spacerRef?.nativeElement;
    if (!c || !spacer) return false;
    const messagesBottom = spacer.offsetTop - (c.offsetTop ?? 0);
    return messagesBottom > c.scrollTop + c.clientHeight + 4;
  }

  /** The status-pill label for the current state (pure). */
  private computeIndicatorLabel(belowFold = this.newestMessageBelowFold()): string | null {
    // "Auto scrolling" only while the process is RUNNING — a stopped team has
    // nothing to follow.
    if (this.following && this.contextService.currentTeamRunning$.value) {
      return 'Auto scrolling';
    }
    if (!belowFold) return null;
    return this.unseen ? 'New messages' : 'Messages';
  }

  /** Icon class for the status pill — a "following" glyph while auto scrolling,
   *  a down-arrow for the jump-to-latest states (keyed off the shown label). */
  get indicatorIcon(): string {
    return this.indicatorLabel === 'Auto scrolling' ? 'pi-sync' : 'pi-arrow-down';
  }

  /** Apply the pill label; clears `unseen` once the user is at the bottom
   *  (caught up), whether following or not. */
  private updateIndicator(): void {
    const belowFold = this.newestMessageBelowFold();
    if (!belowFold) this.unseen = false; // at the bottom → caught up
    this.indicatorLabel = this.computeIndicatorLabel(belowFold);
  }

  /** True when scrollHeight changed since the last check (new content / spacer). */
  private contentGrewSinceLastCheck(): boolean {
    const c = this.scrollContainer?.nativeElement;
    if (!c) return false;
    const changed = c.scrollHeight !== this.lastScrollHeight;
    this.lastScrollHeight = c.scrollHeight;
    return changed;
  }

  /** Newest entry-point (@Human) message id — the user's own send — or null. */
  private latestEntryPointId(msgs: ChatMessage[] = this.chatMessages): string | null {
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].sender.name === ENTRY_POINT_NAME) return msgs[i].id;
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Presentation helpers (unchanged behavior)
  // ---------------------------------------------------------------------------

  onToggleCollapse(chatMsg: ChatMessage): void {
    if (chatMsg.rule !== 3 && chatMsg.rule !== 4) return;
    chatMsg.collapsed = !chatMsg.collapsed;
    if (chatMsg.collapsed) {
      this.expandedMessageIds.delete(chatMsg.id);
    } else {
      this.expandedMessageIds.add(chatMsg.id);
    }
  }

  hasNotification(chatMsg: ChatMessage): boolean {
    // `pendingNotifications` holds inner `BaseMessage.id` values so that
    // `reply.parent_id` (also inner) clears the correct entry.
    return chatMsg.rule === 3 && this.pendingNotifications.has(chatMsg.message_id);
  }

  /**
   * Bubble click updates the visual selection highlight only. Routing is driven
   * exclusively by the Send-to dropdown in `user-input.component` (Story 4-11).
   */
  onBubbleClicked(chatMsg: ChatMessage): void {
    this.selectedMessageId = chatMsg.id;
  }

  /** Open the Rule 3 modal with every still-unanswered message from the clicked
   *  bubble's agent pair. */
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

  /** Re-derive modal pending/answered lists; auto-close when both empty. */
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

  /** Stable tracking key for the mixed display list. */
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

  /** Merge chat messages + thinking states into one chronologically sorted list. */
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
}
