from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class CancelPlugin(Plugin):
    """cmd_cancel 指令触发：经 ctx._self._jobs 定位目标 task → Task.cancel()。"""

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
