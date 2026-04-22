"""Tests for akgentic.infra.cli.client.ApiClient."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from akgentic.infra.cli.client import (
    ApiClient,
    ApiError,
    EventInfo,
    TeamInfo,
    WorkspaceTreeInfo,
    WorkspaceUploadInfo,
)
from tests.fixtures.models import make_event_info, make_team_info


def _transport(
    status: int = 200,
    json_body: dict[str, Any] | list[dict[str, Any]] | None = None,
    content: bytes = b"",
) -> httpx.MockTransport:
    """Build a MockTransport returning a fixed response."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json_body is not None:
            return httpx.Response(status, json=json_body)
        return httpx.Response(status, content=content)

    return httpx.MockTransport(handler)


def _client(
    transport: httpx.MockTransport | httpx.BaseTransport,
    api_key: str | None = None,
) -> ApiClient:
    """Build an ApiClient backed by a mock transport."""
    c = ApiClient(base_url="http://test", api_key=api_key)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    c._client = httpx.Client(base_url="http://test", transport=transport, headers=headers)
    return c


# -- team endpoints --


class TestListTeams:
    def test_returns_team_list(self) -> None:
        team = make_team_info(team_id="abc", name="t1")
        client = _client(_transport(json_body={"teams": [team]}))
        result = client.list_teams()
        assert len(result) == 1
        assert isinstance(result[0], TeamInfo)
        assert result[0].team_id == "abc"
        assert result[0].name == "t1"

    def test_empty_list(self) -> None:
        client = _client(_transport(json_body={"teams": []}))
        assert client.list_teams() == []


class TestGetTeam:
    def test_returns_team_info(self) -> None:
        team = make_team_info(team_id="abc", name="t1")
        client = _client(_transport(json_body=team))
        result = client.get_team("abc")
        assert isinstance(result, TeamInfo)
        assert result.team_id == "abc"
        assert result.name == "t1"


class TestCreateTeam:
    def test_sends_catalog_entry(self) -> None:
        team = make_team_info(team_id="abc")
        sent_bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent_bodies.append(json.loads(request.content))
            return httpx.Response(200, json=team)

        client = _client(httpx.MockTransport(handler))
        result = client.create_team("my-entry")
        assert isinstance(result, TeamInfo)
        assert result.team_id == "abc"
        assert sent_bodies[0] == {"catalog_entry_id": "my-entry"}


class TestStopTeam:
    def test_no_return(self) -> None:
        client = _client(_transport(status=204, content=b""))
        client.stop_team("abc")

    def test_sends_post_to_stop_endpoint(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(204, content=b"")

        client = _client(httpx.MockTransport(handler))
        client.stop_team("abc")
        assert len(requests) == 1
        assert requests[0].method == "POST"
        assert "/teams/abc/stop" in str(requests[0].url)


class TestDeleteTeam:
    def test_no_return(self) -> None:
        client = _client(_transport(status=204, content=b""))
        client.delete_team("abc")


class TestRestoreTeam:
    def test_returns_restored(self) -> None:
        team = make_team_info(team_id="abc", status="running")
        client = _client(_transport(json_body=team))
        result = client.restore_team("abc")
        assert isinstance(result, TeamInfo)
        assert result.status == "running"


class TestGetEvents:
    def test_returns_event_list(self) -> None:
        event = make_event_info(team_id="abc", sequence=1)
        client = _client(_transport(json_body={"events": [event]}))
        result = client.get_events("abc")
        assert len(result) == 1
        assert isinstance(result[0], EventInfo)
        assert result[0].sequence == 1


# -- messaging --


class TestSendMessage:
    def test_sends_content(self) -> None:
        sent: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={})

        client = _client(httpx.MockTransport(handler))
        client.send_message("team-1", "hello")
        assert sent[0] == {"content": "hello"}


class TestHumanInput:
    def test_sends_content_and_message_id(self) -> None:
        sent: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={})

        client = _client(httpx.MockTransport(handler))
        client.human_input("team-1", "yes", "msg-42")
        assert sent[0] == {"content": "yes", "message_id": "msg-42"}


# -- workspace --


class TestWorkspaceTree:
    def test_returns_tree(self) -> None:
        tree = {"team_id": "t1", "path": "/", "entries": []}
        client = _client(_transport(json_body=tree))
        result = client.workspace_tree("t1")
        assert isinstance(result, WorkspaceTreeInfo)
        assert result.team_id == "t1"
        assert result.entries == []


class TestWorkspaceRead:
    def test_returns_bytes(self) -> None:
        client = _client(_transport(content=b"file content here"))
        assert client.workspace_read("t1", "readme.md") == b"file content here"


class TestWorkspaceUpload:
    def test_sends_multipart(self) -> None:
        result_json = {"path": "readme.md", "size": 5}
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            urls.append(str(request.url))
            return httpx.Response(200, json=result_json)

        client = _client(httpx.MockTransport(handler))
        result = client.workspace_upload("t1", "readme.md", b"hello")
        assert isinstance(result, WorkspaceUploadInfo)
        assert result.path == "readme.md"
        assert result.size == 5
        assert "/workspace/t1/file" in urls[0]


# -- error handling --


