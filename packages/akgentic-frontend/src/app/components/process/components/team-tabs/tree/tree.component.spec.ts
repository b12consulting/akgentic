import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { BehaviorSubject, of } from 'rxjs';

import { TreeComponent } from './tree.component';
import { ApiService } from '../../../../../core/http/api.service';
import { GraphDataService } from '../../../selectors/graph.selector';
import { SelectionService } from '../../../ui-state/selection.service';
import {
  TeamTokenTotals,
  TokenUsageSelector,
} from '../../../selectors/token-usage.selector';
import { NodeInterface } from '../../../models/types';

/**
 * Story 26-3 (ADR-022 §Decision 6) — team-tree footer total. A slim,
 * non-interactive footer strip sits BETWEEN the tree (`.tree-container`) and
 * `<app-human-request>` inside `.tree-component-container`, bound to
 * `tokenUsageSelector.teamTotals$ | async`: `Team total ↑<sent> ↓<received>`
 * (each number via `tokenCount`); empty team (`{0,0}`) → `Team total ↑0 ↓0`.
 *
 * The fake `TokenUsageSelector` exposes a driveable `teamTotals$` subject so
 * render / live-update / empty-state are deterministic. The tree + its
 * `<app-human-request>` child also need `SelectionService` / `GraphDataService`
 * / `ApiService`.
 */
describe('TreeComponent — team-total footer (Story 26-3)', () => {
  /** Drives the footer; emits the team totals the spec asserts on. */
  let totals$: BehaviorSubject<TeamTokenTotals>;
  /** Feeds the tree's nodes (the host subscribes in ngOnInit). */
  let nodes$: BehaviorSubject<NodeInterface[]>;
  let handleSelection: jasmine.Spy;

  function makeNode(name: string): NodeInterface {
    return {
      name,
      role: 'Agent',
      actorName: name,
      parentId: '',
      squadId: 's1',
      symbol: 'roundRect',
      category: 0,
      userMessage: false,
    };
  }

  function setup(
    initial: TeamTokenTotals = { totalSent: 0, totalReceived: 0 },
  ): ComponentFixture<TreeComponent> {
    totals$ = new BehaviorSubject<TeamTokenTotals>(initial);
    nodes$ = new BehaviorSubject<NodeInterface[]>([]);
    handleSelection = jasmine.createSpy('handleSelection');

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [TreeComponent],
      providers: [
        // Fake selector provided at the (root) injector level — TreeComponent's
        // bare `inject(TokenUsageSelector)` resolves THIS shared instance.
        {
          provide: TokenUsageSelector,
          useValue: {
            teamTotals$: totals$.asObservable(),
            perAgent$: (_id: string) => of(undefined),
          },
        },
        {
          provide: GraphDataService,
          useValue: {
            nodes$,
            edges$: new BehaviorSubject<unknown[]>([]),
            categories$: new BehaviorSubject<unknown[]>([]),
            categoryService: { COLORS: ['#fff', '#000'] },
            set isLoading(_v: boolean) {
              /* swallowed — buildTree side effect, irrelevant to the footer */
            },
          },
        },
        {
          provide: SelectionService,
          useValue: {
            handleSelection,
            userRequest$: new BehaviorSubject<unknown>({}),
            modalVisible$: new BehaviorSubject<boolean>(false),
            onSave: jasmine.createSpy('onSave'),
          },
        },
        { provide: ApiService, useValue: {} },
        provideNoopAnimations(),
      ],
    });

    return TestBed.createComponent(TreeComponent);
  }

  /** The footer strip element. */
  function footer(fixture: ComponentFixture<TreeComponent>): HTMLElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector(
      '.team-total-footer',
    );
  }

  /** Footer text with whitespace collapsed (glyphs/order are the contract,
   *  not exact spacing). */
  function footerText(fixture: ComponentFixture<TreeComponent>): string {
    return (footer(fixture)?.textContent ?? '').replace(/\s+/g, ' ').trim();
  }

  it('(a) renders the populated `Team total ↑X ↓Y` BETWEEN the tree and app-human-request', () => {
    const fixture = setup({ totalSent: 57_000, totalReceived: 12_500 });
    fixture.detectChanges();

    // tokenCount: 57_000 → "57.0k", 12_500 → "12.5k".
    expect(footerText(fixture)).toBe('Team total ↑57.0k ↓12.5k');

    // Non-interactive: a <div>, not a <button>; no click handler / routerLink.
    const f = footer(fixture)!;
    expect(f.tagName.toLowerCase()).toBe('div');

    // DOM order: footer AFTER the tree region, BEFORE app-human-request, all
    // direct children of `.tree-component-container`.
    const container = (fixture.nativeElement as HTMLElement).querySelector(
      '.tree-component-container',
    )!;
    const kids = Array.from(container.children);
    const treeIdx = kids.findIndex((c) =>
      c.classList.contains('tree-container'),
    );
    const footerIdx = kids.findIndex((c) =>
      c.classList.contains('team-total-footer'),
    );
    const humanIdx = kids.findIndex(
      (c) => c.tagName.toLowerCase() === 'app-human-request',
    );
    expect(treeIdx).toBeGreaterThanOrEqual(0);
    expect(footerIdx).toBeGreaterThan(treeIdx);
    expect(humanIdx).toBeGreaterThan(footerIdx);
  });

  it('(b) live update: a fresh `teamTotals$` emission re-renders the ↑/↓ sums', () => {
    const fixture = setup({ totalSent: 1_000, totalReceived: 200 });
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑1.0k ↓200');

    // A new LlmUsageEvent landed for some agent → the selector re-emits a larger
    // structural sum; the async pipe re-renders without any imperative refresh.
    totals$.next({ totalSent: 73_400, totalReceived: 18_900 });
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑73.4k ↓18.9k');
  });

  it('(c) empty team (`{0,0}`) renders the `Team total ↑0 ↓0` fallback', () => {
    const fixture = setup({ totalSent: 0, totalReceived: 0 });
    fixture.detectChanges();

    // The zero-totals object IS the empty state — the footer is still rendered.
    expect(footer(fixture)).not.toBeNull();
    expect(footerText(fixture)).toBe('Team total ↑0 ↓0');
  });

  it('(d) resolves the SHARED selector instance (no self-provider) and leaves the tree behavior intact', () => {
    const fixture = setup({ totalSent: 100, totalReceived: 50 });
    const component = fixture.componentInstance;

    // The component must NOT re-provide TokenUsageSelector — it resolves the
    // single instance from the (root) injector. The exact fake we provided is
    // what the component holds, proving the bare inject walked UP.
    const shared = TestBed.inject(TokenUsageSelector);
    expect((component as unknown as { tokenUsageSelector: TokenUsageSelector })
      .tokenUsageSelector).toBe(shared);

    // And the standalone component definition declares no own providers for it.
    const def = (TreeComponent as unknown as { ɵcmp?: { providers?: unknown } })
      .ɵcmp;
    expect(def?.providers ?? null).toBeNull();

    // The tree still renders nodes and node-click still flows to the selection
    // service (footer addition is non-disruptive — AC #7).
    nodes$.next([makeNode('a-mgr')]);
    fixture.detectChanges();
    expect(component.treeNodes.length).toBe(1);

    component.onNodeClick({ node: { data: makeNode('a-mgr') } });
    expect(handleSelection).toHaveBeenCalledTimes(1);
  });
});
