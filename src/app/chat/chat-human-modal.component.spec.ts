import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { provideMarkdown } from 'ngx-markdown';

import {
  AnsweredRequest,
  ChatHumanModalComponent,
  HumanModalReply,
} from './chat-human-modal.component';
import { ChatMessage } from '../models/chat-message.model';
import { ActorAddress } from '../models/message.types';

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

function makeChatMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  const id = overrides.id ?? 'msg-1';
  return {
    id,
    message_id: id,
    parent_id: null,
    content: 'Hello world',
    sender: makeAddress({ name: '@Manager', role: 'Manager' }),
    recipient: makeAddress({ name: '@QATester', role: 'Human' }),
    timestamp: new Date('2026-04-08T10:00:00Z'),
    rule: 3,
    alignment: 'left',
    color: '#9ebbcb',
    collapsed: false,
    label: 'Manager ⇒ QATester',
    ...overrides,
  };
}

function makeAnsweredRequest(
  requestOverrides: Partial<ChatMessage> = {},
  replyOverrides: Partial<ChatMessage> = {},
): AnsweredRequest {
  const request = makeChatMessage({
    id: requestOverrides.id ?? 'req-1',
    ...requestOverrides,
  });
  const reply = makeChatMessage({
    id: replyOverrides.id ?? 'reply-for-' + request.id,
    parent_id: request.id,
    content: replyOverrides.content ?? 'answered',
    sender: makeAddress({ name: '@QATester', role: 'Human' }),
    recipient: makeAddress({ name: '@Manager', role: 'Manager' }),
    ...replyOverrides,
  });
  return { request, reply };
}

describe('ChatHumanModalComponent', () => {
  let component: ChatHumanModalComponent;
  let fixture: ComponentFixture<ChatHumanModalComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatHumanModalComponent, NoopAnimationsModule],
      providers: [provideMarkdown()],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatHumanModalComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('headerText', () => {
    it('should return default header when no agentPair', () => {
      fixture.detectChanges();
      expect(component.headerText).toBe('Human Input');
    });

    it('should format header with agent names', () => {
      fixture.componentRef.setInput('agentPair', {
        sender: makeAddress({ name: '@Manager-manager' }),
        recipient: makeAddress({ name: '@QATester-qa_tester' }),
      });
      fixture.detectChanges();

      expect(component.headerText).toContain('Manager');
      expect(component.headerText).toContain('⇒');
      expect(component.headerText).toContain('QATester');
    });
  });

  describe('onSendForRequest', () => {
    it('should emit reply with the id of the request whose Send button was clicked', () => {
      const msgs = [
        makeChatMessage({ id: 'r3-1' }),
        makeChatMessage({ id: 'r3-2' }),
        makeChatMessage({ id: 'r3-3' }),
      ];
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', msgs);
      fixture.detectChanges();

      const emitted: HumanModalReply[] = [];
      component.reply.subscribe((r: HumanModalReply) => emitted.push(r));

      component.replyBuffers.set('r3-2', 'only this one');
      component.onSendForRequest('r3-2');

      expect(emitted.length).toBe(1);
      expect(emitted[0].content).toBe('only this one');
      expect(emitted[0].messageId).toBe('r3-2');
    });

    it('should not clear other request buffers when one is sent', () => {
      const msgs = [
        makeChatMessage({ id: 'r3-1' }),
        makeChatMessage({ id: 'r3-3' }),
      ];
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', msgs);
      fixture.detectChanges();

      component.replyBuffers.set('r3-1', 'looks good');
      component.replyBuffers.set('r3-3', 'need more detail');
      component.onSendForRequest('r3-1');

      expect(component.replyBuffers.get('r3-1')).toBeUndefined();
      expect(component.replyBuffers.get('r3-3')).toBe('need more detail');
    });

    it('should not emit visibleChange(false) after sending one request', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', [makeChatMessage({ id: 'r3-1' })]);
      fixture.detectChanges();

      const visibleChanges: boolean[] = [];
      component.visibleChange.subscribe((v: boolean) => visibleChanges.push(v));

      component.replyBuffers.set('r3-1', 'done');
      component.onSendForRequest('r3-1');

      expect(visibleChanges.length).toBe(0);
    });

    it('should not emit if reply buffer is empty/whitespace for that request', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', [makeChatMessage({ id: 'r3-1' })]);
      fixture.detectChanges();

      const emitted: HumanModalReply[] = [];
      component.reply.subscribe((r: HumanModalReply) => emitted.push(r));

      component.replyBuffers.set('r3-1', '   ');
      component.onSendForRequest('r3-1');

      expect(emitted.length).toBe(0);
    });

    it('should not emit if the buffer for the request id is missing', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', [makeChatMessage({ id: 'r3-1' })]);
      fixture.detectChanges();

      const emitted: HumanModalReply[] = [];
      component.reply.subscribe((r: HumanModalReply) => emitted.push(r));

      component.onSendForRequest('r3-1');

      expect(emitted.length).toBe(0);
    });

    it('should clear only the sent request buffer', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', [makeChatMessage({ id: 'r3-1' })]);
      fixture.detectChanges();

      component.replyBuffers.set('r3-1', 'hello');
      component.onSendForRequest('r3-1');

      expect(component.replyBuffers.has('r3-1')).toBe(false);
    });
  });

  describe('DOM rendering', () => {
    it('renders one .pending-request per pending message', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', [
        makeChatMessage({ id: 'r3-1' }),
        makeChatMessage({ id: 'r3-2' }),
      ]);
      fixture.detectChanges();

      const nodes = document.querySelectorAll('.pending-request');
      expect(nodes.length).toBe(2);
    });

    it('renders answered section when answeredMessages non-empty', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', []);
      fixture.componentRef.setInput('answeredMessages', [
        makeAnsweredRequest({ id: 'a-1' }),
        makeAnsweredRequest({ id: 'a-2' }),
      ]);
      fixture.detectChanges();

      const nodes = document.querySelectorAll('.answered-request');
      expect(nodes.length).toBe(2);
    });

    it('answered section contains no textarea', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', []);
      fixture.componentRef.setInput('answeredMessages', [makeAnsweredRequest({ id: 'a-1' })]);
      fixture.detectChanges();

      const answered = document.querySelector('.answered-request');
      expect(answered).toBeTruthy();
      expect(answered!.querySelector('textarea')).toBeNull();
    });

    it('shows empty placeholder when both lists are empty', () => {
      fixture.componentRef.setInput('visible', true);
      fixture.componentRef.setInput('pendingMessages', []);
      fixture.componentRef.setInput('answeredMessages', []);
      fixture.detectChanges();

      expect(document.querySelector('.empty-state')).toBeTruthy();
    });
  });

  describe('onVisibleChange', () => {
    it('should emit false and clear buffers when dialog is closed', () => {
      fixture.detectChanges();
      component.replyBuffers.set('r3-1', 'draft');
      const emitted: boolean[] = [];
      component.visibleChange.subscribe((v: boolean) => emitted.push(v));

      component.onVisibleChange(false);

      expect(emitted).toEqual([false]);
      expect(component.replyBuffers.size).toBe(0);
    });

    it('should NOT emit when value is true (dialog opens)', () => {
      fixture.detectChanges();
      const emitted: boolean[] = [];
      component.visibleChange.subscribe((v: boolean) => emitted.push(v));

      component.onVisibleChange(true);

      expect(emitted.length).toBe(0);
    });
  });
});
