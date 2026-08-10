from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.loop import AgentContext, Job, MessageEvent
from agent.core.events import Event

logger = logging.getLogger(__name__)


class ConfirmPlugin(Plugin):

    name = "confirm"

    def __init__(self) -> None:
        self._pending: dict[str, tuple[asyncio.Event, str]] = {}
        self._timeout: float = 120

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        self._timeout = float(config.get("confirm_timeout", 120))

        ctx.on("before_tool", self._on_before_tool)
        ctx.on("request_confirm", self._on_request_confirm)
        ctx.on("cmd_confirm", self._on_command_confirm)

    def unload(self) -> None:
        self._pending.clear()

    async def _on_before_tool(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        tool_call = evt.tool_call
        if tool_call is None or tool_call.name != "request_confirmation":
            return

        wait_event = asyncio.Event()
        self._pending[tool_call.id] = (wait_event, "")

        try:
            await asyncio.wait_for(wait_event.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("Confirmation timed out after %ss for %s", self._timeout, tool_call.id)
            evt.abort = True
            evt.result = "Operation cancelled: confirmation timed out."
        else:
            _, decision = self._pending.pop(tool_call.id, (None, "deny"))
            if decision == "deny":
                evt.abort = True
                evt.result = "Operation cancelled by user."
        finally:
            self._pending.pop(tool_call.id, None)

    async def _on_request_confirm(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job

        confirm_id = evt.data.get("confirm_id", "")
        if not confirm_id:
            confirm_id = str(uuid.uuid4())[:8]
            evt.confirm_id = confirm_id

        description = evt.data.get("confirm_description", "")

        if job is not None and job.output is not None:
            job.output.events.append(
                MessageEvent(
                    type="confirm_request",
                    data={"id": confirm_id, "description": description},
                )
            )
            await ctx.emit("msg_output", job=job)

        wait_event = asyncio.Event()
        self._pending[confirm_id] = (wait_event, "")

        try:
            await asyncio.wait_for(wait_event.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("Confirmation timed out after %ss for %s", self._timeout, confirm_id)
            evt.confirm_decision = "deny"
        else:
            _, decision = self._pending.pop(confirm_id, (None, "deny"))
            evt.confirm_decision = decision
        finally:
            self._pending.pop(confirm_id, None)

    async def _on_command_confirm(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        confirm_id = evt.data.get("confirm_id", "")
        decision = evt.data.get("decision", "deny")
        if confirm_id in self._pending:
            wait_event, _ = self._pending[confirm_id]
            self._pending[confirm_id] = (wait_event, decision)
            wait_event.set()
