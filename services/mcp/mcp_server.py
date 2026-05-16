"""Agent MCP HTTP Server.

Manages MCP server processes and exposes HTTP API for tool discovery and execution.
Supports both stdio and SSE transports.
SSE transport follows MCP spec: GET /sse for event stream, POST to message endpoint for requests.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
import yaml
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
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


class MCPClientStdio:
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
        env = os.environ.copy()
        env.update(self._env)

        logger.info("Starting MCP server %s: %s %s", self.name, self._command, " ".join(self._args))

        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._reader_task = asyncio.create_task(self._read_loop())

        result = await asyncio.wait_for(
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-mcp", "version": "0.1.0"},
            }),
            timeout=30.0
        )
        logger.info("MCP server %s initialized: %s", self.name, result.get("serverInfo", {}).get("name", "unknown"))

        await self._load_tools()

    async def stop(self) -> None:
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
        parts = name.split("__", 1)
        tool_name = parts[1] if len(parts) > 1 else name

        result = await self._request("tools/call", {"name": tool_name, "arguments": arguments})
        return self._extract_result(result)

    def _extract_result(self, result: dict) -> str:
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError(f"MCP server {self.name} not started")

        self._request_id += 1
        req_id = self._request_id
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[req_id] = future

        line = json.dumps(request) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()

        return await future

    async def _read_loop(self) -> None:
        if self._process is None or self._process.stdout is None:
            return

        try:
            reader = self._process.stdout
            while True:
                line = await reader.readline()
                if not line:
                    break
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


class MCPClientSSE:
    """MCP server client via SSE transport.
    
    SSE transport follows MCP spec:
    1. GET /sse to establish SSE event stream
    2. Server sends 'endpoint' event with message URL
    3. Client POSTs JSON-RPC requests to message endpoint
    4. Responses arrive via SSE stream
    """

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self._url = config["url"].rstrip("/")
        self._headers = config.get("headers", {})
        self._tools_filter = config.get("tools")
        self._http: httpx.AsyncClient | None = None
        self._request_id = 0
        self._tools: list[ToolDefinition] = []
        self._message_endpoint: str = ""
        self._sse_task: asyncio.Task[None] | None = None
        self._responses: dict[int, asyncio.Future[Any]] = {}
        self._connected = asyncio.Event()

    async def start(self) -> None:
        logger.info("Connecting to SSE MCP server %s: %s", self.name, self._url)

        self._http = httpx.AsyncClient(
            base_url=self._url,
            headers={**self._headers, "Accept": "text/event-stream"},
            timeout=60.0,
        )

        # Start SSE connection
        self._sse_task = asyncio.create_task(self._sse_loop())

        # Wait for endpoint event
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            self._sse_task.cancel()
            raise RuntimeError(f"SSE MCP server {self.name} did not send endpoint")

        # Send initialize request
        result = await asyncio.wait_for(
            self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-mcp", "version": "0.1.0"},
            }),
            timeout=30.0
        )
        logger.info("SSE MCP server %s initialized: %s", self.name, result.get("serverInfo", {}).get("name", "unknown"))

        await self._load_tools()

    async def stop(self) -> None:
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            self._sse_task = None

        if self._http:
            await self._http.aclose()
            self._http = None
        self._tools.clear()
        self._responses.clear()

    async def _sse_loop(self) -> None:
        """Read SSE events and route responses to pending requests."""
        event_type = None
        try:
            async with self._http.stream("GET", "/sse") as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_str = line[5:].strip()
                        if event_type == "endpoint":
                            # Message endpoint URL
                            self._message_endpoint = data_str
                            self._connected.set()
                            logger.info("SSE MCP %s received endpoint: %s", self.name, data_str)
                        elif data_str:
                            # JSON-RPC response
                            try:
                                data = json.loads(data_str)
                                req_id = data.get("id")
                                if req_id is not None and req_id in self._responses:
                                    future = self._responses.pop(req_id)
                                    if "error" in data:
                                        future.set_exception(RuntimeError(data["error"]))
                                    else:
                                        future.set_result(data.get("result", {}))
                            except json.JSONDecodeError:
                                pass
                    elif line == "":
                        event_type = None
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("SSE MCP client %s error: %s", self.name, e)

    async def _load_tools(self) -> None:
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

        logger.info("SSE MCP server %s loaded %d tools", self.name, len(self._tools))

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        parts = name.split("__", 1)
        tool_name = parts[1] if len(parts) > 1 else name

        result = await self._request("tools/call", {"name": tool_name, "arguments": arguments})
        return self._extract_result(result)

    def _extract_result(self, result: dict) -> str:
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if not self._message_endpoint:
            raise RuntimeError(f"SSE MCP server {self.name} not connected")

        self._request_id += 1
        req_id = self._request_id

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._responses[req_id] = future

        # POST to message endpoint
        try:
            resp = await self._http.post(
                self._message_endpoint,
                json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
            )
            resp.raise_for_status()
        except Exception as e:
            self._responses.pop(req_id, None)
            raise

        return await future

    def get_tools(self) -> list[ToolDefinition]:
        return self._tools


MCPClient = MCPClientStdio | MCPClientSSE


def create_client(name: str, config: dict[str, Any]) -> MCPClient:
    """Create appropriate MCP client based on config."""
    if "url" in config:
        return MCPClientSSE(name, config)
    return MCPClientStdio(name, config)


class MCPManager:
    """Manage all MCP server connections."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tools_index: dict[str, tuple[str, MCPClient]] = {}

    async def start(self, servers: dict[str, dict[str, Any]]) -> None:
        for name, config in servers.items():
            client = create_client(name, config)
            self._clients[name] = client
            self._tasks[name] = asyncio.create_task(self._start_server(name, client))

    async def _start_server(self, name: str, client: MCPClient) -> None:
        try:
            await client.start()
            for tool in client.get_tools():
                self._tools_index[tool.name] = (name, client)
        except Exception as e:
            logger.error("Failed to start MCP server %s: %s", name, e)

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

        for client in self._clients.values():
            await client.stop()
        self._clients.clear()
        self._tools_index.clear()

    def get_tools(self) -> list[ToolDefinition]:
        tools = []
        for client in self._clients.values():
            tools.extend(client.get_tools())
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self._tools_index:
            raise ValueError(f"Unknown tool: {name}")
        _, client = self._tools_index[name]
        return await client.call_tool(name, arguments)


manager = MCPManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    return manager.get_tools()


@app.post("/tools/{name}/call")
async def call_tool(name: str, request: ToolCallRequest) -> ToolCallResponse:
    try:
        result = await manager.call_tool(name, request.arguments)
        return ToolCallResponse(result=result)
    except Exception as e:
        return ToolCallResponse(result="", error=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "servers": list(manager._clients.keys())}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
