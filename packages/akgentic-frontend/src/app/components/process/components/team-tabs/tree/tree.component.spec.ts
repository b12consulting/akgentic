import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { BehaviorSubject, of } from 'rxjs';

import { TreeComponent } from './tree.component';
import { ApiService } from '../../../../../core/http/api.service';
import { GraphDataService } from '../../../selectors/graph.selector';
import { SelectionService } from '../../../ui-state/selection.service';
import {
  ModelTokenTotals,
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
    initial: TeamTokenTotals = {
      totalSent: 0,
      totalReceived: 0,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    },
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
            teamByModel$: of([]),
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

  /** The full-width footer strip (wrapper). */
  function footer(fixture: ComponentFixture<TreeComponent>): HTMLElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector(
      '.team-total-footer',
    );
  }

  /** The compact popover trigger button inside the strip. */
  function trigger(
    fixture: ComponentFixture<TreeComponent>,
  ): HTMLButtonElement | null {
    return (fixture.nativeElement as HTMLElement).querySelector(
      '.team-total-trigger',
    );
  }

  /** Trigger text with whitespace collapsed (glyphs/order are the contract,
   *  not exact spacing). */
  function footerText(fixture: ComponentFixture<TreeComponent>): string {
    return (trigger(fixture)?.textContent ?? '').replace(/\s+/g, ' ').trim();
  }

  it('(a) renders the populated `Team total ↑X ↓Y` as an interactive trigger, BETWEEN the tree and app-human-request', () => {
    const fixture = setup({
      totalSent: 57_000,
      totalReceived: 12_500,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    });
    fixture.detectChanges();

    // tokenCount: 57_000 → "57.0k", 12_500 → "12.5k". No cache read → no parens
    // (the em-dash separator before ↓ is always present).
    expect(footerText(fixture)).toBe('Team total ↑57.0k — ↓12.5k');

    // Interactive: a keyboard-focusable <button> trigger, enabled (usage →
    // popover to open); the strip wrapper itself is a plain <div>.
    expect(footer(fixture)!.tagName.toLowerCase()).toBe('div');
    const t = trigger(fixture)!;
    expect(t.tagName.toLowerCase()).toBe('button');
    expect(t.disabled).toBeFalse();

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
    const fixture = setup({
      totalSent: 1_000,
      totalReceived: 200,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    });
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑1.0k — ↓200');

    // A new LlmUsageEvent landed for some agent → the selector re-emits a larger
    // structural sum; the async pipe re-renders without any imperative refresh.
    totals$.next({
      totalSent: 73_400,
      totalReceived: 18_900,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    });
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑73.4k — ↓18.9k');
  });

  it('(c) empty team (`{0,0}`) renders the `Team total ↑0 ↓0` fallback with an inert (disabled) trigger', () => {
    const fixture = setup({
      totalSent: 0,
      totalReceived: 0,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    });
    fixture.detectChanges();

    // The zero-totals object IS the empty state — the footer is still rendered,
    // just showing all-zero sums (cache read 0 → no parens) and disabled (no
    // usage → nothing to break down).
    expect(footer(fixture)).not.toBeNull();
    expect(footerText(fixture)).toBe('Team total ↑0 — ↓0');
    expect(trigger(fixture)!.disabled).toBeTrue();
  });

  it('(d) resolves the SHARED selector instance (no self-provider) and leaves the tree behavior intact', () => {
    const fixture = setup({
      totalSent: 100,
      totalReceived: 50,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    });
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

// ---------------------------------------------------------------------------
// Team-total footer — the chip keeps `Team total ↑{sent} (⚡{cacheRead}) —
// ↓{received}` (cache parens only when non-zero) and, when it carries usage,
// toggles a `<p-popover>` breaking the totals down BY MODEL (`teamByModel$`,
// one row per model: `{model} ↑{sent} (⚡{cacheRead}) ↓{received}`). Driveable
// `totals$` + `byModel$` subjects make chip / toggle / live-update / empty-state
// deterministic. `p-popover` renders in place (no `appendTo`) so its content is
// queryable straight off `fixture.nativeElement`.
// ---------------------------------------------------------------------------
describe('TreeComponent — team-total popover by model', () => {
  let totals$: BehaviorSubject<TeamTokenTotals>;
  let byModel$: BehaviorSubject<ModelTokenTotals[]>;
  let nodes$: BehaviorSubject<NodeInterface[]>;

  function setup(
    initialTotals: TeamTokenTotals = {
      totalSent: 0,
      totalReceived: 0,
      totalCacheRead: 0,
      totalCacheWrite: 0,
    },
    initialByModel: ModelTokenTotals[] = [],
  ): ComponentFixture<TreeComponent> {
    totals$ = new BehaviorSubject<TeamTokenTotals>(initialTotals);
    byModel$ = new BehaviorSubject<ModelTokenTotals[]>(initialByModel);
    nodes$ = new BehaviorSubject<NodeInterface[]>([]);

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [TreeComponent],
      providers: [
        {
          provide: TokenUsageSelector,
          useValue: {
            teamTotals$: totals$.asObservable(),
            teamByModel$: byModel$.asObservable(),
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
            handleSelection: jasmine.createSpy('handleSelection'),
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

  // The popover trigger is the compact button inside the strip.
  function footer(fixture: ComponentFixture<TreeComponent>): HTMLButtonElement {
    return (fixture.nativeElement as HTMLElement).querySelector(
      '.team-total-trigger',
    ) as HTMLButtonElement;
  }

  function footerText(fixture: ComponentFixture<TreeComponent>): string {
    return (footer(fixture)?.textContent ?? '').replace(/\s+/g, ' ').trim();
  }

  /** Each `.usage-popover-row`'s two cells (model label + usage line), inner
   *  whitespace collapsed — empty array when the popover isn't open. */
  function popoverRows(fixture: ComponentFixture<TreeComponent>): string[] {
    const rows = (fixture.nativeElement as HTMLElement).querySelectorAll(
      '.usage-popover-row',
    );
    return Array.from(rows).map((row) =>
      Array.from(row.children)
        .map((c) => (c.textContent ?? '').replace(/\s+/g, ' ').trim())
        .join(' '),
    );
  }

  // The two models whose totals sum to the user's headline chip example.
  const GPT: ModelTokenTotals = {
    modelName: 'gpt-5.4-2026-03-05',
    totalSent: 12_000,
    totalReceived: 100,
    totalCacheRead: 10_000,
    totalCacheWrite: 0,
  };
  const CLAUDE: ModelTokenTotals = {
    modelName: 'claude-opus-4-8',
    totalSent: 4_800,
    totalReceived: 61,
    totalCacheRead: 2_800,
    totalCacheWrite: 0,
  };
  const HEADLINE: TeamTokenTotals = {
    totalSent: 16_800,
    totalReceived: 161,
    totalCacheRead: 12_800,
    totalCacheWrite: 0,
  };

  it('chip shows the cache read in parens (⚡-prefixed) between ↑ and ↓ when team cache tokens were used', () => {
    const fixture = setup(HEADLINE, [GPT, CLAUDE]);
    fixture.detectChanges();
    // tokenCount: 16_800 → "16.8k", 12_800 → "12.8k", 161 → "161".
    expect(footerText(fixture)).toBe('Team total ↑16.8k (⚡12.8k) — ↓161');
  });

  it('chip omits the parens (and ⚡) when team cache read is 0', () => {
    const fixture = setup(
      { totalSent: 1_000, totalReceived: 200, totalCacheRead: 0, totalCacheWrite: 0 },
      [{ modelName: 'gpt-5.4-2026-03-05', totalSent: 1_000, totalReceived: 200, totalCacheRead: 0, totalCacheWrite: 0 }],
    );
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑1.0k — ↓200');
    expect(footer(fixture).textContent).not.toContain('⚡');
  });

  it('clicking the trigger toggles open the popover with one row per model', () => {
    const fixture = setup(HEADLINE, [GPT, CLAUDE]);
    fixture.detectChanges();

    // Closed disclosure control, no popover content yet.
    expect(popoverRows(fixture)).toEqual([]);
    expect(footer(fixture).getAttribute('aria-haspopup')).toBe('dialog');
    expect(footer(fixture).getAttribute('aria-expanded')).toBe('false');

    footer(fixture).click();
    fixture.detectChanges();

    const rows = popoverRows(fixture);
    expect(rows).toContain('gpt-5.4-2026-03-05 ↑12.0k (⚡10.0k) ↓100');
    expect(rows).toContain('claude-opus-4-8 ↑4.8k (⚡2.8k) ↓61');
    expect(footer(fixture).getAttribute('aria-expanded')).toBe('true');
  });

  it('popover per-model rows update live when teamByModel$ re-emits (a cache-free model shows no parens)', () => {
    const fixture = setup(HEADLINE, [GPT, CLAUDE]);
    fixture.detectChanges();
    footer(fixture).click();
    fixture.detectChanges();
    expect(popoverRows(fixture)).toContain('gpt-5.4-2026-03-05 ↑12.0k (⚡10.0k) ↓100');

    byModel$.next([
      { modelName: 'gpt-5.4-2026-03-05', totalSent: 20_000, totalReceived: 300, totalCacheRead: 15_000, totalCacheWrite: 0 },
      { modelName: 'local-llama', totalSent: 1_000, totalReceived: 50, totalCacheRead: 0, totalCacheWrite: 0 },
    ]);
    fixture.detectChanges();

    const rows = popoverRows(fixture);
    expect(rows).toContain('gpt-5.4-2026-03-05 ↑20.0k (⚡15.0k) ↓300');
    // Cache-free model → no ⚡ parens.
    expect(rows).toContain('local-llama ↑1.0k ↓50');
  });

  it('chip cache read updates live when teamTotals$ re-emits a new value', () => {
    const fixture = setup(
      { totalSent: 1_000, totalReceived: 200, totalCacheRead: 300, totalCacheWrite: 0 },
      [{ modelName: 'gpt-5.4-2026-03-05', totalSent: 1_000, totalReceived: 200, totalCacheRead: 300, totalCacheWrite: 0 }],
    );
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑1.0k (⚡300) — ↓200');

    totals$.next({ totalSent: 5_000, totalReceived: 900, totalCacheRead: 4_400, totalCacheWrite: 250 });
    fixture.detectChanges();
    expect(footerText(fixture)).toBe('Team total ↑5.0k (⚡4.4k) — ↓900');
  });

  it('empty team (all-zero totals) renders a disabled trigger with no popover rows to open', () => {
    const fixture = setup();
    fixture.detectChanges();

    expect(footer(fixture).disabled).toBeTrue();
    expect(popoverRows(fixture)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Story 30-3 — component-level OnPush on the usage-display host components.
// TreeComponent now runs under `ChangeDetectionStrategy.OnPush`; the `nodes$`
// subscription in `ngOnInit` mutates `treeNodes` (the critical path — it
// rebuilds the visible `<p-tree>`) from a MANUAL RxJS subscription, not the
// `async` pipe, so only an explicit `ChangeDetectorRef.markForCheck()` keeps
// it repainting. A plain repeated `fixture.detectChanges()` call — with NO
// further `@Input` change and NO DOM event between the emission and the
// assertion — genuinely exercises the OnPush + `markForCheck()` plumbing
// (verified by temporarily removing the `markForCheck()` call and confirming
// this test fails — see Dev Agent Record). No `fakeAsync`/`tick()` here: the
// `nodes$` subscription is fully synchronous, and flushing the fake-timer
// queue would re-arm Angular's zone-driven auto-refresh scheduler, which
// repaints the view regardless of `markForCheck()` — confirmed empirically
// while designing the member-chat regression tests (see Dev Agent Record).
// ---------------------------------------------------------------------------
describe('TreeComponent — OnPush regression (Story 30-3)', () => {
  let nodes$: BehaviorSubject<NodeInterface[]>;

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

  function setup(): ComponentFixture<TreeComponent> {
    nodes$ = new BehaviorSubject<NodeInterface[]>([]);

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [TreeComponent],
      providers: [
        {
          provide: TokenUsageSelector,
          useValue: {
            teamTotals$: new BehaviorSubject<TeamTokenTotals>({
              totalSent: 0,
              totalReceived: 0,
              totalCacheRead: 0,
              totalCacheWrite: 0,
            }),
            teamByModel$: of([]),
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
              /* swallowed — buildTree side effect, irrelevant to this test */
            },
          },
        },
        {
          provide: SelectionService,
          useValue: {
            handleSelection: jasmine.createSpy('handleSelection'),
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

  it('AC4: a mid-stream nodes$ emission (no @Input change, no DOM event) re-renders the tree node labels', () => {
    const fixture = setup();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).not.toContain('a [Mgr]');

    // A source emission on the injected service's observable — not an @Input,
    // not a DOM event — only the manual `ngOnInit` subscription + its
    // `markForCheck()` can repaint this.
    nodes$.next([makeNode('a-mgr')]);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('a [Mgr]');
  });
});
