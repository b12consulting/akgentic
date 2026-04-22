"""WorkerSettings -- typed configuration for the akgentic-infra worker."""

from __future__ import annotations

import warnings
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class WorkerSettings(BaseSettings):
    """Tier-agnostic worker configuration loaded from environment variables.

    Contains only settings common to all deployment tiers.
    All fields can be overridden via environment variables prefixed with ``AKGENTIC_WORKER_``.
    """

    model_config = SettingsConfigDict(env_prefix="AKGENTIC_WORKER_")

    host: str = Field(
        default="0.0.0.0",
        description="Bind address for the worker HTTP server",
    )
    port: int = Field(
        default=8001,
        description="Port number for the worker HTTP server",
    )
    log_level: str = Field(
        default="INFO",
        description="Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    workspaces_root: Path = Field(
        default=Path("/data/workspaces"),
        description="Root directory for team workspace storage",
    )
    shutdown_drain_timeout: int = Field(
        default=30,
        ge=0,
        description="Max seconds for stop_all() during graceful shutdown",
    )
    shutdown_pre_drain_delay: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds to wait after marking draining before teardown "
            "(0 for standalone, 5-10 for LB deployments)"
        ),
    )
    worker_labels: dict[str, str] = Field(
        default_factory=dict,
        description="Labels for placement strategy matching (e.g. gpu=true, region=eu)",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: str) -> str:
        """Normalize to uppercase and fall back to INFO for invalid values."""
        upper = str(v).upper()
        if upper not in _VALID_LOG_LEVELS:
            warnings.warn(
                f"Invalid AKGENTIC_WORKER_LOG_LEVEL '{v}', falling back to INFO",
                UserWarning,
                stacklevel=1,
            )
            return "INFO"
        return upper
