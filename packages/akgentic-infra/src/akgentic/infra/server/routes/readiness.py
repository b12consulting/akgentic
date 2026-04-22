"""Readiness probe endpoint for load-balancer drain support (ADR-013 Decision 3)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["readiness"])


class ReadinessResponse(BaseModel):
    """Response model for the readiness probe endpoint."""

    status: Literal["ready", "draining"]


@router.get("/readiness", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse | JSONResponse:
    """Instance-level readiness probe for load-balancer traffic management.

    Returns 200 when the server is ready to accept traffic, or 503 when
    the server is draining (shutdown in progress). This is distinct from
    the cluster-level ``HealthMonitor`` protocol which tracks worker
    liveness via heartbeats.

    Args:
        request: The incoming HTTP request.

    Returns:
        ReadinessResponse with status "ready" (200) or "draining" (503).
    """
    draining: bool = getattr(request.app.state, "draining", False)
    if draining:
        logger.info("Readiness probe: draining")
        return JSONResponse(content={"status": "draining"}, status_code=503)
    return ReadinessResponse(status="ready")
