from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import websockets.exceptions

from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
from agent.core.tool import ToolRegistry
from agent.core.ws import (
    ChatMessage,
    ClientSession,
    CommandMessage,
    ErrorEvent,
    MessageEvent,
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.core.llm import ModelRegistry, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class JobAborted(Exception):

    def __init__(self, message: str = "Job aborted") -> None:
        self.message = message
        super().__init__(message)


@dataclass
class Job:
    id: str
    parent_id: str | None
    depth: int
    status: str  # pending | thinking | acting | waiting | done | error | cancelled
    content: str
    result: str | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _ctx: "AgentContext | None" = field(default=None, repr=False)


@dataclass
class AgentContext:

    session_id: str | None
    client: ClientSession
    data: dict[str, Any] = field(default_factory=dict)
    models: Any = None
    _loop: "AgentLoop | None" = field(default=None, repr=False)

    async def emit(self, hook_name: str) -> None:
        if self._loop is not None:
            await self._loop._plugins.emit(hook_name, self)


class AgentLoop:

    def __init__(
        self,
        models: ModelRegistry,
        tools: ToolRegistry,
        skills: SkillRegistry,
        plugins: PluginRegistry,
        max_iterations: int = 100,
        max_concurrent: int = 10,
        max_sub_job_depth: int = 2,
    ) -> None:
        self._models = models
        self._tools = tools
        self._skills = skills
        self._plugins = plugins
        self._max_iterations = max_iterations
        self._max_concurrent = max_concurrent
        self._max_sub_job_depth = max_sub_job_depth
        self._jobs: dict[str, Job] = {}
        self._queue_messages: dict[str, list[str]] = {}
        self._contexts: dict[str, AgentContext] = {}

        self._plugins.on("command:cancel", self._on_command_cancel)

    def _ensure_ctx(self, session: ClientSession) -> AgentContext:
        sid = session.session_id
        assert sid is not None
        if sid not in self._contexts:
            self._contexts[sid] = AgentContext(
                session_id=sid,
                client=session,
                models=self._models,
                _loop=self,
            )
        return self._contexts[sid]

    def _fork_ctx(self, session: ClientSession, **extra: Any) -> AgentContext:
        base = self._ensure_ctx(session)
        return AgentContext(
            session_id=base.session_id,
            client=base.client,
            data={**base.data, **extra},
            models=base.models,
            _loop=base._loop,
        )

    def is_running(self, session: ClientSession) -> bool:
        sid = session.session_id
        assert sid is not None
        job = self._jobs.get(sid)
        return job is not None and job._task is not None and not job._task.done()

    async def on_connect(self, session: ClientSession) -> None:
        ctx = self._ensure_ctx(session)
        await ctx.emit("on_connect")

    async def on_disconnect(self, session: ClientSession) -> None:
        ctx = self._ensure_ctx(session)
        await ctx.emit("on_disconnect")
        self._contexts.pop(session.session_id, None)

    async def on_message(
        self, msg: Any, session: ClientSession
    ) -> None:
        if isinstance(msg, ChatMessage):
            await self._handle_chat(msg, session)
        elif isinstance(msg, CommandMessage):
            await self._handle_command(msg, session)

    async def _handle_chat(self, msg: ChatMessage, session: ClientSession) -> None:
        sid = session.session_id
        assert sid is not None

        if self.is_running(session):
            self._queue_messages.setdefault(sid, []).append(msg.content)
            return

        active = sum(1 for j in self._jobs.values()
                     if j.depth == 0 and j._task is not None and not j._task.done())
        if active >= self._max_concurrent:
            await session.emit(
                ErrorEvent(code="busy", message=f"Too many concurrent sessions (max {self._max_concurrent})")
            )
            return

        ctx = self._fork_ctx(session, content=msg.content)
        job = Job(
            id=sid, parent_id=None, depth=0,
            status="pending", content=msg.content, _ctx=ctx,
        )
        self._jobs[sid] = job
        await ctx.emit("before_job")
        job._task = asyncio.create_task(self._run_loop(job))

    async def _handle_command(self, msg: CommandMessage, session: ClientSession) -> None:
        ctx = self._fork_ctx(session, **msg.data)
        await ctx.emit(f"command:{msg.action}")

    async def _run_loop(self, job: Job) -> str:
        ctx = job._ctx
        try:
            for _ in range(self._max_iterations):
                ctx.data["queue_messages"] = self._queue_messages.pop(job.id, None)
                ctx.data["skills_prompt"] = self._skills.get_skills_prompt()

                job.status = "thinking"
                await ctx.client.emit(StatusEvent(status="thinking"))
                await ctx.emit("before_llm")

                messages = ctx.data.get("messages", [])
                tools = self._tools.get_defs()
                response: LLMResponse = await self._models.get("main").chat(
                    messages=messages,
                    tools=tools if tools else None,
                )
                ctx.data["response"] = response
                await ctx.emit("after_llm")

                if response.thinking:
                    await ctx.client.emit(StatusEvent(status="thinking", content=response.thinking))

                if response.tool_calls:
                    job.status = "acting"
                    await ctx.client.emit(StatusEvent(status="acting"))

                    await ctx.emit("before_tools")
                    for tool_call in response.tool_calls:
                        await self._execute_tool(tool_call, ctx)
                    await ctx.emit("after_tools")

                    continue

                if response.text:
                    await ctx.client.emit(MessageEvent(content=response.text))

                ctx.data["reason"] = "done"
                job.status = "done"
                job.result = response.text
                return response.text or ""
            else:
                msg = f"Reached maximum iterations ({self._max_iterations})"
                await ctx.client.emit(ErrorEvent(code="max_iterations", message=msg))
                ctx.data["reason"] = "error"
                job.status = "error"
                job.result = msg
                return msg

        except asyncio.CancelledError:
            logger.info("Job cancelled, session=%s", job.id)
            ctx.data["reason"] = "cancelled"
            job.status = "cancelled"
            return ""
        except JobAborted as e:
            logger.info("Job aborted, session=%s: %s", job.id, e.message)
            await ctx.client.emit(MessageEvent(content=e.message))
            ctx.data["reason"] = "aborted"
            job.status = "done"
            job.result = e.message
            return ""
        except websockets.exceptions.ConnectionClosed:
            logger.info("Client disconnected, session=%s", job.id)
            ctx.data["reason"] = "disconnected"
            job.status = "cancelled"
            return ""
        except Exception as e:
            logger.exception("Error in agent loop")
            await ctx.client.emit(ErrorEvent(code="internal", message=str(e)))
            ctx.data["reason"] = "error"
            job.status = "error"
            job.result = str(e)
            return ""
        finally:
            try:
                await ctx.emit("on_complete")
            except Exception:
                logger.warning("on_complete hook error", exc_info=True)
            self._jobs.pop(job.id, None)
            if job.depth == 0:
                final_status = "idle" if job.status in ("cancelled", "error") else job.status
                await ctx.client.emit(StatusEvent(status=final_status))
                self._queue_messages.pop(job.id, None)
            else:
                try:
                    await ctx.emit("on_disconnect")
                except Exception:
                    logger.warning("on_disconnect hook error in sub-job", exc_info=True)

    async def _execute_tool(
        self, tool_call: ToolCall, ctx: AgentContext
    ) -> None:
        ctx.data["tool_call"] = tool_call

        await ctx.client.emit(
            ToolCallEvent(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )

        try:
            await ctx.emit("before_tool")
            tool = self._tools.get(tool_call.name)
            result = await tool.execute(tool_call.arguments, ctx=ctx) if tool else f"Error: unknown tool '{tool_call.name}'"
        except Exception as e:
            ctx.data["result"] = str(e)
            await ctx.emit("after_tool")
            raise

        await ctx.client.emit(
            ToolResultEvent(
                id=tool_call.id,
                name=tool_call.name,
                result=result,
            )
        )

        ctx.data["result"] = result
        await ctx.emit("after_tool")

    async def spawn(self, content: str, parent_ctx: AgentContext) -> str:
        import uuid

        parent_job = self._jobs.get(parent_ctx.session_id)
        if parent_job is None:
            return "[sub-job] Error: parent job not found"

        depth = parent_job.depth
        if depth >= self._max_sub_job_depth:
            return f"Error: maximum sub-job depth ({self._max_sub_job_depth}) reached"

        root_job = parent_job
        while root_job.parent_id is not None:
            root_job = self._jobs[root_job.parent_id]
        root_id = root_job.id

        sub_id = f"{root_id}/sub-{uuid.uuid4().hex[:8]}"

        sub_client = ClientSession(ws=getattr(parent_ctx.client, "_ws", None))
        sub_client.session_id = sub_id
        sub_client.is_silent = True

        sub_ctx = AgentContext(
            session_id=sub_id, client=sub_client,
            data={**parent_ctx.data, "content": content},
            models=parent_ctx.models,
            _loop=parent_ctx._loop,
        )

        job = Job(
            id=sub_id, parent_id=parent_ctx.session_id,
            depth=depth + 1, status="pending", content=content, _ctx=sub_ctx,
        )
        self._jobs[sub_id] = job
        await sub_ctx.emit("before_job")
        return await self._run_loop(job)

    async def _on_command_cancel(self, ctx: AgentContext) -> None:
        job = self._jobs.get(ctx.client.session_id)
        if job and job._task and not job._task.done():
            job._task.cancel()
