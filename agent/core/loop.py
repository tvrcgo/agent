from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from agent.core.plugin import PluginRegistry
from agent.core.skill import SkillRegistry
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
from agent.core.llm import OpenAIProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class JobAborted(Exception):
    """Raised by a plugin hook to abort the current job."""

    def __init__(self, message: str = "Job aborted") -> None:
        self.message = message
        super().__init__(message)


@dataclass
class JobContext:
    """Mutable context shared by plugins across a job lifecycle."""

    session_id: str | None
    client: ClientSession
    data: dict[str, Any] = field(default_factory=dict)
    llm: OpenAIProvider | None = None
    status: str = "idle"


class AgentLoop:
    """Think → Act → Observe loop. One asyncio.Task per session."""

    def __init__(
        self,
        llm: OpenAIProvider,
        skills: SkillRegistry,
        plugins: PluginRegistry,
        max_iterations: int = 100,
        max_concurrent: int = 10,
    ) -> None:
        self._llm = llm
        self._skills = skills
        self._plugins = plugins
        self._max_iterations = max_iterations
        self._max_concurrent = max_concurrent
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._queue_messages: dict[str, list[str]] = {}

        # Core commands
        self._plugins.on("command:cancel", self._on_command_cancel)

    @staticmethod
    def _session_key(session: ClientSession) -> str:
        return session.session_id or "__default__"

    def is_running(self, session: ClientSession) -> bool:
        key = self._session_key(session)
        t = self._jobs.get(key)
        return t is not None and not t.done()

    async def on_connect(self, session: ClientSession) -> None:
        ctx = JobContext(session_id=session.session_id, client=session)
        await self._plugins.emit("on_connect", ctx)

    async def on_disconnect(self, session: ClientSession) -> None:
        ctx = JobContext(session_id=session.session_id, client=session)
        await self._plugins.emit("on_disconnect", ctx)

    async def on_message(
        self, msg: Any, session: ClientSession
    ) -> None:
        if isinstance(msg, ChatMessage):
            if self.is_running(session):
                key = self._session_key(session)
                self._queue_messages.setdefault(key, []).append(msg.content)
                return
            await self._start_job(msg.content, session)
        elif isinstance(msg, CommandMessage):
            ctx = JobContext(
                session_id=session.session_id,
                client=session,
                data=msg.data,
            )
            ctx.llm = self._llm
            await self._plugins.emit(f"command:{msg.action}", ctx)

    async def _start_job(self, content: str, session: ClientSession) -> None:
        active = sum(1 for t in self._jobs.values() if not t.done())
        if active >= self._max_concurrent:
            await session.emit(
                ErrorEvent(code="busy", message=f"Too many concurrent sessions (max {self._max_concurrent})")
            )
            return

        ctx = JobContext(
            session_id=session.session_id,
            client=session,
            data={"content": content},
        )
        await self._plugins.emit("before_job", ctx)

        key = self._session_key(session)
        self._jobs[key] = asyncio.create_task(self._run_loop(ctx, key))

    async def _run_loop(self, ctx: JobContext, session_key: str) -> None:
        try:
            for _ in range(self._max_iterations):
                ctx.llm = self._llm
                ctx.data["queue_messages"] = self._queue_messages.pop(session_key, None)
                ctx.data["skills_prompt"] = self._skills.get_skills_prompt()

                await ctx.client.emit(StatusEvent(status="thinking"))
                await self._plugins.emit("before_llm", ctx)

                messages = ctx.data.get("messages", [])
                tools = self._skills.get_tools_def()
                response: LLMResponse = await self._llm.chat(
                    messages=messages,
                    tools=tools if tools else None,
                )

                ctx.data["response"] = response
                await self._plugins.emit("after_llm", ctx)

                # Emit thinking content if present
                if response.thinking:
                    await ctx.client.emit(StatusEvent(status="thinking", content=response.thinking))

                if response.tool_calls:
                    await ctx.client.emit(StatusEvent(status="acting"))

                    for tool_call in response.tool_calls:
                        await self._execute_tool(tool_call, ctx)

                    continue

                if response.text:
                    await ctx.client.emit(MessageEvent(content=response.text))

                ctx.data["reason"] = "done"
                ctx.status = "done"
                break
            else:
                await ctx.client.emit(
                    ErrorEvent(
                        code="max_iterations",
                        message=f"Reached maximum iterations ({self._max_iterations})",
                    )
                )
                ctx.data["reason"] = "error"
                ctx.status = "done"

        except asyncio.CancelledError:
            logger.info("Job cancelled, session=%s", session_key)
            ctx.data["reason"] = "cancelled"
            ctx.status = "idle"
        except JobAborted as e:
            logger.info("Job aborted, session=%s: %s", session_key, e.message)
            await ctx.client.emit(MessageEvent(content=e.message))
            ctx.data["reason"] = "aborted"
            ctx.status = "done"
        except Exception as e:
            logger.exception("Error in agent loop")
            await ctx.client.emit(ErrorEvent(code="internal", message=str(e)))
            ctx.data["reason"] = "error"
            ctx.status = "idle"
        finally:
            await self._plugins.emit("on_complete", ctx)
            await ctx.client.emit(StatusEvent(status=ctx.status))
            self._jobs.pop(session_key, None)
            self._queue_messages.pop(session_key, None)

    async def _execute_tool(
        self, tool_call: ToolCall, ctx: JobContext
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
            await self._plugins.emit("before_tool", ctx)
            tool = self._skills.get_tool(tool_call.name)
            result = await tool.execute(tool_call.arguments) if tool else f"Error: unknown tool '{tool_call.name}'"
        except Exception as e:
            ctx.data["result"] = str(e)
            await self._plugins.emit("after_tool", ctx)
            raise

        await ctx.client.emit(
            ToolResultEvent(
                id=tool_call.id,
                name=tool_call.name,
                result=result,
            )
        )

        ctx.data["result"] = result
        await self._plugins.emit("after_tool", ctx)

    async def _on_command_cancel(self, ctx: JobContext) -> None:
        key = self._session_key(ctx.client)
        t = self._jobs.get(key)
        if t and not t.done():
            t.cancel()
