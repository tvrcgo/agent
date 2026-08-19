from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.core.config import Config
from agent.core.events import Event, EventBus
from agent.core.io import InputMessage
from agent.core.model import ModelRegistry, ModelResponse, StreamChunk
from agent.core.plugin import PluginRegistry
from agent.core.tool import ToolRegistry

logger = logging.getLogger(__name__)


class DrainMode(str, Enum):
    ALL = "all"
    ONE_AT_A_TIME = "one-at-a-time"


@dataclass
class MessageQueue:
    messages: list[str] = field(default_factory=list)
    mode: DrainMode = DrainMode.ALL

    def push(self, content: str) -> None:
        self.messages.append(content)

    def drain(self) -> list[str]:
        if not self.messages:
            return []
        if self.mode == DrainMode.ONE_AT_A_TIME:
            return [self.messages.pop(0)]
        result = list(self.messages)
        self.messages.clear()
        return result

    @property
    def has_pending(self) -> bool:
        return len(self.messages) > 0


@dataclass
class Turn:
    steering_messages: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    content: str = ""


@dataclass
class Job:
    id: str
    status: str
    input: InputMessage | None = None
    turn: Turn | None = None
    data: dict[str, Any] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)


@dataclass
class AgentContext:
    config: Any = None
    _bus: "EventBus | None" = field(default=None, repr=False)
    _self: "AgentLoop | None" = field(default=None, repr=False)

    @property
    def models(self) -> Any:
        return self._self._models

    @property
    def tools(self) -> "ToolRegistry":
        return self._self._tools

    def on(self, event: str, handler: Any) -> None:
        self._bus.on(event, handler)

    def off(self, event: str, handler: Any) -> None:
        self._bus.off(event, handler)

    async def emit(self, event: str, job: Job | None = None, timeout: float = 120, **data: Any) -> Any:
        # 转发 EventBus.emit；req: 前缀 → 请求-响应，否则 → 广播
        return await self._bus.emit(event, job, timeout=timeout, ctx=self, **data)


