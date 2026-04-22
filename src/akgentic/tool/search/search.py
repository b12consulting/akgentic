from __future__ import annotations

import logging
import os
from typing import Any, Callable, Literal

from tavily import TavilyClient

from akgentic.tool.core import TOOL_CALL, BaseToolParam, ToolCard, _resolve

logger = logging.getLogger(__name__)


def _has_tavily_api_key() -> bool:
    """Return ``True`` when ``TAVILY_API_KEY`` is present and non-empty."""
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _check_tavily_api_key() -> bool:
    """Check whether ``TAVILY_API_KEY`` is configured and log a warning if not.

    Returns:
        ``True`` when the key is present and non-empty, ``False`` otherwise.
        A warning is logged when the key is missing.
    """
    if _has_tavily_api_key():
        return True
    logger.warning(
        "TAVILY_API_KEY is not set in the environment. "
        "Tavily search tools will be registered but non-functional until the key is configured."
    )
    return False


class WebSearch(BaseToolParam):
    """Parameters for the web search capability."""

    max_results: int = 5
    search_depth: Literal["basic", "advanced"] | None = None


class WebFetch(BaseToolParam):
    """Parameters for the web fetch (extract) capability."""

    timeout: float = 30
    extract_depth: Literal["basic", "advanced"] | None = None


class WebCrawl(BaseToolParam):
    """Parameters for the web crawl capability."""

    timeout: float = 150
    max_depth: int | None = None
    max_breadth: int | None = None
    limit: int | None = None
    instructions: str | None = None
    extract_depth: Literal["basic", "advanced"] | None = None


