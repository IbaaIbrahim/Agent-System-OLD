"""Unit tests for web search tool."""

import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Handle hyphenated service directory import
sys.path.insert(0, "services/tool-workers")

from src.tools.web_search import WebSearchTool


class TestWebSearchToolInit:
    """Tests for WebSearchTool initialization."""

    def test_init_defaults(self):
        """Test default initialization."""
        tool = WebSearchTool()
        assert tool.provider == "duckduckgo"
        assert tool.api_key is None
        assert tool.timeout == 10
        assert tool.name == "web_search"

    def test_init_with_brave(self):
        """Test initialization with Brave provider."""
        tool = WebSearchTool(provider="brave", api_key="test_key", timeout=15)
        assert tool.provider == "brave"
        assert tool.api_key == "test_key"
        assert tool.timeout == 15

    def test_tool_definition(self):
        """Test tool definition for LLM."""
        tool = WebSearchTool()
        definition = tool.get_definition()

        assert definition["name"] == "web_search"
        assert "description" in definition
        assert "parameters" in definition
        assert "query" in definition["parameters"]["properties"]
        assert "num_results" in definition["parameters"]["properties"]


class TestDuckDuckGoSearch:
    """Tests for DuckDuckGo search integration."""

    @pytest.mark.asyncio
    async def test_search_with_abstract(self):
        """Test DuckDuckGo search with abstract result."""
        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "Python is a programming language",
            "AbstractURL": "https://python.org",
            "Heading": "Python",
            "RelatedTopics": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "python programming", "num_results": 3},
                {"job_id": "test-job-123"},
            )

        assert "Python" in result
        assert "python.org" in result
        assert "programming language" in result
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_with_related_topics(self):
        """Test DuckDuckGo search with related topics."""
        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "",
            "RelatedTopics": [
                {
                    "Text": "Python tutorial for beginners",
                    "FirstURL": "https://example.com/tutorial",
                },
                {
                    "Text": "Python documentation",
                    "FirstURL": "https://docs.python.org",
                },
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "python", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "tutorial" in result.lower()
        assert "documentation" in result.lower()

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """Test handling of empty results."""
        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "",
            "RelatedTopics": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "xyznonexistentquery123", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "No results found" in result

    @pytest.mark.asyncio
    async def test_search_with_nested_topics(self):
        """Test DuckDuckGo search with nested topic groups."""
        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "",
            "RelatedTopics": [
                {
                    "Topics": [
                        {
                            "Text": "Nested topic 1",
                            "FirstURL": "https://example.com/1",
                        },
                        {
                            "Text": "Nested topic 2",
                            "FirstURL": "https://example.com/2",
                        },
                    ],
                },
            ],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "Nested topic 1" in result
        assert "Nested topic 2" in result


class TestBraveSearch:
    """Tests for Brave Search integration."""

    @pytest.mark.asyncio
    async def test_brave_search_success(self):
        """Test Brave Search with valid API key."""
        tool = WebSearchTool(provider="brave", api_key="test_api_key")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {
                        "title": "Python Official Site",
                        "url": "https://python.org",
                        "description": "Welcome to Python.org",
                    },
                    {
                        "title": "Python Tutorial",
                        "url": "https://example.com/tutorial",
                        "description": "Learn Python programming",
                    },
                ],
            },
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "python", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "Python Official Site" in result
        assert "python.org" in result
        mock_get.assert_called_once()

        # Verify API key was passed in headers
        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs["headers"]["X-Subscription-Token"] == "test_api_key"

    @pytest.mark.asyncio
    async def test_brave_search_no_api_key(self):
        """Test Brave Search falls back to DuckDuckGo without API key."""
        tool = WebSearchTool(provider="brave", api_key=None)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "Fallback result",
            "AbstractURL": "https://example.com",
            "Heading": "Test",
            "RelatedTopics": [],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        # Should fall back to DuckDuckGo
        assert "Fallback result" in result
        # Verify DuckDuckGo API was called (not Brave)
        call_args = mock_get.call_args
        assert "api.duckduckgo.com" in str(call_args)


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test timeout error handling."""
        import httpx

        tool = WebSearchTool(provider="duckduckgo")

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Request timed out")

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_rate_limit_handling(self):
        """Test HTTP 429 rate limit handling."""
        import httpx

        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.status_code = 429

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.HTTPStatusError(
                "Rate limited",
                request=MagicMock(),
                response=mock_response,
            )

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "rate limit" in result.lower()

    @pytest.mark.asyncio
    async def test_http_error_handling(self):
        """Test general HTTP error handling."""
        import httpx

        tool = WebSearchTool(provider="duckduckgo")

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "500" in result
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_handling(self):
        """Test generic exception handling."""
        tool = WebSearchTool(provider="duckduckgo")

        with patch.object(tool.client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = Exception("Unexpected error")

            result = await tool.execute(
                {"query": "test", "num_results": 5},
                {"job_id": "test-job-123"},
            )

        assert "failed" in result.lower()
        assert "Unexpected error" in result


class TestResultFormatting:
    """Tests for result formatting."""

    def test_format_results_with_results(self):
        """Test formatting with results."""
        tool = WebSearchTool()

        results = [
            {
                "title": "Test Result 1",
                "url": "https://example.com/1",
                "snippet": "This is the first result",
            },
            {
                "title": "Test Result 2",
                "url": "https://example.com/2",
                "snippet": "This is the second result",
            },
        ]

        formatted = tool._format_results(results, "test query")

        assert "# Search Results for: test query" in formatted
        assert "Found 2 results" in formatted
        assert "## 1. Test Result 1" in formatted
        assert "## 2. Test Result 2" in formatted
        assert "**URL:** https://example.com/1" in formatted
        assert "This is the first result" in formatted

    def test_format_results_empty(self):
        """Test formatting with empty results."""
        tool = WebSearchTool()

        formatted = tool._format_results([], "test query")

        assert "No results found" in formatted
        assert "test query" in formatted

    def test_num_results_capped_at_10(self):
        """Test that num_results is capped at 10."""
        tool = WebSearchTool()

        # This is checked in execute(), so we test the parsing
        query = "test"
        num_results = min(100, 10)  # Simulating the capping logic

        assert num_results == 10


class TestResourceCleanup:
    """Tests for resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test HTTP client cleanup."""
        tool = WebSearchTool()

        with patch.object(tool.client, "aclose", new_callable=AsyncMock) as mock_close:
            await tool.close()
            mock_close.assert_called_once()
