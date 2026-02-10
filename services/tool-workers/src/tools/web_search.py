"""Web search tool implementation."""

from typing import Any, Literal

import httpx

from libs.common import get_logger
from libs.common.tool_catalog import ToolBehavior

from .base import BaseTool

logger = get_logger(__name__)


class WebSearchTool(BaseTool):
    """Tool for performing web searches.

    Supports DuckDuckGo (no API key required) and Brave Search (requires API key).
    """

    name = "web_search"
    description = (
        "Search the web for information. Use this when you need to find "
        "current information, facts, or data that may not be in your training data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query",
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default: 5, max: 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    behavior = ToolBehavior.USER_ENABLED
    # required_plan_feature = "tools.web_search"
    required_plan_feature = None  # Always available, no plan restriction

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

        logger.info(
            "Executing web search",
            query=query,
            num_results=num_results,
            provider=self.provider,
            job_id=context.get("job_id"),
        )

        try:
            # Route to appropriate search provider
            if self.provider == "brave" and self.api_key:
                results = await self._search_brave(query, num_results)
            else:
                # Default to DuckDuckGo
                results = await self._search_duckduckgo(query, num_results)

            # Format results
            formatted = self._format_results(results, query)
            return formatted

        except httpx.TimeoutException:
            logger.error(
                "Web search timed out",
                query=query,
                timeout=self.timeout,
            )
            return f"Search timed out after {self.timeout} seconds. Please try again or rephrase your query."

        except httpx.HTTPStatusError as e:
            logger.error(
                "Web search HTTP error",
                query=query,
                status_code=e.response.status_code,
            )
            if e.response.status_code == 429:
                return "Search rate limit exceeded. Please try again in a few moments."
            return f"Search service error (HTTP {e.response.status_code}). Please try again."

        except Exception as e:
            logger.error(
                "Web search failed",
                query=query,
                provider=self.provider,
                error=str(e),
            )
            return f"Search failed: {str(e)}"

    async def _search_duckduckgo(
        self,
        query: str,
        num_results: int,
    ) -> list[dict[str, Any]]:
        """Search using DuckDuckGo Instant Answer API.

        Uses DuckDuckGo's free API (no authentication required).

        Args:
            query: Search query
            num_results: Maximum number of results to return

        Returns:
            List of search results with title, url, and snippet
        """
        response = await self.client.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
        )
        response.raise_for_status()
        data = response.json()

        results = []

        # Extract Abstract (main result)
        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["Abstract"],
            })

        # Extract Related Topics
        for topic in data.get("RelatedTopics", []):
            if len(results) >= num_results:
                break

            # Handle both direct topics and topic groups
            if isinstance(topic, dict):
                if "Text" in topic and "FirstURL" in topic:
                    # Direct topic
                    results.append({
                        "title": topic.get("Text", "")[:100],  # Truncate title
                        "url": topic.get("FirstURL", ""),
                        "snippet": topic.get("Text", ""),
                    })
                elif "Topics" in topic:
                    # Topic group - extract nested topics
                    for nested in topic.get("Topics", []):
                        if len(results) >= num_results:
                            break
                        if "Text" in nested and "FirstURL" in nested:
                            results.append({
                                "title": nested.get("Text", "")[:100],
                                "url": nested.get("FirstURL", ""),
                                "snippet": nested.get("Text", ""),
                            })

        return results[:num_results]

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
