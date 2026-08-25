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


class CmdResetPlugin(Plugin):
    name = "cmd-reset"

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.on("cmd_reset", self._on_cmd_reset)
        logger.info("CmdResetPlugin loaded")

    def unload(self) -> None:
        logger.info("CmdResetPlugin shut down")

    async def _on_cmd_reset(self, ctx: AgentContext, evt: Event) -> None:
        target_id = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        if not target_id:
            return

        try:
            await ctx.invoke("cancel_job", job_id=target_id)
        except KeyError:
            logger.warning("Reset: cancel_job not registered")

        try:
            ctx.invoke("reset_session", session_id=target_id)
        except KeyError:
            logger.warning("Reset: reset_session not registered")

        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="message", content="Session reset", session_id=target_id),
        )
