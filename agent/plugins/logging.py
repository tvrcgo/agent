from __future__ import annotations

import logging

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext, Job


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

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
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

    async def _on_before_job(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        content = job.input.content if job.input else ""
        if content:
            logger.info("[%s] received message: %s", jid, _truncate(content))
            logger.info("[%s] job start", jid)
            self._iteration_counts[jid] = 0

    async def _on_before_llm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        if jid not in self._iteration_counts:
            self._iteration_counts[jid] = 0
        self._iteration_counts[jid] += 1

        messages = job.data.get("messages", [])

        # Find last user message to show what model is responding to
        last_user_msg = None
        for msg in reversed(messages):
            if hasattr(msg, 'role') and msg.role == 'user':
                last_user_msg = msg.content if hasattr(msg, 'content') else str(msg)
                break

        if last_user_msg:
            logger.info("[%s] Round %d >>> %s",
                        jid, self._iteration_counts[jid], _truncate(last_user_msg))
        else:
            logger.info("[%s] Round %d >>> (no user message, %d messages total)",
                        jid, self._iteration_counts[jid], len(messages))

    async def _on_after_llm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        response = job.data.get("response")
        if response is None:
            return

        if response.thinking:
            logger.info("[%s] LLM thinking: %s", jid, _truncate(response.thinking))

        if response.tool_calls:
            tool_names = [tc.name for tc in response.tool_calls]
            logger.info("[%s] LLM maked %d tool call(s): %s",
                        jid, len(response.tool_calls), ", ".join(tool_names))

        if response.text:
            logger.info("[%s] reply message: %s", jid, _truncate(response.text))

    async def _on_before_tool(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        tool_call = job.data.get("tool_call")
        if tool_call:
            logger.info("[%s] tool-call: %s (%s)", jid, tool_call.name, tool_call.arguments)

    async def _on_after_tool(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        tool_call = job.data.get("tool_call")
        result = job.data.get("result", "")
        if tool_call:
            logger.info("[%s] tool-result: %s", jid, _truncate(result))

    async def _on_complete(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        jid = job.id
        reason = job.data.get("reason", "unknown")
        logger.info("[%s] job finished: %s", jid, reason)
        self._iteration_counts.pop(jid, None)
