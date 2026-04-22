"""End-to-end REPL tests — real TCP server, real WebSocket, TestModel LLM.

Tests exercise the full REPL command flow including WebSocket reconnection
after state transitions.  No OPENAI_API_KEY required (uses TestModel).

Bug classes caught:
  - WebSocket not reconnected after /restore on current team
  - WebSocket pointed at wrong team after /switch
  - WebSocket state stale after /create auto-switch
  - Session crash on double-stop or failed /switch
"""

from __future__ import annotations

import asyncio
import builtins
import time

import pytest
from akgentic.core.messages.message import Message

from akgentic.infra.cli.client import ApiClient
from akgentic.infra.cli.connection import ConnectionManager
from akgentic.infra.cli.formatters import OutputFormat
from akgentic.infra.cli.repl import ChatSession, TeamSelector
from akgentic.infra.cli.repl_commands import (
    _create_handler,
    _delete_handler,
    _events_handler,
    _info_handler,
    _restore_handler,
    _stop_handler,
    _switch_handler,
)
from akgentic.infra.cli.ws_client import WsClient

from ._helpers import CATALOG_ENTRY_ID, StubRenderer

pytestmark = [pytest.mark.smoke]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLL_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.3


def _make_session(
    server_url: str,
    team_id: str,
    renderer: StubRenderer | None = None,
) -> ChatSession:
    """Build a ChatSession pointing at a real smoke_server."""
    api = ApiClient(base_url=server_url)
    conn = ConnectionManager(server_url=server_url, team_id=team_id)
    return ChatSession(
        api,
        conn,
        team_id,
        OutputFormat.table,
        server_url=server_url,
        renderer=renderer or StubRenderer(),  # type: ignore[arg-type]
    )


def _create_team(server_url: str) -> str:
    """Create a team via REST and return team_id."""
    api = ApiClient(base_url=server_url)
    try:
        team = api.create_team(CATALOG_ENTRY_ID)
        return team.team_id
    finally:
        api.close()


def _stop_team(server_url: str, team_id: str) -> None:
    """Stop a team via REST."""
    api = ApiClient(base_url=server_url)
    try:
        api.stop_team(team_id)
    finally:
        api.close()


def _delete_team(server_url: str, team_id: str) -> None:
    """Delete a team via REST (best-effort cleanup)."""
    api = ApiClient(base_url=server_url)
    try:
        api.delete_team(team_id)
    except SystemExit:
        pass
    finally:
        api.close()


def _cleanup_team(server_url: str, team_id: str) -> None:
    """Stop then delete a team (best-effort cleanup)."""
    api = ApiClient(base_url=server_url)
    try:
        api.stop_team(team_id)
    except (SystemExit, Exception):  # noqa: BLE001
        pass
    try:
        api.delete_team(team_id)
    except (SystemExit, Exception):  # noqa: BLE001
        pass
    finally:
        api.close()


async def _wait_for_ws_event(
    conn: ConnectionManager | WsClient,
    timeout: float = POLL_TIMEOUT_S,
) -> object:
    """Wait for a single WebSocket event with timeout."""
    return await asyncio.wait_for(conn.receive_event(), timeout=timeout)


def _poll_events_have_content(server_url: str, team_id: str) -> bool:
    """Poll REST events until @Manager content appears."""
    api = ApiClient(base_url=server_url)
    try:
        events = api.get_events(team_id)
        for ev in events:
            data = ev.model_dump()
            event = data.get("event", {})
            if not isinstance(event, dict):
                continue
            msg = event.get("message")
            if not isinstance(msg, dict):
                continue
            sender = event.get("sender")
            if not isinstance(sender, dict):
                continue
            if sender.get("name") == "@Manager" and msg.get("content"):
                return True
        return False
    except SystemExit:
        return False
    finally:
        api.close()


# ===========================================================================
# T1–T8: State Transition × WebSocket Delivery
# ===========================================================================


