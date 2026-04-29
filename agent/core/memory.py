from __future__ import annotations

from typing import Any

from agent.core.plugin import Plugin
from agent.providers.base import Message, ToolCall


class ShortTermMemory(Plugin):
    """Sliding-window short-term memory.

    Keeps the most recent messages within window_size.
    System prompt is always preserved at position 0.
    """

    name = "short_term_memory"

    def __init__(self, window_size: int = 50) -> None:
        self._window_size = window_size
        self._messages: list[Message] = []
        self._system_prompt: Message | None = None

    async def on_init(self, ctx: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        self.clear()

    def set_system_prompt(self, content: str) -> None:
        self._system_prompt = Message(role="system", content=content)

    def add_user_message(self, content: str) -> None:
        self._messages.append(Message(role="user", content=content))
        self._trim()

    def add_assistant_message(
        self,
        content: str | None = None,
        thinking: str | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        self._messages.append(
            Message(
                role="assistant",
                content=content,
                thinking=thinking,
                tool_calls=tool_calls,
            )
        )
        self._trim()

    def add_tool_result(self, tool_call: ToolCall, result: str) -> None:
        self._messages.append(
            Message(role="tool", content=result, tool_call_id=tool_call.id)
        )
        self._trim()

    def get_messages(self) -> list[Message]:
        msgs: list[Message] = []
        if self._system_prompt:
            msgs.append(self._system_prompt)
        msgs.extend(self._messages)
        return msgs

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        if len(self._messages) > self._window_size:
            overflow = len(self._messages) - self._window_size
            self._messages = self._messages[overflow:]
