"""Smoke tests for Task 0 (Option A) — ``commands`` rename to ``repl_commands``.

Ensures:

* The new subpackage :mod:`akgentic.infra.cli.commands` imports cleanly and
  exposes the ``login`` / ``logout`` modules.
* The legacy REPL slash-command registry is reachable under its new name
  :mod:`akgentic.infra.cli.repl_commands`.
* Importing the top-level Typer app does not raise.
"""

from __future__ import annotations


def test_new_commands_subpackage_imports() -> None:
    from akgentic.infra.cli import commands as commands_pkg
    from akgentic.infra.cli.commands import login as login_mod
    from akgentic.infra.cli.commands import logout as logout_mod

    # The subpackage must be a package (has __path__) rather than a module.
    assert hasattr(commands_pkg, "__path__")
    assert hasattr(login_mod, "register")
    assert hasattr(logout_mod, "register")


def test_legacy_repl_commands_reachable_under_new_name() -> None:
    from akgentic.infra.cli.repl_commands import (
        CommandRegistry,
        SlashCommand,
        build_default_registry,
    )

    registry = build_default_registry()
    assert isinstance(registry, CommandRegistry)
    # The registry exposes the expected built-in commands.
    assert "help" in registry.commands
    assert isinstance(registry.commands["help"], SlashCommand)


def test_top_level_cli_app_imports() -> None:
    from akgentic.infra.cli.main import app

    # Top-level Typer app — simply importing should not raise.
    assert app is not None
    # login / logout are registered as commands on the top-level app.
    registered = {cmd.name for cmd in app.registered_commands}
    assert "login" in registered
    assert "logout" in registered