class TestApiError:
    def test_404_not_retryable(self) -> None:
        err = ApiError(404, "not found")
        assert err.status_code == 404
        assert err.detail == "not found"
        assert str(err) == "HTTP 404: not found"
        assert err.retryable is False

    def test_500_retryable(self) -> None:
        err = ApiError(500, "internal")
        assert err.retryable is True

    def test_429_retryable(self) -> None:
        err = ApiError(429, "rate limited")
        assert err.retryable is True

    def test_0_retryable(self) -> None:
        err = ApiError(0, "timeout")
        assert err.retryable is True

    def test_400_not_retryable(self) -> None:
        err = ApiError(400, "bad request")
        assert err.retryable is False

    def test_empty_detail(self) -> None:
        err = ApiError(500, "")
        assert str(err) == "HTTP 500"

    def test_502_retryable(self) -> None:
        err = ApiError(502, "bad gateway")
        assert err.retryable is True

    def test_503_retryable(self) -> None:
        err = ApiError(503, "service unavailable")
        assert err.retryable is True


class TestErrorHandling:
    def test_404_raises_api_error(self) -> None:
        client = _client(_transport(status=404, json_body={"detail": "Not found"}))
        with pytest.raises(ApiError) as exc_info:
            client.get_team("missing")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Not found"

    def test_500_raises_api_error(self) -> None:
        client = _client(_transport(status=500, content=b"Internal error"))
        with pytest.raises(ApiError) as exc_info:
            client.list_teams()
        assert exc_info.value.status_code == 500

    def test_409_raises_api_error(self) -> None:
        client = _client(_transport(status=409, json_body={"detail": "Conflict"}))
        with pytest.raises(ApiError) as exc_info:
            client.delete_team("abc")
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "Conflict"

    def test_404_detail_in_error(self) -> None:
        client = _client(_transport(status=404, json_body={"detail": "Not found"}))
        with pytest.raises(ApiError) as exc_info:
            client.get_team("missing")
        assert "Not found" in exc_info.value.detail

    def test_timeout_raises_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out")

        client = _client(httpx.MockTransport(handler))
        with pytest.raises(ApiError) as exc_info:
            client.list_teams()
        assert exc_info.value.status_code == 0
        assert "timed out" in exc_info.value.detail

    def test_connect_error_raises_api_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        client = _client(httpx.MockTransport(handler))
        with pytest.raises(ApiError) as exc_info:
            client.list_teams()
        assert exc_info.value.status_code == 0
        assert "Connection failed" in exc_info.value.detail

    def test_successful_response_returns_normally(self) -> None:
        team = make_team_info(team_id="abc", name="t1")
        client = _client(_transport(json_body={"teams": [team]}))
        result = client.list_teams()
        assert len(result) == 1


class TestTimeoutConfiguration:
    def test_timeout_configured(self) -> None:
        client = ApiClient(base_url="http://test")
        timeout = client._client.timeout
        assert timeout == httpx.Timeout(30.0, connect=10.0)


class TestValidationErrors:
    def test_get_team_malformed_response(self) -> None:
        """Server returns JSON missing required fields → ValidationError."""
        client = _client(_transport(json_body={"team_id": "abc"}))
        with pytest.raises(ValidationError):
            client.get_team("abc")

    def test_list_teams_malformed_response(self) -> None:
        """Server returns teams with missing fields → ValidationError."""
        client = _client(_transport(json_body={"teams": [{"team_id": "abc"}]}))
        with pytest.raises(ValidationError):
            client.list_teams()

    def test_get_events_malformed_response(self) -> None:
        """Server returns events with missing fields → ValidationError."""
        client = _client(_transport(json_body={"events": [{"sequence": 1}]}))
        with pytest.raises(ValidationError):
            client.get_events("abc")


class TestApiKey:
    def test_api_key_header(self) -> None:
        headers: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            headers.update(dict(request.headers))
            return httpx.Response(200, json={"teams": []})

        client = _client(httpx.MockTransport(handler), api_key="secret-key")
        client.list_teams()
        assert headers.get("authorization") == "Bearer secret-key"


# -- ApiClient constructor modes (story 22.5) --


class TestApiClientConstruction:
    """Exercises the dual-mode constructor introduced in story 22.5."""

    def test_legacy_positional_constructs_own_client(self) -> None:
        """Legacy call site ``ApiClient("http://x", "k")`` still works."""
        client = ApiClient("http://test", "key")
        try:
            assert client._owns_client is True
            assert client._client.headers.get("authorization") == "Bearer key"
        finally:
            client.close()

    def test_legacy_keyword_constructs_own_client(self) -> None:
        client = ApiClient(base_url="http://test", api_key="k2")
        try:
            assert client._owns_client is True
        finally:
            client.close()

    def test_prebuilt_http_client_is_used_verbatim(self) -> None:
        """``ApiClient(http_client=...)`` reuses the given client untouched."""
        external = httpx.Client(base_url="http://external")
        client = ApiClient(http_client=external)
        try:
            assert client._owns_client is False
            assert client._client is external
            # ApiClient must NOT mutate headers when an external client is
            # supplied — the caller (callback) owns all auth.
            assert "authorization" not in external.headers
        finally:
            external.close()

    def test_both_base_url_and_http_client_raises(self) -> None:
        external = httpx.Client(base_url="http://external")
        try:
            with pytest.raises(ValueError, match="either"):
                ApiClient(base_url="http://x", http_client=external)
        finally:
            external.close()

    def test_neither_base_url_nor_http_client_raises(self) -> None:
        with pytest.raises(ValueError, match="requires either"):
            ApiClient()

    def test_close_does_not_close_external_client(self) -> None:
        """Ownership invariant — external clients survive ``ApiClient.close()``."""
        external = httpx.Client(base_url="http://external")
        client = ApiClient(http_client=external)
        client.close()
        # External client should still be usable.
        assert external.is_closed is False
        external.close()

    def test_close_does_close_owned_client(self) -> None:
        client = ApiClient(base_url="http://test")
        inner = client._client
        client.close()
        assert inner.is_closed is True
