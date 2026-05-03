from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import Server, ServerConnection

logger = logging.getLogger(__name__)



@dataclass
class MessageEvent:
    content: str
    type: str = "message"


@dataclass
class ToolCallEvent:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_call"


@dataclass
class ToolResultEvent:
    id: str
    name: str
    result: str
    error: str | None = None
    type: str = "tool_result"


@dataclass
class StatusEvent:
    status: str  # "thinking" | "acting" | "waiting" | "idle" | "done"
    content: str = ""
    type: str = "status"


@dataclass
class ErrorEvent:
    code: str
    message: str
    type: str = "error"


AgentEvent = (
    MessageEvent
    | ToolCallEvent
    | ToolResultEvent
    | StatusEvent
    | ErrorEvent
)


def _serialize_event(event: AgentEvent) -> str:
    payload = asdict(event)
    event_type = payload.pop("type")
    envelope = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    return json.dumps(envelope, ensure_ascii=False)



@dataclass
class ChatMessage:
    content: str


@dataclass
class CommandMessage:
    """Routes action to command:<action> hook."""
    action: str
    data: dict[str, Any] = field(default_factory=dict)


IncomingMessage = ChatMessage | CommandMessage


def _parse_incoming(raw: str) -> IncomingMessage:
    data = json.loads(raw)
    msg_type = data.get("type", "")
    payload = data.get("payload", {})

    match msg_type:
        case "chat":
            return ChatMessage(content=payload["content"])
        case "command":
            action = payload.pop("action", "")
            return CommandMessage(action=action, data=payload)
        case _:
            raise ValueError(f"Unknown message type: {msg_type}")



MessageHandler = Callable[[IncomingMessage, "ClientSession"], Coroutine[Any, Any, None]]
ConnectHandler = Callable[["ClientSession"], Coroutine[Any, Any, None]]
DisconnectHandler = Callable[["ClientSession"], Coroutine[Any, Any, None]]


class ClientSession:

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws
        self.session_id: str | None = None

    async def emit(self, event: AgentEvent) -> None:
        await self._ws.send(_serialize_event(event))


class WebSocketServer:

    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        self._host = host
        self._port = port
        self._server: Server | None = None
        self._message_handler: MessageHandler | None = None
        self._connect_handler: ConnectHandler | None = None
        self._disconnect_handler: DisconnectHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    def on_connect(self, handler: ConnectHandler) -> None:
        self._connect_handler = handler

    def on_disconnect(self, handler: DisconnectHandler) -> None:
        self._disconnect_handler = handler

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
        )
        logger.info(f"WebSocket server listening on ws://{self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, ws: ServerConnection) -> None:
        session = ClientSession(ws)

        request_path = ws.request.path if ws.request else ""
        parsed = urlparse(str(request_path))
        qs = parse_qs(parsed.query)
        session.session_id = qs.get("session_id", [None])[0]

        logger.info(f"Client connected: {ws.remote_address}, session_id={session.session_id}")

        if self._connect_handler:
            try:
                await self._connect_handler(session)
            except Exception:
                logger.warning("Connect handler error", exc_info=True)

        try:
            async for raw in ws:
                try:
                    msg = _parse_incoming(str(raw))
                    if self._message_handler:
                        await self._message_handler(msg, session)
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    await session.emit(ErrorEvent(code="parse_error", message=str(e)))
        except websockets.ConnectionClosed:
            pass
        finally:
            logger.info(f"Client disconnected: {ws.remote_address}, session_id={session.session_id}")
            if self._disconnect_handler:
                try:
                    await self._disconnect_handler(session)
                except Exception:
                    logger.warning("Disconnect handler error", exc_info=True)
