"""Tests for catalog @-reference utilities."""

from akgentic.catalog.refs import _is_catalog_ref, _resolve_ref


class TestIsCatalogRef:
    def test_at_prefixed_string_is_ref(self) -> None:
        assert _is_catalog_ref("@my-template") is True

    def test_at_only_is_ref(self) -> None:
        assert _is_catalog_ref("@") is True

    def test_plain_string_is_not_ref(self) -> None:
        assert _is_catalog_ref("my-template") is False

    def test_empty_string_is_not_ref(self) -> None:
        assert _is_catalog_ref("") is False

    def test_at_in_middle_is_not_ref(self) -> None:
        assert _is_catalog_ref("user@example.com") is False

    def test_dollar_prefix_is_not_ref(self) -> None:
        assert _is_catalog_ref("${VAR}") is False


class TestResolveRef:
    def test_strips_at_prefix(self) -> None:
        assert _resolve_ref("@my-template") == "my-template"

    def test_at_only_returns_empty(self) -> None:
        assert _resolve_ref("@") == ""

    def test_preserves_rest_of_string(self) -> None:
        assert _resolve_ref("@nested/path/id") == "nested/path/id"
