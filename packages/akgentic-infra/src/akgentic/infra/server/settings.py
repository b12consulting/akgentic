"""ServerSettings — typed configuration for the akgentic-infra server."""

from __future__ import annotations

import warnings
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ServerSettings(BaseSettings):
    """Tier-agnostic server configuration loaded from environment variables.

    Contains only settings common to all deployment tiers.
    All fields can be overridden via environment variables prefixed with ``AKGENTIC_``.
    """

    model_config = SettingsConfigDict(env_prefix="AKGENTIC_")

    host: str = Field(
        default="0.0.0.0",
        description="Bind address for the HTTP server",
    )
    port: int = Field(
        default=8000,
        description="Port number for the HTTP server",
    )
    log_level: str = Field(
        default="INFO",
        description="Application log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: str) -> str:
        """Normalize to uppercase and fall back to INFO for invalid values."""
        upper = str(v).upper()
        if upper not in _VALID_LOG_LEVELS:
            warnings.warn(
                f"Invalid AKGENTIC_LOG_LEVEL '{v}', falling back to INFO",
                UserWarning,
                stacklevel=1,
            )
            return "INFO"
        return upper

    frontend_adapter: str | None = Field(
        default=None,
        description="FQDN for frontend adapter plugin class",
    )
    # Community-tier permissive default. Department/enterprise tiers must
    # override with explicit origins in their environment configuration.
    # Set to [] to disable the CORS middleware entirely — useful when an
    # external gateway (e.g. Azure App Service) manages CORS.
    cors_origins: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins for the HTTP server (empty list disables middleware)",
    )
    shutdown_drain_timeout: int = Field(
        default=30,
        ge=0,
        description="Max seconds for stop_all() to complete during graceful shutdown",
    )
    shutdown_pre_drain_delay: int = Field(
        default=0,
        ge=0,
        description=(
            "Seconds to wait after marking draining before starting teardown "
            "(0 for standalone, 5-10 for LB deployments)"
        ),
    )
    ws_reader_pool_size: int = Field(
        default=256,
        ge=1,
        description=(
            "Dedicated thread pool for WebSocket event-stream reader polling. "
            "Each active WS connection holds one thread per read_next(0.5) tick. "
            "Size for concurrent WS budget plus headroom for burst open/close "
            "cycles. Isolated from the default executor to prevent "
            "cross-subsystem starvation."
        ),
    )


class CommunitySettings(ServerSettings):
    """Community-tier settings extending base ServerSettings.

    Adds filesystem-backed workspace and catalog configuration
    specific to the community (single-process) deployment tier.
    """

    workspaces_root: Path = Field(
        default=Path("workspaces"),
        description="Root directory for team workspace storage",
    )
    event_store_path: Path = Field(
        default=Path("data/event_store"),
        description="Root directory for event store persistence",
    )
    catalog_path: Path = Field(
        default=Path("data/catalog"),
        description="Catalog directory for team/agent/tool/template definitions",
    )
    channel_registry_path: Path | None = Field(
        default=None,
        description=(
            "Path to channel registry YAML file; "
            "when unset, channels are disabled (NullChannelRegistry)"
        ),
    )
