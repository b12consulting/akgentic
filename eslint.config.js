// Minimal, boundary-only ESLint flat config (ESLint 9).
//
// SCOPE (Epic 18 / ADR-015 §7 — story 18-6): this config enforces EXACTLY ONE
// rule — the one-way import DAG between the frontend's architectural layers. It
// deliberately does NOT adopt @angular-eslint, @typescript-eslint recommended,
// stylistic, formatting, or type-aware rules; those would flag the pre-existing,
// untouched codebase, which is out of scope for this story.
//
// Mechanism: eslint-plugin-boundaries tags each file by its folder ("element
// type") and enforces allow/deny edges BETWEEN element types regardless of the
// relative-import depth. This is the robust encoding for this codebase, which
// uses relative imports with no tsconfig path aliases.
//
// The allow list below mirrors ADR-015 §7 verbatim:
//
//   App level:
//     protocol        -> nothing app-internal
//     shared          -> protocol
//     core            -> protocol, shared
//     page-*          -> core, shared, protocol, feature-catalog
//                       (NO sibling-page imports, EXCEPT the reusable
//                        feature-catalog dialog which pages may embed)
//     feature-catalog -> core, shared, protocol
//
//   Within process/ (top consumes down):
//     proc-components -> proc-ui-state, proc-selectors, proc-event,
//                        proc-workspace, proc-models, core, shared, protocol
//     proc-ui-state   -> proc-selectors, proc-event, proc-models, core, protocol
//     proc-selectors  -> proc-event, proc-models, core, shared, protocol
//     proc-workspace  -> core, protocol
//     proc-event      -> proc-models, core, protocol
//     proc-models     -> protocol
//
//   Acyclic chain:
//     components -> ui-state -> selectors -> event -> core -> { shared, protocol }
//
// Verification is behavioural (story AC #4/#5): `npm run lint` exits 0 on the
// migrated tree and non-zero on a planted cross-layer import. There are NO
// string-presence assertions on ADR numbers, file paths, or folder names
// (CLAUDE.md Golden Rule #8).

const tseslint = require('typescript-eslint');
const boundaries = require('eslint-plugin-boundaries');

