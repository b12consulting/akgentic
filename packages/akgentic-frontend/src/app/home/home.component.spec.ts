import { CommonModule } from '@angular/common';
import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Router } from '@angular/router';
import { BehaviorSubject, of } from 'rxjs';

import { ApiService } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { ConfigService } from '../services/config.service';
import { ContextService } from '../services/context.service';
import { TeamContext } from '../models/team.interface';
import { HomeComponent } from './home.component';

function makeTeam(overrides: Partial<TeamContext> = {}): TeamContext {
  return {
    team_id: 'team-1',
    name: 'Demo Team',
    status: 'stopped',
    created_at: '2026-04-19T10:00:00Z',
    updated_at: '2026-04-19T10:00:00Z',
    config_name: 'demo',
    description: null,
    ...overrides,
  };
}

describe('HomeComponent', () => {
  let fixture: ComponentFixture<HomeComponent>;
  let component: HomeComponent;
  let teams$: BehaviorSubject<TeamContext[]>;
  let apiSpy: jasmine.SpyObj<ApiService>;
  let contextSpy: jasmine.SpyObj<ContextService> & {
    teams$: BehaviorSubject<TeamContext[]>;
  };
  let authSpy: jasmine.SpyObj<AuthService>;
  let routerSpy: jasmine.SpyObj<Router>;

  beforeEach(async () => {
    teams$ = new BehaviorSubject<TeamContext[]>([]);

    apiSpy = jasmine.createSpyObj('ApiService', [
      'getTeamConfigs',
      'createTeam',
      'deleteTeam',
      'restoreTeam',
      'stopTeam',
      'updateTeamDescription',
    ]);
    apiSpy.getTeamConfigs.and.returnValue(Promise.resolve([]));
    apiSpy.createTeam.and.returnValue(Promise.resolve({} as any));
    apiSpy.deleteTeam.and.returnValue(Promise.resolve());
    apiSpy.restoreTeam.and.returnValue(Promise.resolve({} as any));
    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    apiSpy.updateTeamDescription.and.returnValue(Promise.resolve());

    contextSpy = jasmine.createSpyObj<ContextService>(
      'ContextService',
      ['getTeams', 'deleteTeam', 'createTeamAndNavigate', 'stopTeamAndAwait']
    ) as jasmine.SpyObj<ContextService> & {
      teams$: BehaviorSubject<TeamContext[]>;
    };
    contextSpy.teams$ = teams$;
    contextSpy.getTeams.and.callFake(async () => teams$.value);
    contextSpy.deleteTeam.and.returnValue(Promise.resolve());
    contextSpy.createTeamAndNavigate.and.returnValue(Promise.resolve());
    contextSpy.stopTeamAndAwait.and.returnValue(Promise.resolve());

    authSpy = jasmine.createSpyObj('AuthService', ['checkAuth']);
    authSpy.checkAuth.and.returnValue(of(true as any));

    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    await TestBed.configureTestingModule({
      imports: [HomeComponent, CommonModule, NoopAnimationsModule],
      providers: [
        { provide: ApiService, useValue: apiSpy },
        { provide: ContextService, useValue: contextSpy },
        { provide: AuthService, useValue: authSpy },
        { provide: ConfigService, useValue: { hideHome: false } },
        { provide: Router, useValue: routerSpy },
      ],
      schemas: [CUSTOM_ELEMENTS_SCHEMA],
    }).compileComponents();

    fixture = TestBed.createComponent(HomeComponent);
    component = fixture.componentInstance;
  });

  // --- AC6, AC7 ----------------------------------------------------------

  it('(AC6) component has no `context` field after the refactor', () => {
    expect((component as any).context).toBeUndefined();
  });

  it('(AC6) template renders one row per team emitted on teams$', async () => {
    teams$.next([
      makeTeam({ team_id: 't-1', name: 'Alpha' }),
      makeTeam({ team_id: 't-2', name: 'Beta' }),
    ]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const rows = fixture.nativeElement.querySelectorAll('tr[psellectablerow], tbody tr');
    // The p-table renders its body through ng-template; rows may be produced
    // as <tr> nodes regardless of the selector. Assert at least two rendered.
    const allRows: NodeListOf<HTMLElement> =
      fixture.nativeElement.querySelectorAll('tbody tr');
    expect(allRows.length).toBeGreaterThanOrEqual(2);

    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('t-1');
    expect(text).toContain('t-2');
    // Silence unused local warning from dual querySelectorAll above.
    void rows;
  });

  it('(AC6, AC9) pushing a new list into teams$ triggers a re-render', async () => {
    teams$.next([makeTeam({ team_id: 't-1' })]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect((fixture.nativeElement.textContent as string)).toContain('t-1');

    teams$.next([
      makeTeam({ team_id: 't-1' }),
      makeTeam({ team_id: 't-2' }),
    ]);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    expect((fixture.nativeElement.textContent as string)).toContain('t-2');
  });

  it('(AC7) deleteTeam(id) delegates to contextService.deleteTeam and does NOT call apiService.deleteTeam directly', async () => {
    await component.deleteTeam('t-1');
    expect(contextSpy.deleteTeam).toHaveBeenCalledOnceWith('t-1');
    expect(apiSpy.deleteTeam).not.toHaveBeenCalled();
  });

  it('(AC7) refreshContext() calls contextService.getTeams once without touching a local field', async () => {
    await component.refreshContext();
    expect(contextSpy.getTeams).toHaveBeenCalledTimes(1);
    expect((component as any).context).toBeUndefined();
  });

  // --- Story 10.4 — HomeComponent.createTeamAndNavigate delegation ------

  it('(AC4 10.4) HomeComponent.createTeamAndNavigate delegates to contextService and has no reload compensation', async () => {
    const entry = { id: 'cat-1', name: 'Cat One' };
    component.selectedCatalogEntry$.next(entry);
    // The component has not invoked ngOnInit yet (no detectChanges in this
    // test), so contextSpy.getTeams should not have been called. Reset to
    // guard against any spurious prior invocation.
    contextSpy.getTeams.calls.reset();
    await component.createTeamAndNavigate();
    expect(contextSpy.createTeamAndNavigate).toHaveBeenCalledOnceWith('cat-1');
    // No per-component compensation for the removed reload.
    expect(contextSpy.getTeams).not.toHaveBeenCalled();
  });

  it('(AC4 10.4) HomeComponent.createTeamAndNavigate no-entry guard returns cleanly', async () => {
    component.selectedCatalogEntry$.next(null);
    await component.createTeamAndNavigate();
    expect(contextSpy.createTeamAndNavigate).not.toHaveBeenCalled();
  });

  // --- AC9 ---------------------------------------------------------------

  // --- Story 10.5 — reactive stopTeam delegation -----------------------

  it('(AC5 10.5) HomeComponent.stopTeam delegates to contextService.stopTeamAndAwait without polling', async () => {
    apiSpy.stopTeam.calls.reset();
    contextSpy.getTeams.calls.reset();
    contextSpy.stopTeamAndAwait.and.returnValue(Promise.resolve());

    await component.stopTeam('team-A');

    expect(contextSpy.stopTeamAndAwait).toHaveBeenCalledOnceWith('team-A');
    expect(apiSpy.stopTeam).not.toHaveBeenCalled();
    expect(contextSpy.getTeams).not.toHaveBeenCalled();
  });

  it('(AC5 10.5) stopTeam tracks the teamId in stoppingTeams across the await boundary', async () => {
    let resolveStop: (() => void) | null = null;
    const pending = new Promise<void>((resolve) => {
      resolveStop = resolve;
    });
    contextSpy.stopTeamAndAwait.and.returnValue(pending);

    const stopPromise = component.stopTeam('team-A');

    expect(component.isStopping('team-A')).toBe(true);
    expect(component.stoppingTeams.has('team-A')).toBe(true);

    resolveStop!();
    await stopPromise;

    expect(component.isStopping('team-A')).toBe(false);
    expect(component.stoppingTeams.has('team-A')).toBe(false);
  });

  it('(AC6 10.5) stopTeam catches timeout/error and clears the stoppingTeams entry', async () => {
    const timeoutErr = Object.assign(new Error('timeout'), {
      name: 'TimeoutError',
    });
    contextSpy.stopTeamAndAwait.and.returnValue(Promise.reject(timeoutErr));

    const consoleErrorSpy = spyOn(console, 'error');

    await expectAsync(component.stopTeam('team-A')).toBeResolved();

    expect(component.stoppingTeams.has('team-A')).toBe(false);
    expect(component.isStopping('team-A')).toBe(false);
    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it('(AC7 10.5) stopTeam is safe to call concurrently for different teams', async () => {
    let resolveA: (() => void) | null = null;
    let resolveB: (() => void) | null = null;
    contextSpy.stopTeamAndAwait.and.callFake((teamId: string) => {
      if (teamId === 'team-A')
        return new Promise<void>((r) => {
          resolveA = r;
        });
      if (teamId === 'team-B')
        return new Promise<void>((r) => {
          resolveB = r;
        });
      return Promise.resolve();
    });

    const pA = component.stopTeam('team-A');
    const pB = component.stopTeam('team-B');

    expect(component.stoppingTeams.has('team-A')).toBe(true);
    expect(component.stoppingTeams.has('team-B')).toBe(true);

    resolveA!();
    await pA;

    expect(component.stoppingTeams.has('team-A')).toBe(false);
    expect(component.stoppingTeams.has('team-B')).toBe(true);

    resolveB!();
    await pB;

    expect(component.stoppingTeams.has('team-B')).toBe(false);
  });

  it('(AC9) N=3 mount/unmount cycles leave zero residual subscribers on teams$', async () => {
    for (let i = 0; i < 3; i++) {
      const f = TestBed.createComponent(HomeComponent);
      f.detectChanges();
      await f.whenStable();
      f.detectChanges();
      f.destroy();
    }
    // The first fixture (from beforeEach) also still holds a subscription
    // until destroyed below. Destroy it and then assert no residual observers.
    fixture.destroy();
    expect(teams$.observed).toBeFalse();
  });
});
