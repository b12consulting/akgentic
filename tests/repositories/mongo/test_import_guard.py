"""Tests for the MongoDB lazy import guard (AC-1).

Verifies that importing the mongo package raises ImportError with a helpful
message when pymongo is not installed, and succeeds when it is.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch


class TestLazyImportGuard:
    """AC-1: Lazy import guard raises ImportError when pymongo is missing."""

    def test_import_raises_when_pymongo_unavailable(self) -> None:
        """Simulating missing pymongo triggers ImportError with install instructions."""
        # Remove cached module to force re-import
        mod_name = "akgentic.catalog.repositories.mongo"
        saved = {}
        for key in list(sys.modules):
            if key.startswith(mod_name):
                saved[key] = sys.modules.pop(key)
        # Also remove pymongo itself if cached
        pymongo_saved = {}
        for key in list(sys.modules):
            if key.startswith("pymongo"):
                pymongo_saved[key] = sys.modules.pop(key)

        try:
            with patch.dict(sys.modules, {"pymongo": None}):
                try:
                    importlib.import_module(mod_name)
                    msg = "Expected ImportError was not raised"
                    raise AssertionError(msg)
                except ImportError as exc:
                    assert "pymongo is required" in str(exc)
                    assert "akgentic-catalog[mongo]" in str(exc)
        finally:
            # Restore original modules
            sys.modules.update(saved)
            sys.modules.update(pymongo_saved)

    def test_import_succeeds_when_pymongo_available(self) -> None:
        """When pymongo is installed, the mongo package imports without error."""
        import akgentic.catalog.repositories.mongo  # noqa: F401

    def test_import_exports_mongo_catalog_config(self) -> None:
        """Successful import exposes MongoCatalogConfig."""
        from akgentic.catalog.repositories.mongo import MongoCatalogConfig

        assert MongoCatalogConfig is not None

    def test_import_exports_document_helpers(self) -> None:
        """Successful import exposes to_document and from_document."""
        from akgentic.catalog.repositories.mongo import from_document, to_document

        assert callable(to_document)
        assert callable(from_document)
