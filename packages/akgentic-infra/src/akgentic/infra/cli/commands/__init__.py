"""Top-level Typer commands for the ``akgentic`` CLI.

Houses ``login`` and ``logout`` (Story 21.4) and future top-level commands
(e.g. ``profile list``). The legacy REPL in-session slash-command registry
lives in :mod:`akgentic.infra.cli.repl_commands` (renamed from
``commands.py`` in Task 0, Option A — see that module's docstring).

The command modules register themselves on a caller-supplied Typer ``app``
via ``register(app)`` helpers rather than import-time side effects — explicit
is test-friendly and keeps the Typer app a single source of truth.

ADR references (navigation aids, not runtime invariants):

* ADR-021 §Decision 2 — ``akgentic login`` / ``akgentic logout`` scope and
  retry-once inline auto-auth.
"""