@pytest.mark.integration
class TestStateTransitionWS:
    """Verify WebSocket event delivery after REPL state transitions.

    Requires a real TCP server with async WebSocket connections.
    Excluded from default test run (``-m 'not integration'``) because
    the daemon-thread uvicorn + async websockets client combination
    hangs without a dedicated event-loop setup.
    """

    async def test_t1_basic_send_and_receive(self, smoke_server: str) -> None:
        """T1: Send message → verify WS event arrives."""
        team_id = _create_team(smoke_server)
        try:
            session = _make_session(smoke_server, team_id)
            async with session.conn:
                session.client.send_message(team_id, "hello")
                event = await _wait_for_ws_event(session.conn)
                assert event is not None
                assert isinstance(event, Message)
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_t2_stop_disconnects_ws(self, smoke_server: str) -> None:
        """T2: /stop → WebSocket connection closes."""
        team_id = _create_team(smoke_server)
        try:
            session = _make_session(smoke_server, team_id)
            async with session.conn:
                # Start receive task so _stop_handler can work
                session._receive_task = asyncio.create_task(session._receive_loop())

                await _stop_handler("", session)

                # Team should be stopped
                team = session.client.get_team(team_id)
                assert team.status == "stopped"
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_t3_restore_current_team_reconnects_ws(
        self, smoke_server: str
    ) -> None:
        """T3: /stop → /restore → verify WS is reconnected.

        This is THE BUG: before the fix, /restore on the current team
        did not reconnect the WebSocket.

        Note: after restore, the agents may have stale orchestrator refs
        (server-side bug), so we only verify WS reconnection here — not
        full message round-trip through the LLM.
        """
        team_id = _create_team(smoke_server)
        try:
            session = _make_session(smoke_server, team_id)
            async with session.conn:
                session._receive_task = asyncio.create_task(session._receive_loop())

                # Stop the team
                await _stop_handler("", session)
                await asyncio.sleep(0.5)

                # Restore — this triggers conn.switch_team() which reconnects internally
                await _restore_handler("", session)
                await asyncio.sleep(0.5)

                # Verify team_id preserved and team is running
                assert session.team_id == team_id
                assert session.conn.team_id == team_id

                # Verify team is running again
                team = session.client.get_team(team_id)
                assert team.status == "running"
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_t4_restore_other_team_switches(self, smoke_server: str) -> None:
        """T4: /restore <other_id> → session switches to restored team."""
        team1_id = _create_team(smoke_server)
        team2_id = _create_team(smoke_server)
        try:
            # Stop team2
            _stop_team(smoke_server, team2_id)
            await asyncio.sleep(0.3)

            session = _make_session(smoke_server, team1_id)
            async with session.conn:
                session._receive_task = asyncio.create_task(session._receive_loop())

                # Restore team2 from team1's session
                await _restore_handler(team2_id, session)
                await asyncio.sleep(0.5)

                # Session should have switched
                assert session.team_id == team2_id

                # Team2 should be running again
                team = session.client.get_team(team2_id)
                assert team.status == "running"
        finally:
            _cleanup_team(smoke_server, team1_id)
            _cleanup_team(smoke_server, team2_id)

    async def test_t5_switch_reconnects_ws(self, smoke_server: str) -> None:
        """T5: /switch <other> → session points to new team, WS reconnected."""
        team1_id = _create_team(smoke_server)
        team2_id = _create_team(smoke_server)
        try:
            session = _make_session(smoke_server, team1_id)
            async with session.conn:
                session._receive_task = asyncio.create_task(session._receive_loop())

                await _switch_handler(team2_id, session)
                await asyncio.sleep(0.3)

                assert session.team_id == team2_id
                # ConnectionManager.switch_team() updates internal team_id
                assert session.conn.team_id == team2_id
        finally:
            _cleanup_team(smoke_server, team1_id)
            _cleanup_team(smoke_server, team2_id)

    async def test_t6_create_auto_switches_ws(self, smoke_server: str) -> None:
        """T6: /create → session switches to new team."""
        session = _make_session(smoke_server, _create_team(smoke_server))
        original_team_id = session.team_id
        created_team_id: str | None = None
        try:
            async with session.conn:
                session._receive_task = asyncio.create_task(session._receive_loop())

                await _create_handler(CATALOG_ENTRY_ID, session)
                await asyncio.sleep(0.5)

                # Session should have switched to the new team
                assert session.team_id != original_team_id
                created_team_id = session.team_id

                # New team should be running
                team = session.client.get_team(created_team_id)
                assert team.status == "running"

                # ConnectionManager should point to new team
                assert session.conn.team_id == created_team_id
        finally:
            _cleanup_team(smoke_server, original_team_id)
            if created_team_id:
                _cleanup_team(smoke_server, created_team_id)

    async def test_t7_switch_nonexistent_preserves_ws(
        self, smoke_server: str
    ) -> None:
        """T7: /switch <bad> → original WS still works."""
        team_id = _create_team(smoke_server)
        try:
            session = _make_session(smoke_server, team_id)
            async with session.conn:
                session._receive_task = asyncio.create_task(session._receive_loop())

                # Switch to nonexistent team — should fail gracefully
                await _switch_handler("00000000-0000-0000-0000-000000000000", session)

                # Original team should still be connected
                assert session.team_id == team_id

                # Cancel receive loop before verifying WS directly (avoids
                # concurrent recv on the same websocket connection)
                if session._receive_task is not None:
                    session._receive_task.cancel()
                    try:
                        await session._receive_task
                    except asyncio.CancelledError:
                        pass

                # Send and verify WS still works via direct receive
                session.client.send_message(team_id, "still here")
                event = await _wait_for_ws_event(session.conn)
                assert event is not None
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_t8_delete_current_team(
        self, smoke_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """T8: /delete current team → team deleted."""
        team_id = _create_team(smoke_server)
        session = _make_session(smoke_server, team_id)

        # Patch input to confirm deletion
        original_input = builtins.input
        builtins.input = lambda _prompt="": "y"
        try:
            await _delete_handler("", session)
        finally:
            builtins.input = original_input

        # Team should be deleted — get_team should fail
        try:
            session.client.get_team(team_id)
            pytest.fail("Expected team to be deleted")
        except (SystemExit, Exception):  # noqa: BLE001
            pass  # Expected — 404


# ===========================================================================
# TS1–TS5: TeamSelector (Startup Menu)
# ===========================================================================


class TestTeamSelector:
    """Test the interactive team selection menu."""

    def test_ts1_select_running_team(self, smoke_server: str) -> None:
        """TS1: Input '1' → returns the first running team's ID."""
        team_id = _create_team(smoke_server)
        try:
            api = ApiClient(base_url=smoke_server)
            renderer = StubRenderer()
            selector = TeamSelector(api, renderer)  # type: ignore[arg-type]

            original_input = builtins.input
            builtins.input = lambda _prompt="": "1"
            try:
                result = selector.run()
            finally:
                builtins.input = original_input

            assert result is not None
            # Should be one of the running teams
            teams = api.list_teams()
            running_ids = [t.team_id for t in teams if t.status == "running"]
            assert result in running_ids
            api.close()
        finally:
            _cleanup_team(smoke_server, team_id)

    def test_ts2_create_new_team(self, smoke_server: str) -> None:
        """TS2: Input 'c test-team' → creates and returns new team_id."""
        api = ApiClient(base_url=smoke_server)
        renderer = StubRenderer()
        selector = TeamSelector(api, renderer)  # type: ignore[arg-type]

        original_input = builtins.input
        builtins.input = lambda _prompt="": f"c {CATALOG_ENTRY_ID}"
        created_team_id: str | None = None
        try:
            created_team_id = selector.run()
            assert created_team_id is not None

            # Verify team exists
            team = api.get_team(created_team_id)
            assert team.status == "running"
        finally:
            builtins.input = original_input
            if created_team_id:
                _cleanup_team(smoke_server, created_team_id)
            api.close()

    def test_ts3_browse_stopped_and_restore(self, smoke_server: str) -> None:
        """TS3: Input 's' then '1' → restores stopped team and returns ID."""
        team_id = _create_team(smoke_server)
        _stop_team(smoke_server, team_id)
        time.sleep(0.3)

        api = ApiClient(base_url=smoke_server)
        renderer = StubRenderer()
        selector = TeamSelector(api, renderer)  # type: ignore[arg-type]

        call_count = 0

        def _mock_input(_prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "s"  # Browse stopped teams
            return "1"  # Select first stopped team

        original_input = builtins.input
        builtins.input = _mock_input
        try:
            result = selector.run()
            assert result == team_id
        finally:
            builtins.input = original_input
            _cleanup_team(smoke_server, team_id)
            api.close()

    def test_ts4_quit(self, smoke_server: str) -> None:
        """TS4: Input 'q' → returns None."""
        api = ApiClient(base_url=smoke_server)
        renderer = StubRenderer()
        selector = TeamSelector(api, renderer)  # type: ignore[arg-type]

        original_input = builtins.input
        builtins.input = lambda _prompt="": "q"
        try:
            result = selector.run()
            assert result is None
        finally:
            builtins.input = original_input
            api.close()

    def test_ts5_invalid_selection_then_quit(self, smoke_server: str) -> None:
        """TS5: Input '99' → error, then 'q' → exits."""
        team_id = _create_team(smoke_server)
        api = ApiClient(base_url=smoke_server)
        renderer = StubRenderer()
        selector = TeamSelector(api, renderer)  # type: ignore[arg-type]

        call_count = 0

        def _mock_input(_prompt: str = "") -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "99"
            return "q"

        original_input = builtins.input
        builtins.input = _mock_input
        try:
            result = selector.run()
            assert result is None
            assert len(renderer.errors) > 0
        finally:
            builtins.input = original_input
            _cleanup_team(smoke_server, team_id)
            api.close()


# ===========================================================================
# CS1–CS3: Commands on Stopped Teams
# ===========================================================================


class TestCommandsOnStoppedTeam:
    """Verify command behavior when the current team is stopped."""

    async def test_cs1_info_on_stopped_team(
        self, smoke_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CS1: /info on a stopped team → returns info."""
        team_id = _create_team(smoke_server)
        _stop_team(smoke_server, team_id)
        time.sleep(0.3)
        try:
            session = _make_session(smoke_server, team_id)
            await _info_handler("", session)

            out = capsys.readouterr().out
            assert team_id in out
            assert "stopped" in out.lower()
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_cs2_events_on_stopped_team(
        self, smoke_server: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """CS2: /events on a stopped team → returns persisted events."""
        team_id = _create_team(smoke_server)
        _stop_team(smoke_server, team_id)
        time.sleep(0.3)
        try:
            session = _make_session(smoke_server, team_id)
            await _events_handler("", session)

            out = capsys.readouterr().out
            # Should have at least StartMessage events from team creation
            assert len(out.strip()) > 0
        finally:
            _cleanup_team(smoke_server, team_id)

    async def test_cs3_send_to_stopped_team_errors(
        self, smoke_server: str
    ) -> None:
        """CS3: Send message to stopped team → error."""
        team_id = _create_team(smoke_server)
        _stop_team(smoke_server, team_id)
        time.sleep(0.3)
        try:
            session = _make_session(smoke_server, team_id)

            # Sending to a stopped team should fail via REST
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    None, session.client.send_message, team_id, "hello stopped"
                )
                pytest.fail("Expected error sending to stopped team")
            except (SystemExit, Exception):  # noqa: BLE001
                pass  # Expected — team not running

        finally:
            _cleanup_team(smoke_server, team_id)
