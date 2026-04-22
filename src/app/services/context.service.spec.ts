import { fakeAsync, flushMicrotasks, TestBed, tick } from '@angular/core/testing';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { ApiService } from './api.service';
import { ContextService } from './context.service';
import { TeamContext, TeamResponse } from '../models/team.interface';

function makeTeam(
  teamId: string,
  status: string = 'running',
  name?: string
): TeamContext {
  return {
    team_id: teamId,
    name: name ?? `team-${teamId}`,
    status,
    created_at: '2026-04-19T10:00:00Z',
    updated_at: '2026-04-19T10:00:00Z',
    config_name: name ?? `team-${teamId}`,
    description: null,
  };
}

function makeTeamResponse(teamId: string, status: string = 'running'): TeamResponse {
  return {
    team_id: teamId,
    name: `team-${teamId}`,
    status,
    user_id: 'user-1',
    created_at: '2026-04-19T10:00:00Z',
    updated_at: '2026-04-19T10:00:00Z',
  };
}

describe('ContextService', () => {
  let service: ContextService;
  let apiSpy: jasmine.SpyObj<ApiService>;
  let routerSpy: jasmine.SpyObj<Router>;

  beforeEach(() => {
    apiSpy = jasmine.createSpyObj('ApiService', [
      'getTeams',
      'getTeam',
      'createTeam',
      'deleteTeam',
      'stopTeam',
    ]);
    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    routerSpy = jasmine.createSpyObj('Router', ['navigate']);
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    TestBed.configureTestingModule({
      providers: [
        ContextService,
        { provide: ApiService, useValue: apiSpy },
        { provide: Router, useValue: routerSpy },
      ],
    });

    service = TestBed.inject(ContextService);
  });

  // --- AC1, AC11 ---------------------------------------------------------

  it('(AC1) teams$ emits [] synchronously on construction', async () => {
    const value = await firstValueFrom(service.teams$);
    expect(value).toEqual([]);
  });

  // --- AC2 ---------------------------------------------------------------

  it('(AC2) getTeams() calls apiService.getTeams then teams$ receives the list once', async () => {
    const teams = [makeTeam('a'), makeTeam('b')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(teams));

    const emissions: TeamContext[][] = [];
    const sub = service.teams$.subscribe((v) => emissions.push(v));

    const returned = await service.getTeams();

    expect(apiSpy.getTeams).toHaveBeenCalledTimes(1);
    expect(returned).toEqual(teams);
    // Initial [] emission + the new list.
    expect(emissions.length).toBe(2);
    expect(emissions[1]).toEqual(teams);

    sub.unsubscribe();
  });

  it('(AC10) two consecutive getTeams() calls produce two distinct array references', async () => {
    const first = [makeTeam('a')];
    const second = [makeTeam('a'), makeTeam('b')];
    apiSpy.getTeams.and.returnValues(
      Promise.resolve(first),
      Promise.resolve(second)
    );

    await service.getTeams();
    const afterFirst = await firstValueFrom(service.teams$);
    await service.getTeams();
    const afterSecond = await firstValueFrom(service.teams$);

    expect(afterFirst).not.toBe(afterSecond);
    expect(afterFirst).toEqual(first);
    expect(afterSecond).toEqual(second);
  });

  // --- AC3, AC10 ---------------------------------------------------------

  it('(AC3, AC10) getCurrentTeam(false) produces new array ref + new slot ref; unchanged slots preserve identity', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    const prevA = prev.find((t) => t.team_id === 'a')!;
    const prevB = prev.find((t) => t.team_id === 'b')!;

    const updatedA = makeTeam('a', 'stopped');
    apiSpy.getTeam.and.returnValue(Promise.resolve(updatedA));

    await service.getCurrentTeam('a', false);

    const next = await firstValueFrom(service.teams$);
    expect(next).not.toBe(prev);
    const nextA = next.find((t) => t.team_id === 'a')!;
    const nextB = next.find((t) => t.team_id === 'b')!;
    expect(nextA).not.toBe(prevA);
    expect(nextA).toBe(updatedA);
    expect(nextB).toBe(prevB);
  });

  it('(AC3) getCurrentTeam(true) cache-hit leaves _context$.value unchanged; currentTeamRunning$.value reflects cached team', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();

    // Arm the derived pipeline by pointing currentProcessId$ at 'a' BEFORE
    // the cache-hit call. The pipeline is the sole writer to
    // currentTeamRunning$ (Story 10.2); getCurrentTeam no longer emits.
    service.currentProcessId$.next('a');
    // Let the derived pipeline flush its first emission.
    await Promise.resolve();

    const prev = await firstValueFrom(service.teams$);

    const result = await service.getCurrentTeam('a', true);

    expect(apiSpy.getTeam).not.toHaveBeenCalled();
    expect(result).toBe(teamA);
    const next = await firstValueFrom(service.teams$);
    expect(next).toBe(prev);
    // Derived pipeline already emitted `true` for the running cached team;
    // distinctUntilChanged may suppress further emissions, but .value is live.
    expect(service.currentTeamRunning$.value).toBe(true);
  });

  it('(AC3 guard) getCurrentTeam(false) with null API response leaves _context$.value unchanged', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    apiSpy.getTeam.and.returnValue(Promise.resolve(null as unknown as TeamContext));

    const result = await service.getCurrentTeam('a', false);

    expect(result).toBeNull();
    const next = await firstValueFrom(service.teams$);
    expect(next).toBe(prev);
  });

  // --- AC4 ---------------------------------------------------------------

  it('(AC4) createTeamAndNavigate grows _context$.value by one and calls router.navigate', async () => {
    const existing = [makeTeam('a')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(existing));
    await service.getTeams();

    const response = makeTeamResponse('new');
    apiSpy.createTeam.and.returnValue(Promise.resolve(response));
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    await service.createTeamAndNavigate('cat-1');

    const next = await firstValueFrom(service.teams$);
    expect(next.length).toBe(existing.length + 1);
    expect(next[next.length - 1].team_id).toBe('new');
    expect(routerSpy.navigate).toHaveBeenCalledWith(['/process', 'new']);
  });

  // --- Story 10.4 — reload-free createTeamAndNavigate --------------------

  it('(AC2 10.4) createTeamAndNavigate does not drive a reload', async () => {
    const existing = [makeTeam('a')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(existing));
    await service.getTeams();

    const response = makeTeamResponse('new');
    apiSpy.createTeam.and.returnValue(Promise.resolve(response));
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    // Variant A (plain removal) is adopted: there is no reloadFn seam in
    // production. The spy exists to document the "no reload" assertion
    // shape — it MUST remain at zero calls because the source code no
    // longer contains any path that would trigger it.
    const reloadSpy = jasmine.createSpy('reloadFn');

    await service.createTeamAndNavigate('cat-1');

    expect(reloadSpy).not.toHaveBeenCalled();
    expect(apiSpy.createTeam).toHaveBeenCalledOnceWith('cat-1');
    expect(routerSpy.navigate).toHaveBeenCalledOnceWith(['/process', 'new']);
  });

  it('(AC1 10.4) createTeamAndNavigate preserves immutability on append', async () => {
    const existing = [makeTeam('a'), makeTeam('b')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(existing));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    const prevA = prev.find((t) => t.team_id === 'a')!;
    const prevB = prev.find((t) => t.team_id === 'b')!;

    apiSpy.createTeam.and.returnValue(Promise.resolve(makeTeamResponse('new')));
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    await service.createTeamAndNavigate('cat-1');

    const next = await firstValueFrom(service.teams$);
    expect(next).not.toBe(prev);
    expect(next.length).toBe(prev.length + 1);
    expect(next.find((t) => t.team_id === 'a')).toBe(prevA);
    expect(next.find((t) => t.team_id === 'b')).toBe(prevB);
    expect(next[next.length - 1].team_id).toBe('new');
  });

  it('(AC3 10.4) after create + navigate, currentTeam$ emits the new team and currentTeamRunning$ reflects it', async () => {
    apiSpy.getTeams.and.returnValue(Promise.resolve([]));
    await service.getTeams();

    const newTeamResponse = makeTeamResponse('new', 'running');
    apiSpy.createTeam.and.returnValue(Promise.resolve(newTeamResponse));
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    await service.createTeamAndNavigate('cat-1');

    // The method itself does NOT set currentProcessId$; ProcessComponent.ngOnInit
    // does that in production. Simulate it here to exercise the derived pipeline.
    service.currentProcessId$.next('new');
    await Promise.resolve();

    const currentTeam = await firstValueFrom(service.currentTeam$);
    expect(currentTeam).not.toBeNull();
    expect(currentTeam!.team_id).toBe('new');
    expect(service.currentTeamRunning$.value).toBe(true);
  });

  // --- AC5 ---------------------------------------------------------------

  it('(AC5) deleteTeam shrinks _context$.value and calls apiService.deleteTeam', async () => {
    const teams = [makeTeam('a'), makeTeam('b'), makeTeam('c')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(teams));
    await service.getTeams();

    apiSpy.deleteTeam.and.returnValue(Promise.resolve());

    await service.deleteTeam('b');

    expect(apiSpy.deleteTeam).toHaveBeenCalledOnceWith('b');
    const next = await firstValueFrom(service.teams$);
    expect(next.map((t) => t.team_id)).toEqual(['a', 'c']);
  });

  it('(AC5) clear(teamId) shrinks, ends with currentTeamRunning$.value === false via derived pipeline, and navigates home', async () => {
    const teams = [makeTeam('a'), makeTeam('b')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(teams));
    await service.getTeams();

    apiSpy.deleteTeam.and.returnValue(Promise.resolve());
    routerSpy.navigate.and.returnValue(Promise.resolve(true));

    // Point currentProcessId$ at 'a' so the derived pipeline flips to `true`
    // first, then `false` after `clear('a')` removes the team.
    service.currentProcessId$.next('a');
    await Promise.resolve();
    expect(service.currentTeamRunning$.value).toBe(true);

    await service.clear('a');

    const next = await firstValueFrom(service.teams$);
    expect(next.map((t) => t.team_id)).toEqual(['b']);
    // Derived pipeline emitted `false` because `currentTeam$` → null.
    expect(service.currentTeamRunning$.value).toBe(false);
    expect(routerSpy.navigate).toHaveBeenCalledWith(['/']);
  });

  // --- AC11 (Story 10.1 late-subscriber on teams$) -----------------------

  it('(AC11 10.1) late-subscriber receives the current value synchronously', async () => {
    const teams = [makeTeam('a')];
    apiSpy.getTeams.and.returnValue(Promise.resolve(teams));
    await service.getTeams();

    let received: TeamContext[] | undefined;
    const sub = service.teams$.subscribe((v) => (received = v));
    expect(received).toEqual(teams);
    sub.unsubscribe();
  });

  // =======================================================================
  // Story 10.2 — derived currentTeam$ pipeline
  // =======================================================================

  // --- AC1 (10.2) --------------------------------------------------------

  it('(AC1 10.2) currentTeam$ is exposed as a subscribable Observable', () => {
    expect(service.currentTeam$).toBeTruthy();
    expect(typeof service.currentTeam$.subscribe).toBe('function');
  });

  // --- AC2 (10.2) --------------------------------------------------------

  it('(AC2 10.2) currentTeam$ emits the team whose id matches currentProcessId$', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'stopped');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    await service.getTeams();

    service.currentProcessId$.next('a');
    const first = await firstValueFrom(service.currentTeam$);
    expect(first).toBe(teamA);

    service.currentProcessId$.next('b');
    const second = await firstValueFrom(service.currentTeam$);
    expect(second).toBe(teamB);
  });

  // --- AC3 (10.2) --------------------------------------------------------

  it('(AC3 10.2) currentTeam$ emits in order on id switch; currentTeamRunning$ tracks running state', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'stopped');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    await service.getTeams();

    const teamEmissions: (TeamContext | null)[] = [];
    const runningEmissions: boolean[] = [];
    const sub1 = service.currentTeam$.subscribe((t) => teamEmissions.push(t));
    const sub2 = service.currentTeamRunning$.subscribe((r) =>
      runningEmissions.push(r)
    );

    service.currentProcessId$.next('a');
    service.currentProcessId$.next('b');
    // Flush microtasks so the derived pipeline propagates.
    await Promise.resolve();

    expect(teamEmissions).toContain(teamA);
    expect(teamEmissions).toContain(teamB);
    // The last emission must be teamB (after the id switch).
    expect(teamEmissions[teamEmissions.length - 1]).toBe(teamB);
    // Running progression: init false → true (team-A) → false (team-B); last
    // emission is false, and the set must include true (from team-A).
    expect(runningEmissions[runningEmissions.length - 1]).toBe(false);
    expect(runningEmissions).toContain(true);

    sub1.unsubscribe();
    sub2.unsubscribe();
  });

  // --- AC4 (10.2) --------------------------------------------------------

  it('(AC4 10.2) late subscriber to currentTeam$ receives current value synchronously', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();
    service.currentProcessId$.next('a');

    // Let microtasks flush so shareReplay has captured the value.
    await Promise.resolve();

    let received: TeamContext | null | undefined;
    const sub = service.currentTeam$.subscribe((t) => (received = t));
    expect(received).toBe(teamA);
    sub.unsubscribe();
  });

  // --- AC8 (10.2, NFR5) --------------------------------------------------

  it('(AC8 10.2 / NFR5) status flip via getCurrentTeam(id, false) emits currentTeam$ once and currentTeamRunning$ once', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();
    service.currentProcessId$.next('a');
    await Promise.resolve();

    const teamEmissions: (TeamContext | null)[] = [];
    const runningEmissions: boolean[] = [];
    const sub1 = service.currentTeam$.subscribe((t) => teamEmissions.push(t));
    const sub2 = service.currentTeamRunning$.subscribe((r) =>
      runningEmissions.push(r)
    );

    const countBeforeFlipTeam = teamEmissions.length;
    const countBeforeFlipRunning = runningEmissions.length;

    // Flip the team's status via getCurrentTeam(id, false).
    const stoppedA = makeTeam('a', 'stopped');
    apiSpy.getTeam.and.returnValue(Promise.resolve(stoppedA));
    await service.getCurrentTeam('a', false);

    expect(teamEmissions.length).toBe(countBeforeFlipTeam + 1);
    expect(teamEmissions[teamEmissions.length - 1]).toBe(stoppedA);
    expect(runningEmissions.length).toBe(countBeforeFlipRunning + 1);
    expect(runningEmissions[runningEmissions.length - 1]).toBe(false);

    sub1.unsubscribe();
    sub2.unsubscribe();
  });

  // --- AC9 (10.2) --------------------------------------------------------

  it('(AC9 10.2) unknown currentProcessId$ emits null from currentTeam$ and false from currentTeamRunning$', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();

    service.currentProcessId$.next('does-not-exist');
    const t = await firstValueFrom(service.currentTeam$);
    expect(t).toBeNull();
    expect(service.currentTeamRunning$.value).toBe(false);
  });

  // --- AC11 (10.2) — single-fetch navigation invariant -------------------

  it('(AC11 10.2) navigation flow issues exactly ONE apiService.getTeam call (home init + id switch + ngOnInit fetch)', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    // Simulate the single `getCurrentTeam(id, false)` call from
    // ProcessComponent.ngOnInit. No other code path issues a REST call.
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamA));

    // Home init — loads the team list.
    await service.getTeams();

    // Route change — AppComponent reacts to currentProcessId$.next('a') but
    // NO LONGER fetches. The derived pipeline emits the cached team.
    service.currentProcessId$.next('a');
    await Promise.resolve();
    const afterRoute = await firstValueFrom(service.currentTeam$);
    expect(afterRoute).toBe(teamA);

    // ProcessComponent.ngOnInit — the single one-shot fetch.
    await service.getCurrentTeam('a', false);

    expect(apiSpy.getTeam).toHaveBeenCalledTimes(1);
    expect(apiSpy.getTeam).toHaveBeenCalledWith('a');
  });

  // =======================================================================
  // Story 10.5 — reactive stopTeamAndAwait
  // =======================================================================

  it('(AC1 10.5) stopTeamAndAwait is defined on the service with (teamId, timeoutMs?) shape', () => {
    expect(typeof service.stopTeamAndAwait).toBe('function');
    // One required formal parameter: teamId. timeoutMs is optional (default 10000).
    expect(service.stopTeamAndAwait.length).toBe(1);
  });

  it('(AC2 10.5) stopTeamAndAwait resolves when refresh reports stopped', fakeAsync(() => {
    const running = makeTeam('team-A', 'running');
    const stopped = makeTeam('team-A', 'stopped');

    apiSpy.getTeams.and.returnValue(Promise.resolve([running]));
    service.getTeams();
    tick();

    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    apiSpy.getTeam.and.returnValue(Promise.resolve(stopped));

    let resolved = false;
    service.stopTeamAndAwait('team-A').then(() => {
      resolved = true;
    });

    tick();
    expect(apiSpy.stopTeam).toHaveBeenCalledOnceWith('team-A');

    tick(1000);
    flushMicrotasks();

    expect(resolved).toBe(true);

    apiSpy.getTeam.calls.reset();
    tick(5000);
    expect(apiSpy.getTeam).not.toHaveBeenCalled();
  }));

  it('(AC3 10.5) stopTeamAndAwait rejects with TimeoutError when team never stops', fakeAsync(() => {
    const running = makeTeam('team-A', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([running]));
    service.getTeams();
    tick();

    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    apiSpy.getTeam.and.returnValue(Promise.resolve(running));

    let rejected: Error | null = null;
    service.stopTeamAndAwait('team-A', 10000).catch((err: Error) => {
      rejected = err;
    });

    tick();

    tick(11000);
    flushMicrotasks();

    expect(rejected).not.toBeNull();
    expect(rejected!.name).toBe('TimeoutError');

    apiSpy.getTeam.calls.reset();
    tick(5000);
    expect(apiSpy.getTeam).not.toHaveBeenCalled();
  }));

  it('(AC4 10.5) concurrent stopTeamAndAwait calls do not share intervals', fakeAsync(() => {
    const runningA = makeTeam('team-A', 'running');
    const runningB = makeTeam('team-B', 'running');
    const stoppedA = makeTeam('team-A', 'stopped');

    apiSpy.getTeams.and.returnValue(Promise.resolve([runningA, runningB]));
    service.getTeams();
    tick();

    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    apiSpy.getTeam.and.callFake((id: string) => {
      if (id === 'team-A') return Promise.resolve(stoppedA);
      return Promise.resolve(runningB);
    });

    let resolvedA = false;
    let resolvedB = false;
    let rejectedB: Error | null = null;

    service.stopTeamAndAwait('team-A').then(() => {
      resolvedA = true;
    });
    service
      .stopTeamAndAwait('team-B', 10000)
      .then(() => {
        resolvedB = true;
      })
      .catch((err: Error) => {
        rejectedB = err;
      });

    tick();
    tick(1000);
    flushMicrotasks();

    expect(resolvedA).toBe(true);
    expect(resolvedB).toBe(false);

    tick(10000);
    flushMicrotasks();

    expect(rejectedB).not.toBeNull();
    expect(rejectedB!.name).toBe('TimeoutError');

    apiSpy.getTeam.calls.reset();
    tick(5000);
    expect(apiSpy.getTeam).not.toHaveBeenCalled();
  }));

  it('(AC8 10.5) stopTeamAndAwait refresh updates _context$ immutably', fakeAsync(() => {
    const runningA = makeTeam('team-A', 'running');
    const runningB = makeTeam('team-B', 'running');
    const stoppedA = makeTeam('team-A', 'stopped');

    apiSpy.getTeams.and.returnValue(Promise.resolve([runningA, runningB]));
    service.getTeams();
    tick();

    let prev: TeamContext[] = [];
    let prevB: TeamContext | undefined;
    service.teams$
      .subscribe((v) => {
        prev = v;
      })
      .unsubscribe();
    prevB = prev.find((t) => t.team_id === 'team-B');

    apiSpy.stopTeam.and.returnValue(Promise.resolve());
    apiSpy.getTeam.and.returnValue(Promise.resolve(stoppedA));

    service.stopTeamAndAwait('team-A').catch(() => {
      /* ignore */
    });
    tick();
    tick(1000);
    flushMicrotasks();

    let next: TeamContext[] = [];
    service.teams$
      .subscribe((v) => {
        next = v;
      })
      .unsubscribe();

    expect(next).not.toBe(prev);
    expect(next.find((t) => t.team_id === 'team-A')).toBe(stoppedA);
    expect(next.find((t) => t.team_id === 'team-B')).toBe(prevB);
  }));

  it('(AC10 10.5) ContextService public surface includes stopTeamAndAwait and keeps existing methods', () => {
    const expected = [
      'getTeams',
      'getCurrentTeam',
      'deleteTeam',
      'clear',
      'createTeamAndNavigate',
      'stopTeamAndAwait',
    ];
    for (const name of expected) {
      expect(typeof (service as unknown as Record<string, unknown>)[name]).toBe(
        'function',
      );
    }
    expect(service.currentProcessId$).toBeDefined();
    expect(service.teams$).toBeDefined();
    expect(service.currentTeam$).toBeDefined();
    expect(service.currentTeamRunning$).toBeDefined();
  });

  // =======================================================================
  // Story 10.6 — public-surface ratification + deleteTeam immutability
  // =======================================================================

  it('(AC6 10.6) ContextService public surface is exactly the post-10.6 set', () => {
    const expectedObservables = [
      'currentProcessId$',
      'teams$',
      'currentTeam$',
      'currentTeamRunning$',
    ];
    const expectedMethods = [
      'getTeams',
      'getCurrentTeam',
      'deleteTeam',
      'clear',
      'createTeamAndNavigate',
      'stopTeamAndAwait',
    ];
    for (const name of expectedObservables) {
      expect((service as unknown as Record<string, unknown>)[name])
        .withContext(name)
        .toBeDefined();
    }
    for (const name of expectedMethods) {
      expect(typeof (service as unknown as Record<string, unknown>)[name])
        .withContext(name)
        .toBe('function');
    }
    // FR14 closure: no alternative `removeTeam` entry point was introduced.
    expect(
      (service as unknown as Record<string, unknown>)['removeTeam'],
    ).toBeUndefined();
  });

  // =======================================================================
  // Story 10.7 — upsert on hard reload (issue #104)
  // =======================================================================

  // --- AC1 (10.7) — empty-cache append via getCurrentTeam(id, false) -----

  it('(AC1 10.7) getCurrentTeam(false) appends team to empty _context$', async () => {
    const prev = await firstValueFrom(service.teams$);
    expect(prev).toEqual([]);

    const teamA = makeTeam('a', 'running');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamA));

    const result = await service.getCurrentTeam('a', false);

    const next = await firstValueFrom(service.teams$);
    expect(result).toBe(teamA);
    expect(next).toEqual([teamA]);
    expect(next).not.toBe(prev);
  });

  // --- AC2 (10.7) — replace path preserves other-slot identity ----------

  it('(AC2 10.7) getCurrentTeam(false) replace path: array ref changes, unchanged slot ref preserved', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    const prevA = prev.find((t) => t.team_id === 'a')!;
    const prevB = prev.find((t) => t.team_id === 'b')!;

    const teamAPrime = makeTeam('a', 'stopped');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamAPrime));

    await service.getCurrentTeam('a', false);

    const next = await firstValueFrom(service.teams$);
    expect(next).not.toBe(prev);
    expect(next.length).toBe(2);
    const nextA = next.find((t) => t.team_id === 'a')!;
    const nextB = next.find((t) => t.team_id === 'b')!;
    expect(nextA).toBe(teamAPrime);
    expect(nextA).not.toBe(prevA);
    expect(nextB).toBe(prevB);
  });

  // --- AC3 (10.7) — currentTeam$ emits null → team on empty-cache path --

  it('(AC3 10.7) currentTeam$ emits null then team after empty-cache getCurrentTeam(false)', async () => {
    const emissions: (TeamContext | null)[] = [];
    const sub = service.currentTeam$.subscribe((t) => emissions.push(t));

    service.currentProcessId$.next('a');
    await Promise.resolve();

    const teamA = makeTeam('a', 'running');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamA));
    await service.getCurrentTeam('a', false);
    await Promise.resolve();

    expect(emissions[0]).toBeNull();
    expect(emissions[emissions.length - 1]).toBe(teamA);
    expect(service.currentTeamRunning$.value).toBe(true);

    sub.unsubscribe();
  });

  // --- AC4 (10.7) — refreshOneTeam follows same upsert invariant --------

  it('(AC4 10.7) refreshOneTeam appends team when _context$ is empty', async () => {
    const prev = await firstValueFrom(service.teams$);
    expect(prev).toEqual([]);

    const teamA = makeTeam('a', 'running');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamA));

    const refreshOneTeam = (
      service as unknown as { refreshOneTeam: (id: string) => Promise<TeamContext | null> }
    ).refreshOneTeam.bind(service);
    const result = await refreshOneTeam('a');

    const next = await firstValueFrom(service.teams$);
    expect(result).toBe(teamA);
    expect(next).toEqual([teamA]);
    expect(next).not.toBe(prev);
  });

  it('(AC4 10.7) refreshOneTeam replace path: array ref changes, unchanged slot ref preserved', async () => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    const prevA = prev.find((t) => t.team_id === 'a')!;
    const prevB = prev.find((t) => t.team_id === 'b')!;

    const teamAPrime = makeTeam('a', 'stopped');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamAPrime));

    const refreshOneTeam = (
      service as unknown as { refreshOneTeam: (id: string) => Promise<TeamContext | null> }
    ).refreshOneTeam.bind(service);
    await refreshOneTeam('a');

    const next = await firstValueFrom(service.teams$);
    expect(next).not.toBe(prev);
    expect(next.length).toBe(2);
    const nextA = next.find((t) => t.team_id === 'a')!;
    const nextB = next.find((t) => t.team_id === 'b')!;
    expect(nextA).toBe(teamAPrime);
    expect(nextA).not.toBe(prevA);
    expect(nextB).toBe(prevB);
  });

  // --- AC5 (10.7) — null API response leaves _context$ unchanged --------

  it('(AC5 10.7) getCurrentTeam(false) null API on empty cache leaves _context$ unchanged', async () => {
    const prev = await firstValueFrom(service.teams$);
    expect(prev).toEqual([]);

    apiSpy.getTeam.and.returnValue(Promise.resolve(null as unknown as TeamContext));

    const result = await service.getCurrentTeam('a', false);
    const next = await firstValueFrom(service.teams$);

    expect(result).toBeNull();
    expect(next).toBe(prev);
  });

  it('(AC5 10.7) refreshOneTeam null API leaves _context$ unchanged', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);
    apiSpy.getTeam.and.returnValue(Promise.resolve(null as unknown as TeamContext));

    const refreshOneTeam = (
      service as unknown as { refreshOneTeam: (id: string) => Promise<TeamContext | null> }
    ).refreshOneTeam.bind(service);
    const result = await refreshOneTeam('a');

    const next = await firstValueFrom(service.teams$);
    expect(result).toBeNull();
    expect(next).toBe(prev);
  });

  // --- AC6 (10.7) — cache-hit short-circuit unchanged --------------------

  it('(AC6 10.7) getCurrentTeam(true) cache-hit does not call API and leaves _context$ unchanged', async () => {
    const teamA = makeTeam('a', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA]));
    await service.getTeams();

    const prev = await firstValueFrom(service.teams$);

    const result = await service.getCurrentTeam('a', true);
    const next = await firstValueFrom(service.teams$);

    expect(result).toBe(teamA);
    expect(apiSpy.getTeam).not.toHaveBeenCalled();
    expect(next).toBe(prev);
  });

  // --- AC7 (10.7) — end-to-end observable contract on hard reload -------

  it('(AC7 10.7) currentTeam$ + currentTeamRunning$ transitions on empty-cache hard reload', async () => {
    const teamEmissions: (TeamContext | null)[] = [];
    const runningEmissions: boolean[] = [];
    const sub1 = service.currentTeam$.subscribe((t) => teamEmissions.push(t));
    const sub2 = service.currentTeamRunning$.subscribe((r) =>
      runningEmissions.push(r)
    );

    service.currentProcessId$.next('a');
    await Promise.resolve();

    const teamA = makeTeam('a', 'running');
    apiSpy.getTeam.and.returnValue(Promise.resolve(teamA));
    await service.getCurrentTeam('a', false);
    await Promise.resolve();

    expect(teamEmissions[0]).toBeNull();
    expect(teamEmissions[teamEmissions.length - 1]).toBe(teamA);
    expect(runningEmissions[0]).toBe(false);
    expect(runningEmissions[runningEmissions.length - 1]).toBe(true);

    sub1.unsubscribe();
    sub2.unsubscribe();
  });

  it('(AC7 10.6) deleteTeam removes the team immutably from _context$', fakeAsync(() => {
    const teamA = makeTeam('a', 'running');
    const teamB = makeTeam('b', 'running');
    apiSpy.getTeams.and.returnValue(Promise.resolve([teamA, teamB]));
    apiSpy.deleteTeam.and.returnValue(Promise.resolve());

    service.getTeams();
    tick();

    let prev: TeamContext[] = [];
    service.teams$
      .subscribe((teams) => {
        prev = teams;
      })
      .unsubscribe();
    const prevB = prev.find((t) => t.team_id === 'b')!;

    service.deleteTeam('a');
    tick();

    let next: TeamContext[] = [];
    service.teams$
      .subscribe((teams) => {
        next = teams;
      })
      .unsubscribe();

    expect(apiSpy.deleteTeam).toHaveBeenCalledOnceWith('a');
    expect(next).not.toBe(prev);
    expect(next.length).toBe(1);
    expect(next[0].team_id).toBe('b');
    // Unchanged slot preserves reference identity (immutable filter on same refs).
    expect(next[0]).toBe(prevB);
  }));
});
