"""MCP Plugin - syncs tools from agent-mcp service."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

import httpx

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 600  # 10 minutes
RETRY_INTERVAL = 30  # retry on failure


class MCPTool(Tool):
    """MCP tool that calls via HTTP."""

    def __init__(self, name: str, description: str, parameters: dict[str, Any], plugin: "MCPPlugin") -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._plugin = plugin

    async def execute(self, arguments: dict[str, Any], ctx: "AgentContext", job: "Job") -> str:
        return await self._plugin.call_tool(self.name, arguments)


class MCPPlugin(Plugin):
    """Plugin that syncs MCP tools from agent-mcp service."""

    name = "mcp"

    def __init__(self) -> None:
        self._base_url = "http://localhost:8001"
        self._http: httpx.AsyncClient | None = None
        self._tools: dict[str, MCPTool] = {}
        self._sync_task: asyncio.Task[None] | None = None
        self._ctx: AgentContext | None = None

    def load(self, registry: PluginRegistry, config: dict[str, Any] = {}) -> None:
        self._base_url = config.get("base_url", "http://localhost:8001").rstrip("/")
        registry.on("agent_start", self._on_start)
        registry.on("agent_stop", self._on_stop)
        logger.info("MCP plugin loaded, base_url=%s", self._base_url)

    def unload(self) -> None:
        self._tools.clear()

    async def _on_start(self, ctx: "AgentContext", job: "Job | None") -> None:
        self._ctx = ctx
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=60.0)
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("MCP plugin started")

    async def _on_stop(self, ctx: "AgentContext", job: "Job | None") -> None:
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        if self._http:
            await self._http.aclose()
            self._http = None
        logger.info("MCP plugin stopped")

    async def _sync_loop(self) -> None:
        """Sync tools periodically."""
        while True:
            try:
                await self._sync_tools()
                await asyncio.sleep(SYNC_INTERVAL)
            except Exception as e:
                logger.warning("Failed to sync MCP tools: %s", e)
                await asyncio.sleep(RETRY_INTERVAL)

    async def _sync_tools(self) -> None:
        """Fetch tools from agent-mcp, update registry."""
        if not self._http or not self._ctx or not self._ctx.tools:
            return

        resp = await self._http.get("/tools")
        resp.raise_for_status()
        tools_data = resp.json()

        current_names = set(t["name"] for t in tools_data)
        prev_names = set(self._tools.keys())

        # Remove tools that no longer exist
        removed = prev_names - current_names
        if removed:
            self._ctx.tools.unregister(list(removed))
            for name in removed:
                del self._tools[name]
            logger.info("MCP tools removed: %s", list(removed))

        # Add new tools
        added = []
        for t in tools_data:
            name = t["name"]
            if name not in self._tools:
                tool = MCPTool(
                    name=name,
                    description=t.get("description", ""),
                    parameters=t.get("parameters", {}),
                    plugin=self,
                )
                self._tools[name] = tool
                added.append(tool)

        if added:
            self._ctx.tools.register(added)
            logger.info("MCP tools added: %s", [t.name for t in added])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool via HTTP."""
        if not self._http:
            raise RuntimeError("MCP plugin not started")

        resp = await self._http.post(f"/tools/{name}/call", json={"arguments": arguments})
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            raise RuntimeError(data["error"])

        return data.get("result", "")
