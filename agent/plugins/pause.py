from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event
from agent.core.io import OutputMessage

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class PausePlugin(Plugin):
    """Job 暂停 / 恢复机制（独立插件，不修改 core、不新增事件）。

    复用现有事件钩子作为挂载点：
    - `turn_start`（parallel）：工具跑完后、下一轮开始前暂停。
    - `tools_start`（serial）：行动前暂停（工具不会执行）。

    与守卫链（tool_guard）同挂在 `tools_start`，顺序由 plugins 配置顺序决定：
    pause 在前 → 先暂停后审查；tool_guard 在前 → 先审查后暂停。pause handler
    始终返回 None，不触发 serial 短路、不破坏守卫链返回值语义。

    门闩为插件私有 `self._gates[job.id]`（不写 job.data，无覆盖风险），
    生命周期与 job 同步（job_start 建 / job_end 清）。
    """

    name = "pause"

    def __init__(self) -> None:
        self._gates: dict[str, asyncio.Event] = {}

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.on("job_start", self._on_job_start)
        ctx.on("job_end", self._on_job_end)
        ctx.on("turn_start", self._on_pause_point)
        ctx.on("tools_start", self._on_pause_point)
        ctx.on("cmd_pause", self._on_cmd_pause)
        ctx.on("cmd_resume", self._on_cmd_resume)
        logger.info("PausePlugin loaded")

    def unload(self) -> None:
        self._gates.clear()
        logger.info("PausePlugin shut down")

    async def _on_job_start(self, ctx: AgentContext, evt: Event) -> None:
        if evt.job is not None:
            # 门闩初始为 set（运行/放行）；clear = 暂停（阻塞直到 resume set）
            gate = asyncio.Event()
            gate.set()
            self._gates[evt.job.id] = gate

    async def _on_job_end(self, ctx: AgentContext, evt: Event) -> None:
        if evt.job is not None:
            self._gates.pop(evt.job.id, None)

    async def _on_pause_point(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return None
        gate = self._gates.get(job.id)
        # 门闩 set = 运行中（放行）；clear = 暂停（阻塞）
        # （asyncio.Event.set 是粘性的：set 后 wait 立即返回，clear 后 wait 才阻塞）
        if gate is None or gate.is_set():
            return None

        job.status = "paused"
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="status", content="paused", session_id=job.id),
        )
        # 阻塞挂载点；cmd_cancel 的 Task.cancel() 可注入 CancelledError 中断
        await gate.wait()
        job.status = "running"
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="status", content="running", session_id=job.id),
        )
        # 显式放行，不短路 serial（tools_start 守卫链）
        return None

    async def _on_cmd_pause(self, ctx: AgentContext, evt: Event) -> None:
        target = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        gate = self._gates.get(target)
        if gate is not None:
            gate.clear()   # 暂停 = 清空门闩（阻塞）

    async def _on_cmd_resume(self, ctx: AgentContext, evt: Event) -> None:
        target = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        gate = self._gates.get(target)
        if gate is not None:
            gate.set()     # 恢复 = 置位门闩（放行）
