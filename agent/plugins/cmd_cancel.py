from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)


class CmdCancelPlugin(Plugin):
    name = "cmd-cancel"

    def __init__(self) -> None:
        self._ctx: AgentContext | None = None

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        self._ctx = ctx
        ctx.on("cmd_cancel", self._on_cmd_cancel)
        ctx.register("cancel_job", self._cancel_job)
        logger.info("CmdCancelPlugin loaded")

    def unload(self) -> None:
        self._ctx = None
        logger.info("CmdCancelPlugin shut down")

    async def _on_cmd_cancel(self, ctx: AgentContext, evt: Event) -> None:
        target_id = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        if target_id is None:
            return
        target = ctx.job(target_id)
        if target is not None and target._task is not None:
            target._task.cancel()
            logger.info("Cancel requested for job %s", target_id)

    async def _cancel_job(self, job_id: str) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        job = ctx.job(job_id)
        if job is not None and job._task is not None and not job._task.done():
            job._task.cancel()
            try:
                await job._task
            except (asyncio.CancelledError, Exception):
                pass