class SearchTool(ToolCard):
    """Web search, fetch, and crawl capabilities via Tavily."""

    name: str = "Web Search"
    description: str = "Search the web for current information"

    web_search: WebSearch | bool = True
    web_crawl: WebCrawl | bool = True
    web_fetch: WebFetch | bool = True

    def get_tools(self) -> list[Callable]:
        _check_tavily_api_key()
        tools: list[Callable] = []
        ws = _resolve(self.web_search, WebSearch)
        if ws and TOOL_CALL in ws.expose:
            tools.append(self._web_search_factory(ws))
        wc = _resolve(self.web_crawl, WebCrawl)
        if wc and TOOL_CALL in wc.expose:
            tools.append(self._web_crawl_factory(wc))
        wf = _resolve(self.web_fetch, WebFetch)
        if wf and TOOL_CALL in wf.expose:
            tools.append(self._web_fetch_factory(wf))
        return tools

    def _web_search_factory(self, params: WebSearch) -> Callable:
        def web_search_tool(
            query: str,
            max_results: int = params.max_results,
            search_depth: Literal["basic", "advanced"] | None = params.search_depth,
        ) -> Any:
            """Search the web for sources relevant to a natural-language query.

            Use this tool when knowledge is not available in local context
            (e.g., vector store) or when fresh/public web information is needed.

            Args:
                query: Natural-language search query to execute.
                max_results: Maximum number of results to return.
                    Tavily supports values in the range 0-20.
                search_depth: Search strategy balancing quality vs latency.
                    - ``basic``: balanced relevance/latency, lower credit cost.
                    - ``advanced``: higher relevance, potentially slower and more expensive.
                    If ``None``, Tavily default behavior is used.
            """
            if not _has_tavily_api_key():
                return (
                    "Web search is unavailable: TAVILY_API_KEY is not set. "
                    "Ask the user to configure it and restart."
                )

            try:
                tavily_client = TavilyClient()

                search_kwargs: dict[str, Any] = {}
                if search_depth is not None:
                    search_kwargs["search_depth"] = search_depth

                return tavily_client.search(
                    query,
                    max_results=max_results,
                    **search_kwargs,
                )
            except Exception as exc:
                logger.warning("web_search failed: %s", exc)
                return (
                    f"Web search failed: {exc}. "
                    "The TAVILY_API_KEY may be invalid or the service may be "
                    "temporarily unavailable."
                )

        web_search_tool.__doc__ = params.format_docstring(web_search_tool.__doc__)
        return web_search_tool

    def _web_fetch_factory(self, params: WebFetch) -> Callable:
        def web_fetch_tool(
            urls: list[str],
            timeout: float = params.timeout,
            extract_depth: Literal["basic", "advanced"] | None = params.extract_depth,
        ) -> Any:
            """Extract main content from one or more web pages.

            Use this tool when you already have URLs and need clean page content
            for reading, summarization, or grounding downstream reasoning.

            Args:
                urls: List of absolute URLs to extract content from.
                timeout: Maximum extraction time in seconds per request.
                    Tavily supports values roughly between 1 and 60 seconds.
                extract_depth: Extraction depth.
                    - ``basic``: faster and cheaper extraction.
                    - ``advanced``: richer extraction (e.g., better coverage),
                      potentially slower and more expensive.
                    If ``None``, Tavily default behavior is used.
            """
            if not _has_tavily_api_key():
                return (
                    "Web fetch is unavailable: TAVILY_API_KEY is not set. "
                    "Ask the user to configure it and restart."
                )

            try:
                tavily_client = TavilyClient()

                fetch_kwargs: dict[str, Any] = {}
                if extract_depth is not None:
                    fetch_kwargs["extract_depth"] = extract_depth

                return tavily_client.extract(
                    urls,
                    timeout=timeout,
                    **fetch_kwargs,
                )
            except Exception as exc:
                logger.warning("web_fetch failed: %s", exc)
                return (
                    f"Web fetch failed: {exc}. "
                    "The TAVILY_API_KEY may be invalid or the service may be "
                    "temporarily unavailable."
                )

        web_fetch_tool.__doc__ = params.format_docstring(web_fetch_tool.__doc__)
        return web_fetch_tool

    def _web_crawl_factory(self, params: WebCrawl) -> Callable:
        def web_crawl_tool(
            url: str,
            timeout: float = params.timeout,
            max_depth: int | None = params.max_depth,
            max_breadth: int | None = params.max_breadth,
            limit: int | None = params.limit,
            instructions: str | None = params.instructions,
            extract_depth: Literal["basic", "advanced"] | None = params.extract_depth,
        ) -> Any:
            """Crawl a website from a root URL and extract content from discovered pages.

            Use this tool when you need multi-page discovery from a site section
            (documentation, blog, knowledge base) rather than a single-page fetch.

            Args:
                url: Root URL to start crawling from.
                timeout: Maximum crawl time in seconds.
                    Tavily supports values between 10 and 150 seconds.
                max_depth: Maximum link depth from the root URL.
                    Tavily supports values between 1 and 5.
                max_breadth: Maximum number of links followed per level/page.
                    Tavily supports values between 1 and 500.
                limit: Total number of links/pages processed before stopping.
                    Must be >= 1.
                instructions: Optional natural-language guidance to bias crawl
                    and extraction toward specific topics or sections.
                extract_depth: Extraction depth applied to crawled pages.
                    - ``basic``: faster and cheaper.
                    - ``advanced``: richer extraction with higher latency/cost.
                    If ``None``, Tavily default behavior is used.
            """
            if not _has_tavily_api_key():
                return (
                    "Web crawl is unavailable: TAVILY_API_KEY is not set. "
                    "Ask the user to configure it and restart."
                )

            try:
                tavily_client = TavilyClient()

                crawl_kwargs: dict[str, Any] = {}
                if max_depth is not None:
                    crawl_kwargs["max_depth"] = max_depth
                if max_breadth is not None:
                    crawl_kwargs["max_breadth"] = max_breadth
                if limit is not None:
                    crawl_kwargs["limit"] = limit
                if instructions is not None:
                    crawl_kwargs["instructions"] = instructions
                if extract_depth is not None:
                    crawl_kwargs["extract_depth"] = extract_depth

                return tavily_client.crawl(
                    url,
                    timeout=timeout,
                    **crawl_kwargs,
                )
            except Exception as exc:
                logger.warning("web_crawl failed: %s", exc)
                return (
                    f"Web crawl failed: {exc}. "
                    "The TAVILY_API_KEY may be invalid or the service may be "
                    "temporarily unavailable."
                )

        web_crawl_tool.__doc__ = params.format_docstring(web_crawl_tool.__doc__)
        return web_crawl_tool
