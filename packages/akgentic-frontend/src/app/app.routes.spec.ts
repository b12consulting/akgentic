import { Router, Routes } from '@angular/router';
import { TestBed } from '@angular/core/testing';
import { RouterTestingModule } from '@angular/router/testing';

import { routes } from './app.routes';
import { AuthGuard } from './auth.guard';
import { namespacePanelCanDeactivate } from './admin/catalog/namespace-panel/namespace-panel.guard';

/**
 * Story 11.6 — route-registration tests for the deep-link route.
 *
 * Covers:
 * - AC 1 route entry shape (path, `loadComponent` function, AuthGuard on
 *   canActivate, functional `CanDeactivate` guard).
 * - AC 15 URL path-parameter parsing (`:namespace` is a path segment, not
 *   a query param) + URL-decoded round-trip.
 * - AC 16 guard is a function (functional CanDeactivateFn).
 */
describe('app.routes (Story 11.6 — deep-link route registration)', () => {
  function findAdminRoute(rs: Routes) {
    return rs.find(
      (r) => r.path === 'admin/catalog/namespace/:namespace',
    );
  }

  it('(AC1) admin route entry exists with expected shape', () => {
    const route = findAdminRoute(routes);
    expect(route).toBeDefined();
    expect(route!.path).toBe('admin/catalog/namespace/:namespace');
    expect(typeof route!.loadComponent).toBe('function');
    expect(route!.component).toBeUndefined(); // lazy-loaded, not eager
    expect(route!.title).toBe('Catalog namespace');
    expect(route!.canActivate).toEqual([AuthGuard]);
    expect(route!.canDeactivate).toEqual([namespacePanelCanDeactivate]);
  });

  it('(AC16) canDeactivate guard reference is a function', () => {
    expect(typeof namespacePanelCanDeactivate).toBe('function');
  });

  it('(AC1) existing routes (home, process, login) remain intact', () => {
    // Sanity-check that the story did NOT tamper with sibling entries.
    const paths = routes.map((r) => r.path);
    expect(paths).toContain('');
    expect(paths).toContain('process/:id');
    expect(paths).toContain('login');
    expect(paths).toContain('admin/catalog/namespace/:namespace');
  });

  describe('URL parsing (AC15)', () => {
    let router: Router;

    beforeEach(() => {
      TestBed.configureTestingModule({
        imports: [RouterTestingModule.withRoutes(routes)],
      });
      router = TestBed.inject(Router);
    });

    it('(AC15) /admin/catalog/namespace/foo parses as path segments', () => {
      const tree = router.parseUrl('/admin/catalog/namespace/foo');
      const segments = tree.root.children['primary']!.segments.map(
        (s) => s.path,
      );
      expect(segments).toEqual(['admin', 'catalog', 'namespace', 'foo']);
    });

    it('(AC15) URL-encoded namespace (my%20ns) decodes to "my ns"', () => {
      const tree = router.parseUrl('/admin/catalog/namespace/my%20ns');
      const segments = tree.root.children['primary']!.segments.map(
        (s) => s.path,
      );
      expect(segments[3]).toBe('my ns');
    });
  });
});
