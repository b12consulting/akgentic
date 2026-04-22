"""Tests for graceful degradation of Tavily search tools (Story 15-1)."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from akgentic.tool.search.search import SearchTool, _check_tavily_api_key, _has_tavily_api_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockTavilyClient:
    """Fake TavilyClient that records calls and returns canned responses."""

    def __init__(self) -> None:
        self.search = MagicMock(return_value={"results": [{"title": "test"}]})
        self.extract = MagicMock(return_value={"results": [{"url": "http://example.com"}]})
        self.crawl = MagicMock(return_value={"results": [{"url": "http://example.com"}]})


class _RaisingTavilyClient:
    """Fake TavilyClient whose methods always raise."""

    def __init__(self) -> None:
        self.search = MagicMock(side_effect=RuntimeError("rate limit exceeded"))
        self.extract = MagicMock(side_effect=RuntimeError("network timeout"))
        self.crawl = MagicMock(side_effect=RuntimeError("invalid key"))


def _get_tool_by_name(tools: list, name: str) -> Any:
    """Return the tool function whose ``__name__`` matches *name*."""
    for tool in tools:
        if tool.__name__ == name:
            return tool
    msg = f"Tool {name!r} not found in {[t.__name__ for t in tools]}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Unit tests for _has_tavily_api_key / _check_tavily_api_key helpers
# ---------------------------------------------------------------------------


class TestHasTavilyApiKey:
    """Direct unit tests for the _has_tavily_api_key helper."""

    def test_returns_true_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "sk-test-123")
        assert _has_tavily_api_key() is True

    def test_returns_false_when_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        assert _has_tavily_api_key() is False

    def test_returns_false_when_key_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "")
        assert _has_tavily_api_key() is False

    def test_returns_false_when_key_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "   ")
        assert _has_tavily_api_key() is False


class TestCheckTavilyApiKey:
    """Direct unit tests for _check_tavily_api_key (logs warning on missing key)."""

    def test_returns_true_no_log_when_key_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "sk-test-123")
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            assert _check_tavily_api_key() is True
        assert len(caplog.records) == 0

    def test_returns_false_and_logs_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            assert _check_tavily_api_key() is False
        assert any("non-functional" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# AC #1 — Startup warning when key is unset
# ---------------------------------------------------------------------------


class TestSearchToolStartupWarning:
    """SearchTool.get_tools() logs a warning when TAVILY_API_KEY is unset."""

    def test_warning_logged_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            SearchTool().get_tools()
        assert any("non-functional" in rec.message for rec in caplog.records)

    def test_no_warning_when_key_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            SearchTool().get_tools()
        assert not any("non-functional" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# AC #2 — web_search graceful degradation
# ---------------------------------------------------------------------------


class TestWebSearchGraceful:
    """web_search returns informative string when key is unset."""

    def test_returns_message_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        # Need to set key temporarily so get_tools doesn't warn during tool creation
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_search_tool")
        result = tool(query="test query")
        assert isinstance(result, str)
        assert "unavailable" in result.lower()
        assert "TAVILY_API_KEY" in result

    def test_no_exception_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_search_tool")
        # Must not raise — just return a string
        result = tool(query="test query")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC #3 — web_fetch graceful degradation
# ---------------------------------------------------------------------------


class TestWebFetchGraceful:
    """web_fetch returns informative string when key is unset."""

    def test_returns_message_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_fetch_tool")
        result = tool(urls=["http://example.com"])
        assert isinstance(result, str)
        assert "unavailable" in result.lower()
        assert "TAVILY_API_KEY" in result

    def test_no_exception_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_fetch_tool")
        result = tool(urls=["http://example.com"])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC #4 — web_crawl graceful degradation
# ---------------------------------------------------------------------------


class TestWebCrawlGraceful:
    """web_crawl returns informative string when key is unset."""

    def test_returns_message_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_crawl_tool")
        result = tool(url="http://example.com")
        assert isinstance(result, str)
        assert "unavailable" in result.lower()
        assert "TAVILY_API_KEY" in result

    def test_no_exception_when_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_crawl_tool")
        result = tool(url="http://example.com")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AC #5 — Happy path (key set, API succeeds)
# ---------------------------------------------------------------------------


class TestSearchToolHappyPath:
    """Tools work normally when key is set — TavilyClient is mocked."""

    def test_web_search_happy_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _MockTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_search_tool")
        result = tool(query="python testing")
        assert result == {"results": [{"title": "test"}]}
        mock_client.search.assert_called_once()

    def test_web_fetch_happy_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _MockTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_fetch_tool")
        result = tool(urls=["http://example.com"])
        assert result == {"results": [{"url": "http://example.com"}]}
        mock_client.extract.assert_called_once()

    def test_web_crawl_happy_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _MockTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_crawl_tool")
        result = tool(url="http://example.com")
        assert result == {"results": [{"url": "http://example.com"}]}
        mock_client.crawl.assert_called_once()


# ---------------------------------------------------------------------------
# AC #6 — API error handling (key set, API fails)
# ---------------------------------------------------------------------------


class TestSearchToolApiError:
    """Tools catch exceptions from TavilyClient and return error strings."""

    def test_web_search_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _RaisingTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            tools = SearchTool().get_tools()
            tool = _get_tool_by_name(tools, "web_search_tool")
            result = tool(query="test")
        assert isinstance(result, str)
        assert "failed" in result.lower()
        assert "rate limit exceeded" in result
        assert any("web_search failed" in rec.message for rec in caplog.records)

    def test_web_fetch_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _RaisingTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            tools = SearchTool().get_tools()
            tool = _get_tool_by_name(tools, "web_fetch_tool")
            result = tool(urls=["http://example.com"])
        assert isinstance(result, str)
        assert "failed" in result.lower()
        assert "network timeout" in result
        assert any("web_fetch failed" in rec.message for rec in caplog.records)

    def test_web_crawl_api_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        mock_client = _RaisingTavilyClient()
        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", lambda: mock_client
        )
        with caplog.at_level(logging.WARNING, logger="akgentic.tool.search.search"):
            tools = SearchTool().get_tools()
            tool = _get_tool_by_name(tools, "web_crawl_tool")
            result = tool(url="http://example.com")
        assert isinstance(result, str)
        assert "failed" in result.lower()
        assert "invalid key" in result
        assert any("web_crawl failed" in rec.message for rec in caplog.records)

    def test_client_construction_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TavilyClient() itself raises — still caught gracefully."""
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")

        def _raise_on_construct() -> None:
            msg = "invalid API key format"
            raise ValueError(msg)

        monkeypatch.setattr(
            "akgentic.tool.search.search.TavilyClient", _raise_on_construct
        )
        tools = SearchTool().get_tools()
        tool = _get_tool_by_name(tools, "web_search_tool")
        result = tool(query="test")
        assert isinstance(result, str)
        assert "failed" in result.lower()
