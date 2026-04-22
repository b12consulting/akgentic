import { CommonModule } from '@angular/common';
import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Router, RouterModule } from '@angular/router';
import { RouterTestingModule } from '@angular/router/testing';
import { MenubarModule } from 'primeng/menubar';
import { TagModule } from 'primeng/tag';
import { BehaviorSubject, of } from 'rxjs';

import { AppComponent } from './app.component';
import { isRunning, TeamContext } from './models/team.interface';
import { ApiService } from './services/api.service';
import { AuthService } from './services/auth.service';
import { ConfigService } from './services/config.service';
import { ContextService } from './services/context.service';
import { FaviconService } from './services/favicon.service';
import { ViewService } from './view.service';

function makeTeam(overrides: Partial<TeamContext> = {}): TeamContext {
  return {
    team_id: 'team-1',
    name: 'Demo Team',
    status: 'running',
    created_at: '2026-04-19T10:00:00Z',
    updated_at: '2026-04-19T10:00:00Z',
    config_name: 'demo-config',
    description: null,
    ...overrides,
  };
}

interface ContextStub {
  currentProcessId$: BehaviorSubject<string>;
  currentTeam$: BehaviorSubject<TeamContext | null>;
  currentTeamRunning$: BehaviorSubject<boolean>;
  getCurrentTeam: jasmine.Spy;
  clear: jasmine.Spy;
}

