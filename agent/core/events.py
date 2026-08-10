from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

EventHandler = Callable[["AgentContext", "Event"], Awaitable[None]]


_RESERVED = frozenset({"name", "job", "data"})


@dataclass
class Event:
    name: str
    job: "Job | None" = None
    data: dict[str, Any] = field(default_factory=dict)

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


class EventBus:

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event: str, handler: EventHandler) -> None:
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    async def emit(self, evt: Event, ctx: "AgentContext") -> None:
        for handler in self._handlers.get(evt.name, []):
            await handler(ctx, evt)
