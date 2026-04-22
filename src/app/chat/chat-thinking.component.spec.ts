import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';

import { ChatThinkingComponent } from './chat-thinking.component';
import { ThinkingState } from '../services/chat.service';

function makeState(overrides: Partial<ThinkingState> = {}): ThinkingState {
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

describe('ChatThinkingComponent (Story 4-8)', () => {
  let fixture: ComponentFixture<ChatThinkingComponent>;
  let component: ChatThinkingComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChatThinkingComponent, NoopAnimationsModule],
    }).compileComponents();
    fixture = TestBed.createComponent(ChatThinkingComponent);
    component = fixture.componentInstance;
  });

  function setInputs(state: ThinkingState, expanded = false): void {
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('expanded', expanded);
    fixture.detectChanges();
  }

  it('should create', () => {
    setInputs(makeState());
    expect(component).toBeTruthy();
  });

  it('collapsed non-final state renders header + dots, NO tool list', () => {
    setInputs(makeState({ final: false, tools: [] }), false);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.thinking-header')).not.toBeNull();
    expect(el.querySelector('.thinking-dots')).not.toBeNull();
    expect(el.querySelector('.tool-list')).toBeNull();
  });

  it('final state renders header (no dots) + full tool list', () => {
    const tools = [
      {
        tool_call_id: 'call-1',
        tool_name: 'search_web',
        arguments_preview: 'q=x',
        done: true,
      },
      {
        tool_call_id: 'call-2',
        tool_name: 'analyze',
        arguments_preview: 'p=1',
        done: false,
      },
    ];
    setInputs(makeState({ final: true, tools }), false);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.thinking-dots')).toBeNull();
    const entries = el.querySelectorAll('.tool-entry');
    expect(entries.length).toBe(2);
  });

  it('collapsed non-final state with tools renders tool list (live streaming)', () => {
    const tools = [
      {
        tool_call_id: 'call-1',
        tool_name: 'search_web',
        arguments_preview: 'q=x',
        done: false,
      },
    ];
    setInputs(makeState({ final: false, tools }), false);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.tool-list')).not.toBeNull();
    expect(el.querySelectorAll('.tool-entry').length).toBe(1);
  });

  it('expanded non-final state with tools renders tool list', () => {
    const tools = [
      {
        tool_call_id: 'call-1',
        tool_name: 'search_web',
        arguments_preview: 'q=x',
        done: false,
      },
    ];
    setInputs(makeState({ final: false, tools }), true);
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('.tool-list')).not.toBeNull();
    expect(el.querySelectorAll('.tool-entry').length).toBe(1);
  });

  it('click on bubble emits toggleExpanded with state.anchor_message_id', (done) => {
    const state = makeState({ anchor_message_id: 'my-anchor' });
    setInputs(state, false);
    component.toggleExpanded.subscribe((id: string) => {
      expect(id).toBe('my-anchor');
      done();
    });
    const el: HTMLElement = fixture.nativeElement;
    (el.querySelector('.thinking-bubble') as HTMLElement).click();
  });

  it('trackByToolId returns the tool_call_id', () => {
    const entry = {
      tool_call_id: 'call-9',
      tool_name: 'foo',
      arguments_preview: '',
      done: false,
    };
    expect(component.trackByToolId(0, entry)).toBe('call-9');
  });
});