class AgentLoop:

    def __init__(
        self,
        config: Config = Config(),
    ) -> None:
        self._config = config
        self._bus = EventBus()
        self._ctx = AgentContext(
            config=config.agent,
            _bus=self._bus,
            _self=self,
        )
        self._models = ModelRegistry(config.model)
        self._tools = ToolRegistry(self._ctx)
        self._plugins = PluginRegistry(self._ctx)
        self._jobs: dict[str, Job] = {}
        self._queue_jobs: list[Job] = []
        # steering/follow-up queue
        drain_mode = DrainMode(config.agent.steering.get("drain", "all"))
        self._message_queues: defaultdict[str, MessageQueue] = defaultdict(
            lambda: MessageQueue(mode=drain_mode)
        )

    @property
    def ctx(self) -> AgentContext:
        return self._ctx

    def _is_running(self, job_id: str) -> bool:
        for j in self._jobs.values():
            if j.id == job_id and j._task is not None and not j._task.done():
                return True
        return False

    async def _on_input(self, ctx: AgentContext, evt: Event) -> None:
        input_msg = evt.data.get("input")
        if not isinstance(input_msg, InputMessage):
            return
        job = Job(
            id=input_msg.session_id,
            status="pending",
            input=input_msg,
        )

        if input_msg.type == "chat":
            await self._handle_chat(job)
        elif input_msg.type == "command":
            await self._handle_command(job)

    async def _handle_chat(self, job: Job) -> None:
        if self._is_running(job.id):
            content = job.input.content if job.input else ""
            self._message_queues[job.id].push(content)
            return

        active = sum(1 for j in self._jobs.values() if j._task is not None and not j._task.done())
        if active >= self._config.agent.max_concurrent:
            self._queue_jobs.append(job)
            logger.info("Job %s queued (active: %d, max: %d)", job.id, active, self._config.agent.max_concurrent)
            return

        job.status = "pending"
        self._jobs[job.id] = job
        await self.ctx.emit("job_start", job=job)
        job._task = asyncio.create_task(self._run_loop(job))

    async def _handle_command(self, job: Job) -> None:
        await self.ctx.emit(
            f"cmd_{job.input.action if job.input else ''}",
            job=job,
            **(job.input.data if job.input else {}),
        )

    async def _run_loop(self, job: Job) -> None:
        ctx = self.ctx

        try:
            for _ in range(self._config.agent.max_iterations):
                # steering
                q = self._message_queues[job.id]
                steering = q.drain()

                job.turn = Turn(steering_messages=steering)
                await ctx.emit("turn_start", job=job)
                job.status = "thinking"
                await ctx.emit("llm_start", job=job)

                messages = job.data.get("messages", [])
                tools = self._tools.get_defs()

                if self._config.agent.stream:
                    async def on_chunk(chunk: StreamChunk) -> None:
                        # 流式分块转为事件，由 message plugin 构造输出
                        await ctx.emit("llm_chunk", job=job, chunk=chunk)

                    response: ModelResponse = await self._models.get("main").chat_stream(
                        messages=messages,
                        tools=tools if tools else None,
                        on_chunk=on_chunk,
                    )
                else:
                    response: ModelResponse = await self._models.get("main").chat(
                        messages=messages,
                        tools=tools if tools else None,
                    )
                await ctx.emit("llm_end", job=job, response=response)

                if response.finish_reason == "length":
                    for tool_call in (response.tool_calls or []):
                        await self._tools.fail_tool_call(
                            tool_call, job, "Response truncated, tool call not executed",
                        )
                    job.status = "error"
                    await ctx.emit("turn_end", job=job)
                    await ctx.emit("job_error", job=job, error="Response truncated (length)")
                    await ctx.emit("job_end", job=job, reason="truncated")
                    return

                if response.tool_calls:
                    job.status = "acting"
                    await ctx.emit("tools_start", job=job, tool_calls=response.tool_calls)
                    await self._tools.execute_batch(response.tool_calls, job)

                    await ctx.emit("tools_end", job=job)
                    await ctx.emit("turn_end", job=job)
                    continue

                if response.text:
                    if job.turn:
                        job.turn.content = response.text

                # follow-up
                q = self._message_queues[job.id]
                if q.has_pending:
                    await ctx.emit("turn_end", job=job)
                    continue

                job.status = "done"
                await ctx.emit("turn_end", job=job)
                await ctx.emit("job_end", job=job, reason="done")
                return

            job.status = "error"
            await ctx.emit("turn_end", job=job)
            await ctx.emit("job_error", job=job, error="Reached maximum iterations")
            await ctx.emit("job_end", job=job, reason="max_iterations")

        except asyncio.CancelledError:
            # 用户取消（cmd_cancel → Task.cancel）：有序收尾，不再向上传播
            job.status = "cancelled"
            await ctx.emit("job_end", job=job, reason="cancelled")
        except Exception as e:
            job.status = "error"
            await ctx.emit("job_error", job=job, error=e)
            await ctx.emit("job_end", job=job, reason="error")

        finally:
            await ctx.emit("job_complete", job=job)
            self._jobs.pop(job.id, None)
            self._message_queues.pop(job.id, None)
            if self._queue_jobs:
                next_job = self._queue_jobs.pop(0)
                logger.info("Starting queued job %s", next_job.id)
                asyncio.create_task(self._handle_chat(next_job))

    async def _on_command_cancel(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        target = self._jobs.get(job.id)
        if target is not None and target._task is not None:
            target._task.cancel()

    async def start(self) -> None:
        ctx = self.ctx

        ctx.on("msg_input", self._on_input)
        ctx.on("cmd_cancel", self._on_command_cancel)

        if self._config.tools:
            self._tools.load_modules(self._config.tools)
        if self._config.plugins:
            self._plugins.load_modules(self._config.plugins)

        await ctx.emit("agent_start")

    async def stop(self) -> None:
        await self.ctx.emit("agent_stop")
        self._plugins.unload_all()
        await self._models.close()
