from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class CancelPlugin(Plugin):
    """Job 取消指令触发（独立插件，不修改 core）。

    core 仍保留 `CancelledError` 的有序收尾机制（`_run_loop` 捕获 → job_end），
    但"cmd_cancel 指令 → 找到目标 task 并取消"的触发逻辑下沉到这里。

    - 经 `ctx._self._jobs` 定位目标 job（与 subjob 插件访问 ctx._self 同一先例）。
    - 支持 `session_id` 指定目标（默认取命令所在 job.id），可取消子 job。
    - 与 `cmd_pause`/`cmd_resume` 正交：暂停中取消走 `CancelledError` 中断 gate.wait。
    """

    name = "cancel"

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.on("cmd_cancel", self._on_cmd_cancel)
        logger.info("CancelPlugin loaded")

    def unload(self) -> None:
        logger.info("CancelPlugin shut down")

    async def _on_cmd_cancel(self, ctx: AgentContext, evt: Event) -> None:
        if ctx._self is None:
            return
        target_id = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        if target_id is None:
            return
        target = ctx._self._jobs.get(target_id)
        if target is not None and target._task is not None:
            target._task.cancel()
            logger.info("Cancel requested for job %s", target_id)
