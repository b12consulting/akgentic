import { inject, Injectable } from '@angular/core';
import { Router } from '@angular/router';
import {
  BehaviorSubject,
  combineLatest,
  firstValueFrom,
  interval,
  Observable,
  Subject,
} from 'rxjs';
import {
  distinctUntilChanged,
  filter,
  map,
  shareReplay,
  switchMap,
  take,
  takeUntil,
  timeout,
} from 'rxjs/operators';
import { ApiService } from './api.service';
import { isRunning, TeamContext, toTeamContext } from '../models/team.interface';

@Injectable({
  providedIn: 'root',
})
export class ContextService {
  apiService: ApiService = inject(ApiService);
  router: Router = inject(Router);
  currentProcessId$ = new BehaviorSubject<string>('');
  /** Reactive running state of the current team. Derived selector fed by
   *  `currentTeam$` — navigation code paths no longer push here. Remains a
   *  BehaviorSubject so `.value` reads in consumers keep working. */
  currentTeamRunning$ = new BehaviorSubject<boolean>(false);

  // Single write path for the team list. A future homepage WebSocket
  // will push updates via _context$.next(applyPatch(_context$.value, patch)).
  private _context$ = new BehaviorSubject<TeamContext[]>([]);
  public teams$: Observable<TeamContext[]> = this._context$.asObservable();

  /** Derived selector: the team whose id matches `currentProcessId$`, or
   *  `null` if none matches (including the empty-string initial id). The
   *  `shareReplay(1, refCount:false)` gives late-subscriber safety without
   *  tearing the inner subscription down when consumer count drops to zero. */
  public currentTeam$: Observable<TeamContext | null> = combineLatest([
    this.currentProcessId$,
    this._context$,
  ]).pipe(
    map(([id, teams]) => (id ? (teams.find((t) => t.team_id === id) ?? null) : null)),
    distinctUntilChanged(),
    shareReplay({ bufferSize: 1, refCount: false }),
  );

  constructor() {
    // Derive currentTeamRunning$ from currentTeam$. The subscription lives
    // for the service lifetime (root-scoped singleton) — no teardown needed.
    // Internal subscription is the sole writer to currentTeamRunning$;
    // navigation code paths no longer call .next() directly.
    this.currentTeam$
      .pipe(
        map((t) => t !== null && isRunning(t)),
        distinctUntilChanged(),
      )
      .subscribe((running) => this.currentTeamRunning$.next(running));
  }

  async getTeams(): Promise<TeamContext[]> {
    const teams = await this.apiService.getTeams();
    this._context$.next(teams);
    return teams;
  }

  async getCurrentTeam(
    teamId: string,
    useCache: boolean = true
  ): Promise<TeamContext | null> {
    if (useCache) {
      const cached = this._context$.value.find(
        (t: TeamContext) => t.team_id === teamId
      );
      if (cached) {
        return cached;
      }
    }

    const team = await this.apiService.getTeam(teamId);

    if (team) {
      this._upsertTeam(team);
    }

    return team;
  }

  async deleteTeam(teamId: string): Promise<void> {
    await this.apiService.deleteTeam(teamId);
    const prev = this._context$.value;
    this._context$.next(prev.filter((t: TeamContext) => t.team_id !== teamId));
  }

  async clear(teamId: string) {
    await this.deleteTeam(teamId);
    await this.router.navigate(['/']);
  }

  async createTeamAndNavigate(catalogEntryId: string) {
    const response = await this.apiService.createTeam(catalogEntryId);
    const newTeam = toTeamContext(response);
    const prev = this._context$.value;
    this._context$.next([...prev, newTeam]);
    await this.router.navigate(['/process', response.team_id]);
  }

  private async refreshOneTeam(teamId: string): Promise<TeamContext | null> {
    const fresh = await this.apiService.getTeam(teamId);
    if (fresh) {
      this._upsertTeam(fresh);
    }
    return fresh;
  }

  /** Upsert a team into `_context$`: replace if already cached (preserves
   *  reference identity of other slots); append if not yet cached. Single
   *  write path shared by `getCurrentTeam` and `refreshOneTeam` so the two
   *  cannot diverge in a future change. Issue #104 regression fix. */
  private _upsertTeam(team: TeamContext): void {
    const prev = this._context$.value;
    const exists = prev.some((t) => t.team_id === team.team_id);
    const next = exists
      ? prev.map((t) => (t.team_id === team.team_id ? team : t))
      : [...prev, team];
    this._context$.next(next);
  }

  async stopTeamAndAwait(
    teamId: string,
    timeoutMs: number = 10000,
  ): Promise<void> {
    await this.apiService.stopTeam(teamId);

    // Bounded periodic refresh feeding _context$ with fresh data for this
    // team only. When homepage WebSocket lands this interval is replaced by
    // WS-driven _context$.next(...) updates; the firstValueFrom awaiter below
    // stays unchanged.
    const stop$ = new Subject<void>();
    interval(1000)
      .pipe(
        takeUntil(stop$),
        switchMap(() => this.refreshOneTeam(teamId)),
      )
      .subscribe();

    try {
      await firstValueFrom(
        this.teams$.pipe(
          map((teams) => teams.find((t) => t.team_id === teamId)),
          filter((t): t is TeamContext => t !== undefined && !isRunning(t)),
          take(1),
          timeout(timeoutMs),
        ),
      );
    } finally {
      stop$.next();
      stop$.complete();
    }
  }
}