module.exports = tseslint.config(
  // Only application source is gated; spec files and everything outside src/ are
  // exempt (tests routinely reach across layers to construct fixtures).
  {
    files: ['src/**/*.ts'],
    ignores: ['src/**/*.spec.ts'],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        // Boundaries works off the module graph only; no type-aware program needed.
        project: false,
      },
    },
    plugins: {
      boundaries,
    },
    settings: {
      // Resolve extension-less relative imports (e.g. '../selectors/chat.selector')
      // to their .ts files so the boundaries plugin can tag each dependency by
      // element type. Without .ts in the resolver extensions, every cross-layer
      // import resolves to "unknown" and the DAG rule silently never fires.
      'import/resolver': {
        node: { extensions: ['.ts', '.js', '.json'] },
      },
      // Order matters: the most specific process/* patterns MUST precede the
      // generic page pattern so a process leaf is not mis-tagged as a page.
      // Files matching no pattern (e.g. the app.*.ts composition root) are
      // "unknown" and intentionally unrestricted — app.routes.ts is the router
      // that legitimately wires every page together.
      'boundaries/elements': [
        // --- App-level layers ---
        { type: 'protocol', pattern: 'src/app/protocol' },
        { type: 'shared', pattern: 'src/app/shared' },
        { type: 'core', pattern: 'src/app/core' },

        // --- process/ internal layers (more specific than the page pattern) ---
        // Files directly under process/ (the ProcessComponent page root, e.g.
        // process.component.ts) are the process feature's presentation tier and
        // are tagged proc-components — they compose ui-state/selectors/event/
        // workspace exactly like the nested presentation components do (ADR-015
        // §7 "components/ (presentation)"). The nested process/components/ folder
        // shares the same element type.
        { type: 'proc-components', pattern: 'src/app/components/process/components' },
        { type: 'proc-event', pattern: 'src/app/components/process/event' },
        { type: 'proc-selectors', pattern: 'src/app/components/process/selectors' },
        { type: 'proc-ui-state', pattern: 'src/app/components/process/ui-state' },
        { type: 'proc-workspace', pattern: 'src/app/components/process/workspace' },
        { type: 'proc-models', pattern: 'src/app/components/process/models' },
        { type: 'proc-components', pattern: 'src/app/components/process' },

        // --- catalog is a REUSABLE feature, not a leaf page ---
        // Its namespace-panel dialog is intentionally embedded by other pages
        // (the home page hosts the namespace editor — Epic 11/12 reuse), so
        // catalog gets its own element type that pages are allowed to depend on.
        // This is NOT a general page->page edge: feature-catalog is the only
        // page-level element other pages may import (ADR-015 §7).
        { type: 'feature-catalog', pattern: 'src/app/components/catalog' },

        // --- Pages: one element type per page; each captures its page folder ---
        // Listing distinct types (page-home, page-login, ...) means page->page
        // edges are simply absent from the allow list and therefore forbidden.
        { type: 'page-home', pattern: 'src/app/components/home' },
        { type: 'page-login', pattern: 'src/app/components/login' },
      ],
    },
    rules: {
      // The single enforced rule: the import DAG. Everything not listed below is
      // forbidden (default: disallow). Uses the v6 object-based selector syntax.
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          rules: [
            // protocol imports nothing app-internal.
            { from: { type: 'protocol' }, disallow: { to: { type: '*' } } },

            // shared -> protocol
            { from: { type: 'shared' }, allow: { to: { type: ['protocol'] } } },

            // core -> protocol, shared
            { from: { type: 'core' }, allow: { to: { type: ['protocol', 'shared'] } } },

            // Pages -> core, shared, protocol; NO sibling-page imports — EXCEPT
            // the reusable feature-catalog dialog, which pages may embed.
            {
              from: { type: ['page-home', 'page-login'] },
              allow: { to: { type: ['core', 'shared', 'protocol', 'feature-catalog'] } },
            },

            // catalog feature -> core, shared, protocol (its own intra-feature
            // imports — e.g. namespace-panel -> validation-report — are same-type
            // and allowed by default).
            {
              from: { type: 'feature-catalog' },
              allow: { to: { type: ['core', 'shared', 'protocol'] } },
            },

            // process/components (presentation) -> all process peers it consumes
            // plus the app-level leaves it may read.
            {
              from: { type: 'proc-components' },
              allow: {
                to: {
                  type: [
                    // intra-tier: the ProcessComponent page root composes its
                    // child presentation components, and sibling presentation
                    // components compose each other (e.g. team-tabs -> graph).
                    'proc-components',
                    'proc-ui-state',
                    'proc-selectors',
                    'proc-event',
                    'proc-workspace',
                    'proc-models',
                    'core',
                    'shared',
                    'protocol',
                  ],
                },
              },
            },

            // ui-state -> selectors, event, core (+ models, protocol)
            {
              from: { type: 'proc-ui-state' },
              allow: {
                to: { type: ['proc-selectors', 'proc-event', 'proc-models', 'core', 'protocol'] },
              },
            },

            // selectors -> event, core, protocol (+ models, shared);
            // MUST NOT import ui-state or process/components.
            {
              from: { type: 'proc-selectors' },
              allow: {
                to: { type: ['proc-event', 'proc-models', 'core', 'shared', 'protocol'] },
              },
            },

            // workspace -> core, protocol
            {
              from: { type: 'proc-workspace' },
              allow: { to: { type: ['core', 'protocol'] } },
            },

            // event -> core, protocol (+ models);
            // MUST NOT import selectors, ui-state, or process/components.
            {
              from: { type: 'proc-event' },
              allow: { to: { type: ['proc-models', 'core', 'protocol'] } },
            },

            // process/models -> protocol only.
            {
              from: { type: 'proc-models' },
              allow: { to: { type: ['protocol'] } },
            },
          ],
        },
      ],
    },
  },
);
