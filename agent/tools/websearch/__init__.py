from __future__ import annotations

import logging

from ddgs import DDGS

from agent.core.tool import Tool

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
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 15)",
                "default": 15,
            },
        },
        "required": ["query"],
    }

    async def execute(self, arguments: dict, ctx=None) -> str:
        query = arguments.get("query", "")
        max_results = int(arguments.get("max_results", 5))
        timeout = int(arguments.get("timeout", 15))

        for backend in ("yandex",):
            try:
                results = list(DDGS(timeout=timeout).text(query, max_results=max_results, backend=backend))
                if results:
                    return self._format_results(results, max_results)
            except Exception as e:
                logger.info("Backend %s failed for query=%s: %s", backend, query, e)

        return f"No results found for: {query}"

    @staticmethod
    def _format_results(results: list[dict], max_results: int) -> str:
        lines = []
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "Untitled")
            href = r.get("href", "")
            body = r.get("body", "")
            lines.append(f"{i}. {title}\n   {href}\n   {body}")
        return "\n\n".join(lines)
