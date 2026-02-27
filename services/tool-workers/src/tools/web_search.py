"""Web search tool implementation."""

import asyncio
import time
from typing import Any, Literal

import httpx

from libs.common import get_logger

from .base import BaseTool, catalog_tool

logger = get_logger(__name__)

# Stagger delay between parallel DuckDuckGo requests to avoid rate limiting.
# When multiple tool workers hit DDG simultaneously from the same IP,
# requests get throttled or blocked. This adds a small random delay.
_DDG_STAGGER_SECONDS = 1.5


@catalog_tool("web_search")
class WebSearchTool(BaseTool):
    """Tool for performing web searches.

    Supports DuckDuckGo (no API key required) and Brave Search (requires API key).
    """

    def __init__(
        self,
        provider: Literal["duckduckgo", "brave"] = "duckduckgo",
        api_key: str | None = None,
        timeout: int = 10,
    ) -> None:
        """Initialize web search tool.

        Args:
            provider: Search provider to use ("duckduckgo" or "brave")
            api_key: API key for Brave Search (optional, only if provider="brave")
            timeout: HTTP request timeout in seconds (default: 10)
        """
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Execute web search.

        Args:
            arguments: Search arguments
            context: Execution context

        Returns:
            Search results as formatted string
        """
        query = arguments.get("query", "")
        num_results = min(arguments.get("num_results", 5), 10)
        job_id = context.get("job_id")
        tool_call_id = context.get("tool_call_id")

        logger.info(
            "Executing web search",
            query=query,
            num_results=num_results,
            provider=self.provider,
            job_id=job_id,
            tool_call_id=tool_call_id,
        )

        start_time = time.monotonic()

        try:
            # Route to appropriate search provider
            if self.provider == "brave" and self.api_key:
                results = await self._search_brave(query, num_results)
            else:
                # Default to DuckDuckGo
                results = await self._search_duckduckgo(query, num_results)

            elapsed = time.monotonic() - start_time

            # Format results
            formatted = self._format_results(results, query)

            logger.info(
                "Web search completed",
                query=query,
                result_count=len(results),
                elapsed_seconds=round(elapsed, 2),
                job_id=job_id,
                tool_call_id=tool_call_id,
            )

            return formatted

        except httpx.TimeoutException:
            elapsed = time.monotonic() - start_time
            logger.error(
                "Web search timed out",
                query=query,
                timeout=self.timeout,
                elapsed_seconds=round(elapsed, 2),
                job_id=job_id,
                tool_call_id=tool_call_id,
            )
            return f"Search timed out after {self.timeout} seconds. Please try again or rephrase your query."

        except httpx.HTTPStatusError as e:
            elapsed = time.monotonic() - start_time
            logger.error(
                "Web search HTTP error",
                query=query,
                status_code=e.response.status_code,
                elapsed_seconds=round(elapsed, 2),
                job_id=job_id,
                tool_call_id=tool_call_id,
            )
            if e.response.status_code == 429:
                return "Search rate limit exceeded. Please try again in a few moments."
            return f"Search service error (HTTP {e.response.status_code}). Please try again."

        except Exception as e:
            elapsed = time.monotonic() - start_time
            logger.error(
                "Web search failed",
                query=query,
                provider=self.provider,
                error=str(e),
                error_type=type(e).__name__,
                elapsed_seconds=round(elapsed, 2),
                job_id=job_id,
                tool_call_id=tool_call_id,
            )
            return f"Search failed: {str(e)}"

    async def _search_duckduckgo(
        self,
        query: str,
        num_results: int,
    ) -> list[dict[str, Any]]:
        """Search using DuckDuckGo via ddgs (real web search).

        Uses the ddgs library for actual web search results (not the Instant
        Answer API, which returns no results for most queries).

        Includes a stagger delay to avoid rate limiting when multiple
        parallel searches hit DuckDuckGo from the same IP.

        Args:
            query: Search query
            num_results: Maximum number of results to return

        Returns:
            List of search results with title, url, and snippet
        """
        import random

        from ddgs import DDGS

        # Stagger parallel requests to avoid DuckDuckGo rate limiting
        stagger = random.uniform(0, _DDG_STAGGER_SECONDS)
        if stagger > 0:
            logger.debug(
                "Staggering DuckDuckGo request",
                delay_seconds=round(stagger, 2),
                query=query[:60],
            )
            await asyncio.sleep(stagger)

        def _run_text_search() -> list[dict[str, Any]]:
            with DDGS() as ddgs_client:
                # text() returns generator of dicts: title, href, body
                raw = list(ddgs_client.text(query, max_results=num_results))
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in raw
            ]

        # DDGS is sync; run in thread pool to avoid blocking.
        # Retry once on failure (DuckDuckGo can be flaky under load).
        max_retries = 2
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                results = await asyncio.to_thread(_run_text_search)
                return results[:num_results]
            except Exception as e:
                last_error = e
                logger.warning(
                    "DuckDuckGo search attempt failed",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    query=query[:60],
                    error=str(e),
                    error_type=type(e).__name__,
                )
                if attempt < max_retries - 1:
                    # Wait before retry with exponential backoff
                    await asyncio.sleep(2 ** attempt)

        raise last_error  # type: ignore[misc]

    async def _search_brave(
        self,
        query: str,
        num_results: int,
    ) -> list[dict[str, Any]]:
        """Search using Brave Search API.

        Requires Brave Search API key. Get yours at: https://brave.com/search/api/

        Args:
            query: Search query
            num_results: Maximum number of results to return

        Returns:
            List of search results with title, url, and snippet
        """
        if not self.api_key:
            raise ValueError("Brave Search API key is required but not provided")

        response = await self.client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": self.api_key},
            params={"q": query, "count": num_results},
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for result in data.get("web", {}).get("results", []):
            results.append({
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "snippet": result.get("description", ""),
            })

        return results

    def _format_results(
        self,
        results: list[dict[str, Any]],
        query: str,
    ) -> str:
        """Format search results as markdown for LLM consumption.

        Args:
            results: Search results
            query: Original search query

        Returns:
            Formatted markdown string
        """
        if not results:
            return f"No results found for query: '{query}'. Try rephrasing your search."

        lines = [f"# Search Results for: {query}\n"]
        lines.append(f"Found {len(results)} results:\n")

        for i, result in enumerate(results, 1):
            lines.append(f"## {i}. {result['title']}")
            lines.append(f"**URL:** {result['url']}")
            lines.append(f"{result['snippet']}\n")

        return "\n".join(lines)

    async def close(self) -> None:
        """Clean up HTTP client resources."""
        await self.client.aclose()
