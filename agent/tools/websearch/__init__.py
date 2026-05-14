from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

import httpx
from ddgs import DDGS

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)


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
        self._searxng_url = self.config.get("search_url")

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        query = arguments.get("query", "")
        max_results = int(arguments.get("max_results", 5))

        results = []
        if self._searxng_url:
            results = await self._search_searxng(query, max_results)
        if not results:
            results = await self._search_ddgs(query, max_results)
        if not results:
            return f"No results found for: {query}"
        return self._format_results(results, max_results)

    async def _search_searxng(self, query: str, max_results: int) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._searxng_url}/search",
                    params={"q": query, "format": "json"},
                )
                resp.raise_for_status()
                return resp.json().get("results", [])
        except Exception as e:
            logger.info("SearXNG unavailable, falling back to ddgs: %s", e)
            return []

    async def _search_ddgs(self, query: str, max_results: int) -> list[dict]:
        def _run() -> list[dict]:
            for backend in ("api", "html", "lite"):
                try:
                    results = list(DDGS(timeout=10).text(query, max_results=max_results, backend=backend))
                    if results:
                        return [{"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")} for r in results]
                except Exception as e:
                    logger.info("ddgs backend %s failed: %s", backend, e)
            return []

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("ddgs fallback failed: %s", e)
            return []

    @staticmethod
    def _format_results(results: list[dict], max_results: int) -> str:
        lines = []
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")
            lines.append(f"{i}. {title}\n   {url}\n   {content}")
        return "\n\n".join(lines)
