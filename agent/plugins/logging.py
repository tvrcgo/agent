from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext

if TYPE_CHECKING:
    from agent.core.config import Config

logger = logging.getLogger(__name__)


def _truncate(text: str | None, max_len: int = 200) -> str:
    """Truncate long text for logging."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... ({len(text)} chars total)"


class LoggingPlugin(Plugin):
    name = "logging"

    def __init__(self) -> None:
        self._iteration_counts: dict[str, int] = {}

    def load(self, registry: PluginRegistry, config: Config) -> None:
        registry.on("before_job", self._on_before_job)
        registry.on("before_llm", self._on_before_llm)
        registry.on("after_llm", self._on_after_llm)
        registry.on("before_tool", self._on_before_tool)
        registry.on("after_tool", self._on_after_tool)
        registry.on("on_complete", self._on_complete)

        logger.info("LoggingPlugin initialized")

    def unload(self) -> None:
        self._iteration_counts.clear()
        logger.info("LoggingPlugin shut down")

    async def _on_before_job(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        content = ctx.data.get("content", "")
        if content:
            logger.info("[%s] received message: %s", sid, _truncate(content))
            logger.info("[%s] job start", sid)
            self._iteration_counts[sid] = 0

    async def _on_before_llm(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        if sid not in self._iteration_counts:
            self._iteration_counts[sid] = 0
        self._iteration_counts[sid] += 1

        messages = ctx.data.get("messages", [])

        # Find last user message to show what LLM is responding to
        last_user_msg = None
        for msg in reversed(messages):
            if hasattr(msg, 'role') and msg.role == 'user':
                last_user_msg = msg.content if hasattr(msg, 'content') else str(msg)
                break

        if last_user_msg:
            logger.info("[%s] Round %d >>> %s",
                        sid, self._iteration_counts[sid], _truncate(last_user_msg))
        else:
            logger.info("[%s] Round %d >>> (no user message, %d messages total)",
                        sid, self._iteration_counts[sid], len(messages))

    async def _on_after_llm(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        response = ctx.data.get("response")
        if response is None:
            return

        if response.thinking:
            logger.info("[%s] LLM thinking: %s", sid, _truncate(response.thinking))

        if response.tool_calls:
            tool_names = [tc.name for tc in response.tool_calls]
            logger.info("[%s] LLM requested %d tool call(s): %s",
                        sid, len(response.tool_calls), ", ".join(tool_names))

        if response.text:
            logger.info("[%s] reply message: %s", sid, _truncate(response.text))

    async def _on_before_tool(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        tool_call = ctx.data.get("tool_call")
        if tool_call:
            logger.info("[%s] tool-call: %s (%s)", sid, tool_call.name, tool_call.arguments)

    async def _on_after_tool(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        tool_call = ctx.data.get("tool_call")
        result = ctx.data.get("result", "")
        if tool_call:
            logger.info("[%s] tool-result: %s", sid, _truncate(result))

    async def _on_complete(self, ctx: AgentContext) -> None:
        sid = ctx.session_id or "unknown"
        reason = ctx.data.get("reason", "unknown")
        logger.info("[%s] job finished: %s", sid, reason)
        self._iteration_counts.pop(sid, None)
