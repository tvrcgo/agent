"""Agent MCP HTTP Server.

Manages MCP server processes and exposes HTTP API for tool discovery and execution.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    result: str
    error: str = ""


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class MCPClient:
    """MCP server client via stdio transport."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self._command = config["command"]
        self._args = config.get("args", [])
        self._env = config.get("env", {})
        self._tools_filter = config.get("tools")
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._tools: list[ToolDefinition] = []

    async def start(self) -> None:
        """Start MCP server process and initialize session."""
        env = os.environ.copy()
        env.update(self._env)

        logger.info("Starting MCP server %s: %s %s", self.name, self._command, " ".join(self._args))

        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            logger.debug("Process started with PID %d", self._process.pid)
        except Exception as e:
            logger.error("Failed to start MCP server %s: %s", self.name, e)
            raise

        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            result = await asyncio.wait_for(
                self._request("initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agent-mcp", "version": "0.1.0"},
                }),
                timeout=30.0
            )
            logger.info("MCP server %s initialized: %s", self.name, result.get("serverInfo", {}).get("name", "unknown"))
        except asyncio.TimeoutError:
            logger.error("Timeout initializing MCP server %s", self.name)
            raise

        await self._load_tools()

    async def stop(self) -> None:
        """Stop MCP server process."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                await self._process.wait()
            except Exception:
                pass
            self._process = None

        self._pending.clear()
        self._tools.clear()

    async def _load_tools(self) -> None:
        """Load tools from server."""
        result = await self._request("tools/list", {})
        tools_raw = result.get("tools", [])

        for tool_def in tools_raw:
            tool_name = tool_def.get("name", "")
            if self._tools_filter and tool_name not in self._tools_filter:
                continue
            self._tools.append(ToolDefinition(
                name=f"mcp_{self.name}__{tool_name}",
                description=tool_def.get("description", ""),
                parameters=tool_def.get("inputSchema", {}),
            ))

        logger.info("MCP server %s loaded %d tools", self.name, len(self._tools))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and return result."""
        parts = name.split("_", 2)
        tool_name = parts[2] if len(parts) > 2 else name

        result = await self._request("tools/call", {"name": tool_name, "arguments": arguments})
        content = result.get("content", [])
        if not content:
            return ""
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        """Send JSON-RPC request and wait for response."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError(f"MCP server {self.name} not started")

        self._request_id += 1
        req_id = self._request_id
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future

        line = json.dumps(request) + "\n"
        logger.debug("Sending: %s", line.strip())
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        return await future

    async def _read_loop(self) -> None:
        """Read responses from server stdout."""
        if self._process is None or self._process.stdout is None:
            return

        try:
            reader = self._process.stdout
            while True:
                line = await reader.readline()
                if not line:
                    logger.debug("EOF from stdout")
                    break
                logger.debug("Received: %s", line.decode().strip())
                try:
                    data = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                req_id = data.get("id")
                if req_id is not None and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if "error" in data:
                        future.set_exception(RuntimeError(data["error"]))
                    else:
                        future.set_result(data.get("result", {}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP client %s read error: %s", self.name, e)

    def get_tools(self) -> list[ToolDefinition]:
        return self._tools


class MCPManager:
    """Manage all MCP server connections."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tools_index: dict[str, tuple[str, MCPClient]] = {}

    async def start(self, servers: dict[str, dict[str, Any]]) -> None:
        """Start all MCP servers asynchronously."""
        for name, config in servers.items():
            client = MCPClient(name, config)
            self._clients[name] = client
            self._tasks[name] = asyncio.create_task(self._start_server(name, client))

    async def _start_server(self, name: str, client: MCPClient) -> None:
        """Start a single MCP server."""
        try:
            await client.start()
            for tool in client.get_tools():
                self._tools_index[tool.name] = (name, client)
        except Exception as e:
            logger.error("Failed to start MCP server %s: %s", name, e)

    async def stop(self) -> None:
        """Stop all MCP servers."""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

        for client in self._clients.values():
            await client.stop()
        self._clients.clear()
        self._tools_index.clear()

    def get_tools(self) -> list[ToolDefinition]:
        """Get all tools from all servers."""
        tools = []
        for client in self._clients.values():
            tools.extend(client.get_tools())
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by name."""
        if name not in self._tools_index:
            raise ValueError(f"Unknown tool: {name}")
        _, client = self._tools_index[name]
        return await client.call_tool(name, arguments)


manager = MCPManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config and start MCP servers on startup."""
    config_path = os.environ.get("MCP_CONFIG", "config.yml")

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Config file not found: %s", config_path)
        config = {}

    servers = config.get("mcp", {}).get("servers", {})
    if servers:
        logger.info("Starting %d MCP servers...", len(servers))
        await manager.start(servers)

    yield

    await manager.stop()


app = FastAPI(title="Agent MCP Server", lifespan=lifespan)


@app.get("/tools")
async def list_tools() -> list[ToolDefinition]:
    """List all available MCP tools."""
    return manager.get_tools()


@app.post("/tools/{name}/call")
async def call_tool(name: str, request: ToolCallRequest) -> ToolCallResponse:
    """Call an MCP tool."""
    try:
        result = await manager.call_tool(name, request.arguments)
        return ToolCallResponse(result=result)
    except Exception as e:
        return ToolCallResponse(result="", error=str(e))


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "servers": list(manager._clients.keys())}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
