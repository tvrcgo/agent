from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.events import Event
from agent.core.io import OutputMessage

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)


class ConfirmPlugin(Plugin):

    name = "confirm"

    def __init__(self) -> None:
        self._timeout: float = 120
        self._ctx: AgentContext | None = None

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        self._ctx = ctx
        self._timeout = float(config.get("timeout", 120))
        ctx.on("req:request_confirm", self._on_request_confirm)
        ctx.on("req:confirm_ui", self._on_confirm_ui)
        logger.info("ConfirmPlugin loaded: timeout=%ds", self._timeout)

    def unload(self) -> None:
        self._ctx = None

    async def _on_request_confirm(self, ctx: "AgentContext", evt: Event) -> None:
        job = evt.job
        req = evt.request
        if job is None or req is None:
            return
        decision = await ctx.emit(
            "req:confirm_ui", job,
            timeout=self._timeout,
            confirm_id=req.id,
            confirm_description=req.data.get("confirm_description", ""),
        )
        # 隐式返回，总线自动 req.done
        return decision if decision is not None else {"decision": "deny"}

    async def _on_confirm_ui(self, ctx: "AgentContext", evt: Event) -> None:
        job = evt.job
        ui_req = evt.request
        if job is None or ui_req is None:
            return
        confirm_id = ui_req.data.get("confirm_id", "")

        async def on_cmd(ctx2: "AgentContext", evt2: Event) -> None:
            # cmd_confirm 带的是新构造的 Job（id=session_id），按 id 匹配
            if evt2.job is not None and evt2.job.id == job.id and evt2.data.get("confirm_id") == confirm_id:
                ui_req.done({"decision": evt2.data.get("decision", "deny")})

        ctx.on("cmd_confirm", on_cmd)
        try:
            await self._push_confirm(job, confirm_id, ui_req.data.get("confirm_description", ""))
            return await ui_req.wait(self._timeout)
        finally:
            ctx.off("cmd_confirm", on_cmd)

    async def _push_confirm(self, job: Job, confirm_id: str, description: str) -> None:
        if self._ctx is not None:
            await self._ctx.emit(
                "msg_output",
                output=OutputMessage(
                    type="confirm",
                    data={"id": confirm_id, "description": description},
                    session_id=job.id,
                ),
            )
