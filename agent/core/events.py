from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

EventHandler = Callable[["AgentContext", "Event"], Awaitable[None]]


_RESERVED = frozenset({"name", "job", "data", "request"})


@dataclass
class Event:
    name: str
    job: "Job | None" = None
    data: dict[str, Any] = field(default_factory=dict)
    request: Request | None = None

    def __getattr__(self, key: str) -> Any:
        data = object.__getattribute__(self, "data")
        if key in data:
            return data[key]
        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        if key in _RESERVED:
            object.__setattr__(self, key, value)
        else:
            object.__getattribute__(self, "data")[key] = value


@dataclass
class Request:
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    data: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    _done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    async def wait(self, timeout: float) -> Any | None:
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.result

    def done(self, result: Any) -> None:
        self.result = result
        self._done.set()


class EventBus:

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, job: "Job | None" = None, timeout: float = 120, ctx: "AgentContext | None" = None, **data: Any) -> Any:
        # req: 前缀 → 请求-响应；否则 → 广播
        if event.startswith("req:"):
            return await self._request(event, job, timeout, ctx=ctx, **data)
        return await self._broadcast(event, job, ctx=ctx, **data)

    async def _request(self, event: str, job: "Job | None", timeout: float, ctx: "AgentContext | None" = None, **data: Any) -> Any | None:
        req = Request(name=event, data=data)
        evt = Event(name=event, job=job, data={}, request=req)
        await self._dispatch(evt, ctx)
        return await req.wait(timeout)

    async def _broadcast(self, event: str, job: "Job | None", ctx: "AgentContext | None" = None, **data: Any) -> Event:
        evt = Event(name=event, job=job, data=data)
        await self._dispatch(evt, ctx)
        return evt

    async def _dispatch(self, evt: Event, ctx: "AgentContext | None" = None) -> None:
        if evt.request is not None:
            # 请求事件只允许单个响应方，隐式返回回填结果
            handlers = self._handlers.get(evt.name, [])
            if len(handlers) > 1:
                raise RuntimeError(f"Request event '{evt.name}' has multiple handlers")
            if handlers:
                result = await handlers[0](ctx, evt)
                if result is not None:
                    evt.request.done(result)
            return
        # 广播：同步并发，单个 handler 异常隔离
        handlers = self._handlers.get(evt.name, [])
        if handlers:
            await asyncio.gather(*(self._safe_call(h, ctx, evt) for h in handlers))

    async def _safe_call(self, handler: EventHandler, ctx: "AgentContext | None", evt: Event) -> None:
        try:
            await handler(ctx, evt)
        except Exception:
            logger.exception("Event handler error for '%s'", evt.name)
