import { TestBed } from '@angular/core/testing';
import { ConfirmationService } from 'primeng/api';

import { NamespacePanelComponent } from './namespace-panel.component';
import { NamespacePanelRouteComponent } from './namespace-panel-route.component';
import { namespacePanelCanDeactivate } from './namespace-panel.guard';

/**
 * Story 11.6 — functional `CanDeactivate` guard tests.
 *
 * The guard runs inside a router-owned injection context at navigation time.
 * In tests we invoke it via `TestBed.runInInjectionContext(() => ...)` so
 * `inject(ConfirmationService)` resolves correctly against the test module's
 * providers.
 */
describe('namespacePanelCanDeactivate (Story 11.6 CanDeactivate guard)', () => {
  let confirmationSpy: jasmine.SpyObj<ConfirmationService>;

  beforeEach(() => {
    // Real service instance + spy on `.confirm` — matches the panel's unit
    // tests (a bare `createSpyObj` breaks PrimeNG's internal
    // `requireConfirmation$` subject).
    confirmationSpy =
      new ConfirmationService() as jasmine.SpyObj<ConfirmationService>;
    spyOn(confirmationSpy, 'confirm').and.callThrough();

    TestBed.configureTestingModule({
      providers: [{ provide: ConfirmationService, useValue: confirmationSpy }],
    });
  });

  // Helper — invoke the functional guard in an injection context.
  // Narrow the declared `MaybeAsync<GuardResult>` return to the
  // guard-specific `boolean | Promise<boolean>` subset this guard actually
  // returns (Angular's CanDeactivateFn signature type includes UrlTree /
  // Observable in the union, neither of which this guard produces).
  function invokeGuard(
    component: Partial<NamespacePanelRouteComponent>,
  ): boolean | Promise<boolean> {
    return TestBed.runInInjectionContext(
      () =>
        namespacePanelCanDeactivate(
          component as NamespacePanelRouteComponent,
          null!,
          null!,
          null!,
        ) as boolean | Promise<boolean>,
    );
  }

  it('(AC16) is declared as a function (functional CanDeactivateFn)', () => {
    expect(typeof namespacePanelCanDeactivate).toBe('function');
  });

  it('(AC4, AC8) clean buffer → returns true synchronously, no prompt', () => {
    const panel = {
      hasUnsavedChanges: jasmine.createSpy('hasUnsavedChanges').and.returnValue(false),
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    expect(result).toBe(true);
    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
  });

  it('(AC4) dirty buffer + confirm-accept → resolves true with exact message', async () => {
    const panel = {
      hasUnsavedChanges: () => true,
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;
    // Auto-accept the first confirm call.
    confirmationSpy.confirm.and.callFake((cfg) => {
      cfg.accept!();
      return confirmationSpy;
    });

    const result = invokeGuard(component);

    expect(result).toEqual(jasmine.any(Promise));
    await expectAsync(result as Promise<boolean>).toBeResolvedTo(true);
    const args = confirmationSpy.confirm.calls.mostRecent().args[0];
    expect(args.message).toBe('You have unsaved changes. Discard?');
  });

  it('(AC4) dirty buffer + confirm-reject → resolves false', async () => {
    const panel = {
      hasUnsavedChanges: () => true,
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;
    confirmationSpy.confirm.and.callFake((cfg) => {
      cfg.reject!();
      return confirmationSpy;
    });

    const result = invokeGuard(component);

    await expectAsync(result as Promise<boolean>).toBeResolvedTo(false);
  });

  it('(AC4) panel undefined (not yet mounted) → returns true synchronously', () => {
    const component = { panel: undefined } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    expect(result).toBe(true);
    expect(confirmationSpy.confirm).not.toHaveBeenCalled();
  });
});
