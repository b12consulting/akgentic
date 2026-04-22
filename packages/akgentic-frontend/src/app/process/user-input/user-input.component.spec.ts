import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormsModule } from '@angular/forms';
import { BehaviorSubject } from 'rxjs';

import { ProcessUserInputComponent } from './user-input.component';
import { ApiService } from '../../services/api.service';
import { ChatService } from '../../services/chat.service';
import { ContextService } from '../../services/context.service';
import { GraphDataService } from '../../services/graph-data.service';
import { ActorAddress } from '../../models/message.types';
import { NodeInterface } from '../../models/types';
import { makeAgentNameUserFriendly } from '../../lib/util';

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

function makeNode(overrides: Partial<NodeInterface> = {}): NodeInterface {
  return {
    name: 'agent-uuid-1',
    role: 'Worker',
    actorName: '@Worker',
    parentId: 'parent-1',
    squadId: 'squad-1',
    symbol: 'circle',
    category: 0,
    userMessage: true,
    ...overrides,
  };
}

describe('ProcessUserInputComponent', () => {
  let component: ProcessUserInputComponent;
  let fixture: ComponentFixture<ProcessUserInputComponent>;
  let apiServiceSpy: jasmine.SpyObj<ApiService>;
  let chatServiceMock: any;
  let contextServiceStub: { currentTeamRunning$: BehaviorSubject<boolean> };
  let nodesSubject: BehaviorSubject<NodeInterface[]>;

  beforeEach(async () => {
    apiServiceSpy = jasmine.createSpyObj('ApiService', ['sendMessage', 'sendMessageFromTo']);
    apiServiceSpy.sendMessage.and.returnValue(Promise.resolve());
    apiServiceSpy.sendMessageFromTo.and.returnValue(Promise.resolve());

    chatServiceMock = {
      messages$: new BehaviorSubject<any[]>([]),
      loadingProcess$: new BehaviorSubject<boolean>(false),
    };

    // sendMessage() guards on currentTeamRunning$.value (Story 2-5). Default
    // to running=true so the guard does not short-circuit routing tests.
    contextServiceStub = {
      currentTeamRunning$: new BehaviorSubject<boolean>(true),
    };

    nodesSubject = new BehaviorSubject<NodeInterface[]>([]);

    const graphDataService = {
      nodes$: nodesSubject,
    };

    await TestBed.configureTestingModule({
      imports: [FormsModule, ProcessUserInputComponent],
      providers: [
        { provide: ApiService, useValue: apiServiceSpy },
        { provide: ChatService, useValue: chatServiceMock },
        { provide: ContextService, useValue: contextServiceStub },
        { provide: GraphDataService, useValue: graphDataService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ProcessUserInputComponent);
    component = fixture.componentInstance;
    component.processId = 'test-team-id';
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('dropdown population from nodes$', () => {
    it('should populate dropdown with @-named agents minus @Human', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Manager' }),
        makeNode({ name: 'dev-1', actorName: '@Developer', role: 'Developer' }),
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
      ]);

      expect(component.dropdownAgents.length).toBe(2);
      expect(component.dropdownAgents.map(a => a.value)).toEqual(['@Manager', '@Developer']);
      expect(component.dropdownAgents.map(a => a.value)).not.toContain('@Human');
    });

    it('should populate mentionItems with same filter as dropdown', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Manager' }),
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
      ]);

      expect(component.mentionItems.length).toBe(1);
      expect(component.mentionItems[0].actorName).toBe('@Manager');
    });

    it('should remove fired agent from selectedAgents when nodes$ emits updated list', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager' }),
        makeNode({ name: 'dev-1', actorName: '@Developer' }),
      ]);

      component.selectedAgents = ['@Manager', '@Developer'];

      // Fire @Developer by emitting nodes without it
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager' }),
      ]);

      expect(component.selectedAgents).toEqual(['@Manager']);
    });

    it('should exclude nodes without @ prefix from dropdown', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager' }),
        makeNode({ name: 'sys-1', actorName: 'system-process' }),
      ]);

      expect(component.dropdownAgents.length).toBe(1);
      expect(component.dropdownAgents[0].value).toBe('@Manager');
    });
  });

  describe('sendMessage()', () => {
    it('should not send when input is empty', async () => {
      component.userInput = '';
      await component.sendMessage();
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
    });

    it('should not send when input is whitespace only', async () => {
      component.userInput = '   ';
      await component.sendMessage();
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
    });

    it('should broadcast when no agents selected (default)', async () => {
      component.userInput = 'hello everyone';
      component.selectedAgents = [];

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledOnceWith(
        'test-team-id',
        'hello everyone',
      );
    });

    it('should send to single selected agent via dropdown', async () => {
      component.selectedAgents = ['@Manager'];
      component.userInput = 'hello manager';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledOnceWith(
        'test-team-id',
        'hello manager',
        '@Manager',
      );
    });

    it('should send to multiple selected agents via dropdown', async () => {
      component.selectedAgents = ['@Manager', '@Developer'];
      component.userInput = 'hello team';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledTimes(2);
      expect(apiServiceSpy.sendMessage).toHaveBeenCalledWith(
        'test-team-id',
        'hello team',
        '@Manager',
      );
      expect(apiServiceSpy.sendMessage).toHaveBeenCalledWith(
        'test-team-id',
        'hello team',
        '@Developer',
      );
    });

    it('should clear userInput after sending', async () => {
      component.userInput = 'will be cleared';
      component.selectedAgents = [];
      await component.sendMessage();
      expect(component.userInput).toBe('');
    });

    it('should persist dropdown selection across multiple sends', async () => {
      component.selectedAgents = ['@Manager'];
      component.userInput = 'first message';

      await component.sendMessage();

      expect(component.selectedAgents).toEqual(['@Manager']);

      component.userInput = 'second message';
      apiServiceSpy.sendMessage.calls.reset();
      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledOnceWith(
        'test-team-id',
        'second message',
        '@Manager',
      );
      expect(component.selectedAgents).toEqual(['@Manager']);
    });

    it('@mention text in input should NOT affect API call target', async () => {
      // Populate mentionItems so there IS an agent name in the text
      component.mentionItems = [
        { name: 'Manager [Manager]', actorName: '@Manager', agentId: 'mgr-1' },
      ];
      // No dropdown selection
      component.selectedAgents = [];
      component.userInput = 'hey Manager [Manager] do this';

      await component.sendMessage();

      // Should broadcast, NOT route to @Manager -- text matching is removed
      expect(apiServiceSpy.sendMessage).toHaveBeenCalledOnceWith(
        'test-team-id',
        'hey Manager [Manager] do this',
      );
    });
  });

  describe('Send-to echo indicator (Story 4-11)', () => {
    it('should hide the indicator when no agent is selected (broadcast case)', () => {
      component.selectedAgents = [];
      fixture.detectChanges();

      const indicator = fixture.nativeElement.querySelector('.reply-indicator');
      expect(indicator).toBeNull();
    });

    it('should render the indicator with a single-agent label when one is selected', () => {
      component.selectedAgents = ['@Manager'];
      fixture.detectChanges();

      const indicator = fixture.nativeElement.querySelector('.reply-indicator');
      expect(indicator).toBeTruthy();
      expect(indicator.textContent).toContain('Send to');
      expect(indicator.textContent).toContain('Manager');
    });

    it('should render the indicator with a comma-joined label when multiple are selected', () => {
      component.selectedAgents = ['@Manager', '@Developer'];
      fixture.detectChanges();

      expect(component.selectedAgentsDisplay).toContain('Manager');
      expect(component.selectedAgentsDisplay).toContain('Developer');
      expect(component.selectedAgentsDisplay).toContain(',');

      const indicator = fixture.nativeElement.querySelector('.reply-indicator');
      expect(indicator).toBeTruthy();
      expect(indicator.textContent).toContain('Send to');
    });

    it('clearSendTo() should empty selectedAgents', () => {
      component.selectedAgents = ['@Manager', '@Developer'];
      component.clearSendTo();
      expect(component.selectedAgents).toEqual([]);
    });

    it('the `×` button in the indicator should clear selectedAgents', () => {
      component.selectedAgents = ['@Manager'];
      fixture.detectChanges();

      const closeBtn = fixture.nativeElement.querySelector(
        '.reply-indicator .reply-indicator-close',
      );
      expect(closeBtn).toBeTruthy();
      closeBtn.click();
      fixture.detectChanges();

      expect(component.selectedAgents).toEqual([]);
      const indicatorAfter = fixture.nativeElement.querySelector('.reply-indicator');
      expect(indicatorAfter).toBeNull();
    });

    it('component should NOT expose legacy reply-context API', () => {
      // Retired by Story 4-11 — these fields/methods are gone.
      expect((component as any).replyContext).toBeUndefined();
      expect((component as any).replyContextDisplayName).toBeUndefined();
      expect((component as any).clearReplyContext).toBeUndefined();
    });
  });

  describe('"Send as" dropdown population (Story 7-1)', () => {
    it('initializes humanAgents / humanAgentOptions / selectedSender to defaults', () => {
      expect(component.humanAgents).toEqual([]);
      expect(component.humanAgentOptions).toEqual([]);
      expect(component.selectedSender).toBeNull();
    });

    it('populates humanAgents with role === Human (Story 7-3)', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Worker' }),
      ]);

      // Story 7-3: @Human is now INCLUDED alongside other human-role nodes.
      expect(component.humanAgents.length).toBe(3);
      expect(component.humanAgents.map((n) => n.actorName)).toEqual([
        '@Support',
        '@Operator',
        '@Human',
      ]);
      expect(component.humanAgentOptions.map((o) => o.value)).toEqual([
        '@Support',
        '@Operator',
        '@Human',
      ]);
      // Labels come from makeAgentNameUserFriendly (passes @Support through
      // as-is since there is no '-' role segment).
      expect(component.humanAgentOptions[0].label).toBe('@Support');
    });

    it('includes the entry-point @Human when role === Human (Story 7-3)', () => {
      nodesSubject.next([
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);

      // Story 7-3 (AC #1, AC #2): the @Human entry point is a first-class
      // selectable sender. The filter uses role === HUMAN_ROLE only.
      expect(component.humanAgents.length).toBe(2);
      expect(component.humanAgents.map((n) => n.actorName)).toContain('@Human');
      expect(component.humanAgents.map((n) => n.actorName)).toContain('@Support');
      expect(component.humanAgentOptions.map((o) => o.value)).toContain('@Human');
      // AC #2: label for @Human uses the friendly helper, not a hard-coded string.
      const humanOpt = component.humanAgentOptions.find((o) => o.value === '@Human');
      expect(humanOpt).toBeTruthy();
      expect(humanOpt!.label).toBe(makeAgentNameUserFriendly('@Human'));
    });

    it('excludes nodes whose role !== Human', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Manager' }),
        makeNode({ name: 'dev-1', actorName: '@Developer', role: 'Worker' }),
      ]);

      expect(component.humanAgents.length).toBe(0);
      expect(component.humanAgentOptions.length).toBe(0);
    });
  });

  describe('"Send as" dropdown visibility (Story 7-1)', () => {
    it('is hidden when humanAgents.length === 0', () => {
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Worker' }),
      ]);
      fixture.detectChanges();

      const dropdown = fixture.nativeElement.querySelector('p-dropdown');
      expect(dropdown).toBeNull();
    });

    it('is hidden when humanAgents.length === 1 (solo @Human) (Story 7-3)', () => {
      // Story 7-3: humans include @Human; solo-@Human still hides dropdown.
      nodesSubject.next([
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
      ]);
      fixture.detectChanges();

      const dropdown = fixture.nativeElement.querySelector('p-dropdown');
      expect(dropdown).toBeNull();
    });

    it('is visible when humanAgents.length === 2 (@Human + @Support) (Story 7-3)', () => {
      // Story 7-3: threshold fires at 2 humans INCLUDING @Human.
      nodesSubject.next([
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);
      fixture.detectChanges();

      const dropdown = fixture.nativeElement.querySelector('p-dropdown');
      expect(dropdown).not.toBeNull();
      expect(component.humanAgentOptions.length).toBe(2);
      expect(component.humanAgentOptions.map((o) => o.value)).toContain('@Human');
      expect(component.humanAgentOptions.map((o) => o.value)).toContain('@Support');
    });

    it('picking an option sets selectedSender to the option value', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);
      fixture.detectChanges();

      component.selectedSender = '@Support';
      fixture.detectChanges();

      expect(component.selectedSender).toBe('@Support');
    });

  });

  describe('"Send as" routing (Story 7-2)', () => {
    beforeEach(() => {
      // 2 non-entry-point humans so the dropdown would be visible
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Worker' }),
        makeNode({ name: 'dev-1', actorName: '@Developer', role: 'Worker' }),
      ]);
    });

    it('Priority 1: sender + recipients -> sendMessageFromTo per recipient (AC #1)', async () => {
      component.selectedSender = '@Support';
      component.selectedAgents = ['@Manager', '@Developer'];
      component.userInput = 'hello';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledTimes(2);
      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledWith(
        'test-team-id', '@Support', '@Manager', 'hello',
      );
      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledWith(
        'test-team-id', '@Support', '@Developer', 'hello',
      );
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });

    it('Priority 2: sender, no recipient, non-empty dropdownAgents -> first dropdown agent (AC #2)', async () => {
      // Override the beforeEach roster with workers only so dropdownAgents[0]
      // is @Manager (the "typical supervisor" case from AC #2). The current
      // dropdown filter (Story 3-1) excludes only the entry-point @Human; any
      // other @-prefixed node — including humans like @Support — would
      // otherwise land in dropdownAgents and shadow the worker at index 0.
      nodesSubject.next([
        makeNode({ name: 'mgr-1', actorName: '@Manager', role: 'Worker' }),
        makeNode({ name: 'dev-1', actorName: '@Developer', role: 'Worker' }),
      ]);
      // selectedSender is set AFTER the emission, so the clear-on-count-drop
      // logic (which runs inside the nodes$ subscription) doesn't see it.
      component.selectedSender = '@Support';
      component.selectedAgents = [];
      component.userInput = 'first-agent auto-target';

      // sanity-check the expected first entry.
      expect(component.dropdownAgents[0].value).toBe('@Manager');

      await component.sendMessage();

      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledOnceWith(
        'test-team-id', '@Support', '@Manager', 'first-agent auto-target',
      );
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });

    it('Priority 2 edge case: sender, no recipient, empty dropdownAgents -> no send, input preserved (AC #3)', async () => {
      // Emit a roster with 2 humans but NO worker agents in the Send-to dropdown.
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
      ]);
      // Force the edge case by emptying dropdownAgents directly:
      component.dropdownAgents = [];
      component.selectedSender = '@Support';
      component.selectedAgents = [];
      component.userInput = 'orphan sender';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessageFromTo).not.toHaveBeenCalled();
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(component.userInput).toBe('orphan sender');
    });

    it('Priority 3: no sender + recipients -> sendMessage per recipient (AC #4, Story 3-1 preserved)', async () => {
      component.selectedSender = null;
      component.selectedAgents = ['@Manager', '@Developer'];
      component.userInput = 'hello team';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledTimes(2);
      expect(apiServiceSpy.sendMessage).toHaveBeenCalledWith(
        'test-team-id', 'hello team', '@Manager',
      );
      expect(apiServiceSpy.sendMessage).toHaveBeenCalledWith(
        'test-team-id', 'hello team', '@Developer',
      );
      expect(apiServiceSpy.sendMessageFromTo).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });

    it('Priority 4: no sender + no recipient -> broadcast (AC #5, Story 3-1 preserved)', async () => {
      component.selectedSender = null;
      component.selectedAgents = [];
      component.userInput = 'broadcast hello';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).toHaveBeenCalledOnceWith(
        'test-team-id', 'broadcast hello',
      );
      expect(apiServiceSpy.sendMessageFromTo).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });

    it('empty input guard runs first across all priorities (AC #6)', async () => {
      component.selectedSender = '@Support';
      component.selectedAgents = ['@Manager'];
      component.userInput = '   '; // whitespace only

      await component.sendMessage();

      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(apiServiceSpy.sendMessageFromTo).not.toHaveBeenCalled();
      expect(component.userInput).toBe('   ');
    });

    it('Priority 1: @Human as sender routes via sendMessageFromTo (Story 7-3)', async () => {
      component.selectedSender = '@Human';
      component.selectedAgents = ['@Manager'];
      component.userInput = 'hello from human';

      await component.sendMessage();

      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledOnceWith(
        'test-team-id', '@Human', '@Manager', 'hello from human',
      );
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });

    it('Priority 2: @Human as sender with no recipient auto-targets first dropdown agent (Story 7-3)', async () => {
      component.selectedSender = '@Human';
      component.selectedAgents = [];
      component.userInput = 'auto-target from @Human';

      await component.sendMessage();

      const firstDropdownAgent = component.dropdownAgents[0]?.value;
      expect(firstDropdownAgent).toBeTruthy();
      expect(apiServiceSpy.sendMessageFromTo).toHaveBeenCalledOnceWith(
        'test-team-id', '@Human', firstDropdownAgent!, 'auto-target from @Human',
      );
      expect(apiServiceSpy.sendMessage).not.toHaveBeenCalled();
      expect(component.userInput).toBe('');
    });
  });

  describe('"Send as" dynamic state (Story 7-2)', () => {
    it('clears selectedSender when the selected sender is fired (AC #7)', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'thi-1', actorName: '@Third', role: 'Human' }),
      ]);
      component.selectedSender = '@Support';

      // Fire @Support by emitting a roster without it (count stays >= 2).
      nodesSubject.next([
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'thi-1', actorName: '@Third', role: 'Human' }),
      ]);

      expect(component.selectedSender).toBeNull();
      expect(component.humanAgents.length).toBe(2);
    });

    it('clears selectedSender when human count drops below 2 (AC #8)', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);
      component.selectedSender = '@Support';

      // Drop to 1 non-entry-point human.
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);

      expect(component.selectedSender).toBeNull();
      expect(component.humanAgents.length).toBe(1);
    });

    it('preserves selectedSender when the selection still exists and count >= 2', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
        makeNode({ name: 'thi-1', actorName: '@Third', role: 'Human' }),
      ]);
      component.selectedSender = '@Support';

      // Fire an unrelated human -> @Support stays selected.
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);

      expect(component.selectedSender).toBe('@Support');
    });

    it('does not resurrect a cleared selectedSender when count recovers', () => {
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);
      component.selectedSender = '@Support';

      // Drop below 2 -> selection cleared.
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);
      expect(component.selectedSender).toBeNull();

      // Recover to 2 humans -> selection STAYS null (user must repick).
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);
      expect(component.selectedSender).toBeNull();
    });

    it('clears selectedSender === "@Human" when @Human disappears from the roster (Story 7-3)', () => {
      // Initial: @Human + @Support both present, selectedSender = @Human
      nodesSubject.next([
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);
      component.selectedSender = '@Human';

      // Defensive case: @Human somehow removed (leaving only non-entry humans).
      // The clear-on-fire predicate fires because @Human is no longer in humanAgents.
      nodesSubject.next([
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
        makeNode({ name: 'ops-1', actorName: '@Operator', role: 'Human' }),
      ]);

      expect(component.selectedSender).toBeNull();
      expect(component.humanAgents.length).toBe(2);
    });
  });

  describe('"Send as" layout and panel positioning (Story 7-3)', () => {
    beforeEach(() => {
      nodesSubject.next([
        makeNode({ name: 'human-1', actorName: '@Human', role: 'Human' }),
        makeNode({ name: 'sup-1', actorName: '@Support', role: 'Human' }),
      ]);
      fixture.detectChanges();
    });

    it('renders p-dropdown with appendTo="body" (AC #14)', () => {
      const dropdown = fixture.nativeElement.querySelector('p-dropdown');
      expect(dropdown).not.toBeNull();
      // In Angular dev-mode runtime, string inputs appear as DOM attributes.
      // `appendTo` is bound as a literal string on the template, so it
      // surfaces as an attribute on the <p-dropdown> element.
      expect(dropdown.getAttribute('appendTo')).toBe('body');
    });

    it('renders p-dropdown with the upward panel style class configured (AC #14)', () => {
      const dropdown = fixture.nativeElement.querySelector('p-dropdown');
      expect(dropdown).not.toBeNull();
      // [panelStyleClass] is an input binding — Angular reflects its current
      // value via `ng-reflect-panel-style-class` in dev mode. Accept any
      // deterministic channel that carries the class name.
      const panelClass =
        dropdown.getAttribute('ng-reflect-panel-style-class') ||
        dropdown.getAttribute('panelStyleClass');
      expect(panelClass).toContain('send-as-panel-up');
    });

    it('right-aligns the Send-as group inside .button-group (AC #15)', () => {
      const group = fixture.nativeElement.querySelector('.send-as-group');
      expect(group).not.toBeNull();
      // The implementation uses both `justify-content: flex-end` (intra-group
      // alignment) and `margin-left: auto` (pushes the group to the right
      // inside `.button-group`). Either is acceptable evidence that the
      // Send-as block is right-aligned.
      const style = window.getComputedStyle(group);
      expect(
        style.justifyContent === 'flex-end' || style.marginLeft === 'auto',
      ).toBeTrue();
    });
  });
});
