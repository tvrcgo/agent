from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.core.config import Config
from agent.core.events import Event, EventBus
from agent.core.model import ModelRegistry, ModelResponse, StreamChunk, ToolResult
from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
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
class MessageEvent:
    type: str
    content: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class InputMessage:
    content: str
    type: str = "chat"
    action: str = ""
    data: dict = field(default_factory=dict)
    session_id: str = ""


@dataclass
class LoopData:
    thinking: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    steering_messages: list[str] = field(default_factory=list)
    skills_prompt: str = ""


@dataclass
class OutputMessage:
    session_id: str = ""
    content: str = ""
    loops: list[LoopData] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)


@dataclass
class Job:
    id: str
    session_id: str
    status: str
    input: InputMessage | None = None
    output: OutputMessage | None = None
    loop: LoopData | None = None
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
        self._skills = SkillRegistry()
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
        job = evt.job
        if job is None or job.input is None:
            return

        if job.input.type == "chat":
            await self._handle_chat(job)
        elif job.input.type == "command":
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

        job.output = OutputMessage(session_id=job.session_id)
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

                job.loop = LoopData(
                    steering_messages=steering,
                    skills_prompt=self._skills.get_skills_prompt(),
                )
                await ctx.emit("turn_start", job=job)
                await ctx.emit("llm_start", job=job)
                job.status = "thinking"

                messages = job.data.get("messages", [])
                tools = self._tools.get_defs()

                if self._config.agent.stream:
                    async def on_chunk(chunk: StreamChunk) -> None:
                        if chunk.text and job.output is not None:
                            job.output.events.append(MessageEvent(type="stream", content=chunk.text))
                            await ctx.emit("msg_output", job=job)

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

                if response.thinking:
                    job.loop.thinking = response.thinking

                if response.tool_calls:
                    job.status = "acting"
                    await ctx.emit("tools_start", job=job, tool_calls=response.tool_calls)

                    if response.finish_reason == "length":
                        logger.warning("Response truncated (length), auto-failing tool calls")
                        for tool_call in response.tool_calls:
                            await self._tools.fail_tool_call(tool_call, job, "Response truncated, tool call not executed")
                    else:
                        await self._tools.execute_batch(response.tool_calls, job)

                    if job.output is not None and job.loop is not None:
                        job.output.loops.append(job.loop)
                    await ctx.emit("tools_end", job=job)
                    await ctx.emit("turn_end", job=job)
                    continue

                if response.text:
                    if job.output:
                        job.output.content = response.text

                if job.output is not None and job.loop is not None:
                    job.output.loops.append(job.loop)

                # follow-up
                q = self._message_queues[job.id]
                if q.has_pending:
                    await ctx.emit("turn_end", job=job)
                    continue

                job.status = "done"
                await ctx.emit("turn_end", job=job)
                await ctx.emit("job_end", job=job, reason="done")
                return

            msg = f"Reached maximum iterations ({self._config.agent.max_iterations})"
            logger.warning(msg)
            job.status = "error"
            await ctx.emit("turn_end", job=job)
            await ctx.emit("job_end", job=job, reason="max_iterations")

        except asyncio.CancelledError:
            # 用户取消（cmd_cancel → Task.cancel）：有序收尾，不再向上传播
            job.status = "cancelled"
            await ctx.emit("job_end", job=job, reason="cancelled")
        except Exception as e:
            logger.exception("Error in agent loop, id=%s", job.id)
            job.status = "error"
            await ctx.emit("job_error", job=job, error=e)

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
        # 取消单个 job：Task.cancel() 注入 CancelledError，打断当前执行
        target = self._jobs.get(job.id)
        if target is not None and target._task is not None:
            target._task.cancel()

    async def start(self) -> None:
        ctx = self.ctx

        ctx.on("msg_input", self._on_input)
        ctx.on("cmd_cancel", self._on_command_cancel)

        if self._config.tools:
            self._tools.load_modules(self._config.tools)
        self._skills.load_skills("agent/skills", "skills")

        if self._config.plugins:
            self._plugins.load_modules(self._config.plugins)

        await ctx.emit("agent_start")

    async def stop(self) -> None:
        await self.ctx.emit("agent_stop")
        self._plugins.unload_all()
        await self._models.close()
