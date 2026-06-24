"""Workspace file access endpoints — tree listing, file read, file upload.

All three routes (GET ``.../tree``, GET ``.../file``, POST ``.../file``) accept an
optional ``workspace_id`` **query** parameter that selects which directory under
``workspaces_root`` is served:

- When omitted, the directory is the team's own (``<workspaces_root>/<team_id>``) —
  byte-identical to the historical behaviour.
- When present, it must be a single safe path segment matching
  ``[A-Za-z0-9._-]{1,128}`` (``_validate_workspace_id``); anything else (empty,
  ``.``/``..``, separators, absolute paths, over-length) is rejected with HTTP 400
  *before* any ``Filesystem`` is constructed. This mirrors the agent side's
  ``WorkspaceTool.workspace_id or str(team_id)`` (ADR-029), letting an HTTP caller
  name the same non-default workspace an agent was configured with.

The guard is a route-boundary traversal/correctness invariant, NOT an access
policy: it proves the value is a safe segment, not that the caller may read the
selected workspace. **Ownership authorization is deferred to the ADR-023
request-user-identity seam.**
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.params import File, Form

from akgentic.infra.server.models import (
    WorkspaceFileEntry,
    WorkspaceFileUploadResponse,
    WorkspaceTreeResponse,
)
from akgentic.infra.server.settings import ServerSettings
from akgentic.infra.server.state_keys import SETTINGS, TEAM_SERVICE
from akgentic.tool.workspace import Filesystem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])

_MAX_FILE_SIZE = 10_485_760  # 10 MB

# A workspace_id is a single safe path segment: alphanumerics plus dot, dash, and
# underscore, 1-128 chars. This is a route-boundary traversal guard (a correctness
# invariant), NOT an access-policy check — ownership authz is deferred to the
# ADR-023 request-user-identity seam.
_WORKSPACE_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")


def _validate_workspace_id(workspace_id: str) -> str:
    """Reject any workspace_id that is not a single safe path segment.

    Mandatory traversal guard (ADR-029 §2): rejects ``""``, ``"."``, ``".."``,
    any value containing a path separator, absolute paths, and over-length
    values with HTTP 400, raising **before** any ``Filesystem`` is constructed.
    Returns the value unchanged when it is a valid single segment.
    """
    if workspace_id in ("", ".", "..") or not _WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise HTTPException(status_code=400, detail="Invalid workspace_id")
    return workspace_id


def _get_workspace(
    team_id: uuid.UUID,
    settings: ServerSettings,
    workspace_id: str | None = None,
) -> Filesystem:
    """Instantiate a Filesystem scoped to a workspace directory.

    Uses ``workspace_id`` when provided, otherwise falls back to the team's own
    directory (``team_id``). Mirrors ``WorkspaceTool.workspace_id or str(team_id)``
    on the agent side (ADR-029). Validation runs before the ``Filesystem`` is
    constructed, so a rejected ``workspace_id`` never resolves a traversal root.

    ``workspaces_root`` is declared on ``CommunitySettings``; a base
    ``ServerSettings`` deployment falls back to the same default the field
    declares, mirroring ``create_app``'s own defensive read (byte-identical
    behaviour to the historical ``cast(CommunitySettings, ...)``).
    """
    # Distinguish an *omitted* param (None → team-id fallback, AC #1) from an
    # *empty* one (""→ 400, AC #6): only None falls back; any present value,
    # including "", goes through the guard.
    name = str(team_id) if workspace_id is None else _validate_workspace_id(workspace_id)
    workspaces_root = getattr(settings, "workspaces_root", Path("workspaces"))
    return Filesystem(base_path=str(workspaces_root), workspace_name=name)


def _validate_team(team_id: uuid.UUID, request: Request) -> None:
    """Raise 404 if the team does not exist."""
    service = TEAM_SERVICE.require(request)
    if service.get_team(team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")


@router.get("/{team_id}/tree", response_model=WorkspaceTreeResponse)
def list_workspace_tree(
    team_id: uuid.UUID,
    request: Request,
    path: str = "",
    workspace_id: str | None = None,
) -> WorkspaceTreeResponse:
    """List files in a team's workspace directory."""
    logger.debug("GET /workspace/%s/tree path=%s", team_id, path)
    _validate_team(team_id, request)
    settings = SETTINGS.require(request)
    ws = _get_workspace(team_id, settings, workspace_id)
    try:
        entries = ws.list(path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path access denied") from None
    return WorkspaceTreeResponse(
        team_id=str(team_id),
        path=path,
        entries=[WorkspaceFileEntry(name=e.name, is_dir=e.is_dir, size=e.size) for e in entries],
    )


@router.get("/{team_id}/file")
def read_workspace_file(
    team_id: uuid.UUID,
    request: Request,
    path: str,
    workspace_id: str | None = None,
) -> Response:
    """Read a file from a team's workspace."""
    logger.debug("GET /workspace/%s/file path=%s", team_id, path)
    _validate_team(team_id, request)
    settings = SETTINGS.require(request)
    ws = _get_workspace(team_id, settings, workspace_id)
    try:
        data = ws.read(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found") from None
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path access denied") from None
    if len(data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB size limit")
    filename = PurePosixPath(path).name
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{team_id}/file", status_code=201, response_model=WorkspaceFileUploadResponse)
async def upload_workspace_file(
    team_id: uuid.UUID,
    request: Request,
    path: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    workspace_id: str | None = None,
) -> WorkspaceFileUploadResponse:
    """Upload a file to a team's workspace."""
    await asyncio.to_thread(_validate_team, team_id, request)
    settings = SETTINGS.require(request)
    ws = _get_workspace(team_id, settings, workspace_id)
    data = await file.read()
    if len(data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB size limit")
    logger.info("POST /workspace/%s/file path=%s, size=%d", team_id, path, len(data))
    try:
        await asyncio.to_thread(ws.write, path, data)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path access denied") from None
    return WorkspaceFileUploadResponse(path=path, size=len(data))
