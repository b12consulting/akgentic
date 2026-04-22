"""No-extra regression guard: ``api.app`` imports without the ``[postgres]`` extra.

This module deliberately does NOT gate itself with ``pytest.importorskip("nagra")``.
The single test inside is inverted: it runs only when ``nagra`` is NOT available
so it can verify that importing ``akgentic.catalog.api.app`` still succeeds
without the Postgres extra installed. Per AC #7 / AC #14 of Story 15.6.

In CI environments where the ``[postgres]`` extra is installed the test is
skipped cleanly. The separate file exists because the sibling
``test_app_postgres.py`` cannot host it — that module top-levels
``importorskip("nagra")`` which would itself skip the entire file in no-extra
environments, making the regression guard unreachable.
"""

from __future__ import annotations

import importlib.util

import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("nagra") is not None,
    reason="Regression guard for no-extra environments; nagra is installed here.",
)
def test_create_app_postgres_backend_importless_extra() -> None:
    """Top-level ``create_app`` import succeeds without the ``[postgres]`` extra."""
    from akgentic.catalog.api.app import create_app

    assert create_app is not None
