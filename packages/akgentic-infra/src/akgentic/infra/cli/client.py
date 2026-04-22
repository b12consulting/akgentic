"""HTTP client wrapper for the akgentic-infra REST API."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from pydantic import BaseModel, model_validator

from akgentic.catalog.models import AgentEntry, TeamEntry, TemplateEntry, ToolEntry
from akgentic.core.messages.message import Message
from akgentic.core.utils.deserializer import deserialize_object

# Typed admin-catalog entry union (ADR-022 §D4 response side).
CatalogEntry = TemplateEntry | ToolEntry | AgentEntry | TeamEntry

_ENTITY_MODELS: dict[str, type[BaseModel]] = {
    "templates": TemplateEntry,
    "tools": ToolEntry,
    "agents": AgentEntry,
    "teams": TeamEntry,
}

_log = logging.getLogger(__name__)

# -- CLI-side response models (independent of server models) --


class TeamInfo(BaseModel):
    """Team response from GET/POST /teams endpoints."""

    team_id: str
    name: str
    status: str
    user_id: str
    created_at: str
    updated_at: str


class TeamListInfo(BaseModel):
    """Wrapper for GET /teams list response."""

    teams: list[TeamInfo]


class EventInfo(BaseModel):
    """Single event from GET /teams/{team_id}/events."""

    team_id: str
    sequence: int
    event: dict[str, object] | Message
    timestamp: str

    @model_validator(mode="after")
    def _deserialize_event(self) -> EventInfo:
        """Deserialize raw event dict into a typed Message if possible."""
        if isinstance(self.event, dict) and "__model__" in self.event:
            try:
                result = deserialize_object(dict(self.event))
                if isinstance(result, Message):
                    self.event = result
            except ValueError:
                _log.debug("EventInfo: failed to deserialize event dict", exc_info=True)
        return self


class EventListInfo(BaseModel):
    """Wrapper for GET /teams/{team_id}/events list response."""

    events: list[EventInfo]


class WorkspaceEntry(BaseModel):
    """Single entry in workspace tree."""

    name: str
    is_dir: bool
    size: int


class WorkspaceTreeInfo(BaseModel):
    """Response from GET /workspace/{team_id}/tree."""

    team_id: str
    path: str
    entries: list[WorkspaceEntry]


class WorkspaceUploadInfo(BaseModel):
    """Response from POST /workspace/{team_id}/file."""

    path: str
    size: int


class CatalogTeamInfo(BaseModel):
    """Catalog team entry for display (subset of server-side TeamEntry)."""

    id: str
    name: str
    description: str


class ApiError(Exception):
    """HTTP API call failed."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}" if detail else f"HTTP {status_code}")

    @property
    def retryable(self) -> bool:
        """True for transient server errors and network issues."""
        return self.status_code >= 500 or self.status_code == 429 or self.status_code == 0


def _require_json_object(body: object) -> dict[str, Any]:
    """Guard: return ``body`` as a ``dict`` or raise :class:`ApiError`.

    Replaces the previous bare ``assert isinstance(body, dict)`` so malformed
    server responses still surface as an ``ApiError`` under ``python -O``.
    """
    if not isinstance(body, dict):
        raise ApiError(0, "unexpected response shape: expected JSON object")
    return dict(body)


