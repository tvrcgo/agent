from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import websockets.exceptions

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

    def __init__(self, message: str = "Job aborted") -> None:
        self.message = message
        super().__init__(message)


@dataclass
class AgentContext:

    session_id: str | None
    client: ClientSession
    data: dict[str, Any] = field(default_factory=dict)
    llm: OpenAIProvider | None = None
    status: str = "idle"
    _plugins: PluginRegistry | None = field(default=None, repr=False)

    async def emit(self, hook_name: str) -> None:
        if self._plugins is not None:
            await self._plugins.emit(hook_name, self)


class AgentLoop:

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
        self._contexts: dict[str, AgentContext] = {}

        self._plugins.on("command:cancel", self._on_command_cancel)

    def _ensure_ctx(self, session: ClientSession) -> AgentContext:
        key = self._session_key(session)
        if key not in self._contexts:
            self._contexts[key] = AgentContext(
                session_id=session.session_id,
                client=session,
                llm=self._llm,
                _plugins=self._plugins,
            )
        return self._contexts[key]

    def _fork_ctx(self, session: ClientSession, **extra: Any) -> AgentContext:
        base = self._ensure_ctx(session)
        return AgentContext(
            session_id=base.session_id,
            client=base.client,
            data={**base.data, **extra},
            llm=base.llm,
            _plugins=base._plugins,
        )

    @staticmethod
    def _session_key(session: ClientSession) -> str:
        assert session.session_id is not None
        return session.session_id

    def is_running(self, session: ClientSession) -> bool:
        key = self._session_key(session)
        t = self._jobs.get(key)
        return t is not None and not t.done()

    async def on_connect(self, session: ClientSession) -> None:
        ctx = self._ensure_ctx(session)
        await ctx.emit("on_connect")

    async def on_disconnect(self, session: ClientSession) -> None:
        ctx = self._ensure_ctx(session)
        await ctx.emit("on_disconnect")
        self._contexts.pop(self._session_key(session), None)

    async def on_message(
        self, msg: Any, session: ClientSession
    ) -> None:
        if isinstance(msg, ChatMessage):
            await self._handle_chat(msg, session)
        elif isinstance(msg, CommandMessage):
            await self._handle_command(msg, session)

    async def _handle_chat(self, msg: ChatMessage, session: ClientSession) -> None:
        if self.is_running(session):
            key = self._session_key(session)
            self._queue_messages.setdefault(key, []).append(msg.content)
            return

        active = sum(1 for t in self._jobs.values() if not t.done())
        if active >= self._max_concurrent:
            await session.emit(
                ErrorEvent(code="busy", message=f"Too many concurrent sessions (max {self._max_concurrent})")
            )
            return

        ctx = self._fork_ctx(session, content=msg.content)
        await ctx.emit("before_job")

        key = self._session_key(session)
        self._jobs[key] = asyncio.create_task(self._run_loop(ctx, key))

    async def _handle_command(self, msg: CommandMessage, session: ClientSession) -> None:
        ctx = self._fork_ctx(session, **msg.data)
        await ctx.emit(f"command:{msg.action}")

    async def _run_loop(self, ctx: AgentContext, session_key: str) -> None:
        try:
            for _ in range(self._max_iterations):
                ctx.data["queue_messages"] = self._queue_messages.pop(session_key, None)
                ctx.data["skills_prompt"] = self._skills.get_skills_prompt()

                await ctx.client.emit(StatusEvent(status="thinking"))
                await ctx.emit("before_llm")

                messages = ctx.data.get("messages", [])
                tools = self._skills.get_tools_def()
                response: LLMResponse = await self._llm.chat(
                    messages=messages,
                    tools=tools if tools else None,
                )
                ctx.data["response"] = response
                await ctx.emit("after_llm")

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
        except websockets.exceptions.ConnectionClosed:
            logger.info("Client disconnected, session=%s", session_key)
            ctx.data["reason"] = "disconnected"
            ctx.status = "idle"
        except Exception as e:
            logger.exception("Error in agent loop")
            await ctx.client.emit(ErrorEvent(code="internal", message=str(e)))
            ctx.data["reason"] = "error"
            ctx.status = "idle"
        finally:
            try:
                await ctx.emit("on_complete")
            except Exception:
                logger.warning("on_complete hook error", exc_info=True)
            await ctx.client.emit(StatusEvent(status=ctx.status))
            self._jobs.pop(session_key, None)
            self._queue_messages.pop(session_key, None)

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
            tool = self._skills.get_tool(tool_call.name)
            result = await tool.execute(tool_call.arguments) if tool else f"Error: unknown tool '{tool_call.name}'"
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

    async def _on_command_cancel(self, ctx: AgentContext) -> None:
        key = self._session_key(ctx.client)
        t = self._jobs.get(key)
        if t and not t.done():
            t.cancel()
