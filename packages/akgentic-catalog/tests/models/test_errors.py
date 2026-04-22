"""Tests for catalog error models."""

import pytest

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError


class TestCatalogValidationError:
    def test_stores_error_list(self) -> None:
        errors = ["field required", "invalid format"]
        exc = CatalogValidationError(errors)
        assert exc.errors == errors

    def test_message_joins_errors(self) -> None:
        errors = ["missing name", "bad type"]
        exc = CatalogValidationError(errors)
        assert str(exc) == "Catalog validation failed: missing name; bad type"

    def test_single_error(self) -> None:
        exc = CatalogValidationError(["only one"])
        assert exc.errors == ["only one"]
        assert str(exc) == "Catalog validation failed: only one"

    def test_empty_errors_list(self) -> None:
        exc = CatalogValidationError([])
        assert exc.errors == []
        assert str(exc) == "Catalog validation failed: "

    def test_is_exception(self) -> None:
        exc = CatalogValidationError(["err"])
        assert isinstance(exc, Exception)

    def test_raises_and_catches(self) -> None:
        with pytest.raises(CatalogValidationError) as exc_info:
            raise CatalogValidationError(["test error"])
        assert exc_info.value.errors == ["test error"]


class TestEntryNotFoundError:
    def test_is_exception(self) -> None:
        exc = EntryNotFoundError("not found")
        assert isinstance(exc, Exception)

    def test_raises_and_catches(self) -> None:
        with pytest.raises(EntryNotFoundError):
            raise EntryNotFoundError("agent 'foo' not found")

    def test_message(self) -> None:
        exc = EntryNotFoundError("agent 'foo' not found")
        assert str(exc) == "agent 'foo' not found"
