from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.exceptions import ConnectionClosed

from agent.core.plugin import Plugin
from agent.core.io import InputMessage, OutputMessage
from agent.core.loop import AgentContext
from agent.core.events import Event


logger = logging.getLogger(__name__)


@dataclass
class HeartbeatEvent:
    type: str = "heartbeat"


AgentEvent = OutputMessage | HeartbeatEvent


def _serialize_event(event: AgentEvent) -> str:
    data = asdict(event)
    event_type = data.pop("type")
    envelope = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": data,
    }
    return json.dumps(envelope, ensure_ascii=False)


@dataclass
class ChatMessage:
    content: str


@dataclass
class CommandMessage:
    action: str
    data: dict[str, Any] = field(default_factory=dict)


IncomingMessage = ChatMessage | CommandMessage


def _parse_incoming(raw: str) -> IncomingMessage:
    data = json.loads(raw)
    msg_type = data.get("type", "")
    payload = data.get("payload", {})

    if msg_type == "chat":
        return ChatMessage(content=payload["content"])
    elif msg_type == "command":
        action = payload.pop("action", "")
        return CommandMessage(action=action, data=payload)
    else:
        raise ValueError(f"Unknown message type: {msg_type}")


class ClientSession:

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws
        self.session_id: str | None = None
        self._outgoing: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        self._closed = asyncio.Event()

    def start(self) -> None:
        asyncio.create_task(self._write_loop())

    def enqueue(self, event: AgentEvent) -> None:
        self._outgoing.put_nowait(event)

    async def flush(self) -> None:
        if not self._closed.is_set():
            await self._outgoing.join()

    def close(self) -> None:
        self._outgoing.put_nowait(None)

    async def _write_loop(self) -> None:
        try:
            while True:
                event = await self._outgoing.get()
                try:
                    if event is None:
                        break
                    await self._ws.send(_serialize_event(event))
                except websockets.exceptions.ConnectionClosed:
                    break
                finally:
                    self._outgoing.task_done()
        finally:
            self._closed.set()
            while True:
                try:
                    self._outgoing.get_nowait()
                    self._outgoing.task_done()
                except asyncio.QueueEmpty:
                    break


class _SuppressHandshakeNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            if isinstance(record.exc_info[1], ConnectionClosed):
                return False
        return True


class WebSocketPlugin(Plugin):

    name = "websocket"
    _handshake_filter_added = False

    def __init__(self) -> None:
        self._host: str = "0.0.0.0"
        self._port: int = 8765
        self._server: Server | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._ctx: AgentContext | None = None
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}

        if not WebSocketPlugin._handshake_filter_added:
            logging.getLogger("websockets.server").addFilter(_SuppressHandshakeNoise())
            WebSocketPlugin._handshake_filter_added = True

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        self._host = config.get('host', '0.0.0.0')
        self._port = config.get('port', 8765)

        ctx.on("agent_start", self._on_agent_start)
        ctx.on("agent_stop", self._on_agent_stop)
        ctx.on("msg_output", self._on_output)

        logger.info("WebSocketPlugin initialized, host=%s, port=%d", self._host, self._port)

    def unload(self) -> None:
        for task in self._heartbeat_tasks.values():
            task.cancel()
        self._heartbeat_tasks.clear()
        self._sessions.clear()
        logger.info("WebSocketPlugin shut down")

    async def _on_agent_start(self, ctx: AgentContext, evt: Event) -> None:
        self._ctx = ctx
        await self._start_server()

    async def _on_agent_stop(self, ctx: AgentContext, evt: Event) -> None:
        for session in self._sessions.values():
            await session.flush()
        await self._stop_server()
        self.unload()

    async def _start_server(self) -> None:
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
            ping_interval=None,
            ping_timeout=None,
        )
        logger.info("WebSocket server listening on ws://%s:%d", self._host, self._port)

    async def _stop_server(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, ws: ServerConnection) -> None:
        request_path = ws.request.path if ws.request else ""
        parsed = urlparse(str(request_path))
        qs = parse_qs(parsed.query)
        session_id = qs.get("session_id", [None])[0]

        if not session_id:
            error_event = OutputMessage(type="error", data={"code": "bad_request", "message": "session_id is required"}, session_id="")
            await ws.send(_serialize_event(error_event))
            await ws.close(4000, "session_id is required")
            return

        session = ClientSession(ws)
        session.session_id = session_id
        self._sessions[session_id] = session
        session.start()

        logger.info("Client connected: %s, session_id=%s", ws.remote_address, session_id)

        heartbeat_task = asyncio.create_task(self._heartbeat(session))
        self._heartbeat_tasks[session_id] = heartbeat_task

        try:
            async for raw in ws:
                try:
                    msg = _parse_incoming(str(raw))
                    if self._ctx is None:
                        continue

                    if isinstance(msg, ChatMessage):
                        input_msg = InputMessage(
                            content=msg.content,
                            session_id=session_id
                        )
                    elif isinstance(msg, CommandMessage):
                        input_msg = InputMessage(
                            content="",
                            type="command",
                            action=msg.action,
                            data=msg.data,
                            session_id=session_id
                        )
                    else:
                        continue

                    await self._ctx.emit("msg_input", input=input_msg)
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    error_event = OutputMessage(type="error", data={"code": "parse_error", "message": str(e)}, session_id=session_id)
                    session.enqueue(error_event)
        except websockets.ConnectionClosed:
            pass
        finally:
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(session_id, None)
            session.close()
            self._sessions.pop(session_id, None)
            logger.info("Client disconnected: %s, session_id=%s", ws.remote_address, session_id)

    async def _heartbeat(self, session: ClientSession) -> None:
        try:
            while True:
                await asyncio.sleep(15)
                session.enqueue(HeartbeatEvent())
        except Exception:
            pass

    async def _on_output(self, ctx: AgentContext, evt: Event) -> None:
        output = evt.data.get("output")
        if output is None:
            return
        session = self._sessions.get(output.session_id)
        if session is None:
            return
        session.enqueue(output)
        if output.type == "confirm":
            # confirm 依赖送达：用户须先看到确认框，等待才有意义
            await session.flush()
