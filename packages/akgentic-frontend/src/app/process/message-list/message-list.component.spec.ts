import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { MessageService } from 'primeng/api';

import { MessageListComponent } from './message-list.component';
import { MessageLogService } from '../../services/message-log.service';
import { AkgenticMessage, SentMessage } from '../../models/message.types';

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

function workerSent(id: string): SentMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-05-18T00:00:00Z',
    sender: {
      __actor_address__: true,
      name: '@Worker',
      role: 'Worker',
      agent_id: 'worker-1',
      team_id: 'team-1',
      squad_id: 's',
      user_message: false,
    },
    display_type: 'ai',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    recipient: {
      __actor_address__: true,
      name: '@Manager',
      role: 'Manager',
      agent_id: 'manager-1',
      team_id: 'team-1',
      squad_id: 's',
      user_message: false,
    },
    message: {
      id: `${id}-inner`,
      parent_id: null,
      team_id: 'team-1',
      timestamp: '2026-05-18T00:00:00Z',
      sender: {
        __actor_address__: true,
        name: '@Worker',
        role: 'Worker',
        agent_id: 'worker-1',
        team_id: 'team-1',
        squad_id: 's',
        user_message: false,
      },
      display_type: 'ai',
      content: 'ordinary message',
      __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    },
  };
}

/** A welcome `SentMessage`: outer `ActorSystem` sender, inner `WelcomeMessage`
 *  payload with `display_type === 'other'` (Story 2.6, ADR-011). */
function welcomeSent(id: string): SentMessage {
  return {
    id,
    parent_id: null,
    team_id: 'team-1',
    timestamp: '2026-05-18T00:00:00Z',
    sender: {
      __actor_address__: true,
      name: '@ActorSystem',
      role: 'ActorSystem',
      agent_id: 'sys',
      team_id: 'team-1',
      squad_id: 's',
      user_message: false,
    },
    display_type: 'other',
    content: null,
    __model__: 'akgentic.core.messages.orchestrator.SentMessage',
    recipient: {
      __actor_address__: true,
      name: '@Human',
      role: 'Human',
      agent_id: 'human',
      team_id: 'team-1',
      squad_id: 's',
      user_message: false,
    },
    message: {
      id: `${id}-inner`,
      parent_id: null,
      team_id: 'team-1',
      timestamp: '2026-05-18T00:00:00Z',
      sender: {
        __actor_address__: true,
        name: '@Orchestrator',
        role: 'Orchestrator',
        agent_id: 'orch',
        team_id: 'team-1',
        squad_id: 's',
        user_message: false,
      },
      display_type: 'other',
      content: 'Welcome to the agent team !',
      __model__: 'akgentic.team.messages.WelcomeMessage',
    },
  };
}

describe('MessageListComponent (Story 2.6, AC8)', () => {
  let component: MessageListComponent;
  let fixture: ComponentFixture<MessageListComponent>;
  let log: MessageLogService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MessageListComponent, NoopAnimationsModule],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        MessageService,
        // MessageLogService is component-scoped in production; provide it at
        // module level here so the test can drive the log directly.
        MessageLogService,
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MessageListComponent);
    component = fixture.componentInstance;
    log = TestBed.inject(MessageLogService);
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  it('excludes the welcome announcement from filteredMessages', () => {
    log.appendAll([welcomeSent('w1') as AkgenticMessage]);
    fixture.detectChanges();

    expect(component.filteredMessages.map((m) => m.id)).not.toContain('w1');
    expect(component.filteredMessages.length).toBe(0);
  });

  it('keeps ordinary messages while filtering out the welcome announcement', () => {
    log.appendAll([
      welcomeSent('w1') as AkgenticMessage,
      workerSent('s1') as AkgenticMessage,
    ]);
    fixture.detectChanges();

    expect(component.filteredMessages.map((m) => m.id)).toEqual(['s1']);
  });
});
