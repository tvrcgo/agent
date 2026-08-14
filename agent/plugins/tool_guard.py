from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event
from agent.core.model import ToolResult, UserMessage

if TYPE_CHECKING:
    from agent.core.loop import AgentContext
    from agent.core.model import ToolCall

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_PROMPT = (
    "你是一个安全审查助手。判断以下工具调用是否具有破坏性风险。"
    "只回复 safe 或 dangerous，不要其他内容。"
    "危险操作包括：删除文件、覆盖文件、修改系统配置、执行不可逆命令（如 rm、format、shutdown）等。"
    "如果用户明确要求该操作则视为 safe。"
)


class ToolGuardPlugin(Plugin):
    # 工具执行守卫：审查清单中的工具交 flash 判断，dangerous 委托确认

    name = "tool_guard"

    def __init__(self) -> None:
        self._review_tools: set[str] = set()
        self._review_prompt: str = DEFAULT_REVIEW_PROMPT

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        self._review_tools = set(config.get("review_tools", []))
        self._review_prompt = config.get("review_prompt", DEFAULT_REVIEW_PROMPT)
        ctx.on("tools_start", self._on_tools_start)
        logger.info("ToolGuardPlugin loaded: review_tools=%d", len(self._review_tools))

    def unload(self) -> None:
        pass

    async def _on_tools_start(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return

        tool_calls = evt.data.get("tool_calls") or []
        if not tool_calls:
            return

        flash = ctx.models.get("flash")
        blocked: list[ToolCall] = []

        for tc in tool_calls:
            if tc.name not in self._review_tools:
                continue

            verdict = "dangerous"
            if flash is not None:
                try:
                    verdict = await self._review(flash, tc.name, tc.arguments, job)
                except Exception as e:
                    logger.warning("Flash LLM review failed for %s: %s", tc.name, e)

            if verdict == "safe":
                continue

            # 委托确认，deny 则阻断：记录待剔除，循环外统一处理
            response = await ctx.emit(
                "req:request_confirm", job,
                confirm_description=f"Tool '{tc.name}': {tc.arguments} (verdict: {verdict})",
            )
            approved = bool(response and response.get("decision") == "approve")
            if not approved:
                blocked.append(tc)

        # 循环外统一剔除（避免迭代中修改列表）
        # 未执行不触发 tool_start/tool_end；结果写 tool_results + emit tool_error 供 session 持久化
        for tc in blocked:
            reason = f"Tool '{tc.name}' execution denied by user."
            if job.loop is not None:
                job.loop.tool_results.append(ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    error=reason,
                    content=f"Error: {reason}",
                ))
            await ctx.emit("tool_error", job=job, tool_call=tc, error=reason)
            try:
                tool_calls.remove(tc)
            except ValueError:
                pass

    async def _review(self, flash, tool_name: str, arguments: dict, job) -> str:
        user_msgs = job.loop.steering_messages if job.loop else []
        user_msg = user_msgs[-1] if user_msgs else ""
        review_msg = (
            f"{self._review_prompt}\n\n"
            f"工具名称: {tool_name}\n"
            f"参数: {arguments}\n"
            f"用户消息: {user_msg}"
        )
        resp = await flash.chat([UserMessage(content=review_msg)], tools=None)
        return (resp.text or "").strip().lower()
