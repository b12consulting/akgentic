# Archived v1 Examples (historical reference only)

> **Do not use these examples as a learning resource for current
> `akgentic-catalog`.** They were authored against the v1 catalog
> surface (`TemplateCatalog`, `ToolCatalog`, `AgentCatalog`,
> `TeamCatalog` and their per-kind `*Entry` / `*Query` models) which
> was superseded by the v2 unified `Catalog` + `Entry` design
> (ADR-009, Epic 15, Epic 19).

The scripts and markdown in this directory reference v1 types and
APIs that no longer exist in the public surface. They are preserved
as a historical record of the v1 usage patterns for readers comparing
the before/after shapes. Running any `.py` file in this directory
will raise `ImportError`.

## For up-to-date authoring guidance

- Main README: [../README.md](../README.md) — v2 Quick Start and
  architecture overview.
- CLI guide: [../docs/cli-usage-guide.md](../docs/cli-usage-guide.md) —
  `ak-catalog <kind> <verb>` reference.
- Architecture shards (v2 design):
  - `_bmad-output/akgentic-catalog/architecture/09-yaml-examples.md` —
    v2 YAML authoring examples (namespace bundles).
  - `_bmad-output/akgentic-catalog/architecture/10-package-structure.md` —
    the final `src/akgentic/catalog/` module layout.
- ADRs documenting the v1 → v2 transition:
  - `_bmad-output/akgentic-catalog/decisions/adr-06-*.md`
  - `_bmad-output/akgentic-catalog/decisions/adr-07-*.md`
  - `_bmad-output/akgentic-catalog/decisions/adr-08-*.md`
  - `_bmad-output/akgentic-catalog/decisions/adr-09-catalog-v2-big-bang-rewrite.md`

A v2 examples rewrite is future work and is not tracked by Epic 19.
