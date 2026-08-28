from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

EventHandler = Callable[["AgentContext", "Event"], Awaitable[None]]


class DispatchMode(str, Enum):
    PARALLEL = "parallel"   # 并发观察，异常隔离，返回值忽略
    SERIAL = "serial"       # 顺序执行，首个非 None 短路并作为 emit 返回值
    WATERFALL = "waterfall" # 顺序流水线：非 None 返回值写入 evt.data 供下游读取，主体跑完后再跑链尾 tail，最终结果作为 emit 返回值
    FIRE = "fire"           # 预留：提交即返回（当前无实现，勿登记）


# 事件分发模式登记表：事件契约的集中来源。
# 未登记的事件按 parallel 分发并打 warning 日志（cmd_<action> 钩子除外）。
# serial 条目语义：handler 返回非 None → 短路并作为 emit 返回值。
# waterfall 条目语义：handler 依次执行，非 None 返回值写入 evt.data 供下一个 handler 读取，最终结果作为 emit 返回值。
EVENT_MODES: dict[str, DispatchMode] = {
    "agent_start": DispatchMode.PARALLEL,
    "agent_stop": DispatchMode.PARALLEL,
    "msg_input": DispatchMode.PARALLEL,
    "msg_output": DispatchMode.PARALLEL,
    "job_start": DispatchMode.PARALLEL,
    "job_end": DispatchMode.PARALLEL,
    "job_error": DispatchMode.PARALLEL,
    "turn_start": DispatchMode.WATERFALL, # 提示段组装：贡献者顺序产出提示段 → loop 落盘 job.turn.prompts
    "turn_end": DispatchMode.PARALLEL,
    "llm_start": DispatchMode.PARALLEL,
    "llm_chunk": DispatchMode.PARALLEL,
    "llm_end": DispatchMode.PARALLEL,
    "tools_start": DispatchMode.SERIAL,       # 守卫审查链：审查→剔除→loop 执行
    "tools_end": DispatchMode.PARALLEL,
    "tool_start": DispatchMode.PARALLEL,
    "tool_end": DispatchMode.PARALLEL,
    "tool_error": DispatchMode.PARALLEL,
    "confirm_request": DispatchMode.SERIAL,   # confirm 请求：用户决策短路回填
}


_RESERVED = frozenset({"name", "job", "data", "mode"})


@dataclass
class Event:
    name: str
    job: "Job | None" = None
    data: dict[str, Any] = field(default_factory=dict)
    mode: str = DispatchMode.PARALLEL.value

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
        self._tails: dict[str, list[tuple[int, int, EventHandler]]] = defaultdict(list)
        self._seq = 0

    def on(self, event: str, handler: EventHandler, tail: bool | int = False) -> None:
        # tail 标记链尾兜底 handler（仅 waterfall 在主体流水线之后执行）；
        # tail 为整数时同时指定链尾顺序（小者先执行，同值按注册顺序）；True 等同 0
        if tail is not False:
            order = 0 if tail is True else tail
            self._tails[event].append((self._seq, order, handler))
        else:
            self._handlers[event].append(handler)
        self._seq += 1

    def off(self, event: str, handler: EventHandler) -> None:
        handlers = self._handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)
        tails = self._tails.get(event)
        if tails:
            for entry in tails:
                if entry[2] is handler:
                    tails.remove(entry)
                    break

    async def emit(self, event: str, job: "Job | None" = None, ctx: "AgentContext | None" = None, **data: Any) -> Any:
        # 模式由登记表决定：serial（非 None 短路）/ parallel（并发观察）/ waterfall（顺序流水线）
        mode = EVENT_MODES.get(event, DispatchMode.PARALLEL)
        if event not in EVENT_MODES and not event.startswith("cmd_"):
            logger.warning(
                "Event '%s' not registered in EVENT_MODES, dispatched as parallel", event,
            )
        if mode is DispatchMode.FIRE:
            raise NotImplementedError(
                f"Fire mode is reserved but not implemented (event '{event}')",
            )
        evt = Event(name=event, job=job, data=data, mode=mode.value)
        if mode is DispatchMode.SERIAL:
            return await self._serial(evt, ctx)
        if mode is DispatchMode.WATERFALL:
            return await self._waterfall(evt, ctx)
        await self._parallel(evt, ctx)
        return None

    async def _serial(self, evt: Event, ctx: "AgentContext | None") -> Any:
        for handler in self._handlers.get(evt.name, []):
            try:
                result = await handler(ctx, evt)
            except Exception:
                logger.exception("Event handler error for '%s'", evt.name)
                continue
            if result is not None:
                return result
        return None

    async def _waterfall(self, evt: Event, ctx: "AgentContext | None") -> Any:
        # 主体流水线（注册顺序）→ 链尾兜底（tail：按 tail 值升序、同值按注册顺序，保证在主体之后执行）
        tails = [h for _, _, h in sorted(self._tails.get(evt.name, []), key=lambda e: (e[1], e[0]))]
        handlers = self._handlers.get(evt.name, []) + tails
        for handler in handlers:
            try:
                result = await handler(ctx, evt)
            except Exception:
                logger.exception("Event handler error for '%s'", evt.name)
                continue
            if result is not None:
                evt.data = result  # 非 None 返回值写入 evt.data，供下一个 handler 读取
        return evt.data

    async def _parallel(self, evt: Event, ctx: "AgentContext | None") -> None:
        handlers = self._handlers.get(evt.name, [])
        if handlers:
            await asyncio.gather(*(self._safe_call(h, ctx, evt) for h in handlers))

    async def _safe_call(self, handler: EventHandler, ctx: "AgentContext | None", evt: Event) -> None:
        try:
            await handler(ctx, evt)
        except Exception:
            logger.exception("Event handler error for '%s'", evt.name)
