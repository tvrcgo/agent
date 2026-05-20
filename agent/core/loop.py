from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from agent.core.config import AgentConfig

from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
from agent.core.tool import ToolRegistry
from agent.core.model import ModelRegistry, ModelResponse, StreamChunk, ToolCall

if TYPE_CHECKING:
    from agent.core.tool import Tool

logger = logging.getLogger(__name__)


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
class ToolCallItem:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolResultItem:
    id: str
    name: str
    result: str
    error: str = ""


@dataclass
class LoopData:
    thinking: str = ""
    tool_calls: list[ToolCallItem] = field(default_factory=list)
    tool_results: list[ToolResultItem] = field(default_factory=list)
    queue_messages: list[str] = field(default_factory=list)
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
    models: Any = None
    tools: "ToolRegistry | None" = None
    config: Any = None
    _self: "AgentLoop | None" = field(default=None, repr=False)

    async def emit(self, hook_name: str, job: Job | None = None) -> None:
        if self._self is not None:
            await self._self._plugins.emit(hook_name, self, job)


class AgentLoop:

    def __init__(
        self,
        models: ModelRegistry,
        tools: ToolRegistry,
        skills: SkillRegistry,
        plugins: PluginRegistry,
        config: AgentConfig = AgentConfig(),
    ) -> None:
        self._models = models
        self._tools = tools
        self._skills = skills
        self._plugins = plugins
        self._config = config
        self._jobs: dict[str, Job] = {}
        self._queue_jobs: list[Job] = []
        self._ctx: AgentContext | None = None
        self._queue_messages: dict[str, list[str]] = {}

        self._plugins.on("on_input", self._on_input)
        self._plugins.on("command:cancel", self._on_command_cancel)

    @property
    def ctx(self) -> AgentContext:
        if self._ctx is None:
            self._ctx = AgentContext(models=self._models, tools=self._tools, config=self._config, _self=self)
        return self._ctx

    def _is_running(self, job_id: str) -> bool:
        for j in self._jobs.values():
            if j.id == job_id and j._task is not None and not j._task.done():
                return True
        return False

    async def _on_input(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or job.input is None:
            return

        if job.input.type == "chat":
            await self._handle_chat(job)
        elif job.input.type == "command":
            await self._handle_command(job)

    async def _handle_chat(self, job: Job) -> None:
        if self._is_running(job.id):
            self._queue_messages.setdefault(job.id, []).append(job.input.content if job.input else "")
            return

        active = sum(1 for j in self._jobs.values() if j._task is not None and not j._task.done())
        if active >= self._config.max_concurrent:
            self._queue_jobs.append(job)
            logger.info("Job %s queued (active: %d, max: %d)", job.id, active, self._config.max_concurrent)
            return

        job.output = OutputMessage(session_id=job.session_id)
        job.status = "pending"
        self._jobs[job.id] = job
        await self.ctx.emit("before_job", job)
        job._task = asyncio.create_task(self._run_loop(job))

    async def _handle_command(self, job: Job) -> None:
        await self.ctx.emit(f"command:{job.input.action if job.input else ''}", job)

    async def _run_loop(self, job: Job) -> None:
        ctx = self.ctx

        try:
            for _ in range(self._config.max_iterations):

                job.loop = LoopData(
                    queue_messages=self._queue_messages.pop(job.id, None) or [],
                    skills_prompt=self._skills.get_skills_prompt(),
                )
                job.status = "thinking"
                await ctx.emit("before_loop", job)
                await ctx.emit("before_llm", job)

                messages = job.data.get("messages", [])
                tools = self._tools.get_defs()

                if self._config.stream:
                    async def on_chunk(chunk: StreamChunk) -> None:
                        if chunk.text and job.output is not None:
                            job.output.events.append(MessageEvent(type="stream", content=chunk.text))
                            await ctx.emit("on_output", job)

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
                job.data["response"] = response
                await ctx.emit("after_llm", job)

                if response.thinking:
                    job.loop.thinking = response.thinking

                if response.tool_calls:
                    job.status = "acting"
                    await ctx.emit("before_tools", job)

                    for tool_call in response.tool_calls:
                        await self._execute_tool(tool_call, job)

                    if job.output is not None and job.loop is not None:
                        job.output.loops.append(job.loop)
                    await ctx.emit("after_tools", job)
                    await ctx.emit("after_loop", job)
                    continue

                if response.text:
                    if job.output:
                        job.output.content = response.text

                if job.output is not None and job.loop is not None:
                    job.output.loops.append(job.loop)

                job.data["reason"] = "done"
                job.status = "done"
                await ctx.emit("after_loop", job)
                await ctx.emit("after_job", job)
                return

            msg = f"Reached maximum iterations ({self._config.max_iterations})"
            logger.warning(msg)
            job.data["error"] = msg
            job.status = "error"
            await ctx.emit("after_loop", job)
            await ctx.emit("after_job", job)

        except Exception as e:
            logger.exception("Error in agent loop, id=%s", job.id)
            job.data["error"] = e
            job.status = "error"
            await ctx.emit("on_error", job)

        finally:
            await ctx.emit("on_complete", job)
            self._jobs.pop(job.id, None)
            if self._queue_jobs:
                next_job = self._queue_jobs.pop(0)
                logger.info("Starting queued job %s", next_job.id)
                asyncio.create_task(self._handle_chat(next_job))

    async def _execute_tool(self, tool_call: ToolCall, job: Job) -> None:
        job.data["tool_call"] = tool_call

        if job.loop is not None:
            job.loop.tool_calls.append(ToolCallItem(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            ))

        ctx = self.ctx
        result = ""
        error = ""

        await ctx.emit("before_tool", job)

        try:
            tool = self._tools.get(tool_call.name)
            if tool:
                result = await tool.execute(tool_call.arguments, ctx=ctx, job=job)
            else:
                result = f"Error: unknown tool '{tool_call.name}'"
                error = result
        except Exception as e:
            logger.exception("Tool execution error: %s", tool_call.name)
            result = str(e)
            error = result

        job.data["result"] = result

        if job.loop is not None:
            job.loop.tool_results.append(ToolResultItem(
                id=tool_call.id,
                name=tool_call.name,
                result=result,
                error=error,
            ))

        await ctx.emit("after_tool", job)

    async def _on_command_cancel(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        for j in self._jobs.values():
            if j.session_id == job.session_id and j._task and not j._task.done():
                j._task.cancel()

    async def start(self) -> None:
        await self._plugins.emit("agent_start", self.ctx, None)

    async def stop(self) -> None:
        await self._plugins.emit("agent_stop", self.ctx, None)
