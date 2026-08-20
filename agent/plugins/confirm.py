from __future__ import annotations

import asyncio
import logging
import uuid
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
        ctx.on("confirm_request", self._on_confirm_request)
        logger.info("ConfirmPlugin loaded: timeout=%ds", self._timeout)

    def unload(self) -> None:
        self._ctx = None

    async def _on_confirm_request(self, ctx: AgentContext, evt: Event) -> dict:
        job = evt.job
        if job is None:
            return {"decision": "deny"}
        decision = await self._ask_ui(job, evt.data.get("confirm_description", ""))
        # serial 响应方：返回非 None（决策 dict）→ 短路并作为 emit 返回值
        return decision if decision is not None else {"decision": "deny"}

    async def _ask_ui(self, job: Job, description: str) -> dict | None:
        confirm_id = uuid.uuid4().hex[:8]
        future: asyncio.Future[dict | None] = asyncio.get_event_loop().create_future()

        async def on_cmd(ctx2: AgentContext, evt2: Event) -> None:
            # cmd_confirm 带的是新构造的 Job（id=session_id），按 id 匹配
            if (evt2.job is not None and evt2.job.id == job.id
                    and evt2.data.get("confirm_id") == confirm_id):
                if not future.done():
                    future.set_result({"decision": evt2.data.get("decision", "deny")})

        ctx = self._ctx
        if ctx is None:
            return None
        ctx.on("cmd_confirm", on_cmd)
        try:
            await self._push_confirm(job, confirm_id, description)
            try:
                return await asyncio.wait_for(future, timeout=self._timeout)
            except asyncio.TimeoutError:
                return None
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
