from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent.core.plugin import PluginRegistry
from agent.core.ws import (
    CancelMessage,
    ClientSession,
    ErrorEvent,
    MessageEvent,
    StatusEvent,
    TaskMessage,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserMessage,
)
from agent.core.memory import ShortTermMemory
from agent.providers.base import LLMProvider, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class AgentLoop:
    """Autonomous reasoning loop: Think → Act → Observe → repeat until done."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: PluginRegistry,
        memory: ShortTermMemory,
        max_iterations: int = 100,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._memory = memory
        self._max_iterations = max_iterations
        self._current_task: asyncio.Task[None] | None = None
        self._cancelled = False

    @property
    def is_running(self) -> bool:
        return self._current_task is not None and not self._current_task.done()

    async def handle_message(
        self, msg: Any, session: ClientSession
    ) -> None:
        if isinstance(msg, TaskMessage):
            await self._start_task(msg.content, session)
        elif isinstance(msg, UserMessage):
            self._memory.add_user_message(msg.content)
        elif isinstance(msg, CancelMessage):
            await self._cancel(session)

    async def _start_task(self, task: str, session: ClientSession) -> None:
        if self.is_running:
            await session.emit(
                ErrorEvent(code="busy", message="A task is already running")
            )
            return

        self._cancelled = False
        self._memory.add_user_message(task)
        self._current_task = asyncio.create_task(self._run_loop(session))

    async def _cancel(self, session: ClientSession) -> None:
        self._cancelled = True
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        await session.emit(StatusEvent(state="idle"))

    async def _run_loop(self, session: ClientSession) -> None:
        try:
            for iteration in range(self._max_iterations):
                if self._cancelled:
                    break

                # Think
                await session.emit(StatusEvent(state="thinking"))
                tools = self._registry.get_tool_definitions()
                response: LLMResponse = await self._provider.chat(
                    messages=self._memory.get_messages(),
                    tools=tools if tools else None,
                )

                # Emit thinking if present
                if response.thinking:
                    await session.emit(ThinkingEvent(content=response.thinking))

                # Act: execute tool calls
                if response.tool_calls:
                    await session.emit(StatusEvent(state="acting"))
                    self._memory.add_assistant_message(
                        content=response.text,
                        thinking=response.thinking,
                        tool_calls=response.tool_calls,
                    )

                    for tool_call in response.tool_calls:
                        if self._cancelled:
                            break
                        await self._execute_tool(tool_call, session)

                    continue

                # Observe / Done: no tool calls means task is complete
                if response.text:
                    await session.emit(MessageEvent(content=response.text))
                    self._memory.add_assistant_message(
                        content=response.text,
                        thinking=response.thinking,
                    )

                await session.emit(StatusEvent(state="done"))
                break
            else:
                await session.emit(
                    ErrorEvent(
                        code="max_iterations",
                        message=f"Reached maximum iterations ({self._max_iterations})",
                    )
                )
                await session.emit(StatusEvent(state="done"))

        except asyncio.CancelledError:
            logger.info("Task cancelled")
        except Exception as e:
            logger.exception("Error in agent loop")
            await session.emit(ErrorEvent(code="internal", message=str(e)))
            await session.emit(StatusEvent(state="idle"))
        finally:
            self._current_task = None

    async def _execute_tool(
        self, tool_call: ToolCall, session: ClientSession
    ) -> None:
        await session.emit(
            ToolCallEvent(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            )
        )

        result = await self._registry.execute_tool(
            tool_call.name, tool_call.arguments
        )

        await session.emit(
            ToolResultEvent(
                id=tool_call.id,
                name=tool_call.name,
                result=result,
            )
        )

        self._memory.add_tool_result(tool_call, result)
