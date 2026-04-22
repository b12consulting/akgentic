"""Integration tests — channel spec compliance: form-encoded webhook payloads.

Validates ADR-002 remediation for story 6.1 channel subsystem fixes.

Note: TestMultiAdapterDispatch and TestChannelConfigPassthrough were reclassified
as unit tests and moved to tests/adapters/shared/ and tests/adapters/community/ (story 9.4).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.llm]


# ---------------------------------------------------------------------------
# AC #3 — Form-encoded webhook payloads
# ---------------------------------------------------------------------------


class TestFormEncodedWebhook:
    """Verify form-encoded webhook payloads are parsed correctly."""

    def test_form_encoded_webhook_returns_204(
        self,
        channel_client: TestClient,
    ) -> None:
        """AC #3: POST form-encoded to /webhook/{channel} returns 204.

        Uses an existing team via reply flow to avoid creating a new team
        with in-flight LLM calls that would block teardown.
        """
        # Create a team first so we can use reply flow (no LLM initiation)
        create_resp = channel_client.post(
            "/teams/",
            json={"catalog_entry_id": "test-team"},
        )
        assert create_resp.status_code == 201
        team_id = create_resp.json()["team_id"]

        try:
            resp = channel_client.post(
                "/webhook/test-channel",
                data={
                    "content": "form test message",
                    "channel_user_id": "form-user-1",
                    "team_id": team_id,
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            assert resp.status_code == 204
        finally:
            # Stop team to allow clean teardown
            channel_client.post(f"/teams/{team_id}/stop")
            time.sleep(0.5)
