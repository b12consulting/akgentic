"""Tests for environment variable substitution utility."""

import pytest

from akgentic.catalog.env import resolve_env_vars


class TestResolveEnvVars:
    def test_replaces_single_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "hello")
        assert resolve_env_vars("${MY_VAR}") == "hello"

    def test_replaces_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        assert resolve_env_vars("${HOST}:${PORT}") == "localhost:8080"

    def test_preserves_text_around_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NAME", "world")
        assert resolve_env_vars("hello ${NAME}!") == "hello world!"

    def test_no_vars_returns_unchanged(self) -> None:
        assert resolve_env_vars("plain text") == "plain text"

    def test_empty_string_returns_empty(self) -> None:
        assert resolve_env_vars("") == ""

    def test_raises_on_missing_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)
        with pytest.raises(OSError, match="NONEXISTENT_VAR_12345"):
            resolve_env_vars("${NONEXISTENT_VAR_12345}")

    def test_underscore_in_var_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR_2", "value")
        assert resolve_env_vars("${MY_VAR_2}") == "value"

    def test_ignores_invalid_var_syntax(self) -> None:
        # $VAR without braces should not be substituted
        assert resolve_env_vars("$VAR") == "$VAR"

    def test_ignores_empty_braces(self) -> None:
        # ${} should not match the pattern (requires valid identifier)
        assert resolve_env_vars("${}") == "${}"

    def test_var_starting_with_digit_not_matched(self) -> None:
        # ${1VAR} is not a valid identifier per the regex
        assert resolve_env_vars("${1VAR}") == "${1VAR}"

    def test_empty_env_var_value_resolves_to_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMPTY_VAR", "")
        assert resolve_env_vars("prefix-${EMPTY_VAR}-suffix") == "prefix--suffix"

    def test_partial_resolution_raises_on_first_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOD_VAR", "resolved")
        monkeypatch.delenv("BAD_VAR", raising=False)
        with pytest.raises(OSError, match="BAD_VAR"):
            resolve_env_vars("${GOOD_VAR}/${BAD_VAR}")
