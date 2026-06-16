import { TestBed } from '@angular/core/testing';

import { NamespacePanelComponent } from './namespace-panel.component';
import { NamespacePanelRouteComponent } from './namespace-panel-route.component';
import { namespacePanelCanDeactivate } from './namespace-panel.guard';

/**
 * Functional `CanDeactivate` guard tests.
 *
 * The guard delegates its dirty-navigation prompt to the panel's custom confirm
 * modal via `panel.confirmDiscard()`. Tests stub `panel.confirmDiscard()` to
 * resolve true (Proceed) / false (Cancel/dismiss) and assert the guard relays
 * that verbatim.
 */
describe('namespacePanelCanDeactivate (CanDeactivate guard)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [] });
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

  it('(AC4, AC8) clean buffer → returns true synchronously, no confirmDiscard call', () => {
    const confirmDiscard = jasmine.createSpy('confirmDiscard');
    const panel = {
      hasUnsavedChanges: jasmine
        .createSpy('hasUnsavedChanges')
        .and.returnValue(false),
      confirmDiscard,
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    expect(result).toBe(true);
    expect(confirmDiscard).not.toHaveBeenCalled();
  });

  it('(ADR-018 §c) dirty buffer + Proceed → delegates to confirmDiscard, resolves true', async () => {
    const confirmDiscard = jasmine
      .createSpy('confirmDiscard')
      .and.returnValue(Promise.resolve(true));
    const panel = {
      hasUnsavedChanges: () => true,
      confirmDiscard,
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    expect(confirmDiscard).toHaveBeenCalledTimes(1);
    await expectAsync(result as Promise<boolean>).toBeResolvedTo(true);
  });

  it('(ADR-018 §c) dirty buffer + Cancel/dismiss → confirmDiscard resolves false', async () => {
    const confirmDiscard = jasmine
      .createSpy('confirmDiscard')
      .and.returnValue(Promise.resolve(false));
    const panel = {
      hasUnsavedChanges: () => true,
      confirmDiscard,
    } as unknown as NamespacePanelComponent;
    const component = { panel } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    await expectAsync(result as Promise<boolean>).toBeResolvedTo(false);
  });

  it('(AC4) panel undefined (not yet mounted) → returns true synchronously', () => {
    const component = { panel: undefined } as NamespacePanelRouteComponent;

    const result = invokeGuard(component);

    expect(result).toBe(true);
  });
});
