from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.server import Server, ServerConnection

logger = logging.getLogger(__name__)


# --- Event types emitted by the agent ---


@dataclass
class ThinkingEvent:
    content: str
    type: str = "thinking"


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
class ConfirmEvent:
    id: str
    title: str
    description: str
    actions: list[dict[str, str]] = field(default_factory=list)
    type: str = "confirm"


@dataclass
class StatusEvent:
    state: str  # "thinking" | "acting" | "waiting" | "idle" | "done"
    type: str = "status"


@dataclass
class ErrorEvent:
    code: str
    message: str
    type: str = "error"


AgentEvent = (
    ThinkingEvent
    | MessageEvent
    | ToolCallEvent
    | ToolResultEvent
    | ConfirmEvent
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


# --- Incoming message types ---


@dataclass
class TaskMessage:
    content: str


@dataclass
class UserMessage:
    content: str


@dataclass
class ConfirmResponse:
    id: str
    action_id: str


@dataclass
class CancelMessage:
    pass


IncomingMessage = TaskMessage | UserMessage | ConfirmResponse | CancelMessage


def _parse_incoming(raw: str) -> IncomingMessage:
    data = json.loads(raw)
    msg_type = data.get("type", "")
    payload = data.get("payload", {})

    match msg_type:
        case "task":
            return TaskMessage(content=payload["content"])
        case "message":
            return UserMessage(content=payload["content"])
        case "confirm_response":
            return ConfirmResponse(id=payload["id"], action_id=payload["action_id"])
        case "cancel":
            return CancelMessage()
        case _:
            raise ValueError(f"Unknown message type: {msg_type}")


# --- WebSocket Server ---

MessageHandler = Callable[[IncomingMessage, "ClientSession"], Coroutine[Any, Any, None]]


class ClientSession:
    """Represents a connected WebSocket client."""

    def __init__(self, ws: ServerConnection) -> None:
        self._ws = ws
        self._confirm_futures: dict[str, asyncio.Future[str]] = {}

    async def emit(self, event: AgentEvent) -> None:
        await self._ws.send(_serialize_event(event))

    async def request_confirm(
        self,
        confirm_id: str,
        title: str,
        description: str,
        actions: list[dict[str, str]],
    ) -> str:
        """Send a confirm event and wait for user response. Returns action_id."""
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._confirm_futures[confirm_id] = future
        await self.emit(
            ConfirmEvent(
                id=confirm_id,
                title=title,
                description=description,
                actions=actions,
            )
        )
        return await future

    def resolve_confirm(self, confirm_id: str, action_id: str) -> None:
        future = self._confirm_futures.pop(confirm_id, None)
        if future and not future.done():
            future.set_result(action_id)


class WebSocketServer:
    """WebSocket server for agent communication."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        self._host = host
        self._port = port
        self._server: Server | None = None
        self._handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

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
        logger.info(f"Client connected: {ws.remote_address}")
        try:
            async for raw in ws:
                try:
                    msg = _parse_incoming(str(raw))
                    if isinstance(msg, ConfirmResponse):
                        session.resolve_confirm(msg.id, msg.action_id)
                    elif self._handler:
                        await self._handler(msg, session)
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    await session.emit(ErrorEvent(code="parse_error", message=str(e)))
        except websockets.ConnectionClosed:
            pass
        finally:
            logger.info(f"Client disconnected: {ws.remote_address}")
