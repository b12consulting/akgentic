import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { provideMarkdown } from 'ngx-markdown';
import { ChatMessageComponent } from './chat-message.component';
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
    recipient: makeAddress({ name: '@Human', role: 'Human' }),
    timestamp: new Date('2026-04-08T10:00:00Z'),
    rule: 2,
    alignment: 'left',
    color: '#9ebbcb',
    collapsed: false,
    label: 'Manager [Manager]',
    ...overrides,
  };
}

describe('ChatMessageComponent', () => {
  let component: ChatMessageComponent;
  let fixture: ComponentFixture<ChatMessageComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatMessageComponent, NoopAnimationsModule],
      providers: [provideMarkdown()],
    }).compileComponents();

    fixture = TestBed.createComponent(ChatMessageComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.componentRef.setInput('message', makeChatMessage());
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('Rule 1 (user sends)', () => {
    it('should render right-aligned bubble', () => {
      const msg = makeChatMessage({
        rule: 1,
        alignment: 'right',
        color: '#efeeee',
        label: 'You ⇒ Manager',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      const bubble = el.querySelector('.message.right');
      expect(bubble).toBeTruthy();
      expect(el.querySelector('.collapsed-line')).toBeNull();
    });

    it('should disable label pill for Rule 1', () => {
      const msg = makeChatMessage({
        rule: 1,
        alignment: 'right',
        label: 'You ⇒ Manager',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.label-pill');
      expect(btn.disabled).toBe(true);
    });
  });

  describe('Rule 2 (reply to @Human)', () => {
    it('should render left-aligned bubble', () => {
      const msg = makeChatMessage({ rule: 2, alignment: 'left' });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      expect(el.querySelector('.message.left')).toBeTruthy();
    });

    it('should enable label pill', () => {
      const msg = makeChatMessage({ rule: 2, alignment: 'left' });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.label-pill');
      expect(btn.disabled).toBe(false);
    });
  });

  describe('Rule 3 (notification + collapse + Reply button)', () => {
    it('should show hand-raised icon (🙋) on expanded bubble when notification is true', () => {
      const msg = makeChatMessage({
        rule: 3,
        alignment: 'left',
        collapsed: false,
        label: 'Agent ⇒ OtherHuman',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      const icon = el.querySelector('.notification-icon');
      expect(icon).toBeTruthy();
      expect(icon.textContent).toContain('🙋');
      // Bell icon must NOT be present anywhere for Rule 3
      expect(el.querySelector('.pi-bell')).toBeNull();
    });

    it('should NOT show hand-raised icon when notification input is false', () => {
      const msg = makeChatMessage({
        rule: 3,
        alignment: 'left',
        collapsed: false,
        label: 'Agent ⇒ OtherHuman',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      expect(el.querySelector('.notification-icon')).toBeNull();
      expect(el.querySelector('.pi-bell')).toBeNull();
    });

    it('should render collapsed line by default when Rule 3 collapsed', () => {
      const msg = makeChatMessage({
        rule: 3,
        collapsed: true,
        label: 'Agent ⇒ OtherHuman',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      expect(el.querySelector('.collapsed-line')).toBeTruthy();
      expect(el.querySelector('.message-bubble')).toBeNull();
      const label = el.querySelector('.collapsed-label');
      expect(label.textContent).toContain('Agent ⇒ OtherHuman');
    });

    it('should append (🙋) in collapsed line when notification is true', () => {
      const msg = makeChatMessage({
        rule: 3,
        collapsed: true,
        label: 'Agent ⇒ OtherHuman',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      expect(label.textContent).toContain('🙋');
      expect(label.textContent).toContain('(');
      expect(label.textContent).toContain(')');
    });

    it('should NOT append (🙋) in collapsed line when notification is false', () => {
      const msg = makeChatMessage({
        rule: 3,
        collapsed: true,
        label: 'Agent ⇒ OtherHuman',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      expect(label.textContent).not.toContain('🙋');
    });

    it('should render Reply button on collapsed Rule 3 line', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.open-button');
      expect(btn).toBeTruthy();
      expect(btn.textContent).toContain('Reply');
    });

    it('should render Reply button on expanded Rule 3 bubble header', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const header = fixture.nativeElement.querySelector('.bubble-header');
      const btn = header.querySelector('.open-button');
      expect(btn).toBeTruthy();
      expect(btn.textContent).toContain('Reply');
    });

    it('bubble body click on expanded Rule 3 should emit toggleCollapse and NOT rule3Clicked', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.toggleCollapse, 'emit');
      spyOn(component.rule3Clicked, 'emit');

      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.toggleCollapse.emit).toHaveBeenCalledWith(msg);
      expect(component.rule3Clicked.emit).not.toHaveBeenCalled();
    });

    it('collapsed-line click on Rule 3 should emit toggleCollapse and NOT rule3Clicked', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.toggleCollapse, 'emit');
      spyOn(component.rule3Clicked, 'emit');

      const line = fixture.nativeElement.querySelector('.collapsed-line');
      line.click();

      expect(component.toggleCollapse.emit).toHaveBeenCalledWith(msg);
      expect(component.rule3Clicked.emit).not.toHaveBeenCalled();
    });

    it('Reply button click should emit rule3Clicked and NOT toggleCollapse', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      spyOn(component.toggleCollapse, 'emit');
      spyOn(component.rule3Clicked, 'emit');

      const btn = fixture.nativeElement.querySelector('.open-button button');
      btn.click();

      expect(component.rule3Clicked.emit).toHaveBeenCalledWith(msg);
      expect(component.toggleCollapse.emit).not.toHaveBeenCalled();
    });
  });

  describe('Reply button visibility (Rule 3 only)', () => {
    it('should NOT render Reply button for Rule 1', () => {
      const msg = makeChatMessage({ rule: 1, alignment: 'right' });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.open-button')).toBeNull();
    });

    it('should NOT render Reply button for Rule 2', () => {
      const msg = makeChatMessage({ rule: 2, alignment: 'left' });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.open-button')).toBeNull();
    });

    it('should NOT render Reply button for Rule 4 (collapsed or expanded)', () => {
      const collapsed = makeChatMessage({ rule: 4, collapsed: true });
      fixture.componentRef.setInput('message', collapsed);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.open-button')).toBeNull();

      const expanded = makeChatMessage({ rule: 4, collapsed: false });
      fixture.componentRef.setInput('message', expanded);
      fixture.detectChanges();
      expect(fixture.nativeElement.querySelector('.open-button')).toBeNull();
    });
  });

  describe('Reply button disabled state (Story 4.5)', () => {
    it('should disable Reply button on collapsed Rule 3 when notification is false', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.open-button button');
      expect(btn).toBeTruthy();
      expect(btn.disabled).toBe(true);
    });

    it('should enable Reply button on collapsed Rule 3 when notification is true', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.open-button button');
      expect(btn).toBeTruthy();
      expect(btn.disabled).toBe(false);
    });

    it('should disable Reply button on expanded Rule 3 when notification is false', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.open-button button');
      expect(btn).toBeTruthy();
      expect(btn.disabled).toBe(true);
    });

    it('should enable Reply button on expanded Rule 3 when notification is true', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const btn = fixture.nativeElement.querySelector('.open-button button');
      expect(btn).toBeTruthy();
      expect(btn.disabled).toBe(false);
    });

    it('should NOT emit rule3Clicked when Reply button is disabled and clicked', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      spyOn(component.rule3Clicked, 'emit');
      const btn = fixture.nativeElement.querySelector('.open-button button');
      btn.click();

      expect(component.rule3Clicked.emit).not.toHaveBeenCalled();
    });

    it('should emit rule3Clicked when Reply button is enabled and clicked', () => {
      const msg = makeChatMessage({ rule: 3, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      spyOn(component.rule3Clicked, 'emit');
      const btn = fixture.nativeElement.querySelector('.open-button button');
      btn.click();

      expect(component.rule3Clicked.emit).toHaveBeenCalledWith(msg);
    });
  });

  describe('Rule 4 (AI-to-AI collapsed)', () => {
    it('should render collapsed line by default', () => {
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        label: 'Worker ⇒ Manager',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const el = fixture.nativeElement;
      expect(el.querySelector('.collapsed-line')).toBeTruthy();
      expect(el.querySelector('.message-bubble')).toBeNull();
    });

    it('should NOT render collapse-indicator when collapsed (caret removed)', () => {
      const msg = makeChatMessage({ rule: 4, collapsed: true });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const indicator = fixture.nativeElement.querySelector('.collapse-indicator');
      expect(indicator).toBeNull();
    });

    it('should emit toggleCollapse on click', () => {
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        content: 'AI message',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.toggleCollapse, 'emit');
      const collapsedLine = fixture.nativeElement.querySelector('.collapsed-line');
      collapsedLine.click();

      expect(component.toggleCollapse.emit).toHaveBeenCalledWith(msg);
    });

    it('should NOT render collapse-indicator-bubble when expanded (caret removed)', () => {
      const msg = makeChatMessage({ rule: 4, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const indicator = fixture.nativeElement.querySelector(
        '.collapse-indicator-bubble',
      );
      expect(indicator).toBeNull();
    });

    it('should emit toggleCollapse on click when expanded', () => {
      const msg = makeChatMessage({ rule: 4, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.toggleCollapse, 'emit');
      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.toggleCollapse.emit).toHaveBeenCalledWith(msg);
    });
  });

  describe('bubbleClicked output', () => {
    it('should emit bubbleClicked for Rule 1 bubble click', () => {
      const msg = makeChatMessage({
        rule: 1,
        alignment: 'right',
        color: '#efeeee',
        label: 'You ⇒ Manager',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.bubbleClicked, 'emit');
      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.bubbleClicked.emit).toHaveBeenCalledWith(msg);
    });

    it('should emit bubbleClicked for Rule 2 bubble click', () => {
      const msg = makeChatMessage({ rule: 2, alignment: 'left' });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.bubbleClicked, 'emit');
      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.bubbleClicked.emit).toHaveBeenCalledWith(msg);
    });

    it('should NOT emit bubbleClicked for Rule 4 bubble click', () => {
      const msg = makeChatMessage({ rule: 4, collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.bubbleClicked, 'emit');
      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.bubbleClicked.emit).not.toHaveBeenCalled();
    });
  });

  describe('rule3Clicked output', () => {
    it('should NOT emit bubbleClicked for Rule 3 bubble click', () => {
      const msg = makeChatMessage({ rule: 3, alignment: 'left', collapsed: false });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.bubbleClicked, 'emit');
      const messageEl = fixture.nativeElement.querySelector('.message');
      messageEl.click();

      expect(component.bubbleClicked.emit).not.toHaveBeenCalled();
    });
  });

  describe('selected input', () => {
    it('should apply .selected class when selected is true', () => {
      const msg = makeChatMessage({ rule: 2 });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('selected', true);
      fixture.detectChanges();

      const bubble = fixture.nativeElement.querySelector('.message-bubble');
      expect(bubble.classList.contains('selected')).toBe(true);
    });

    it('should NOT apply .selected class when selected is false', () => {
      const msg = makeChatMessage({ rule: 2 });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('selected', false);
      fixture.detectChanges();

      const bubble = fixture.nativeElement.querySelector('.message-bubble');
      expect(bubble.classList.contains('selected')).toBe(false);
    });

    it('should default selected to false', () => {
      const msg = makeChatMessage({ rule: 2 });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const bubble = fixture.nativeElement.querySelector('.message-bubble');
      expect(bubble.classList.contains('selected')).toBe(false);
    });
  });

  describe('collapsed line preview (Story 4.2)', () => {
    it('Rule 4 collapsed line renders preview after " : " when content is non-empty', () => {
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        label: 'Worker ⇒ Manager',
        content: 'Start of the message',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      expect(label.textContent).toContain('[Worker ⇒ Manager]');
      expect(label.textContent).toContain(' : Start of the message');
    });

    it('Rule 4 collapsed line omits " : " and preview when content is empty', () => {
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        label: 'Worker ⇒ Manager',
        content: '',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      expect(label.textContent).toContain('[Worker ⇒ Manager]');
      expect(label.textContent).not.toContain(' : ');
      expect(label.querySelector('.collapsed-preview')).toBeNull();
    });

    it('Rule 3 collapsed with notification renders (🙋) inside bracket, preview after " : "', () => {
      const msg = makeChatMessage({
        rule: 3,
        collapsed: true,
        label: 'Manager ⇒ Support',
        content: 'Can you verify the auth flow',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', true);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      const text = label.textContent.replace(/\s+/g, ' ');
      // Bracket encloses label + marker, then preview follows
      expect(text).toContain('[Manager ⇒ Support (🙋)]');
      expect(text).toContain(' : Can you verify the auth flow');
    });

    it('Rule 3 collapsed without notification omits (🙋) marker, preview still present', () => {
      const msg = makeChatMessage({
        rule: 3,
        collapsed: true,
        label: 'Manager ⇒ Support',
        content: 'Hello',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.componentRef.setInput('notification', false);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      const text = label.textContent.replace(/\s+/g, ' ');
      expect(text).not.toContain('🙋');
      expect(text).toContain('[Manager ⇒ Support]');
      expect(text).toContain(' : Hello');
    });

    it('long content is truncated with "..." in the rendered preview', () => {
      const longContent = 'x'.repeat(80);
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        label: 'Worker ⇒ Manager',
        content: longContent,
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const label = fixture.nativeElement.querySelector('.collapsed-label');
      expect(label.textContent).toContain('...');
      // Preview span ends with "..."
      const preview = label.querySelector('.collapsed-preview');
      expect(preview.textContent.trim().endsWith('...')).toBe(true);
    });

    it('timestamp remains visible on the same collapsed row', () => {
      const msg = makeChatMessage({
        rule: 4,
        collapsed: true,
        label: 'Worker ⇒ Manager',
        content: 'hi',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const ts = fixture.nativeElement.querySelector('.collapsed-timestamp');
      expect(ts).toBeTruthy();
      expect(ts.textContent.trim().length).toBeGreaterThan(0);
    });
  });

  describe('messageSelected output', () => {
    it('should emit on label click for non-Rule-1 messages', () => {
      const msg = makeChatMessage({ rule: 2 });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.messageSelected, 'emit');
      const btn = fixture.nativeElement.querySelector('.label-pill');
      btn.click();
      expect(component.messageSelected.emit).toHaveBeenCalledWith(msg);
    });

    it('should NOT emit on label click for Rule 1', () => {
      const msg = makeChatMessage({ rule: 1 });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      spyOn(component.messageSelected, 'emit');
      component.onLabelClick();
      expect(component.messageSelected.emit).not.toHaveBeenCalled();
    });
  });

  describe('Rule 2 label — @Sender ⇒ You (Story 4.3)', () => {
    it('renders label pill ending with "⇒ You" for Rule 2', () => {
      const msg = makeChatMessage({
        rule: 2,
        alignment: 'left',
        label: '@Manager ⇒ You',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const pill = fixture.nativeElement.querySelector('.label-pill');
      expect(pill).toBeTruthy();
      expect(pill.textContent.trim().endsWith('⇒ You')).toBe(true);
    });

    it('Rule 2 label pill is NOT disabled (clickable for selection)', () => {
      const msg = makeChatMessage({
        rule: 2,
        alignment: 'left',
        label: '@Manager ⇒ You',
      });
      fixture.componentRef.setInput('message', msg);
      fixture.detectChanges();

      const pill = fixture.nativeElement.querySelector('.label-pill');
      expect(pill.disabled).toBe(false);
    });
  });

  describe('Expanded bubble header layout (Story 4.3)', () => {
    function makeExpanded(rule: 1 | 2 | 3 | 4): ChatMessage {
      return makeChatMessage({
        rule,
        alignment: rule === 1 ? 'right' : 'left',
        collapsed: false,
        label:
          rule === 1
            ? 'You ⇒ @Manager'
            : rule === 2
              ? '@Manager ⇒ You'
              : rule === 3
                ? '@Manager ⇒ @Support'
                : '@Worker ⇒ @Manager',
        timestamp: new Date('2026-04-08T10:45:00Z'),
      });
    }

    for (const rule of [1, 2, 3, 4] as const) {
      it(`Rule ${rule}: .bubble-header .bubble-timestamp exists and matches HH:mm`, () => {
        fixture.componentRef.setInput('message', makeExpanded(rule));
        fixture.detectChanges();

        const ts = fixture.nativeElement.querySelector(
          '.bubble-header .bubble-timestamp',
        );
        expect(ts).toBeTruthy();
        expect(ts.textContent.trim()).toMatch(/^\d{2}:\d{2}$/);
      });

      it(`Rule ${rule}: standalone .timestamp span removed`, () => {
        fixture.componentRef.setInput('message', makeExpanded(rule));
        fixture.detectChanges();

        const standalone = fixture.nativeElement.querySelectorAll(
          '.message-bubble > .timestamp',
        );
        expect(standalone.length).toBe(0);
      });
    }

    it('Rule 3 expanded: .bubble-timestamp is the last element child of .bubble-header', () => {
      fixture.componentRef.setInput('message', makeExpanded(3));
      fixture.detectChanges();

      const header = fixture.nativeElement.querySelector('.bubble-header');
      expect(header).toBeTruthy();
      expect(header.lastElementChild.classList.contains('bubble-timestamp')).toBe(true);
    });

    it('Rule 4 expanded: .bubble-timestamp is the last element child of .bubble-header', () => {
      fixture.componentRef.setInput('message', makeExpanded(4));
      fixture.detectChanges();

      const header = fixture.nativeElement.querySelector('.bubble-header');
      expect(header).toBeTruthy();
      expect(header.lastElementChild.classList.contains('bubble-timestamp')).toBe(true);
    });
  });
});
