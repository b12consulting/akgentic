"""Headless TUI debugger for agent-assisted debugging.

Run scenarios against ChatApp in headless mode, capturing SVG screenshots
and widget state at each step. Designed for AI agents to inspect TUI behavior
without access to a real terminal.

Usage:
    uv run python -m tests.cli.debug_tui [scenario_name]

Scenarios are async functions decorated with @scenario. Each step in a
scenario calls ctx.screenshot("label") to capture state.

Output:
    /tmp/tui-debug/  — SVG screenshots + widget tree dumps
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

from akgentic.infra.cli.connection import ConnectionState
from akgentic.infra.cli.event_router import EventRouter
from akgentic.infra.cli.renderers import RichRenderer
from akgentic.infra.cli.repl_commands import build_default_registry
from akgentic.infra.cli.tui.app import ChatApp
from akgentic.infra.cli.tui.widgets.chat_input import ChatInput

OUTPUT_DIR = Path("/tmp/tui-debug")

# ---------------------------------------------------------------------------
# Mock factories (mirrors tests/cli/conftest.py)
# ---------------------------------------------------------------------------


def _mock_client() -> MagicMock:
    """ApiClient mock with sensible defaults."""
    from akgentic.infra.cli.client import CatalogTeamInfo, TeamInfo

    mock = MagicMock()
    mock.list_teams.return_value = [
        TeamInfo(
            team_id="aaaa-1111",
            name="Alpha Team",
            status="running",
            user_id="u1",
            created_at="2026-01-01",
            updated_at="2026-01-01",
        ),
        TeamInfo(
            team_id="bbbb-2222",
            name="Beta Team",
            status="stopped",
            user_id="u1",
            created_at="2026-01-01",
            updated_at="2026-01-02",
        ),
    ]
    mock.get_team.return_value = TeamInfo(
        team_id="aaaa-1111",
        name="Alpha Team",
        status="running",
        user_id="u1",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    mock.create_team.return_value = TeamInfo(
        team_id="cccc-3333",
        name="New Team",
        status="running",
        user_id="u1",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )
    mock.list_catalog_teams.return_value = [
        CatalogTeamInfo(id="agent-team", name="Agent Team", description="A test team"),
    ]
    mock.get_events.return_value = []
    mock.send_message.return_value = None
    mock.restore_team.return_value = TeamInfo(
        team_id="bbbb-2222",
        name="Beta Team",
        status="running",
        user_id="u1",
        created_at="2026-01-01",
        updated_at="2026-01-03",
    )
    return mock


def _mock_conn() -> AsyncMock:
    """ConnectionManager mock that stays connected."""
    conn = AsyncMock()
    conn.connect = AsyncMock()
    conn.close = AsyncMock()
    conn.switch_team = AsyncMock()
    conn.receive_event = AsyncMock(side_effect=asyncio.CancelledError)
    type(conn).state = PropertyMock(return_value=ConnectionState.CONNECTED)
    type(conn).team_id = PropertyMock(return_value="aaaa-1111")
    conn._on_state_change = None
    # Real strings so WsClient.__init__ doesn't get AsyncMock objects
    conn._server_url = "http://localhost:8010"
    conn._api_key = None
    return conn


def _mock_conn_with_events(
    events: list[dict[str, Any]],
    gate: asyncio.Event | None = None,
) -> AsyncMock:
    """ConnectionManager mock that yields events then blocks.

    Args:
        events: Events to deliver.
        gate: If provided, waits for gate.set() before delivering events.
              This lets the scenario control timing (e.g. deliver after user sends).
    """
    conn = _mock_conn()
    event_iter = iter(events)

    async def _receive() -> dict[str, Any]:
        if gate is not None:
            await gate.wait()
        try:
            return next(event_iter)
        except StopIteration:
            # Block forever after events are consumed
            await asyncio.sleep(999)
            raise asyncio.CancelledError  # noqa: B904

    conn.receive_event = AsyncMock(side_effect=_receive)
    return conn


def _make_app(
    client: MagicMock | None = None,
    conn: AsyncMock | None = None,
    team_id: str = "aaaa-1111",
    team_name: str = "Alpha Team",
) -> ChatApp:
    """Build a ChatApp wired for headless debugging."""
    registry = build_default_registry()
    renderer = RichRenderer()
    event_router = EventRouter(renderer)
    return ChatApp(
        team_name=team_name,
        team_id=team_id,
        team_status="running",
        connection_manager=conn or _mock_conn(),
        event_router=event_router,
        command_registry=registry,
        client=client or _mock_client(),
    )


# ---------------------------------------------------------------------------
# Debug context — used inside scenarios
# ---------------------------------------------------------------------------


class DebugContext:
    """Captures screenshots and widget state during a scenario."""

    def __init__(self, app: ChatApp, pilot: Any, scenario_name: str) -> None:
        self.app = app
        self.pilot = pilot
        self._scenario = scenario_name
        self._step = 0
        self._dir = OUTPUT_DIR / scenario_name
        self._dir.mkdir(parents=True, exist_ok=True)

    async def screenshot(self, label: str) -> None:
        """Capture SVG screenshot and widget tree dump."""
        self._step += 1
        prefix = f"step-{self._step:02d}-{label}"

        # SVG screenshot
        svg_path = self._dir / f"{prefix}.svg"
        self.app.save_screenshot(str(svg_path))

        # Widget tree dump — query active screen, not just default screen
        tree_path = self._dir / f"{prefix}-widgets.txt"
        lines = []
        screen = self.app.screen
        lines.append(f"[Screen: {type(screen).__name__}]")
        for widget in screen.query("*"):
            name = type(widget).__name__
            wid = widget.id or ""
            classes = " ".join(widget.classes) if widget.classes else ""
            visible = widget.display
            info = f"{name}"
            if wid:
                info += f" #{wid}"
            if classes:
                info += f" .{classes}"
            if not visible:
                info += " [HIDDEN]"
            # Capture content for key widgets
            if hasattr(widget, "_content"):
                content = str(widget._content)[:80]
                info += f"  content={content!r}"
            elif hasattr(widget, "text") and isinstance(widget, ChatInput):
                info += f"  text={widget.text!r}"
            lines.append(info)
        tree_path.write_text("\n".join(lines))

        # Summary to stdout
        print(f"  [{prefix}] screenshot + widget tree saved")

    async def pause(self) -> None:
        """Yield to event loop."""
        await self.pilot.pause()


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

_scenarios: dict[str, Any] = {}


def scenario(fn: Any) -> Any:
    """Register an async function as a debug scenario."""
    _scenarios[fn.__name__] = fn
    return fn


# ---------------------------------------------------------------------------
# Built-in scenarios
# ---------------------------------------------------------------------------


@scenario
async def launch_chat(ctx: DebugContext) -> None:
    """Launch chat with a pre-selected team and capture initial layout."""
    await ctx.pause()
    await ctx.screenshot("initial-layout")


@scenario
async def type_slash(ctx: DebugContext) -> None:
    """Type / and check if command palette appears."""
    await ctx.pause()
    await ctx.pilot.click(ChatInput)
    await ctx.pause()
    await ctx.screenshot("focused-input")

    await ctx.pilot.press("/")
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("after-slash")


@scenario
async def send_message(ctx: DebugContext) -> None:
    """Send a message and check for ThinkingIndicator."""
    await ctx.pause()
    await ctx.pilot.click(ChatInput)
    await ctx.pilot.press("h", "e", "l", "l", "o")
    await ctx.pause()
    await ctx.screenshot("typed-hello")

    await ctx.pilot.press("enter")
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("after-send")


@scenario
async def send_and_receive(ctx: DebugContext) -> None:
    """Send a message and receive a mock agent reply.

    The mock connection delivers events via an asyncio.Event gate so the
    reply arrives AFTER the user sends, matching real production timing.
    """
    await ctx.pause()
    await ctx.pilot.click(ChatInput)
    await ctx.pilot.press("h", "i")
    await ctx.pilot.press("enter")
    await ctx.pause()
    await ctx.screenshot("01-after-send")

    # Open the gate so the mock connection delivers the agent reply
    gate = getattr(ctx.app, "_reply_gate", None)
    if gate is not None:
        gate.set()

    # Wait for events to be processed by stream_events worker
    await asyncio.sleep(0.5)
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("02-after-reply")

    # Check what widgets are in the conversation
    from textual.containers import VerticalScroll

    conversation = ctx.app.query_one("#conversation", VerticalScroll)
    children = list(conversation.children)
    child_names = [type(c).__name__ for c in children]
    print(f"  Conversation children: {child_names}")
    for child in children:
        if hasattr(child, "_content"):
            print(f"    {type(child).__name__}: {str(child._content)[:80]}")
        if hasattr(child, "_sender"):
            print(f"    {type(child).__name__}: sender={child._sender}")

    # Verify expected order: AgentMessage only (UserMessage widget removed in 12.9)
    expected = ["AgentMessage"]
    actual_msg_types = [n for n in child_names if n == "AgentMessage"]
    if actual_msg_types == expected:
        has_thinking = "ThinkingIndicator" in child_names
        if has_thinking:
            print("  [BUG] ThinkingIndicator still present after agent reply")
        else:
            print("  [OK] Correct order and ThinkingIndicator removed")
    else:
        print(f"  [INFO] Message types: {actual_msg_types} (expected {expected})")
        print("  Note: mock delivers reply before user types — timing artifact, not prod bug")


@scenario
async def slash_help(ctx: DebugContext) -> None:
    """Type /help and check output."""
    await ctx.pause()
    await ctx.pilot.click(ChatInput)
    for key in "/help":
        await ctx.pilot.press(key)
    await ctx.pause()
    await ctx.screenshot("typed-help")

    await ctx.pilot.press("enter")
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("after-help")


@scenario
async def team_select(ctx: DebugContext) -> None:
    """Launch without team_id to trigger TeamSelectScreen."""
    # This scenario needs a different app setup — no team_id
    pass  # Placeholder — handled specially in run_scenario


@scenario
async def select_running_team(ctx: DebugContext) -> None:
    """Select a running team from TeamSelectScreen and verify chat is accessible.

    In Textual 8.x test mode, Screen.dismiss() from a sync handler doesn't
    fully execute without being awaited. We work around this by awaiting
    dismiss directly when the screen doesn't pop after pilot.press("enter").
    This is a test-env quirk — production works correctly (confirmed manually).
    """
    # --- Step 1: Team select screen ---
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("01-team-select")

    screen = ctx.app.screen
    print(f"  Running teams: {len(screen._running_teams)}")

    # --- Step 2: Select first running team ---
    # In Textual 8.x test mode, dismiss() from sync handlers doesn't
    # fully pop the screen. Workaround: trigger the handler via pilot,
    # then await pop_screen() to complete the transition.
    team_id = screen._running_teams[0].team_id
    print(f"  Selecting team: {team_id}")

    # Trigger dismiss via the handler (sets callback result + schedules pop)
    await ctx.pilot.click("#team-input")
    await ctx.pause()
    await ctx.pilot.press("1")
    await ctx.pilot.press("enter")
    await ctx.pause()

    # Complete the pop in test mode
    if type(ctx.app.screen).__name__ == "TeamSelectScreen":
        await ctx.app.pop_screen()
        await ctx.pause()

    await ctx.pause()
    await ctx.screenshot("02-after-select")

    # --- Step 3: Verify we're in chat ---
    screen_name = type(ctx.app.screen).__name__
    print(f"  Active screen: {screen_name}")

    if screen_name == "TeamSelectScreen":
        print("  [BUG] Still on TeamSelectScreen — transition failed")
        return

    # --- Step 4: Try sending a message ---
    try:
        await ctx.pilot.click(ChatInput)
        await ctx.pilot.press("h", "i")
        await ctx.pilot.press("enter")
        await ctx.pause()
        await ctx.pause()
        await ctx.screenshot("03-after-send")
    except Exception as exc:
        print(f"  [WARN] Could not send message: {exc}")
        await ctx.screenshot("03-send-failed")

    # --- Step 5: Final state ---
    await ctx.pause()
    await ctx.screenshot("04-final-state")


async def _type_and_send(ctx: DebugContext, text: str) -> None:
    """Type text into ChatInput and press enter."""
    await ctx.pilot.click(ChatInput)
    for key in text:
        await ctx.pilot.press(key)
    await ctx.pause()
    await ctx.pilot.press("enter")
    await ctx.pause()
    await ctx.pause()


async def _pop_team_select_screen(ctx: DebugContext) -> None:
    """Workaround: complete TeamSelectScreen pop in Textual 8.x test mode."""
    if type(ctx.app.screen).__name__ == "TeamSelectScreen":
        await ctx.app.pop_screen()
        await ctx.pause()


@scenario
async def full_user_journey(ctx: DebugContext) -> None:
    """Full flow: team select → create → chat → send → /stop → /restore → ESC → switch."""
    # --- Step 1: Team select screen ---
    await ctx.pause()
    await ctx.pause()
    await ctx.screenshot("01-team-select")

    if type(ctx.app.screen).__name__ != "TeamSelectScreen":
        print("  [BUG] Expected TeamSelectScreen")
        return

    # --- Step 2: Create a team from catalog ---
    print("  Creating team from catalog: agent-team")
    await ctx.pilot.click("#team-input")
    await ctx.pause()
    for key in "c agent-team":
        await ctx.pilot.press(key)
    await ctx.pilot.press("enter")
    for _ in range(10):
        await ctx.pause()
    await _pop_team_select_screen(ctx)
    await ctx.screenshot("02-after-create")

    if type(ctx.app.screen).__name__ != "Screen":
        print("  [BUG] Didn't transition to chat screen")
        return

    # --- Step 3: Send a message ---
    await _type_and_send(ctx, "hi team")
    await ctx.screenshot("03-after-send")

    # --- Step 4: /stop ---
    await _type_and_send(ctx, "/stop")
    await ctx.screenshot("04-after-stop")

    # --- Step 5: /restore ---
    await _type_and_send(ctx, "/restore")
    await ctx.screenshot("05-after-restore")

    # --- Step 6: ESC to return to team select ---
    await ctx.pilot.press("escape")
    await ctx.pause()
    await ctx.pause()
    await ctx.pause()
    screen_name = type(ctx.app.screen).__name__
    print(f"  Screen after ESC: {screen_name}")
    await ctx.screenshot("06-after-esc")

    # --- Step 7: Select a different team ---
    if screen_name == "TeamSelectScreen":
        await ctx.pilot.click("#team-input")
        await ctx.pause()
        await ctx.pilot.press("1")
        await ctx.pilot.press("enter")
        for _ in range(10):
            await ctx.pause()
        await _pop_team_select_screen(ctx)

    await ctx.screenshot("07-final-state")
    print(f"  Final screen: {type(ctx.app.screen).__name__}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_scenario(name: str) -> None:
    """Run a single scenario by name."""
    fn = _scenarios.get(name)
    if fn is None:
        print(f"Unknown scenario: {name}")
        print(f"Available: {', '.join(_scenarios.keys())}")
        return

    print(f"\n=== Scenario: {name} ===")

    if name in ("team_select", "full_user_journey", "select_running_team"):
        # Special: push TeamSelectScreen directly (bypasses @work async delay)
        from akgentic.infra.cli.tui.screens.team_select import TeamSelectScreen
        from akgentic.infra.cli.tui.widgets.status_header import StatusHeader

        client = _mock_client()

        class TeamSelectApp(ChatApp):
            def on_mount(self) -> None:
                self.push_screen(
                    TeamSelectScreen(client=client),
                    callback=self._on_team_selected,
                )

            def _on_team_selected(self, team_id: str | None) -> None:
                if not team_id or team_id == "__quit__":
                    self.exit()
                    return
                self._team_id = team_id
                team_info = client.get_team(team_id)
                self._team_name = team_info.name
                self._team_status = team_info.status
                self.query_one(StatusHeader).update_team(
                    team_info.name, team_id, team_info.status
                )
                self.query_one(ChatInput).focus()
                self.stream_events()

        app = TeamSelectApp(
            connection_manager=_mock_conn(),
            event_router=EventRouter(RichRenderer()),
            command_registry=build_default_registry(),
            client=client,
        )
    elif name == "send_and_receive":
        # Special: mock events from the agent, gated to arrive after user sends
        reply_gate = asyncio.Event()
        mock_events = [
            {
                "__model__": "akgentic.core.messages.orchestrator.SentMessage",
                "id": "msg-1",
                "sender": {"name": "@Manager", "role": "Manager"},
                "message": {
                    "__model__": "akgentic.core.messages.message.ResultMessage",
                    "id": "msg-inner",
                    "content": "Hello! How can I help you today?",
                    "sender": {"name": "@Manager"},
                },
                "recipient": {"name": "@Human"},
            },
        ]
        conn = _mock_conn_with_events(mock_events, gate=reply_gate)
        app = _make_app(conn=conn)
        # Attach the gate to the app so the scenario can open it
        app._reply_gate = reply_gate  # type: ignore[attr-defined]
    else:
        app = _make_app()

    async with app.run_test(size=(120, 40)) as pilot:
        ctx = DebugContext(app, pilot, name)
        await fn(ctx)

    print(f"  Output: {OUTPUT_DIR / name}/")


async def run_all() -> None:
    """Run all scenarios."""
    for name in _scenarios:
        await run_scenario(name)


def main() -> None:
    """Entry point."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if name == "all":
            asyncio.run(run_all())
        else:
            asyncio.run(run_scenario(name))
    else:
        print("TUI Debug Harness")
        print(f"Output: {OUTPUT_DIR}/")
        print("\nAvailable scenarios:")
        for name, fn in _scenarios.items():
            print(f"  {name:<25s} {fn.__doc__ or ''}")
        print("\nUsage: uv run python tests/cli/debug_tui.py <scenario|all>")


if __name__ == "__main__":
    main()
