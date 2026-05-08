from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.core.tool import Tool

logger = logging.getLogger(__name__)


class GoogleSearchTool(Tool):
    name = "google_search"
    description = "Search the web using Google Custom Search API. High quality results."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default 5, max 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._api_key = self.config.get("api_key")
        self._cx = self.config.get("cx")  # Custom Search Engine ID

    async def execute(self, arguments: dict, ctx=None) -> str:
        if not self._api_key or not self._cx:
            return "Google Search not configured. Set api_key and cx in config."

        query = arguments.get("query", "")
        max_results = min(int(arguments.get("max_results", 5)), 10)

        try:
            results = await self._search(query, max_results)
            if not results:
                return f"No results for: {query}"
            return self._format_results(results)
        except Exception as e:
            logger.error("Google Search failed: %s", e)
            return f"Search error: {e}"

    async def _search(self, query: str, max_results: int) -> list[dict]:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "num": max_results,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
            }
            for item in items
        ]

    @staticmethod
    def _format_results(results: list[dict]) -> str:
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}\n   {url}\n   {content}")
        return "\n\n".join(lines)
