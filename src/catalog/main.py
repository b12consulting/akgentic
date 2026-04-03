"""Catalog + Team Manager Integration Example

Demonstrates how to combine akgentic-catalog and akgentic-team to go from
YAML catalog definitions to a fully managed team with full lifecycle support
(create, stop, resume, delete) and event-sourced persistence.

Pipeline:
    1. Load catalogs from YAML files (akgentic-catalog)
    2. Resolve TeamEntry → TeamCard via to_team_card() (catalog → team bridge)
    3. Create and manage the team via TeamManager (akgentic-team)
    4. Interactive command loop: start, stop, restore, delete, and chat

Usage:
    python src/catalog/main.py
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from pathlib import Path

import logfire
from akgentic.catalog.cli._catalog import build_catalogs
from akgentic.core import ActorSystem, EventSubscriber
from akgentic.core.messages import Message
from akgentic.core.messages.orchestrator import EventMessage, SentMessage
from akgentic.llm import ToolCallEvent
from akgentic.team import TeamManager, YamlEventStore
from akgentic.team.models import TeamStatus

logging.basicConfig(level=logging.ERROR, format="%(name)s - %(levelname)s - %(message)s")

CATALOG_DIR = Path(__file__).parent.parent / "catalog"
DATA_DIR = Path("./data/catalog-team")


class MessagePrinter(EventSubscriber):
    """Prints messages as they flow through the orchestrator."""

    def on_stop(self) -> None:
        pass

    def on_message(self, message: Message) -> None:
        assert message.sender is not None
        sender = message.sender.name

        if isinstance(message, SentMessage):
            msg = message.message
            team_id = message.team_id
            recipient = message.recipient.name
            if hasattr(msg, "content"):
                content = getattr(msg, "content", "")
                print("-" * 100)
                header = f"[{team_id}] [{sender}] -> {msg.__class__.__name__} ({recipient})"
                print(f"{header}: \n{content}\n")

        if isinstance(message, EventMessage) and isinstance(message.event, ToolCallEvent):
            event = message.event
            team_id = message.team_id
            print("-" * 100)
            print(f"[{team_id}] [{sender}] -> {event.tool_name} - {event.arguments}\n")


def main() -> None:
    """Run the catalog + team manager example."""

    ## The logfire traces will now appear in the Logfire web UI as long as
    ## you have the LOGFIRE_TOKEN environment variable set.
    logfire.configure(console=False)

    # --- Load catalogs from YAML ---
    template_catalog, tool_catalog, agent_catalog, team_catalog = build_catalogs(CATALOG_DIR)

    # --- Create infrastructure ---
    actor_system = ActorSystem()
    event_store = YamlEventStore(DATA_DIR)

    team_manager = TeamManager(
        actor_system=actor_system, 
        event_store=event_store, 
        subscribers=[MessagePrinter()],
    )

    # --- State: active runtime (if any) ---
    runtime = None
    supervisor = None

    # --- Signal handling ---
    shutdown_event = threading.Event()

    def _sigint_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        shutdown_event.set()

    signal.signal(signal.SIGINT, _sigint_handler)

    # --- Commands ---
    def cmd_catalog() -> None:
        """List all team definitions in the catalog."""
        entries = team_catalog.list()
        if not entries:
            print("No teams in catalog.")
            return
        print("Available teams in catalog:")
        for entry in entries:
            print(f"  - {entry.id}: {entry.name}")
        print()

    def cmd_start(team_name: str) -> None:
        """Start a team from catalog by name."""
        nonlocal runtime, supervisor
        if runtime is not None:
            print(f"Team already running: {runtime.id}")
            print("Stop it first with /stop\n")
            return
        team_entry = team_catalog.get(team_name)
        if team_entry is None:
            print(f"Team '{team_name}' not found in catalog. Use /catalog to list.\n")
            return
        team_card = team_entry.to_team_card(agent_catalog, tool_catalog, template_catalog)
        runtime = team_manager.create_team(team_card)
        time.sleep(0.3)
        supervisor = next(iter(runtime.supervisor_proxies.values()))  # type: ignore
        print(f"Team '{team_card.name}' started (id={runtime.id})")
        print()
        print(supervisor.cmd_get_team_roster())
        print()

    def cmd_stop() -> None:
        """Stop the active team."""
        nonlocal runtime, supervisor
        if runtime is None:
            print("No team running.\n")
            return
        team_id = runtime.id
        team_manager.stop_team(team_id)
        print(f"Team stopped and persisted: {team_id}")
        print(f"Data saved to: {DATA_DIR}\n")
        runtime = None
        supervisor = None

    def cmd_list_stopped() -> None:
        """List all stopped teams."""
        processes = event_store.list_teams()
        stopped = [p for p in processes if p.status == TeamStatus.STOPPED]
        if not stopped:
            print("No stopped teams.\n")
            return
        print("Stopped teams:")
        for p in stopped:
            print(f"  - {p.team_id}: {p.team_card.name} (stopped)")
        print()

    def cmd_delete(team_id_str: str) -> None:
        """Delete a stopped team by ID, removing all persisted data."""
        nonlocal runtime, supervisor
        import uuid

        try:
            team_id = uuid.UUID(team_id_str)
        except ValueError:
            print(f"Invalid team ID: {team_id_str}\n")
            return
        process = team_manager.get_team(team_id)
        if process is None:
            print(f"Team '{team_id_str}' not found. Use /stopped to list.\n")
            return
        if runtime is not None and runtime.id == team_id:
            print("Cannot delete the running team. Use /stop first.\n")
            return
        if process.status != TeamStatus.STOPPED:
            print(f"Team '{team_id_str}' is {process.status}, not stopped.\n")
            return
        team_manager.delete_team(team_id)
        print(f"Team '{team_id_str}' deleted.\n")

    def cmd_restore(team_id_str: str) -> None:
        """Restore a stopped team by ID."""
        nonlocal runtime, supervisor
        if runtime is not None:
            print(f"Team already running: {runtime.id}")
            print("Stop it first with /stop\n")
            return
        import uuid

        try:
            team_id = uuid.UUID(team_id_str)
        except ValueError:
            print(f"Invalid team ID: {team_id_str}\n")
            return
        process = team_manager.get_team(team_id)
        if process is None:
            print(f"Team '{team_id_str}' not found. Use /stopped to list.\n")
            return
        if process.status != TeamStatus.STOPPED:
            print(f"Team '{team_id_str}' is {process.status}, not stopped.\n")
            return
        runtime = team_manager.resume_team(team_id)
        time.sleep(0.3)
        supervisor = next(iter(runtime.supervisor_proxies.values()))  # type: ignore
        print(f"Team '{runtime.team.name}' restored (id={runtime.id})")
        print()
        print(supervisor.cmd_get_team_roster())
        print()

    def print_help() -> None:
        print("Available commands:")
        print("  /catalog             - List all team definitions in the catalog")
        print("  /start <catalog-id>  - Start a team from catalog")
        print("  /stop                - Stop the active team")
        print("  /stopped             - List all stopped teams")
        print("  /restore <team-id>   - Restore a stopped team")
        print("  /delete <team-id>    - Delete a stopped team")
        print("  /exit                - Exit")
        print("  /help                - Show this help message")
        print()

    # --- Main loop ---
    print_help()
    print("-" * 100)

    while not shutdown_event.is_set():
        try:
            user_input = input("")
        except EOFError:
            break
        print()

        stripped = user_input.strip()
        if stripped.lower() in ("exit", "/exit"):
            break

        if stripped == "":
            continue

        if stripped.startswith("/"):
            parts = stripped.split(" ", 1)
            command = parts[0][1:]
            arg = parts[1].strip() if len(parts) > 1 else ""

            if command == "catalog":
                cmd_catalog()
            elif command == "start" and arg:
                cmd_start(arg)
            elif command == "stop":
                cmd_stop()
            elif command == "stopped":
                cmd_list_stopped()
            elif command == "delete" and arg:
                cmd_delete(arg)
            elif command == "restore" and arg:
                cmd_restore(arg)
            elif command == "help":
                print_help()
            else:
                print_help()
            continue

        # --- Chat requires a running team ---
        if runtime is None or supervisor is None:
            print("No team running. Use /catalog and /start <team-id> first.\n")
            continue

        if stripped.startswith("@"):
            target_name = stripped.split(" ")[0]
            try:
                runtime.send_to(target_name, stripped)
            except ValueError:
                print(f"Error: Agent {target_name} not found")
            continue

        runtime.send(stripped)

    # --- Graceful shutdown ---
    if runtime is not None:
        print("\nStopping team...")
        team_manager.stop_team(runtime.id)
        print(f"Team stopped and persisted: {runtime.id}")
        print(f"Data saved to: {DATA_DIR}")


if __name__ == "__main__":
    main()
