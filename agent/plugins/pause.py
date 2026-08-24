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
        if gate is None or gate.is_set():
            return None

        job.status = "paused"
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="status", content="paused", session_id=job.id),
        )
        await gate.wait()
        job.status = "running"
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="status", content="running", session_id=job.id),
        )
        return None

    async def _on_cmd_pause(self, ctx: AgentContext, evt: Event) -> None:
        target = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        gate = self._gates.get(target)
        if gate is not None:
            gate.clear()

    async def _on_cmd_resume(self, ctx: AgentContext, evt: Event) -> None:
        target = evt.data.get("session_id") or (evt.job.id if evt.job else None)
        gate = self._gates.get(target)
        if gate is not None:
            gate.set()