describe('AppComponent (Story 10-2 — reactive currentTeam$ subscription)', () => {
  let fixture: ComponentFixture<AppComponent>;
  let component: AppComponent;
  let contextStub: ContextStub;
  let viewStub: { isRightColumnCollapsed$: BehaviorSubject<boolean>; toggleRightColumn: jasmine.Spy };
  let authStub: { currentUser$: BehaviorSubject<any>; checkAuth: jasmine.Spy; logout: jasmine.Spy };
  let configStub: { logo: string; hideLogin: boolean; favicon: string; hideHome: boolean };
  let apiStub: { getTeam: jasmine.Spy; getTeams: jasmine.Spy };

  beforeEach(async () => {
    contextStub = {
      currentProcessId$: new BehaviorSubject<string>(''),
      currentTeam$: new BehaviorSubject<TeamContext | null>(null),
      currentTeamRunning$: new BehaviorSubject<boolean>(false),
      getCurrentTeam: jasmine.createSpy('getCurrentTeam'),
      clear: jasmine.createSpy('clear'),
    };

    viewStub = {
      isRightColumnCollapsed$: new BehaviorSubject<boolean>(false),
      toggleRightColumn: jasmine.createSpy('toggleRightColumn'),
    };

    authStub = {
      currentUser$: new BehaviorSubject<any>({ name: 'Alice', user_id: 'u-1' }),
      checkAuth: jasmine.createSpy('checkAuth').and.returnValue(of(true)),
      logout: jasmine.createSpy('logout'),
    };

    configStub = {
      logo: 'logo.png',
      hideLogin: true,
      favicon: 'favicon.ico',
      hideHome: false,
    };

    const faviconStub = { setFavicon: jasmine.createSpy('setFavicon') };
    apiStub = {
      getTeam: jasmine.createSpy('getTeam'),
      getTeams: jasmine.createSpy('getTeams'),
    };

    await TestBed.configureTestingModule({
      imports: [AppComponent, NoopAnimationsModule, RouterTestingModule],
      providers: [
        { provide: ContextService, useValue: contextStub },
        { provide: ViewService, useValue: viewStub },
        { provide: AuthService, useValue: authStub },
        { provide: ConfigService, useValue: configStub },
        { provide: FaviconService, useValue: faviconStub },
        { provide: ApiService, useValue: apiStub },
      ],
    })
      .overrideComponent(AppComponent, {
        set: {
          imports: [CommonModule, MenubarModule, TagModule, RouterModule],
          schemas: [CUSTOM_ELEMENTS_SCHEMA],
        },
      })
      .compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  // Drive both `currentTeam$` and `currentTeamRunning$` consistently so the
  // template bindings (which read `| async` from both) see the same state.
  // In production `ContextService` derives `currentTeamRunning$` from
  // `currentTeam$`; the stub must emulate that derivation.
  async function emitTeam(team: TeamContext | null): Promise<void> {
    contextStub.currentTeam$.next(team);
    contextStub.currentTeamRunning$.next(team !== null && isRunning(team));
    await fixture.whenStable();
    fixture.detectChanges();
  }

  function headerTagValues(): string[] {
    const nodes = fixture.nativeElement.querySelectorAll('p-tag');
    return Array.from(nodes).map(
      (n: any) => n.getAttribute('value') || '',
    );
  }

  // --- AC5 — AppComponent drops getCurrentTeam fetch -------------------

  it('(AC5) AppComponent never calls contextService.getCurrentTeam on currentProcessId$ emissions', async () => {
    contextStub.currentProcessId$.next('team-1');
    await fixture.whenStable();
    fixture.detectChanges();

    contextStub.currentProcessId$.next('team-2');
    await fixture.whenStable();
    fixture.detectChanges();

    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
  });

  it('(AC9 10.6) header renders name/config_name/Running tag when currentTeam$ emits a running team', async () => {
    const team = makeTeam({ name: 'Alpha', config_name: 'alpha-cfg', status: 'running' });
    await emitTeam(team);

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Alpha');
    expect(text).toContain('alpha-cfg');
    const tags = headerTagValues();
    expect(tags).toContain('Running');
    expect(tags).not.toContain('Stopped');
  });

  it('(AC9 10.6) header renders Stopped tag when currentTeam$ emits a stopped team', async () => {
    const team = makeTeam({ name: 'Beta', config_name: 'beta-cfg', status: 'stopped' });
    await emitTeam(team);

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Beta');
    expect(text).toContain('beta-cfg');
    const tags = headerTagValues();
    expect(tags).toContain('Stopped');
    expect(tags).not.toContain('Running');
  });

  it('(AC9 10.6) header metadata block is hidden when currentTeam$ emits null', async () => {
    // Seed then clear so transitions exercise both branches.
    await emitTeam(makeTeam({ name: 'Gamma' }));
    expect(
      fixture.nativeElement.querySelector('.process-type'),
    ).not.toBeNull();

    await emitTeam(null);

    expect(fixture.nativeElement.querySelector('.process-type')).toBeNull();
  });

  // --- AC11 — REST call count invariant --------------------------------

  it('(AC11) sequence of currentProcessId$ + currentTeam$ emissions triggers zero getCurrentTeam calls', async () => {
    contextStub.currentProcessId$.next('team-1');
    contextStub.currentTeam$.next(makeTeam({ team_id: 'team-1' }));
    await fixture.whenStable();
    fixture.detectChanges();

    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
  });

  // --- AC12 — Right-column toggle emits zero REST calls ---------------

  it('(AC12) toggling isRightColumnCollapsed$ rebuilds the menubar but does not call getCurrentTeam', async () => {
    contextStub.currentProcessId$.next('team-1');
    contextStub.currentTeam$.next(makeTeam({ team_id: 'team-1' }));
    await fixture.whenStable();
    fixture.detectChanges();

    viewStub.isRightColumnCollapsed$.next(true);
    await fixture.whenStable();
    fixture.detectChanges();
    viewStub.isRightColumnCollapsed$.next(false);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
    // The menubar rebuild populates `items`; verify the array was produced.
    expect(component.items).toBeDefined();
  });

  // --- AC10 — Subscription lifecycle ------------------------------------

  it('(AC10) 3 mount/unmount cycles leave zero residual observers on currentTeam$', async () => {
    // The first fixture (from beforeEach) still has an active subscription
    // until we destroy it below. Create and destroy 3 more fixtures first.
    for (let i = 0; i < 3; i++) {
      const f = TestBed.createComponent(AppComponent);
      f.detectChanges();
      await f.whenStable();
      f.detectChanges();
      f.destroy();
    }
    fixture.destroy();
    expect(contextStub.currentTeam$.observed).toBeFalse();
  });

  // --- Story 10-3 — decouple AppComponent from isRightColumnCollapsed$ ---

  it('(AC2) right-column toggle does not call contextService.getCurrentTeam or apiService.getTeam', async () => {
    const team = makeTeam({ team_id: 'team-A', name: 'Alpha' });
    contextStub.currentProcessId$.next('team-A');
    contextStub.currentTeam$.next(team);
    await fixture.whenStable();
    fixture.detectChanges();

    contextStub.getCurrentTeam.calls.reset();
    apiStub.getTeam.calls.reset();

    viewStub.isRightColumnCollapsed$.next(true);
    await fixture.whenStable();
    fixture.detectChanges();
    viewStub.isRightColumnCollapsed$.next(false);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
    expect(apiStub.getTeam).not.toHaveBeenCalled();
  });

  it('(AC2) right-column toggle rebuilds items with a new Hide/Show details label', async () => {
    contextStub.currentProcessId$.next('team-A');
    await fixture.whenStable();
    fixture.detectChanges();
    viewStub.isRightColumnCollapsed$.next(false);
    await fixture.whenStable();
    fixture.detectChanges();
    const expandedLabel = component.items?.find(
      (i) => i.label === 'Hide details' || i.label === 'Show details',
    )?.label;
    expect(expandedLabel).toBe('Hide details');

    viewStub.isRightColumnCollapsed$.next(true);
    await fixture.whenStable();
    fixture.detectChanges();
    const collapsedLabel = component.items?.find(
      (i) => i.label === 'Hide details' || i.label === 'Show details',
    )?.label;
    expect(collapsedLabel).toBe('Show details');
  });

  it('(AC3) currentUser$ change does not call getCurrentTeam or getTeam', async () => {
    contextStub.currentProcessId$.next('team-A');
    await fixture.whenStable();
    fixture.detectChanges();

    contextStub.getCurrentTeam.calls.reset();
    apiStub.getTeam.calls.reset();

    authStub.currentUser$.next({ name: 'Bob', user_id: 'u-2' });
    await fixture.whenStable();
    fixture.detectChanges();
    authStub.currentUser$.next(null);
    await fixture.whenStable();
    fixture.detectChanges();

    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
    expect(apiStub.getTeam).not.toHaveBeenCalled();
  });

  it('(AC3) username dropdown is present when currentUser$ has a name and absent when null', async () => {
    authStub.currentUser$.next({ name: 'Alice', user_id: 'u-1' });
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component.items?.some((i) => i.label === 'Alice')).toBeTrue();

    authStub.currentUser$.next(null);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(component.items?.some((i) => i.label === 'Alice')).toBeFalse();
  });

  it('(AC5) on destroy, zero residual observers on currentTeam$, currentProcessId$, currentUser$, and isRightColumnCollapsed$', async () => {
    fixture.destroy();
    expect(contextStub.currentTeam$.observed).toBeFalse();
    expect(contextStub.currentProcessId$.observed).toBeFalse();
    expect(authStub.currentUser$.observed).toBeFalse();
    expect(viewStub.isRightColumnCollapsed$.observed).toBeFalse();
  });

  // --- Story 10-4 — header renders new team after reload-free flow ------

  it('(AC5 10.4) AppComponent header renders new team after create + navigate sequence with zero REST calls', async () => {
    // Starting state: no process, no team.
    contextStub.currentProcessId$.next('');
    await emitTeam(null);

    expect(fixture.nativeElement.querySelector('.process-type')).toBeNull();

    contextStub.getCurrentTeam.calls.reset();
    apiStub.getTeam.calls.reset();

    // Simulate the exact sequence produced by createTeamAndNavigate followed by
    // ProcessComponent.ngOnInit: _context$ mutation (driving currentTeam$) then
    // currentProcessId$ flipping to the new id.
    const newTeam = makeTeam({
      team_id: 'new',
      name: 'Alpha',
      config_name: 'cfg-alpha',
      status: 'running',
    });
    await emitTeam(newTeam);
    contextStub.currentProcessId$.next('new');
    await fixture.whenStable();
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Alpha');
    expect(text).toContain('cfg-alpha');
    expect(headerTagValues()).toContain('Running');
    expect(contextStub.getCurrentTeam).not.toHaveBeenCalled();
    expect(apiStub.getTeam).not.toHaveBeenCalled();
  });
});
