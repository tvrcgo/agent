from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.core.tool import Tool

logger = logging.getLogger(__name__)

SEARXNG_URL_DEFAULT = "http://searxng:8080"


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for information. Returns title, URL, and snippet for each result."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._searxng_url = self.config.get("search_url", SEARXNG_URL_DEFAULT)

    async def execute(self, arguments: dict, ctx=None) -> str:
        query = arguments.get("query", "")
        max_results = int(arguments.get("max_results", 5))

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._searxng_url}/search",
                    params={"q": query, "format": "json"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("SearXNG request failed for query=%s: %s", query, e)
            return f"Search failed: {e}"

        results = data.get("results", [])
        if not results:
            return f"No results found for: {query}"
        return self._format_results(results, max_results)

    @staticmethod
    def _format_results(results: list[dict], max_results: int) -> str:
        lines = []
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}\n   {url}\n   {content}")
        return "\n\n".join(lines)
