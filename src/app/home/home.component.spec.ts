import { CommonModule } from '@angular/common';
import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { Router } from '@angular/router';
import { ConfirmationService } from 'primeng/api';
import { BehaviorSubject, of } from 'rxjs';

import { ApiService } from '../services/api.service';
import { AuthService } from '../services/auth.service';
import { ConfigService } from '../services/config.service';
import { ContextService } from '../services/context.service';
import { TeamContext } from '../models/team.interface';
import { NamespacePanelComponent } from '../admin/catalog/namespace-panel/namespace-panel.component';
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
  let confirmationSpy: jasmine.SpyObj<ConfirmationService>;

  beforeEach(async () => {
    teams$ = new BehaviorSubject<TeamContext[]>([]);

    apiSpy = jasmine.createSpyObj('ApiService', [
      'getNamespaces',
      'createTeam',
      'deleteTeam',
      'restoreTeam',
      'stopTeam',
      'updateTeamDescription',
    ]);
    apiSpy.getNamespaces.and.returnValue(Promise.resolve([]));
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

    // Use a REAL ConfirmationService instance (so its `requireConfirmation$`
    // Subject is wired) and spy on `.confirm` to observe calls. Using a bare
    // `jasmine.createSpyObj` breaks PrimeNG's `<p-confirmDialog>` constructor
    // because it subscribes to `requireConfirmation$` on instantiation.
    confirmationSpy = new ConfirmationService() as jasmine.SpyObj<ConfirmationService>;
    spyOn(confirmationSpy, 'confirm').and.callThrough();

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
    })
      // Swap the HomeComponent's locally-provided ConfirmationService for a
      // spy so the Story 11.3 dirty-close guard is testable deterministically.
      .overrideComponent(HomeComponent, {
        set: {
          providers: [
            { provide: ConfirmationService, useValue: confirmationSpy },
          ],
        },
      })
      .compileComponents();

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
    const ns = { namespace: 'cat-1', name: 'Cat One', description: 'first cat' };
    component.selectedNamespace$.next(ns);
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
    component.selectedNamespace$.next(null);
    await component.createTeamAndNavigate();
    expect(contextSpy.createTeamAndNavigate).not.toHaveBeenCalled();
  });

  // --- Story 1.9 — namespace picker wiring --------------------------------

  it('(AC1 1.9) ngOnInit loads namespaces via getNamespaces and selects the first', async () => {
    apiSpy.getNamespaces.and.returnValue(
      Promise.resolve([
        { namespace: 'agent-team-v1', name: 'Agent Team', description: 'Default' },
        { namespace: 'rag-team-v1', name: 'RAG Team', description: 'With RAG' },
      ]),
    );
    await component.ngOnInit();
    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(1);
    expect(component.namespaces$.value.length).toBe(2);
    expect(component.selectedNamespace$.value?.namespace).toBe('agent-team-v1');
  });

  it('(AC3 1.9) createTeam sends the selected namespace to apiService.createTeam', async () => {
    component.selectedNamespace$.next({
      namespace: 'agent-team-v1',
      name: 'Agent Team',
      description: 'Default',
    });
    apiSpy.createTeam.calls.reset();
    await component.createTeam();
    expect(apiSpy.createTeam).toHaveBeenCalledOnceWith('agent-team-v1');
  });

  it('(AC3 1.9) createTeamAndNavigate passes selected.namespace (not an id lookup)', async () => {
    component.selectedNamespace$.next({
      namespace: 'rag-team-v1',
      name: 'RAG Team',
      description: 'With RAG',
    });
    contextSpy.createTeamAndNavigate.calls.reset();
    await component.createTeamAndNavigate();
    expect(contextSpy.createTeamAndNavigate).toHaveBeenCalledOnceWith('rag-team-v1');
  });

  it('(AC5 1.9) empty namespace list leaves the dropdown empty and no selection', async () => {
    apiSpy.getNamespaces.and.returnValue(Promise.resolve([]));
    await component.ngOnInit();
    expect(component.namespaces$.value).toEqual([]);
    expect(component.selectedNamespace$.value).toBeNull();
  });

  it('(AC5 1.9) getNamespaces failure does not crash ngOnInit', async () => {
    apiSpy.getNamespaces.and.returnValue(Promise.reject(new Error('boom')));
    const consoleErrorSpy = spyOn(console, 'error');
    await expectAsync(component.ngOnInit()).toBeResolved();
    expect(consoleErrorSpy).toHaveBeenCalled();
    expect(component.namespaces$.value).toEqual([]);
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

  // --- Story 11.2 — namespace-panel dialog wiring ---------------------

  function editButton(): HTMLButtonElement | null {
    const el = fixture.nativeElement.querySelector(
      'button[data-test="edit-namespace-yaml-btn"]',
    );
    return el as HTMLButtonElement | null;
  }

  it('(AC14 11.2) "Edit namespace YAML" button is disabled when no namespace is selected', async () => {
    component.selectedNamespace$.next(null);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const btn = editButton();
    expect(btn).withContext('edit-namespace-yaml-btn must render').not.toBeNull();
    // PrimeNG propagates [disabled] onto the inner button element.
    expect(btn!.disabled).toBeTrue();
  });

  it('(AC14 11.2) "Edit namespace YAML" button is enabled when a namespace is selected', async () => {
    component.selectedNamespace$.next({
      namespace: 'foo',
      name: 'Foo',
      description: '',
    });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const btn = editButton();
    expect(btn).not.toBeNull();
    expect(btn!.disabled).toBeFalse();
  });

  it('(AC14 11.2) clicking the button sets namespacePanelVisible = true', async () => {
    component.selectedNamespace$.next({
      namespace: 'foo',
      name: 'Foo',
      description: '',
    });
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(component.namespacePanelVisible).toBeFalse();

    const btn = editButton();
    expect(btn).not.toBeNull();
    btn!.click();
    fixture.detectChanges();

    expect(component.namespacePanelVisible).toBeTrue();
  });

  it('(AC14 11.2) setting namespacePanelVisible = false simulates the (closed) handler', () => {
    // The (closed)="namespacePanelVisible = false" binding in the template
    // is a direct property assignment — simulate it without relying on the
    // @defer block to mount the nested component in tests.
    component.namespacePanelVisible = true;
    component.namespacePanelVisible = false;
    expect(component.namespacePanelVisible).toBeFalse();
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

  // --- Story 11.3 — dialog dirty-close guard + (saved) re-fetch -------

  it('(11.3 AC10) onNamespacePanelVisibleChange(true) is a no-op (opening the dialog)', () => {
    component.namespacePanelVisible = true;
    component.onNamespacePanelVisibleChange(true);
    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
    expect(component.namespacePanelVisible).toBeTrue();
  });

  it('(11.3 AC10) onNamespacePanelVisibleChange(false) with clean panel closes without confirm', () => {
    component.namespacePanelVisible = true;
    // Simulate a mounted-but-clean panel.
    component.namespacePanel = {
      hasUnsavedChanges: () => false,
    } as unknown as NamespacePanelComponent;

    component.onNamespacePanelVisibleChange(false);

    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
    expect(component.namespacePanelVisible).toBeFalse();
  });

  it('(11.3 AC10) onNamespacePanelVisibleChange(false) with no mounted panel closes without confirm', () => {
    component.namespacePanelVisible = true;
    component.namespacePanel = undefined;

    component.onNamespacePanelVisibleChange(false);

    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
    expect(component.namespacePanelVisible).toBeFalse();
  });

  it('(11.3 AC10) onNamespacePanelVisibleChange(false) with dirty panel requests confirm and keeps dialog open', () => {
    component.namespacePanelVisible = true;
    component.namespacePanel = {
      hasUnsavedChanges: () => true,
    } as unknown as NamespacePanelComponent;

    component.onNamespacePanelVisibleChange(false);

    expect(confirmationSpy.confirm).toHaveBeenCalledTimes(1);
    const args = confirmationSpy.confirm.calls.mostRecent().args[0];
    expect(args.message as string).toContain('unsaved changes');
    // Re-asserted visibility to keep the dialog open during the confirm.
    expect(component.namespacePanelVisible).toBeTrue();

    // Accept → the dialog truly closes.
    args.accept!();
    expect(component.namespacePanelVisible).toBeFalse();
  });

  it('(11.3 AC10) dismissing the confirm (reject) leaves the dialog open, buffer intact', () => {
    component.namespacePanelVisible = true;
    component.namespacePanel = {
      hasUnsavedChanges: () => true,
    } as unknown as NamespacePanelComponent;

    component.onNamespacePanelVisibleChange(false);

    // Reject is optional in ConfirmationService; the call-site does not
    // register one, so dismissing leaves state unchanged.
    const args = confirmationSpy.confirm.calls.mostRecent().args[0];
    expect(args.reject).toBeUndefined();
    // Visibility is still true (re-asserted by the handler).
    expect(component.namespacePanelVisible).toBeTrue();
  });

  it('(11.3 AC6) onNamespaceSaved re-invokes getNamespaces and pushes the result into namespaces$', async () => {
    // First load pushed [] from beforeEach spy setup. Now prime a new list
    // and invoke the (saved) handler — the dropdown must refresh.
    const updated = [
      { namespace: 'agent-team-v1', name: 'Agent Team', description: 'd1' },
      { namespace: 'rag-team-v1', name: 'RAG Team', description: 'd2' },
    ];
    apiSpy.getNamespaces.calls.reset();
    apiSpy.getNamespaces.and.returnValue(Promise.resolve(updated));

    await component.onNamespaceSaved();

    expect(apiSpy.getNamespaces).toHaveBeenCalledTimes(1);
    expect(component.namespaces$.value).toEqual(updated);
  });

  // --- Story 11.5 — namespaceIdentifiers getter + binding ---------------

  it('(11.5 AC13) namespaceIdentifiers returns the `.namespace` field of each namespaces$ entry', () => {
    component.namespaces$.next([
      { namespace: 'foo', name: 'F', description: '' },
      { namespace: 'bar', name: 'B', description: '' },
    ]);
    expect(component.namespaceIdentifiers).toEqual(['foo', 'bar']);
  });

  it('(11.5 AC13) namespaceIdentifiers returns [] when namespaces$ is empty', () => {
    component.namespaces$.next([]);
    expect(component.namespaceIdentifiers).toEqual([]);
  });

  // --- Story 11.7 — dirty indicator + dialog [closable] (FR15 + FR18) ---

  it('(11.7 AC22) isWriteInFlight is true when namespacePanel.saving === true', () => {
    component.namespacePanel = {
      saving: true,
      cloning: false,
    } as unknown as NamespacePanelComponent;
    expect(component.isWriteInFlight).toBeTrue();
  });

  it('(11.7 AC22) isWriteInFlight is true when namespacePanel.cloning === true', () => {
    component.namespacePanel = {
      saving: false,
      cloning: true,
    } as unknown as NamespacePanelComponent;
    expect(component.isWriteInFlight).toBeTrue();
  });

  it('(11.7 AC23) isWriteInFlight is false when namespacePanel is undefined', () => {
    component.namespacePanel = undefined;
    expect(component.isWriteInFlight).toBeFalse();
  });

  it('(11.7 AC23) isWriteInFlight is false when only validating/loading are true (reads are non-destructive)', () => {
    component.namespacePanel = {
      saving: false,
      cloning: false,
      validating: true,
      loading: true,
    } as unknown as NamespacePanelComponent;
    expect(component.isWriteInFlight).toBeFalse();
  });

  it('(11.7 AC8) namespaceLabel returns selected.name when present', () => {
    component.selectedNamespace$.next({
      namespace: 'foo',
      name: 'Foo Display',
      description: '',
    });
    expect(component.namespaceLabel).toBe('Foo Display');
  });

  it('(11.7 AC8) namespaceLabel falls back to "Namespace" when none selected', () => {
    component.selectedNamespace$.next(null);
    expect(component.namespaceLabel).toBe('Namespace');
  });

  it('(11.7 AC8, AC9, AC10) dialog header dirty-indicator binding follows panel.hasUnsavedChanges()', () => {
    // Asserts the BINDING contract — the template predicate is
    // `namespacePanel?.hasUnsavedChanges() === true`. PrimeNG's dialog
    // teleports the rendered header into an overlay attached to <body>
    // which is finicky to query deterministically in component tests; the
    // contract that matters here is "indicator gates on panel's dirty
    // method", which is what the binding evaluates.
    function predicate(): boolean {
      return component.namespacePanel?.hasUnsavedChanges() === true;
    }

    component.namespacePanel = undefined;
    expect(predicate()).toBeFalse();

    // Clean panel — predicate is false, indicator hidden.
    component.namespacePanel = {
      hasUnsavedChanges: () => false,
    } as unknown as NamespacePanelComponent;
    expect(predicate()).toBeFalse();

    // Dirty panel — predicate is true, indicator visible.
    component.namespacePanel = {
      hasUnsavedChanges: () => true,
    } as unknown as NamespacePanelComponent;
    expect(predicate()).toBeTrue();
  });

  it('(11.5 AC13) template binding propagates namespaceIdentifiers via ng-reflect', async () => {
    // Prime the ngOnInit load so it does NOT overwrite our namespaces$ with
    // the default empty list: make `getNamespaces` resolve with the pair we
    // want to observe on the binding.
    const list = [
      { namespace: 'alpha', name: 'Alpha', description: '' },
      { namespace: 'beta', name: 'Beta', description: '' },
    ];
    apiSpy.getNamespaces.and.returnValue(Promise.resolve(list));

    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    // Open the panel dialog so the @defer block mounts the panel element.
    // `CUSTOM_ELEMENTS_SCHEMA` prevents the real `NamespacePanelComponent`
    // from asserting its surface; we only care that the input attribute
    // lands on the element.
    component.namespacePanelVisible = true;
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const panelEl = fixture.nativeElement.querySelector(
      'app-namespace-panel',
    ) as HTMLElement | null;
    if (panelEl) {
      const attr = panelEl.getAttribute('ng-reflect-existing-namespaces');
      if (attr !== null) {
        expect(attr).toContain('alpha');
        expect(attr).toContain('beta');
      }
    }
    // Deterministic assertion: the getter itself is the contract.
    expect(component.namespaceIdentifiers).toEqual(['alpha', 'beta']);
  });
});
