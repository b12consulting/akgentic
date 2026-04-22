"""Integration tests — interaction channel flows with real LLM."""

from __future__ import annotations

import time
import uuid

import pytest
from akgentic.core.messages import SentMessage
from fastapi.testclient import TestClient

from akgentic.infra.adapters.community.yaml_channel_registry import (
    YamlChannelRegistry,
)
from akgentic.infra.protocols.channels import ChannelMessage, JsonValue

from ._helpers import (
    POLL_INTERVAL_S,
    POLL_TIMEOUT_S,
    has_llm_content,
    wait_for_llm_response,
)

pytestmark = [pytest.mark.integration, pytest.mark.llm]

FOLLOWUP_CONTENT = "What is 7 + 7? Answer with the number."


# ---------------------------------------------------------------------------
# Test stubs — structural subtyping against channel protocols
# ---------------------------------------------------------------------------


class StubChannelParser:
    """Stub ChannelParser for integration tests."""

    @property
    def channel_name(self) -> str:
        return "test-channel"

    @property
    def default_catalog_entry(self) -> str:
        return "test-team"

    async def parse(
        self,
        payload: dict[str, JsonValue],
    ) -> ChannelMessage:
        """Parse test payload into ChannelMessage."""
        content = str(payload.get("content", ""))
        channel_user_id = str(payload.get("channel_user_id", ""))
        raw_team_id = payload.get("team_id")
        team_id = uuid.UUID(str(raw_team_id)) if raw_team_id is not None else None
        message_id = str(payload["message_id"]) if payload.get("message_id") else None
        return ChannelMessage(
            content=content,
            channel_user_id=channel_user_id,
            team_id=team_id,
            message_id=message_id,
        )


class StubChannelAdapter:
    """Stub InteractionChannelAdapter for integration tests."""

    def __init__(self) -> None:
        self.delivered: list[SentMessage] = []

    def matches(self, msg: SentMessage) -> bool:  # noqa: ARG002
        return True

    def deliver(self, msg: SentMessage) -> None:
        self.delivered.append(msg)

    def on_stop(self, team_id: uuid.UUID) -> None:  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_team_via_registry(
    registry: YamlChannelRegistry,
    channel: str,
    channel_user_id: str,
) -> uuid.UUID | None:
    """Synchronously look up a team from the YAML channel registry."""
    import asyncio

    return asyncio.run(registry.find_team(channel, channel_user_id))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChannelInitiation:
    """AC #1: Webhook with no existing team creates team."""

    def test_initiation_creates_team_and_gets_llm_response(
        self,
        channel_client: TestClient,
        channel_registry_instance: YamlChannelRegistry,
    ) -> None:
        payload = {
            "content": "What is 2 + 2? Answer with the number.",
            "channel_user_id": "ext-user-1",
        }
        resp = channel_client.post(
            "/webhook/test-channel",
            json=payload,
        )
        assert resp.status_code == 204

        team_id = _find_team_via_registry(
            channel_registry_instance,
            "test-channel",
            "ext-user-1",
        )
        assert team_id is not None, "Channel registry should map ext-user-1 after initiation"

        try:
            events = wait_for_llm_response(
                channel_client,
                str(team_id),
            )
            assert has_llm_content(events)
        finally:
            channel_client.post(f"/teams/{team_id}/stop")


class TestChannelReply:
    """AC #2: Webhook with team_id routes to the correct team."""

    def test_reply_routes_to_existing_team(
        self,
        channel_client: TestClient,
    ) -> None:
        create_resp = channel_client.post(
            "/teams/",
            json={"catalog_entry_id": "test-team"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["team_id"]

        try:
            resp = channel_client.post(
                "/webhook/test-channel",
                json={
                    "content": "What is 3 + 3? Answer with the number.",
                    "channel_user_id": "ext-user-2",
                    "team_id": team_id,
                },
            )
            assert resp.status_code == 204

            events = wait_for_llm_response(channel_client, team_id)
            assert has_llm_content(events)
        finally:
            channel_client.post(f"/teams/{team_id}/stop")


class TestChannelContinuation:
    """AC #3: Webhook with known channel_user_id resolves team."""

    def test_continuation_routes_to_same_team(
        self,
        channel_client: TestClient,
        channel_registry_instance: YamlChannelRegistry,
    ) -> None:
        # Step 1: Initiate via webhook to establish channel mapping
        payload = {
            "content": "Respond with one word.",
            "channel_user_id": "ext-user-cont",
        }
        resp = channel_client.post(
            "/webhook/test-channel",
            json=payload,
        )
        assert resp.status_code == 204

        team_id = _find_team_via_registry(
            channel_registry_instance,
            "test-channel",
            "ext-user-cont",
        )
        assert team_id is not None, "Channel registry should have mapping after initiation"

        try:
            wait_for_llm_response(channel_client, str(team_id))

            # Step 2: Follow-up with same channel_user_id, no team_id
            resp = channel_client.post(
                "/webhook/test-channel",
                json={
                    "content": FOLLOWUP_CONTENT,
                    "channel_user_id": "ext-user-cont",
                },
            )
            assert resp.status_code == 204

            # Verify follow-up is routed to the SAME team
            deadline = time.monotonic() + POLL_TIMEOUT_S
            found_followup = False
            while time.monotonic() < deadline:
                ev_resp = channel_client.get(f"/teams/{team_id}/events")
                assert ev_resp.status_code == 200
                events = ev_resp.json()["events"]
                for ev_wrapper in events:
                    ev = ev_wrapper["event"]
                    if not isinstance(ev, dict):
                        continue
                    content = ev.get("content")
                    if content == FOLLOWUP_CONTENT:
                        found_followup = True
                        break
                    msg = ev.get("message")
                    if isinstance(msg, dict) and msg.get("content") == FOLLOWUP_CONTENT:
                        found_followup = True
                        break
                if found_followup:
                    break
                time.sleep(POLL_INTERVAL_S)

            assert found_followup, (
                "Follow-up message should appear in the same team's "
                "events (continuation flow via channel_user_id)"
            )
        finally:
            channel_client.post(f"/teams/{team_id}/stop")


class TestDispatcherRestoreSuppression:
    """AC #4: Stop/restore preserves events without duplicating them."""

    def test_restore_cycle_preserves_events(
        self,
        channel_client: TestClient,
    ) -> None:
        create_resp = channel_client.post(
            "/teams/",
            json={"catalog_entry_id": "test-team"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["team_id"]

        try:
            resp = channel_client.get(f"/teams/{team_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            ev_resp = channel_client.get(f"/teams/{team_id}/events")
            assert ev_resp.status_code == 200
            events_before = len(ev_resp.json()["events"])

            resp = channel_client.post(f"/teams/{team_id}/stop")
            assert resp.status_code == 204
            resp = channel_client.get(f"/teams/{team_id}")
            assert resp.json()["status"] == "stopped"

            resp = channel_client.post(f"/teams/{team_id}/restore")
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"

            ev_resp = channel_client.get(f"/teams/{team_id}/events")
            assert ev_resp.status_code == 200
            events_after = ev_resp.json()["events"]
            assert len(events_after) == events_before
        finally:
            channel_client.post(f"/teams/{team_id}/stop")
