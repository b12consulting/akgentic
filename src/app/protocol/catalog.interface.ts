/**
 * Catalog data models — map to catalog-service DTOs.
 */

/**
 * Flat summary of a catalog namespace, returned by `GET /catalog/namespaces`.
 *
 * Maps to the catalog backend's `NamespaceSummary` DTO (catalog Story 16.6):
 * a purpose-built, picker-friendly shape — no `Entry` envelope, no payload,
 * no user/parent/model metadata.
 */
export interface NamespaceSummary {
  namespace: string;
  name: string;
  description: string;
}

/**
 * Catalog v1 team entry — `GET /admin/catalog/teams` (enterprise tier, which
 * has no namespaces). The wire object carries more (members, agent_profiles,
 * entry_point, …); only the fields the home "team type" dropdown needs are
 * typed here. A team is created from its `id` (sent as `catalog_entry_id`).
 */
export interface CatalogTeamEntry {
  id: string;
  name: string;
  description: string;
}

/**
 * The five catalog entry kinds.
 *
 * Mirrors the server-side `EntryKind` literal at
 * `packages/akgentic-catalog/src/akgentic/catalog/models/entry.py`:
 * `Literal["team", "agent", "tool", "model", "prompt"]`.
 *
 * `team`, `agent`, `tool` keep v1 semantics; `model` and `prompt` are new in
 * v2 (promoted to first-class so they can be referenced via the ref-sentinel
 * mechanism).
 */
export type EntryKind = 'team' | 'agent' | 'tool' | 'model' | 'prompt';

/**
 * Unified v2 catalog entry — mirrors the server's Pydantic `Entry` model at
 * `packages/akgentic-catalog/src/akgentic/catalog/models/entry.py`.
 *
 * Field names are translated verbatim (snake_case), matching the wire shape
 * — no camelCase rewriting.
 *
 */
export interface Entry {
  id: string;
  kind: EntryKind;
  namespace: string;
  user_id: string;
  model_type: string;
  description: string;
  payload: Record<string, unknown>;
}

/**
 * Per-entry validation issue — mirrors the server's `EntryValidationIssue`
 * at `packages/akgentic-catalog/src/akgentic/catalog/validation.py`.
 */
export interface EntryValidationIssue {
  entry_id: string;
  kind: EntryKind;
  errors: string[];
}

/**
 * Namespace-level validation report — mirrors the server's
 * `NamespaceValidationReport` at
 * `packages/akgentic-catalog/src/akgentic/catalog/validation.py`.
 *
 * `ok` is a derived invariant on the server: true iff `global_errors` is
 * empty AND every `entry_issues[].errors` list is empty. Clients can branch
 * on `ok` alone — no need to re-check the two lists.
 *
 * `namespace` is nullable because the server returns `null` when the bundle
 * is empty (no entries to derive a namespace from).
 */
export interface NamespaceValidationReport {
  namespace: string | null;
  ok: boolean;
  global_errors: string[];
  entry_issues: EntryValidationIssue[];
}
