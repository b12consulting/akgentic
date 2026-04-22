import { ComponentFixture, fakeAsync, flushMicrotasks, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { BehaviorSubject, of } from 'rxjs';
import { map } from 'rxjs/operators';
import { provideMarkdown } from 'ngx-markdown';

import { ChatPanelComponent } from './chat-panel.component';
import {
  ChatService,
  computePendingNotifications,
  ThinkingState,
} from '../services/chat.service';
import { SelectionService } from '../services/selection.service';
import { ActorAddress, SentMessage, AkgenticMessage, StartMessage, isSentMessage } from '../models/message.types';
import { ChatMessage, classifyMessage } from '../models/chat-message.model';
import { ApiService } from '../services/api.service';
import { AkgentService } from '../services/akgent.service';
import { GraphDataService } from '../services/graph-data.service';

function makeAddress(overrides: Partial<ActorAddress> = {}): ActorAddress {
  return {
    __actor_address__: true,
    name: '@Agent',
    role: 'Worker',
    agent_id: 'agent-1',
    squad_id: 'squad-1',
    user_message: false,
    ...overrides,
  };
}

function makeSentMessage(
  sender: Partial<ActorAddress>,
  recipient: Partial<ActorAddress>,
  content: string = 'test',
  id: string = 'msg-1',
  parentId: string | null = null,
): SentMessage {
  // Mirror the real backend contract: `parent_id` on the INNER BaseMessage
  // references the INNER id of the parent (`inner-<parentId>`), not the
  // outer envelope id. This is what `HumanProxy` produces on real replies
  // (see team_service.py#process_human_input → HumanProxy sets
  // `_current_message = event.message` before send()).
  const innerParentId = parentId === null ? null : 'inner-' + parentId;
  return {
    id,
    parent_id: parentId,
    team_id: 'team-1',
    timestamp: '2026-04-08T10:00:00Z',
    sender: makeAddress(sender),
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    message: {
      id: 'inner-' + id,
      parent_id: innerParentId,
      team_id: 'team-1',
      timestamp: '2026-04-08T10:00:00Z',
      sender: makeAddress(sender),
      display_type: 'other',
      content,
      __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    },
    recipient: makeAddress(recipient),
  };
}

describe('ChatPanelComponent', () => {
  let component: ChatPanelComponent;
  let fixture: ComponentFixture<ChatPanelComponent>;
  let messagesSubject: BehaviorSubject<AkgenticMessage[]>;

  beforeEach(async () => {
    messagesSubject = new BehaviorSubject<AkgenticMessage[]>([]);

    // Story 6.4 (AC3): `ChatPanelComponent` no longer injects
    // `ActorMessageService`. The spec feeds SentMessages via
    // `messagesSubject` and projects them through the same classification
    // `chatFold` performs in production — the component now reads the
    // derived `chatService.messages$` directly.
    const classifiedMessages$ = messagesSubject.pipe(
      map((msgs: AkgenticMessage[]) =>
        msgs
          .filter(isSentMessage)
          .filter((m) => m.sender.role !== 'ActorSystem')
          .filter((m) => m.message.content != null && m.message.content !== '')
          .map((m) => classifyMessage(m)),
      ),
    );
    const thinkingAgentsSubj = new BehaviorSubject<ThinkingState[]>([]);
    const chatService = {
      messages$: classifiedMessages$,
      loadingProcess$: new BehaviorSubject<boolean>(false),
      pendingNotifications$: classifiedMessages$.pipe(
        map(computePendingNotifications),
      ),
      thinkingAgents$: thinkingAgentsSubj,
    };

    const selectionService = jasmine.createSpyObj('SelectionService', [
      'handleSelection',
    ]);

    const apiService = jasmine.createSpyObj('ApiService', ['sendMessage', 'processHumanInput']);
    apiService.sendMessage.and.returnValue(Promise.resolve());
    apiService.processHumanInput.and.returnValue(Promise.resolve());

    const akgentService = {
      selectedAkgent$: new BehaviorSubject<any>(null),
    };

    const graphDataService = {
      nodes$: of([]),
    };

    await TestBed.configureTestingModule({
      imports: [ChatPanelComponent, NoopAnimationsModule],
      providers: [
        provideMarkdown(),
        { provide: ChatService, useValue: chatService },
        { provide: SelectionService, useValue: selectionService },
        { provide: ApiService, useValue: apiService },
        { provide: AkgentService, useValue: akgentService },
        { provide: GraphDataService, useValue: graphDataService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatPanelComponent);
    component = fixture.componentInstance;
    component.processId = 'test-team';
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should pass chatService.messages$ through to this.chatMessages', () => {
    const sent = makeSentMessage(
      { name: '@Human', role: 'Human' },
      { name: '@Manager', role: 'Manager' },
      'hello',
    );
    messagesSubject.next([sent]);
    fixture.detectChanges();

    expect(component.chatMessages.length).toBe(1);
    expect(component.chatMessages[0].rule).toBe(1);
    expect(component.chatMessages[0].alignment).toBe('right');
  });

  it('should filter out ActorSystem messages', () => {
    const systemMsg = makeSentMessage(
      { name: '@System', role: 'ActorSystem' },
      { name: '@Manager', role: 'Manager' },
      'system msg',
    );
    messagesSubject.next([systemMsg]);
    fixture.detectChanges();

    expect(component.chatMessages.length).toBe(0);
  });

  it('should filter out messages with empty content', () => {
    const emptyMsg = makeSentMessage(
      { name: '@Human', role: 'Human' },
      { name: '@Manager', role: 'Manager' },
      '',
    );
    messagesSubject.next([emptyMsg]);
    fixture.detectChanges();

    expect(component.chatMessages.length).toBe(0);
  });

  it('should filter out non-SentMessage types', () => {
    const startMsg: StartMessage = {
      id: 'start-1',
      parent_id: null,
      team_id: 'team-1',
      timestamp: '2026-04-08T10:00:00Z',
      sender: makeAddress(),
      display_type: 'other',
      content: null,
      __model__: 'akgentic.core.messages.orchestrator.StartMessage',
      config: {} as any,
      parent: makeAddress(),
    };
    messagesSubject.next([startMsg]);
    fixture.detectChanges();

    expect(component.chatMessages.length).toBe(0);
  });

  it('should handle multiple messages with different rules', () => {
    const msg1 = makeSentMessage(
      { name: '@Human', role: 'Human' },
      { name: '@Manager', role: 'Manager' },
      'from human',
      'msg-1',
    );
    const msg2 = makeSentMessage(
      { name: '@Manager', role: 'Manager' },
      { name: '@Human', role: 'Human' },
      'reply to human',
      'msg-2',
    );
    const msg3 = makeSentMessage(
      { name: '@Worker', role: 'Worker' },
      { name: '@Manager', role: 'Manager' },
      'ai to ai',
      'msg-3',
    );
    messagesSubject.next([msg1, msg2, msg3]);
    fixture.detectChanges();

    expect(component.chatMessages.length).toBe(3);
    expect(component.chatMessages[0].rule).toBe(1);
    expect(component.chatMessages[1].rule).toBe(2);
    expect(component.chatMessages[2].rule).toBe(4);
  });

  it('onMessageSelected should call selectionService.handleSelection', () => {
    const chatMsg: ChatMessage = {
      id: 'msg-1',
      message_id: 'msg-1',
      parent_id: null,
      content: 'test',
      sender: makeAddress({ name: '@Manager', agent_id: 'mgr-1' }),
      recipient: makeAddress({ name: '@Human' }),
      timestamp: new Date(),
      rule: 2,
      alignment: 'left',
      color: '#9ebbcb',
      collapsed: false,
      label: 'Manager',
    };
    component.onMessageSelected(chatMsg);
    const selSvc = TestBed.inject(SelectionService);
    expect(selSvc.handleSelection).toHaveBeenCalledWith({
      type: 'message',
      data: {
        name: 'mgr-1',
        actorName: '@Manager',
      },
    });
  });

  it('trackById should return message id', () => {
    const chatMsg = { id: 'abc-123' } as ChatMessage;
    expect(component.trackById(0, chatMsg)).toBe('abc-123');
  });

  // Story 6.3 (Task 6.1 / AC2): the test that asserted
  // `chatService.messages$.value` after the component pushed classified
  // messages is retired. `chatService.messages$` is now a read-only derived
  // observable over `MessageLogService.log$`; `chatFold` owns the
  // classification. Coverage moved to `chat.service.spec.ts`.

  describe('bubble selection (Story 4-11 — routing retired)', () => {
    it('onBubbleClicked should update selectedMessageId only (no chatService call)', () => {
      const chatMsg: ChatMessage = {
        id: 'msg-reply',
        message_id: 'msg-reply',
        parent_id: null,
        content: 'test',
        sender: makeAddress({ name: '@Manager', agent_id: 'mgr-1' }),
        recipient: makeAddress({ name: '@Human' }),
        timestamp: new Date(),
        rule: 2,
        alignment: 'left',
        color: '#9ebbcb',
        collapsed: false,
        label: 'Manager',
      };
      const svc = TestBed.inject(ChatService) as any;
      // The retired API must not be present on the service mock.
      expect(svc.setReplyContext).toBeUndefined();
      expect(svc.replyContext$).toBeUndefined();

      component.onBubbleClicked(chatMsg);
      expect(component.selectedMessageId).toBe('msg-reply');
    });

    it('onBackgroundClick should clear selectedMessageId locally', () => {
      component.selectedMessageId = 'msg-abc';
      component.onBackgroundClick();
      expect(component.selectedMessageId).toBeNull();
    });

    it('onEscapePress should clear selectedMessageId locally', () => {
      component.selectedMessageId = 'msg-abc';
      component.onEscapePress();
      expect(component.selectedMessageId).toBeNull();
    });

    it('clicking different bubble should switch selectedMessageId', () => {
      const msg1: ChatMessage = {
        id: 'msg-1',
        message_id: 'msg-1',
        parent_id: null,
        content: 'first',
        sender: makeAddress({ name: '@Agent1' }),
        recipient: makeAddress({ name: '@Human' }),
        timestamp: new Date(),
        rule: 2,
        alignment: 'left',
        color: '#9ebbcb',
        collapsed: false,
        label: 'Agent1',
      };
      const msg2: ChatMessage = {
        id: 'msg-2',
        message_id: 'msg-2',
        parent_id: null,
        content: 'second',
        sender: makeAddress({ name: '@Agent2' }),
        recipient: makeAddress({ name: '@Human' }),
        timestamp: new Date(),
        rule: 2,
        alignment: 'left',
        color: '#9ebbcb',
        collapsed: false,
        label: 'Agent2',
      };

      component.onBubbleClicked(msg1);
      expect(component.selectedMessageId).toBe('msg-1');

      component.onBubbleClicked(msg2);
      expect(component.selectedMessageId).toBe('msg-2');
    });

    it('onRule3Clicked should open modal with pending messages', () => {
      // Set up a Rule 3 message
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-modal',
      );
      messagesSubject.next([rule3Msg]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages[0];
      expect(chatMsg.rule).toBe(3);

      component.onRule3Clicked(chatMsg);

      expect(component.modalVisible).toBe(true);
      expect(component.modalAgentPair?.sender.name).toBe('@Manager');
      expect(component.modalAgentPair?.recipient.name).toBe('@QATester');
      expect(component.modalPendingMessages.length).toBe(1);
    });

    it('onRule3Clicked opens modal with answered-only history (no pending)', () => {
      // A Rule 3 message that has been replied to — now opens modal in
      // read-only mode (Story 4-7 AC6 early-return change).
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-1',
      );
      const replyMsg = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'approved',
        'reply-1',
        'r3-1',
      );
      messagesSubject.next([rule3Msg, replyMsg]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages.find(m => m.id === 'r3-1')!;
      component.onRule3Clicked(chatMsg);

      expect(component.modalVisible).toBe(true);
      expect(component.modalPendingMessages.length).toBe(0);
      expect(component.modalAnsweredMessages.length).toBe(1);
      expect(component.modalAnsweredMessages[0].request.id).toBe('r3-1');
    });

    it('onRule3Clicked with two pending in the same pair opens modal with both (AC #8)', () => {
      const r3a = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'first',
        'r3-pair-1',
      );
      const r3b = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'second',
        'r3-pair-2',
      );
      messagesSubject.next([r3a, r3b]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages.find(m => m.id === 'r3-pair-1')!;
      component.onRule3Clicked(chatMsg);

      expect(component.modalVisible).toBe(true);
      expect(component.modalPendingMessages.length).toBe(2);
    });

    it('onRule3Clicked after one reply opens modal with single still-pending message (AC #8)', () => {
      const r3a = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'first',
        'r3-after-1',
      );
      const r3b = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'second',
        'r3-after-2',
      );
      const reply = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'answering first',
        'reply-after-1',
        'r3-after-1',
      );
      messagesSubject.next([r3a, r3b, reply]);
      fixture.detectChanges();

      const stillPending = component.chatMessages.find(m => m.id === 'r3-after-2')!;
      component.onRule3Clicked(stillPending);

      expect(component.modalVisible).toBe(true);
      expect(component.modalPendingMessages.length).toBe(1);
      expect(component.modalPendingMessages[0].id).toBe('r3-after-2');
    });

    it('onModalReply should call processHumanInput and KEEP modal open (Story 4-7 AC3)', () => {
      component.processId = 'team-42';
      component.modalVisible = true;
      component.modalAgentPair = {
        sender: makeAddress({ name: '@Manager' }),
        recipient: makeAddress({ name: '@QATester' }),
      };
      const dummyMsg: ChatMessage = {
        id: 'msg-123', message_id: 'inner-msg-123', parent_id: null, content: 'test',
        sender: makeAddress({ name: '@Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
        timestamp: new Date(), rule: 3, alignment: 'left', color: '#9ebbcb',
        collapsed: false, label: 'Manager ⇒ QATester',
      };
      component.modalPendingMessages = [dummyMsg];
      component.modalAnsweredMessages = [];

      component.onModalReply({ content: 'approved', messageId: 'msg-123' });

      const api = TestBed.inject(ApiService) as any;
      expect(api.processHumanInput).toHaveBeenCalledWith('team-42', 'approved', 'msg-123');
      // Modal stays open — reclassification is handled by recomputeModalInputs.
      expect(component.modalVisible).toBe(true);
      expect(component.modalAgentPair).not.toBeNull();
      expect(component.modalPendingMessages).toEqual([dummyMsg]);
    });

    it('onModalVisibleChange should update modalVisible and clear ALL state when closed', () => {
      component.modalVisible = true;
      component.modalAgentPair = {
        sender: makeAddress({ name: '@Manager' }),
        recipient: makeAddress({ name: '@QATester' }),
      };
      const dummyMsg: ChatMessage = {
        id: 'msg-1', message_id: 'inner-msg-1', parent_id: null, content: 'test',
        sender: makeAddress({ name: '@Manager' }),
        recipient: makeAddress({ name: '@QATester', role: 'Human' }),
        timestamp: new Date(), rule: 3, alignment: 'left', color: '#9ebbcb',
        collapsed: false, label: 'Manager ⇒ QATester',
      };
      const dummyReq: ChatMessage = { ...dummyMsg, id: 'req-1', message_id: 'inner-req-1' };
      const dummyReply: ChatMessage = {
        ...dummyMsg, id: 'reply-1', message_id: 'inner-reply-1', parent_id: 'inner-req-1',
      };
      component.modalPendingMessages = [dummyMsg];
      component.modalAnsweredMessages = [{ request: dummyReq, reply: dummyReply }];
      component.onModalVisibleChange(false);
      expect(component.modalVisible).toBe(false);
      expect(component.modalAgentPair).toBeNull();
      expect(component.modalPendingMessages).toEqual([]);
      expect(component.modalAnsweredMessages).toEqual([]);
    });
  });

  describe('Story 4-7: modal answered wiring + reactive recompute', () => {
    it('onRule3Clicked populates BOTH modalPendingMessages AND modalAnsweredMessages', () => {
      // Two pending and one already-answered pair in the same pair.
      const r3a = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'pending-1',
        'r3-pend-1',
      );
      const r3b = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'pending-2',
        'r3-pend-2',
      );
      const r3answered = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'answered',
        'r3-answered',
      );
      const reply = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'done',
        'reply-answered',
        'r3-answered',
      );
      messagesSubject.next([r3answered, reply, r3a, r3b]);
      fixture.detectChanges();

      const pending = component.chatMessages.find((m) => m.id === 'r3-pend-1')!;
      component.onRule3Clicked(pending);

      expect(component.modalVisible).toBe(true);
      expect(component.modalPendingMessages.length).toBe(2);
      expect(component.modalAnsweredMessages.length).toBe(1);
      expect(component.modalAnsweredMessages[0].request.id).toBe('r3-answered');
      expect(component.modalAnsweredMessages[0].reply.id).toBe('reply-answered');
    });

    it('reactive recompute: reply arriving while modal is open reclassifies lists', () => {
      const r3a = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'one',
        'r3-react-1',
      );
      const r3b = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'two',
        'r3-react-2',
      );
      messagesSubject.next([r3a, r3b]);
      fixture.detectChanges();

      const first = component.chatMessages.find((m) => m.id === 'r3-react-1')!;
      component.onRule3Clicked(first);
      expect(component.modalPendingMessages.length).toBe(2);
      expect(component.modalAnsweredMessages.length).toBe(0);

      // A reply to r3-react-1 arrives — without re-click, modal inputs must
      // reclassify.
      const reply = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'answering',
        'reply-react-1',
        'r3-react-1',
      );
      messagesSubject.next([r3a, r3b, reply]);
      fixture.detectChanges();

      expect(component.modalVisible).toBe(true);
      expect(component.modalPendingMessages.length).toBe(1);
      expect(component.modalPendingMessages[0].id).toBe('r3-react-2');
      expect(component.modalAnsweredMessages.length).toBe(1);
      expect(component.modalAnsweredMessages[0].request.id).toBe('r3-react-1');
      expect(component.modalAnsweredMessages[0].reply.id).toBe('reply-react-1');
    });

    it('auto-closes modal when last pending is answered AND no answered entries remain', () => {
      // Single Rule 3 with no reply in play.
      const r3 = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'only',
        'r3-auto-1',
      );
      messagesSubject.next([r3]);
      fixture.detectChanges();

      const pending = component.chatMessages.find((m) => m.id === 'r3-auto-1')!;
      component.onRule3Clicked(pending);
      expect(component.modalVisible).toBe(true);

      // Force the edge case: no messages at all (both lists empty after
      // recompute). In practice a reply would produce an answered entry, but
      // this guards the defensive auto-close branch.
      messagesSubject.next([]);
      fixture.detectChanges();

      expect(component.modalVisible).toBe(false);
      expect(component.modalAgentPair).toBeNull();
      expect(component.modalPendingMessages).toEqual([]);
      expect(component.modalAnsweredMessages).toEqual([]);
    });
  });

  describe('notification wiring (Task 2)', () => {
    it('hasNotification should return true for Rule 3 messages with pending notifications', () => {
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-1',
      );
      messagesSubject.next([rule3Msg]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages[0];
      expect(chatMsg.rule).toBe(3);
      expect(component.hasNotification(chatMsg)).toBe(true);
    });

    it('hasNotification should return false for Rule 3 messages after reply clears notification', () => {
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-1',
      );
      // Reply carries parent_id === r3-1 — per-message clearing requires this linkage.
      const replyMsg = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'approved',
        'reply-1',
        'r3-1',
      );
      messagesSubject.next([rule3Msg, replyMsg]);
      fixture.detectChanges();

      const chatMsgR3 = component.chatMessages.find(m => m.id === 'r3-1')!;
      expect(component.hasNotification(chatMsgR3)).toBe(false);
    });

    it('two separate messages — one cleared, one still pending (AC #8)', () => {
      const r3First = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'first',
        'r3-1',
      );
      const r3Second = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'second',
        'r3-2',
      );
      const reply = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'answering first',
        'reply-1',
        'r3-1',
      );
      messagesSubject.next([r3First, r3Second, reply]);
      fixture.detectChanges();

      const msg1 = component.chatMessages.find(m => m.id === 'r3-1')!;
      const msg2 = component.chatMessages.find(m => m.id === 'r3-2')!;
      expect(component.hasNotification(msg1)).toBe(false);
      expect(component.hasNotification(msg2)).toBe(true);
    });

    it('hasNotification should return false for non-Rule-3 messages', () => {
      const msg = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'hello',
      );
      messagesSubject.next([msg]);
      fixture.detectChanges();

      expect(component.chatMessages[0].rule).toBe(1);
      expect(component.hasNotification(component.chatMessages[0])).toBe(false);
    });
  });

  describe('notification clears on reply (Task 5)', () => {
    it('notification disappears when reply message arrives via messages$', () => {
      // 1. Rule 3 message pending
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-clear-1',
      );
      messagesSubject.next([rule3Msg]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages[0];
      expect(component.hasNotification(chatMsg)).toBe(true);

      // 2. Reply arrives (simulating WebSocket message); parent_id points at r3-clear-1.
      const replyMsg = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'approved',
        'reply-clear-1',
        'r3-clear-1',
      );
      messagesSubject.next([rule3Msg, replyMsg]);
      fixture.detectChanges();

      // 3. Notification should be gone
      const chatMsgAfter = component.chatMessages.find(m => m.id === 'r3-clear-1')!;
      expect(component.hasNotification(chatMsgAfter)).toBe(false);
    });

    it('notification reappears if sender sends again after reply', () => {
      const rule3First = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'first request',
        'r3-reappear-1',
      );
      const reply = makeSentMessage(
        { name: '@QATester', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'done',
        'reply-reappear-1',
        'r3-reappear-1',
      );
      const rule3Second = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'second request',
        'r3-reappear-2',
      );
      messagesSubject.next([rule3First, reply, rule3Second]);
      fixture.detectChanges();

      const secondMsg = component.chatMessages.find(m => m.id === 'r3-reappear-2')!;
      expect(component.hasNotification(secondMsg)).toBe(true);
    });
  });

  describe('Rule 3 collapse (Story 4.1)', () => {
    it('Rule 3 messages arrive collapsed by default', () => {
      const rule3 = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-collapsed',
      );
      messagesSubject.next([rule3]);
      fixture.detectChanges();

      expect(component.chatMessages[0].rule).toBe(3);
      expect(component.chatMessages[0].collapsed).toBe(true);
    });

    it('onToggleCollapse on Rule 3 toggles state and preserves across re-emission', () => {
      const rule3 = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-preserve',
      );
      messagesSubject.next([rule3]);
      fixture.detectChanges();

      expect(component.chatMessages[0].collapsed).toBe(true);

      component.onToggleCollapse(component.chatMessages[0]);
      expect(component.chatMessages[0].collapsed).toBe(false);

      // Re-emit messages — Rule 3 expanded state should be preserved
      messagesSubject.next([rule3]);
      fixture.detectChanges();

      expect(component.chatMessages[0].rule).toBe(3);
      expect(component.chatMessages[0].collapsed).toBe(false);
    });

    it('onToggleCollapse should ignore non Rule-3/4 messages', () => {
      const msg: ChatMessage = {
        id: 'r1',
        message_id: 'r1',
        parent_id: null,
        content: 'x',
        sender: makeAddress({ name: '@Human' }),
        recipient: makeAddress({ name: '@Manager' }),
        timestamp: new Date(),
        rule: 1,
        alignment: 'right',
        color: '#efeeee',
        collapsed: false,
        label: 'You ⇒ Manager',
      };
      component.onToggleCollapse(msg);
      expect(msg.collapsed).toBe(false);
    });

    it('Open button click path: onRule3Clicked still opens modal (regression)', () => {
      const rule3Msg = makeSentMessage(
        { name: '@Manager', role: 'Manager' },
        { name: '@QATester', role: 'Human' },
        'need approval',
        'r3-open',
      );
      messagesSubject.next([rule3Msg]);
      fixture.detectChanges();

      const chatMsg = component.chatMessages[0];
      component.onRule3Clicked(chatMsg);

      expect(component.modalVisible).toBe(true);
      component.onModalReply({ content: 'ok', messageId: chatMsg.id });
      const api = TestBed.inject(ApiService) as any;
      expect(api.processHumanInput).toHaveBeenCalledWith('test-team', 'ok', chatMsg.id);
    });
  });

  it('onToggleCollapse should toggle collapsed state and preserve across re-classification', () => {
    const msg4 = makeSentMessage(
      { name: '@Worker', role: 'Worker' },
      { name: '@Manager', role: 'Manager' },
      'ai msg',
      'msg-r4',
    );
    messagesSubject.next([msg4]);
    fixture.detectChanges();

    expect(component.chatMessages[0].collapsed).toBe(true);

    // Expand it
    component.onToggleCollapse(component.chatMessages[0]);
    expect(component.chatMessages[0].collapsed).toBe(false);

    // Re-emit messages (simulating new message arrival)
    messagesSubject.next([msg4]);
    fixture.detectChanges();

    // Collapsed state should be preserved
    expect(component.chatMessages[0].collapsed).toBe(false);
  });

  describe('displayItems merge (Story 4-8)', () => {
    function getThinkingSubj(): BehaviorSubject<ThinkingState[]> {
      const svc = TestBed.inject(ChatService) as any;
      return svc.thinkingAgents$ as BehaviorSubject<ThinkingState[]>;
    }

    function makeThinking(
      overrides: Partial<ThinkingState> = {},
    ): ThinkingState {
      return {
        agent_id: 'a1',
        agent_name: '@Researcher',
        start_time: new Date('2026-04-12T10:00:00Z'),
        tools: [],
        anchor_message_id: 'anchor-1',
        final: false,
        ...overrides,
      };
    }

    it('sorts a message and a thinking state chronologically', () => {
      const sent = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'later',
        'm-1',
      );
      sent.timestamp = '2026-04-12T10:00:10Z';
      messagesSubject.next([sent]);
      getThinkingSubj().next([
        makeThinking({
          start_time: new Date('2026-04-12T10:00:00Z'),
        }),
      ]);
      fixture.detectChanges();

      expect(component.displayItems.length).toBe(2);
      expect(component.displayItems[0].kind).toBe('thinking');
      expect(component.displayItems[1].kind).toBe('message');
    });

    it('ties (same timestamp) order messages BEFORE thinking bubbles', () => {
      const sent = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'sametime',
        'tie-1',
      );
      sent.timestamp = '2026-04-12T10:00:00Z';
      messagesSubject.next([sent]);
      getThinkingSubj().next([
        makeThinking({ start_time: new Date('2026-04-12T10:00:00Z') }),
      ]);
      fixture.detectChanges();

      expect(component.displayItems.length).toBe(2);
      expect(component.displayItems[0].kind).toBe('message');
      expect(component.displayItems[1].kind).toBe('thinking');
    });

    it('trackByDisplayItem produces stable keys across ephemeral → persistent transition', () => {
      const s1 = makeThinking({ final: false, anchor_message_id: 'anc-stable' });
      const key1 = component.trackByDisplayItem(0, { kind: 'thinking', data: s1 });
      const s2 = makeThinking({ final: true, anchor_message_id: 'anc-stable' });
      const key2 = component.trackByDisplayItem(0, { kind: 'thinking', data: s2 });
      expect(key1).toBe(key2);
    });

    it('trackByDisplayItem distinguishes message ids from thinking anchor ids', () => {
      const chatMsg: ChatMessage = {
        id: 'same-id',
        message_id: 'same-id',
        parent_id: null,
        content: 'x',
        sender: makeAddress({ name: '@A' }),
        recipient: makeAddress({ name: '@B' }),
        timestamp: new Date(),
        rule: 2,
        alignment: 'left',
        color: '#9ebbcb',
        collapsed: false,
        label: 'A ⇒ B',
      };
      const state = makeThinking({ anchor_message_id: 'same-id' });
      const k1 = component.trackByDisplayItem(0, {
        kind: 'message',
        data: chatMsg,
      });
      const k2 = component.trackByDisplayItem(0, { kind: 'thinking', data: state });
      expect(k1).not.toBe(k2);
    });

    it('loading$ DOM block is gone (no .thinking-animation inside .message-list)', () => {
      const sent = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'hello',
      );
      messagesSubject.next([sent]);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const list = el.querySelector('.message-list');
      expect(list).not.toBeNull();
      expect(list!.querySelector('.thinking-animation')).toBeNull();
    });

    it('onToggleThinkingExpanded toggles the anchor id in the internal set', () => {
      component.onToggleThinkingExpanded('anc-1');
      const state = makeThinking({ anchor_message_id: 'anc-1' });
      expect(component.isThinkingExpanded(state)).toBe(true);
      component.onToggleThinkingExpanded('anc-1');
      expect(component.isThinkingExpanded(state)).toBe(false);
    });
  });

  describe('Hover-aware auto-scroll lock (Story 4.4)', () => {
    // Helper to install a mock scrollContainer with controllable
    // scrollTop/scrollHeight/clientHeight on the component.
    function installMockScrollContainer(
      initialHeight = 1000,
      clientHeight = 400,
    ): { scrollTop: number; scrollHeight: number; clientHeight: number } {
      const mockEl = { scrollTop: 0, scrollHeight: initialHeight, clientHeight };
      (component as any).scrollContainer = { nativeElement: mockEl };
      (component as any).lastScrollHeight = initialHeight;
      return mockEl;
    }

    it('(a) auto-scroll fires when NOT hovered and a new message arrives', () => {
      const mockEl = installMockScrollContainer(1000, 400);
      // Not hovered (default)
      expect((component as any).isHovered).toBe(false);
      // Simulate DOM growth from a new message.
      mockEl.scrollHeight = 1200;
      component.ngAfterViewChecked();
      expect(mockEl.scrollTop).toBe(1200);
    });

    it('(b) auto-scroll is SUSPENDED when hovered; pendingCatchUpScroll is set', () => {
      const mockEl = installMockScrollContainer(1000, 400);
      mockEl.scrollTop = 850; // near bottom so shouldScrollToBottom will be true
      (component as any).checkShouldAutoScroll();
      component.onMouseEnter();
      expect((component as any).isHovered).toBe(true);

      // New message arrives while hovered.
      const sent = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'hovered-arrival',
        'hov-1',
      );
      messagesSubject.next([sent]);
      mockEl.scrollHeight = 1200;
      component.ngAfterViewChecked();

      // scrollTop was NOT moved to bottom — hover suspends.
      expect(mockEl.scrollTop).toBe(850);
      expect((component as any).pendingCatchUpScroll).toBe(true);
    });

    it('(c) mouseleave performs catch-up when a message arrived during hover', fakeAsync(() => {
      const mockEl = installMockScrollContainer(1000, 400);
      mockEl.scrollTop = 850;
      (component as any).checkShouldAutoScroll();
      component.onMouseEnter();

      const sent = makeSentMessage(
        { name: '@Human', role: 'Human' },
        { name: '@Manager', role: 'Manager' },
        'catch-up',
        'cu-1',
      );
      messagesSubject.next([sent]);
      mockEl.scrollHeight = 1200;
      component.ngAfterViewChecked();
      expect(mockEl.scrollTop).toBe(850);
      expect((component as any).pendingCatchUpScroll).toBe(true);

      component.onMouseLeave();
      flushMicrotasks();

      expect(mockEl.scrollTop).toBe(1200);
      expect((component as any).pendingCatchUpScroll).toBe(false);
      expect((component as any).isHovered).toBe(false);
    }));

    it('(d) mouseleave does NOT catch-up if no message arrived during hover', fakeAsync(() => {
      const mockEl = installMockScrollContainer(1000, 400);
      mockEl.scrollTop = 850;
      (component as any).checkShouldAutoScroll();

      component.onMouseEnter();
      // No message arrives — scrollHeight unchanged.
      component.ngAfterViewChecked();
      component.onMouseLeave();
      flushMicrotasks();

      expect(mockEl.scrollTop).toBe(850);
      expect((component as any).pendingCatchUpScroll).toBe(false);
    }));

    it('(e) collapse toggle during hover does NOT queue a catch-up', fakeAsync(() => {
      // Seed a Rule 4 message so we have something to toggle.
      const r4 = makeSentMessage(
        { name: '@Worker', role: 'Worker' },
        { name: '@Manager', role: 'Manager' },
        'ai msg',
        'r4-hover',
      );
      messagesSubject.next([r4]);
      fixture.detectChanges();

      const mockEl = installMockScrollContainer(1000, 400);
      mockEl.scrollTop = 850;
      (component as any).checkShouldAutoScroll();

      component.onMouseEnter();

      // User toggles collapse — this changes DOM height but is NOT a
      // message-arrival event, so pendingCatchUpScroll MUST remain false.
      component.onToggleCollapse(component.chatMessages[0]);
      mockEl.scrollHeight = 1100; // toggle grew the bubble
      component.ngAfterViewChecked();

      // Hover still suspends the scroll.
      expect(mockEl.scrollTop).toBe(850);
      // CRITICAL: no catch-up queued for a collapse toggle.
      expect((component as any).pendingCatchUpScroll).toBe(false);

      component.onMouseLeave();
      flushMicrotasks();

      // No catch-up fired — scrollTop still at 850.
      expect(mockEl.scrollTop).toBe(850);
    }));
  });
});
