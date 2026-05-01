from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.core.plugin import PluginRegistry, PluginContext
from agent.core.skill import SkillRegistry
from agent.core.ws import (
    ChatMessage,
    ClientSession,
    CommandMessage,
    ErrorEvent,
    MessageEvent,
    StatusEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from agent.core.llm import OpenAIProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class AgentLoop:
    """Autonomous reasoning loop: Think → Act → Observe → repeat until done.

    Supports multiple concurrent WebSocket sessions. Each session runs an
    independent job — starting or cancelling one does not affect others.
    """

    def __init__(
        self,
        llm: OpenAIProvider,
        skills: SkillRegistry,
        plugins: PluginRegistry,
        max_iterations: int = 100,
    ) -> None:
        self._llm = llm
        self._skills = skills
        self._plugins = plugins
        self._max_iterations = max_iterations
        self._max_concurrent: int = 10
        self._jobs: dict[str, asyncio.Task[None]] = {}

        # Core commands handled by the loop itself
        self._plugins.on("command:cancel", self._on_command_cancel)

    @staticmethod
    def _session_key(session: ClientSession) -> str:
        return session.session_id or "__default__"

    def is_running(self, session: ClientSession) -> bool:
        key = self._session_key(session)
        t = self._jobs.get(key)
        return t is not None and not t.done()

    async def on_connect(self, session: ClientSession) -> None:
        ctx = PluginContext(session_id=session.session_id, client=session)
        await self._plugins.emit("on_connect", ctx)

    async def on_disconnect(self, session: ClientSession) -> None:
        ctx = PluginContext(session_id=session.session_id, client=session)
        await self._plugins.emit("on_disconnect", ctx)

    async def on_message(
        self, msg: Any, session: ClientSession
    ) -> None:
        if isinstance(msg, ChatMessage):
            await self._start_job(msg.content, session)
        elif isinstance(msg, CommandMessage):
            ctx = PluginContext(
                session_id=session.session_id,
                client=session,
                data={"action": msg.action},
            )
            ctx.llm = self._llm
            await self._plugins.emit(f"command:{msg.action}", ctx)

    async def _start_job(self, content: str, session: ClientSession) -> None:
        ctx = PluginContext(
            session_id=session.session_id,
            client=session,
            data={"content": content},
        )
        await self._plugins.emit("before_job", ctx)

        # Already running: content appended, loop will pick it up
        if self.is_running(session):
            return

        # Check concurrent limit
        active = sum(1 for t in self._jobs.values() if not t.done())
        if active >= self._max_concurrent:
            await session.emit(
                ErrorEvent(code="busy", message=f"Too many concurrent sessions (max {self._max_concurrent})")
            )
            return

        key = self._session_key(session)
        self._jobs[key] = asyncio.create_task(self._run_loop(ctx, key))

    async def _run_loop(self, ctx: PluginContext, session_key: str) -> None:
        try:
            for _ in range(self._max_iterations):
                # Think
                await ctx.client.emit(StatusEvent(state="thinking"))
                ctx.llm = self._llm
                await self._plugins.emit("before_llm", ctx)

                messages = ctx.data.get("messages", [])
                tools = self._skills.get_definitions()
                response: LLMResponse = await self._llm.chat(
                    messages=messages,
                    tools=tools if tools else None,
                )

                ctx.data["response"] = response
                await self._plugins.emit("after_llm", ctx)

                # Emit thinking if present
                if response.thinking:
                    await ctx.client.emit(ThinkingEvent(content=response.thinking))

                # Act: execute tool calls
                if response.tool_calls:
                    await ctx.client.emit(StatusEvent(state="acting"))

                    for tool_call in response.tool_calls:
                        await self._execute_tool(tool_call, ctx)

                    continue

                # Done: no tool calls means job is complete
                if response.text:
                    await ctx.client.emit(MessageEvent(content=response.text))

                ctx.data["reason"] = "done"
                await self._plugins.emit("on_complete", ctx)
                await ctx.client.emit(StatusEvent(state="done"))
                break
            else:
                await ctx.client.emit(
                    ErrorEvent(
                        code="max_iterations",
                        message=f"Reached maximum iterations ({self._max_iterations})",
                    )
                )
                ctx.data["reason"] = "error"
                await self._plugins.emit("on_complete", ctx)
                await ctx.client.emit(StatusEvent(state="done"))

        except asyncio.CancelledError:
            logger.info("Job cancelled, session=%s", session_key)
            ctx.data["reason"] = "cancelled"
            await self._plugins.emit("on_complete", ctx)
        except Exception as e:
            logger.exception("Error in agent loop")
            await ctx.client.emit(ErrorEvent(code="internal", message=str(e)))
            ctx.data["reason"] = "error"
            await self._plugins.emit("on_complete", ctx)
            await ctx.client.emit(StatusEvent(state="idle"))
        finally:
            self._jobs.pop(session_key, None)

    async def _execute_tool(
        self, tool_call: ToolCall, ctx: PluginContext
    ) -> None:
        ctx.data["tool_call"] = tool_call
        await self._plugins.emit("before_tool", ctx)

        await ctx.client.emit(
            ToolCallEvent(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )

        result = await self._skills.execute(
            tool_call.name, tool_call.arguments
        )

        await ctx.client.emit(
            ToolResultEvent(
                id=tool_call.id,
                name=tool_call.name,
                result=result,
            )
        )

        ctx.data["result"] = result
        await self._plugins.emit("after_tool", ctx)

    async def _on_command_cancel(self, ctx: PluginContext) -> None:
        key = self._session_key(ctx.client)
        t = self._jobs.get(key)
        if t and not t.done():
            t.cancel()
        await ctx.client.emit(StatusEvent(state="idle"))
