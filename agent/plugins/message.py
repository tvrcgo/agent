"""Message plugin：把核心事件翻译成对外输出消息（msg_output）。

loop 只 emit 领域事件（llm_start / llm_chunk / llm_end / tools_start / tool_start
/ tool_end / job_end / job_error 等），本插件监听这些事件，按当前模式（流式/非流式）
构造 `OutputMessage` 并发出。loop 与核心组件不感知输出细节。
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event
from agent.core.io import OutputMessage

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class MessagePlugin(Plugin):

    name = "message"

    def __init__(self) -> None:
        self._stream: bool = False

    def load(self, ctx: "AgentContext", config: dict = {}) -> None:
        # 需要判断是否流式模式（决定 llm_end 是否补发完整消息）
        if ctx.config is not None:
            self._stream = bool(getattr(ctx.config, "stream", False))

        ctx.on("llm_start", self._on_llm_start)
        ctx.on("llm_chunk", self._on_llm_chunk)
        ctx.on("llm_end", self._on_llm_end)
        ctx.on("tools_start", self._on_tools_start)
        ctx.on("tool_start", self._on_tool_start)
        ctx.on("tool_end", self._on_tool_end)
        ctx.on("job_end", self._on_job_end)
        ctx.on("job_error", self._on_job_error)
        logger.info("MessagePlugin initialized, stream=%s", self._stream)

    def unload(self) -> None:
        logger.info("MessagePlugin shut down")

    async def _on_llm_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        # status:thinking 表示进入推理阶段
        await ctx.emit("msg_output", output=OutputMessage(
            type="status", content="thinking", session_id=job.id,
        ))

    async def _on_llm_chunk(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        chunk = evt.data.get("chunk")
        if chunk is None:
            return
        # 流式分块：按内容分发为 thinking / message 两条流
        if getattr(chunk, "thinking", None):
            await ctx.emit("msg_output", output=OutputMessage(
                type="thinking",
                content=chunk.thinking,
                session_id=job.id,
                stream=True,
            ))
        if getattr(chunk, "text", None):
            await ctx.emit("msg_output", output=OutputMessage(
                type="message",
                content=chunk.text,
                session_id=job.id,
                stream=True,
            ))

    async def _on_llm_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        response = evt.data.get("response")
        if response is None:
            return
        # 非流式：整条输出（流式已由 llm_chunk 实时分发，不重复补发）
        if self._stream:
            return
        if getattr(response, "thinking", None):
            await ctx.emit("msg_output", output=OutputMessage(
                type="thinking",
                content=response.thinking,
                session_id=job.id,
            ))
        if getattr(response, "text", None) and not getattr(response, "tool_calls", None):
            await ctx.emit("msg_output", output=OutputMessage(
                type="message",
                content=response.text,
                session_id=job.id,
            ))

    async def _on_tools_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        # status:acting 表示进入工具执行阶段
        await ctx.emit("msg_output", output=OutputMessage(
            type="status", content="acting", session_id=job.id,
        ))

    async def _on_tool_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        tool_call = evt.data.get("tool_call")
        if tool_call is None:
            return
        # 工具调用即将执行：展示调用文本与参数
        await ctx.emit("msg_output", output=OutputMessage(
            type="tool_call",
            content=f"{tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})",
            session_id=job.id,
            data={
                "id": tool_call.id,
                "tool": tool_call.name,
                "arguments": tool_call.arguments,
            },
        ))

    async def _on_tool_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        tool_call = evt.data.get("tool_call")
        if tool_call is None:
            return
        result = evt.data.get("result", "")
        error = evt.data.get("error", "")
        # 工具执行结束：展示结果
        await ctx.emit("msg_output", output=OutputMessage(
            type="tool_result",
            content=result,
            session_id=job.id,
            data={
                "id": tool_call.id,
                "tool": tool_call.name,
                "error": error,
            },
        ))

    async def _on_job_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        if job.status == "error":
            reason = evt.data.get("reason", "")
            if reason == "max_iterations":
                reason_text = "Reached maximum iterations"
            else:
                reason_text = reason or "Unknown error"
            status_event = OutputMessage(type="error", content=reason_text, data={"reason": reason_text}, session_id=job.id)
        elif job.status == "cancelled":
            status_event = OutputMessage(type="status", content="cancelled", session_id=job.id)
        else:
            status_event = OutputMessage(type="status", content="done", session_id=job.id)

        await ctx.emit("msg_output", output=status_event)

    async def _on_job_error(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        error = evt.data.get("error")
        reason = str(error) if error is not None else "Unknown error"
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="error", content=reason, data={"reason": reason}, session_id=job.id),
        )
