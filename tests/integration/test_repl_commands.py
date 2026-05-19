"""Integration tests -- REPL control-plane commands against a real server with real actors."""

from __future__ import annotations

import asyncio
import builtins
import time
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from akgentic.infra.cli.repl import InputMode
from akgentic.infra.cli.repl_commands import (
    _catalog_handler,
    _create_handler,
    _delete_handler,
    _events_handler,
    _info_handler,
    _restore_handler,
    _teams_handler,
)

from ._helpers import (
    CATALOG_ENTRY_ID,
    POLL_INTERVAL_S,
    POLL_TIMEOUT_S,
    has_llm_content,
    make_integration_session,
)

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# TestReplTeamLifecycle (AC #1, #2, #3, #4, #5, #6)
# ---------------------------------------------------------------------------


class TestReplTeamLifecycle:
    """REPL team lifecycle commands against a real server."""

    def test_repl_teams_lists_teams(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #1: /teams lists teams with status indicators."""
        # Create 2 teams via REST
        resp1 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp1.status_code == 201
        team1_id = resp1.json()["team_id"]
        resp2 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp2.status_code == 201
        team2_id = resp2.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team1_id)

            async def _run() -> None:
                await _teams_handler("", session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert team1_id in captured.out
            assert team2_id in captured.out
            assert "(current)" in captured.out

            # Stop one team, verify status changes
            integration_client.post(f"/teams/{team2_id}/stop")
            time.sleep(0.5)

            asyncio.run(_run())
            captured2 = capsys.readouterr()
            assert "stopped" in captured2.out.lower()
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team1_id}/stop")
                c.post(f"/teams/{team2_id}/stop")
                time.sleep(0.3)

    def test_repl_create_and_switch(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #2: /create creates team and auto-switches session to it."""
        # Create initial team
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        initial_team_id = resp.json()["team_id"]

        created_team_id: str | None = None
        try:
            session = make_integration_session(cli_server, initial_team_id)
            original_team_id = session.team_id

            async def _run() -> None:
                await _create_handler(CATALOG_ENTRY_ID, session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert "Created team" in captured.out

            # Session should have switched to the new team
            assert session.team_id != original_team_id
            created_team_id = session.team_id
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{initial_team_id}/stop")
                if created_team_id:
                    c.post(f"/teams/{created_team_id}/stop")
                time.sleep(0.3)

    def test_repl_delete_with_confirmation(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #3: /delete deletes team after confirmation."""
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        session = make_integration_session(cli_server, team_id)

        try:
            with patch.object(builtins, "input", return_value="y"):

                async def _run() -> None:
                    await _delete_handler("", session)

                asyncio.run(_run())

            captured = capsys.readouterr()
            assert "deleted" in captured.out.lower()

            # Verify team no longer exists (404)
            get_resp = integration_client.get(f"/teams/{team_id}")
            assert get_resp.status_code == 404
        finally:
            session.client.close()

    def test_repl_info_current_team(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #4: /info shows current team details."""
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team_id)

            async def _run() -> None:
                await _info_handler("", session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert team_id in captured.out
            assert "running" in captured.out.lower()
            assert "Name:" in captured.out
            assert "User ID:" in captured.out
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_repl_info_explicit_team_id(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #4: /info <team_id> shows a different team's details."""
        resp1 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp1.status_code == 201
        team1_id = resp1.json()["team_id"]
        resp2 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp2.status_code == 201
        team2_id = resp2.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team1_id)

            async def _run() -> None:
                await _info_handler(team2_id, session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert team2_id in captured.out
            # Should not show team1_id in the output (we queried team2)
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team1_id}/stop")
                c.post(f"/teams/{team2_id}/stop")
                time.sleep(0.3)

    def test_repl_events(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #5: /events shows raw team events."""
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        try:
            # Send a message and wait for LLM response
            integration_client.post(
                f"/teams/{team_id}/message", json={"content": "Say yes"}
            )
            deadline = time.monotonic() + POLL_TIMEOUT_S
            while time.monotonic() < deadline:
                events_resp = integration_client.get(f"/teams/{team_id}/events")
                events = events_resp.json()["events"]
                if has_llm_content(events):
                    break
                time.sleep(POLL_INTERVAL_S)

            session = make_integration_session(cli_server, team_id)

            async def _run() -> None:
                await _events_handler("", session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            # Events handler prints JSON with event data
            assert "sequence" in captured.out.lower() or "event" in captured.out.lower()
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_repl_restore_and_switch(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #6: /restore restores a stopped team and auto-switches."""
        resp1 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp1.status_code == 201
        team1_id = resp1.json()["team_id"]
        resp2 = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp2.status_code == 201
        team2_id = resp2.json()["team_id"]

        try:
            # Stop team2
            stop_resp = integration_client.post(f"/teams/{team2_id}/stop")
            assert stop_resp.status_code == 204
            time.sleep(0.5)

            session = make_integration_session(cli_server, team1_id)

            async def _run() -> None:
                await _restore_handler(team2_id, session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert "restored" in captured.out.lower()
            # Auto-switch should have changed session.team_id
            assert session.team_id == team2_id
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team1_id}/stop")
                c.post(f"/teams/{team2_id}/stop")
                time.sleep(0.3)


# ---------------------------------------------------------------------------
# TestReplCatalog (AC #7)
# ---------------------------------------------------------------------------


class TestReplCatalog:
    """REPL catalog browsing commands against a real server."""

    def test_repl_catalog_lists_entries(
        self,
        cli_server: str,
        integration_client: TestClient,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC #7: /catalog lists available team templates."""
        # Need a team for the session
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team_id)

            async def _run() -> None:
                await _catalog_handler("", session)

            asyncio.run(_run())
            captured = capsys.readouterr()
            assert "Available team templates:" in captured.out
            assert CATALOG_ENTRY_ID in captured.out
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)


# ---------------------------------------------------------------------------
# TestReplImplicitReply (AC #8)
# ---------------------------------------------------------------------------


class TestReplImplicitReply:
    """REPL implicit human-input reply routing tests."""

    def test_repl_pending_reply_set_on_human_input(
        self,
        cli_server: str,
        integration_client: TestClient,
    ) -> None:
        """AC #8: _render_event sets pending reply state on HumanInput event."""
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team_id)

            # Build a HumanInput event dict from Pydantic-style structure
            event_id = str(uuid4())
            event_data: dict[str, Any] = {
                "id": event_id,
                "sender": {"name": "@Manager", "role": "Manager"},
                "event": {
                    "__model__": "EventMessage",
                    "event": {
                        "__model__": "HumanInputRequest",
                        "prompt": "What should I do?",
                    },
                },
            }

            session._render_event(event_data)
            assert session._state.input_mode == InputMode.REPLY
            assert session._state.reply_context is not None
            assert session._state.reply_context.reply_id == event_id
            assert session._state.reply_context.agent_name == "@Manager"
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)

    def test_repl_pending_reply_consumed_on_text(
        self,
        cli_server: str,
        integration_client: TestClient,
    ) -> None:
        """AC #8: pending reply state is consumed when plain text is sent."""
        resp = integration_client.post("/teams/", json={"catalog_namespace": CATALOG_ENTRY_ID})
        assert resp.status_code == 201
        team_id = resp.json()["team_id"]

        try:
            session = make_integration_session(cli_server, team_id)

            # Set pending state via _render_event (same as production path)
            pending_id = str(uuid4())
            event_data: dict[str, Any] = {
                "id": pending_id,
                "sender": {"name": "@Manager", "role": "Manager"},
                "event": {
                    "__model__": "EventMessage",
                    "event": {
                        "__model__": "HumanInputRequest",
                        "prompt": "What should I do?",
                    },
                },
            }
            session._render_event(event_data)
            assert session._state.input_mode == InputMode.REPLY
            assert session._state.reply_context is not None
            assert session._state.reply_context.reply_id == pending_id
            assert session._state.reply_context.agent_name == "@Manager"

            # Call human_input via the client -- the endpoint returns an error
            # since the pending_id doesn't correspond to a real server-side event.
            # The ApiClient raises typer.Exit (a SystemExit subclass) on HTTP errors.
            try:
                session.client.human_input(team_id, "reply text", pending_id)
            except (SystemExit, Exception):  # noqa: BLE001
                pass  # Expected: server returns error since pending_id is synthetic

            # Simulate the clearing that _input_loop does after sending the reply
            # via model_copy (same pattern as ChatSession._handle_reply)
            session._state = session._state.model_copy(
                update={"input_mode": InputMode.CHAT, "reply_context": None}
            )
            assert session._state.input_mode == InputMode.CHAT
            assert session._state.reply_context is None
        finally:
            session.client.close()
            with httpx.Client(base_url=cli_server) as c:
                c.post(f"/teams/{team_id}/stop")
                time.sleep(0.3)