class ApiClient:
    """Thin HTTP client mapping CLI commands to server endpoints.

    Two construction modes are supported:

    * **Legacy** — ``ApiClient(base_url=..., api_key=...)`` constructs its own
      :class:`httpx.Client` with the same headers, timeout, and redirect policy
      it has always used. This path is what the Typer callback takes when
      ``~/.akgentic/config.yaml`` does NOT exist (backward-compat invariant).
    * **Pre-built** — ``ApiClient(http_client=<client>)`` uses the supplied
      client verbatim. Ownership stays with the caller: :meth:`close` will NOT
      close the externally supplied client. The profile-driven Typer callback
      (story 22.5) uses this mode to hand ``ApiClient`` a client that already
      has OIDC auto-auth wired.

    Exactly one of ``base_url`` or ``http_client`` MUST be supplied.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        if http_client is not None and base_url is not None:
            raise ValueError("ApiClient accepts either http_client or base_url, not both.")
        if http_client is None and base_url is None:
            raise ValueError("ApiClient requires either http_client or base_url.")

        if http_client is not None:
            # External client: caller retains ownership; close() is a no-op.
            self._client = http_client
            self._owns_client = False
        else:
            assert base_url is not None  # narrowed by the guards above
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            self._client = httpx.Client(
                base_url=base_url,
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
            self._owns_client = True

    def close(self) -> None:
        """Close the underlying HTTP client iff this ``ApiClient`` owns it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    # -- helpers --

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            resp = self._client.request(
                method,
                path,
                json=json,
                params=params,
                files=files,
                data=data,
            )
        except httpx.TimeoutException as exc:
            raise ApiError(0, f"Request timed out: {method} {path}") from exc
        except httpx.ConnectError as exc:
            raise ApiError(0, f"Connection failed: {exc}") from exc

        if not resp.is_success:
            raise ApiError(resp.status_code, self._extract_detail(resp))
        return resp

    @staticmethod
    def _extract_detail(resp: httpx.Response) -> str:
        """Pull ``detail`` from a JSON error body, falling back to empty string."""
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(body, dict):
            detail = body.get("detail", "")
            if isinstance(detail, str):
                return detail
        return ""

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        content: bytes,
        content_type: str,
    ) -> httpx.Response:
        """Send a raw body with an explicit ``Content-Type`` header.

        Reuses the same timeout / connect-error / non-2xx translation as
        :meth:`_request` but bypasses the ``json=...`` shortcut so the caller
        can post YAML (or any other encoding) without double-serialization.
        """
        try:
            resp = self._client.request(
                method,
                path,
                content=content,
                headers={"Content-Type": content_type},
            )
        except httpx.TimeoutException as exc:
            raise ApiError(0, f"Request timed out: {method} {path}") from exc
        except httpx.ConnectError as exc:
            raise ApiError(0, f"Connection failed: {exc}") from exc

        if not resp.is_success:
            raise ApiError(resp.status_code, self._extract_detail(resp))
        return resp

    # -- catalog endpoints --

    def list_catalog_teams(self) -> list[CatalogTeamInfo]:
        """GET /catalog/api/teams/ -> list of CatalogTeamInfo models."""
        resp = self._request("GET", "/catalog/api/teams/")
        return [CatalogTeamInfo.model_validate(entry) for entry in resp.json()]

    # -- team endpoints --

    def list_teams(self) -> list[TeamInfo]:
        """GET /teams → list of TeamInfo models."""
        resp = self._request("GET", "/teams")
        return TeamListInfo.model_validate(resp.json()).teams

    def get_team(self, team_id: str) -> TeamInfo:
        """GET /teams/{team_id} → TeamInfo model."""
        return TeamInfo.model_validate(self._request("GET", f"/teams/{team_id}").json())

    def create_team(self, catalog_entry_id: str) -> TeamInfo:
        """POST /teams → created TeamInfo model."""
        resp = self._request("POST", "/teams", json={"catalog_entry_id": catalog_entry_id})
        return TeamInfo.model_validate(resp.json())

    def stop_team(self, team_id: str) -> None:
        """POST /teams/{team_id}/stop — stop actors but preserve event store data."""
        self._request("POST", f"/teams/{team_id}/stop")

    def delete_team(self, team_id: str) -> None:
        """DELETE /teams/{team_id}."""
        self._request("DELETE", f"/teams/{team_id}")

    def restore_team(self, team_id: str) -> TeamInfo:
        """POST /teams/{team_id}/restore → restored TeamInfo model."""
        return TeamInfo.model_validate(self._request("POST", f"/teams/{team_id}/restore").json())

    def get_events(self, team_id: str) -> list[EventInfo]:
        """GET /teams/{team_id}/events → list of EventInfo models."""
        resp = self._request("GET", f"/teams/{team_id}/events")
        return EventListInfo.model_validate(resp.json()).events

    # -- messaging --

    def send_message(self, team_id: str, content: str) -> None:
        """POST /teams/{team_id}/message."""
        self._request("POST", f"/teams/{team_id}/message", json={"content": content})

    def send_message_to(self, team_id: str, agent_name: str, content: str) -> None:
        """POST /teams/{team_id}/message/{agent_name}."""
        self._request("POST", f"/teams/{team_id}/message/{agent_name}", json={"content": content})

    def human_input(self, team_id: str, content: str, message_id: str) -> None:
        """POST /teams/{team_id}/human-input."""
        self._request(
            "POST",
            f"/teams/{team_id}/human-input",
            json={"content": content, "message_id": message_id},
        )

    # -- workspace --

    def workspace_tree(self, team_id: str) -> WorkspaceTreeInfo:
        """GET /workspace/{team_id}/tree → WorkspaceTreeInfo model."""
        return WorkspaceTreeInfo.model_validate(
            self._request("GET", f"/workspace/{team_id}/tree").json()
        )

    def workspace_read(self, team_id: str, path: str) -> bytes:
        """GET /workspace/{team_id}/file → raw file bytes."""
        resp = self._request("GET", f"/workspace/{team_id}/file", params={"path": path})
        return resp.content

    # -- admin catalog (thin wire — server is validation point, ADR-022 §D4) --

    def admin_catalog_list(
        self,
        entity: str,
        q: str | None = None,
    ) -> list[CatalogEntry]:
        """GET /admin/catalog/<entity> → list of typed entries.

        ``q`` is forwarded as ``?q=<text>`` when supplied. Malformed responses
        (non-list top-level) raise :class:`ApiError` — no silent empty-list
        fallback.
        """
        params: dict[str, str] = {"q": q} if q is not None else {}
        resp = self._request("GET", f"/admin/catalog/{entity}", params=params or None)
        body = resp.json()
        if not isinstance(body, list):
            raise ApiError(0, "unexpected response shape: expected JSON array")
        model = _ENTITY_MODELS[entity]
        return [cast(CatalogEntry, model.model_validate(item)) for item in body]

    def admin_catalog_get(self, entity: str, entry_id: str) -> CatalogEntry:
        """GET /admin/catalog/<entity>/<id> → typed entry."""
        resp = self._request("GET", f"/admin/catalog/{entity}/{entry_id}")
        body = _require_json_object(resp.json())
        return cast(CatalogEntry, _ENTITY_MODELS[entity].model_validate(body))

    def admin_catalog_create(
        self,
        entity: str,
        body: bytes,
        content_type: str,
    ) -> CatalogEntry:
        """POST /admin/catalog/<entity> → created typed entry.

        ``body`` is forwarded unchanged; ``content_type`` is one of
        ``application/json`` or ``application/yaml``. The server is the
        validation point — the CLI does not parse the outbound payload.
        """
        resp = self._raw_request(
            "POST",
            f"/admin/catalog/{entity}",
            content=body,
            content_type=content_type,
        )
        data = _require_json_object(resp.json())
        return cast(CatalogEntry, _ENTITY_MODELS[entity].model_validate(data))

    def admin_catalog_update(
        self,
        entity: str,
        entry_id: str,
        body: bytes,
        content_type: str,
    ) -> CatalogEntry:
        """PUT /admin/catalog/<entity>/<id> → updated typed entry."""
        resp = self._raw_request(
            "PUT",
            f"/admin/catalog/{entity}/{entry_id}",
            content=body,
            content_type=content_type,
        )
        data = _require_json_object(resp.json())
        return cast(CatalogEntry, _ENTITY_MODELS[entity].model_validate(data))

    def admin_catalog_delete(self, entity: str, entry_id: str) -> None:
        """DELETE /admin/catalog/<entity>/<id> — 204 on success."""
        self._request("DELETE", f"/admin/catalog/{entity}/{entry_id}")

    # -- admin channels (thin wire — ADR-022 §D5) --

    def reload_channels(self) -> dict[str, Any]:
        """POST /admin/channels/reload → reload summary dict."""
        resp = self._request("POST", "/admin/channels/reload")
        if not resp.content:
            return {}
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(body, dict):
            return {}
        return dict(body)

    def workspace_upload(self, team_id: str, path: str, file_data: bytes) -> WorkspaceUploadInfo:
        """POST /workspace/{team_id}/file → WorkspaceUploadInfo model."""
        resp = self._request(
            "POST",
            f"/workspace/{team_id}/file",
            data={"path": path},
            files={"file": ("upload", file_data)},
        )
        return WorkspaceUploadInfo.model_validate(resp.json())
