"""Workspace file access endpoints — tree listing, file read, file upload."""

from __future__ import annotations

import logging
import uuid
from pathlib import PurePosixPath
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.params import File, Form

from akgentic.infra.server.models import (
    WorkspaceFileEntry,
    WorkspaceFileUploadResponse,
    WorkspaceTreeResponse,
)
from akgentic.infra.server.services.team_service import TeamService
from akgentic.infra.server.settings import CommunitySettings
from akgentic.tool.workspace import Filesystem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])

_MAX_FILE_SIZE = 10_485_760  # 10 MB


def _get_workspace(team_id: uuid.UUID, settings: CommunitySettings) -> Filesystem:
    """Instantiate a Filesystem scoped to a team's workspace directory."""
    return Filesystem(base_path=str(settings.workspaces_root), workspace_name=str(team_id))


def _validate_team(team_id: uuid.UUID, request: Request) -> None:
    """Raise 404 if the team does not exist."""
    service = cast(TeamService, request.app.state.team_service)
    if service.get_team(team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")


@router.get("/{team_id}/tree", response_model=WorkspaceTreeResponse)
def list_workspace_tree(
    team_id: uuid.UUID,
    request: Request,
    path: str = "",
) -> WorkspaceTreeResponse:
    """List files in a team's workspace directory."""
    logger.debug("GET /workspace/%s/tree path=%s", team_id, path)
    _validate_team(team_id, request)
    settings = cast(CommunitySettings, request.app.state.settings)
    ws = _get_workspace(team_id, settings)
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
) -> Response:
    """Read a file from a team's workspace."""
    logger.debug("GET /workspace/%s/file path=%s", team_id, path)
    _validate_team(team_id, request)
    settings = cast(CommunitySettings, request.app.state.settings)
    ws = _get_workspace(team_id, settings)
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
) -> WorkspaceFileUploadResponse:
    """Upload a file to a team's workspace."""
    _validate_team(team_id, request)
    settings = cast(CommunitySettings, request.app.state.settings)
    ws = _get_workspace(team_id, settings)
    data = await file.read()
    if len(data) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB size limit")
    logger.info("POST /workspace/%s/file path=%s, size=%d", team_id, path, len(data))
    try:
        ws.write(path, data)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Path access denied") from None
    return WorkspaceFileUploadResponse(path=path, size=len(data))
