import yaml from 'js-yaml';

/**
 * Thrown by {@link rewriteNamespaceInYaml} when the input YAML cannot be
 * parsed, the root is not a mapping, the root lacks a `namespace` key, or
 * the caller passed an empty `destNs`. The panel's Clone handler catches
 * this class BEFORE any HTTP status branch and surfaces `err.message` in an
 * error toast — the import request never fires.
 *
 * See Story 11.5 AC 6, AC 11 for the error contract.
 */
export class CloneYamlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CloneYamlError';
  }
}

/**
 * Rewrite the document-level `namespace` field of a catalog bundle YAML
 * string, returning the re-dumped YAML with every other field unchanged.
 *
 * The v2 bundle wire format is a single root mapping:
 *
 * ```
 * namespace: src          # ← document-level, ONE key
 * user_id: null
 * entries:
 *   team-1: { kind: team, parent_namespace: src-parent, payload: {...} }
 *   agent-1: { kind: agent, ... }
 * ```
 *
 * The helper touches EXACTLY one key: the root `namespace`. It does NOT walk
 * `entries`. It does NOT touch per-entry `parent_namespace` fields (those
 * are lineage pointers — "which namespace did my ancestor live in?" — not
 * the entry's current namespace; rewriting them would corrupt ADR-006
 * parent_namespace semantics). It does NOT touch any `payload` sub-tree
 * (payloads may legitimately contain the source namespace name as a string
 * literal inside `description` / `config.name` / arbitrary user-supplied
 * fields — plain-text find-and-replace would corrupt those, per ADR-011 D5
 * step 3).
 *
 * Dump options:
 *   - `sortKeys: false` — preserves root-key order (namespace → user_id → entries).
 *   - `lineWidth: -1` — disables line wrapping so our emit matches the
 *     server's PyYAML emit cosmetically (no spurious `\n`s).
 *   - `noRefs: true` — suppresses `&anchor` / `*alias` emission on repeated
 *     payload sub-objects (the server's emit does not use anchors).
 *
 * On a successful clone import the panel re-exports `destNs` (Story 11.5
 * AC 12), so the operator ultimately sees the server's canonical emit, not
 * this helper's dump. Round-trip fidelity here just needs to be good enough
 * for the import to succeed — minor cosmetic differences (key ordering
 * inside payload, block-scalar quoting) are invisible to the user after
 * re-export.
 */
export function rewriteNamespaceInYaml(
  input: string,
  destNs: string,
  destName?: string,
): string {
  // Defence-in-depth: the panel's Confirm-button disabled rule prevents
  // empty destNs from ever reaching the helper, but mirror Story 11.3's
  // `savedBuffer` pattern and validate the caller-supplied string.
  if (typeof destNs !== 'string' || destNs.length === 0) {
    throw new CloneYamlError('destNs must be a non-empty string');
  }
  if (destName !== undefined && (typeof destName !== 'string' || destName.length === 0)) {
    throw new CloneYamlError('destName must be a non-empty string when provided');
  }

  let parsed: unknown;
  try {
    // `yaml.load` with the default schema is a safe loader in js-yaml v4 —
    // no arbitrary-class instantiation. Leaving the schema implicit matches
    // the rest of the codebase's js-yaml usage (see ApiService if applicable
    // — here the helper stands alone).
    parsed = yaml.load(input);
  } catch (err) {
    const underlying = err instanceof Error ? err.message : String(err);
    throw new CloneYamlError(`yaml-parse-error: ${underlying}`);
  }

  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new CloneYamlError('bundle root is not a mapping');
  }

  const doc = parsed as Record<string, unknown>;
  if (typeof doc['namespace'] !== 'string') {
    throw new CloneYamlError('bundle root has no `namespace` key');
  }

  // Rewrite only the document-level namespace key — entries and payload
  // subtrees are untouched (ADR-011 D5 step 3; see docstring above).
  doc['namespace'] = destNs;

  // Optionally rewrite the document-level `name` key (the meta header's
  // display name surfaced in the home-page namespace dropdown). The export
  // serializer hoists `meta.payload.name` to a root `name:` field, so the
  // rewrite happens at the same level as `namespace`.
  if (destName !== undefined) {
    doc['name'] = destName;
  }

  return yaml.dump(doc, { sortKeys: false, lineWidth: -1, noRefs: true });
}

/**
 * Best-effort extraction of the top-level `namespace:` field from a bundle
 * YAML string. Returns `null` when the input cannot be parsed, the root
 * is not a mapping, or the `namespace` key is missing / not a string.
 *
 * Used by `NamespacePanelComponent.onSaveClick` to guard against saving a
 * buffer whose namespace has been edited away from the panel's namespace
 * — the import endpoint treats the YAML's namespace as authoritative, so
 * a naive save would silently create or overwrite a DIFFERENT namespace.
 * Returning `null` on parse failure intentionally defers the decision to
 * the server (which will reject with 422), so a malformed buffer does
 * not get double-reported — operator sees one clear error from the
 * server path rather than a speculative frontend one.
 */
export function extractYamlNamespace(input: string): string | null {
  let parsed: unknown;
  try {
    parsed = yaml.load(input);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return null;
  }
  const ns = (parsed as Record<string, unknown>)['namespace'];
  return typeof ns === 'string' ? ns : null;
}

/**
 * Best-effort extraction of the top-level `name:` field (the meta header's
 * display name) from a bundle YAML string. Returns `null` when the input
 * cannot be parsed, the root is not a mapping, or the `name` key is missing
 * or not a string. Used by the Clone modal to pre-fill the destination-name
 * input with a "<source>_copy" suggestion.
 */
export function extractYamlName(input: string): string | null {
  let parsed: unknown;
  try {
    parsed = yaml.load(input);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return null;
  }
  const name = (parsed as Record<string, unknown>)['name'];
  return typeof name === 'string' ? name : null;
}

const RANDOM_SUFFIX_RE = /_[A-Za-z0-9]{5}$/;
const RANDOM_SUFFIX_ALPHABET =
  'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';

function randomAlphanumeric(length: number): string {
  // crypto.getRandomValues is available in every browser Angular targets and
  // in jsdom (Karma test env). Avoids Math.random's predictability without
  // pulling in a dependency.
  const out: string[] = [];
  const buf = new Uint32Array(length);
  crypto.getRandomValues(buf);
  for (let i = 0; i < length; i++) {
    out.push(RANDOM_SUFFIX_ALPHABET[buf[i] % RANDOM_SUFFIX_ALPHABET.length]);
  }
  return out.join('');
}

/**
 * Suggest a destination namespace for a clone: strip a trailing
 * `_<5 alphanumerics>` suffix from `srcNs` if one is present, then append a
 * freshly-generated `_<5 alphanumerics>` suffix. So `foo_a1b2c` becomes
 * `foo_x9k2m`, and `foo` becomes `foo_x9k2m`. The collision check in the
 * Clone modal still applies, so the operator can re-roll by reopening the
 * dialog if the suggestion happens to clash with an existing namespace.
 */
export function suggestDestNamespace(srcNs: string): string {
  const base = srcNs.replace(RANDOM_SUFFIX_RE, '');
  return `${base}_${randomAlphanumeric(5)}`;
}

/**
 * Suggest a destination display name for a clone: append `_copy` to the
 * source name. Returns `'_copy'` for the empty string (rare — the meta
 * header is required) so the suggestion is always non-empty.
 */
export function suggestDestName(srcName: string): string {
  return `${srcName}_copy`;
}
