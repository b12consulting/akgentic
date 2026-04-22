"""Logging configuration — explicit root logger setup for the akgentic-infra server."""

from __future__ import annotations

import logging

_THIRD_PARTY_LOGGERS = (
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
    "httpx",
    "httpcore",
    "pydantic_ai",
)

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _DowngradeGracefulShutdownFilter(logging.Filter):
    """Downgrade Uvicorn's 'Cancel N running task(s)' from ERROR to WARNING.

    This message is expected when ``timeout_graceful_shutdown`` cancels
    WebSocket streaming tasks during shutdown (see ADR-015).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.ERROR and "timeout graceful shutdown exceeded" in record.msg:
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


def configure_logging(level: str) -> None:
    """Configure the root logger with a human-readable StreamHandler.

    Uses explicit handler assignment (not ``basicConfig``) to guarantee
    deterministic behaviour regardless of import order.

    Args:
        level: Log level name (e.g. "DEBUG", "INFO").
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("uvicorn.error").addFilter(_DowngradeGracefulShutdownFilter())
