"""Tests for configure_logging() utility."""

from __future__ import annotations

import logging
from collections.abc import Generator

import pytest

from akgentic.infra.server.logging_config import configure_logging


class TestConfigureLogging:
    """configure_logging() sets up root logger deterministically."""

    @pytest.fixture(autouse=True)
    def _reset_root_logger(self) -> Generator[None, None, None]:
        """Save and restore root logger state around each test."""
        root = logging.getLogger()
        original_level = root.level
        original_handlers = list(root.handlers)
        yield
        root.setLevel(original_level)
        root.handlers = original_handlers

    def test_sets_root_level_debug(self) -> None:
        """configure_logging('DEBUG') sets root logger to DEBUG."""
        configure_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_sets_root_level_info(self) -> None:
        """configure_logging('INFO') sets root logger to INFO."""
        configure_logging("INFO")
        assert logging.getLogger().level == logging.INFO

    def test_sets_format(self) -> None:
        """Handler format matches the expected pattern."""
        configure_logging("INFO")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        formatter = root.handlers[0].formatter
        assert formatter is not None
        assert "%(asctime)s" in formatter._fmt
        assert "%(levelname)" in formatter._fmt
        assert "%(name)s" in formatter._fmt
        assert "%(message)s" in formatter._fmt

    def test_suppresses_third_party_loggers(self) -> None:
        """Third-party loggers are set to WARNING."""
        configure_logging("DEBUG")
        for name in (
            "uvicorn",
            "uvicorn.access",
            "uvicorn.error",
            "httpx",
            "httpcore",
            "pydantic_ai",
        ):
            assert logging.getLogger(name).level == logging.WARNING

    def test_replaces_existing_handlers(self) -> None:
        """configure_logging replaces existing handlers, not appends."""
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        assert len(root.handlers) >= 2

        configure_logging("INFO")
        assert len(root.handlers) == 1
