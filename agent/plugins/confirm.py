from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext, Job, MessageEvent


class ConfirmPlugin(Plugin):

    name = "confirm"

    def __init__(self) -> None:
        self._pending: dict[str, tuple[asyncio.Event, str]] = {}

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        registry.on("before_tool", self._on_before_tool)
        registry.on("request_confirm", self._on_request_confirm)
        registry.on("command:confirm", self._on_command_confirm)

    async def _on_before_tool(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        tool_call = job.data.get("tool_call")
        if tool_call is None or tool_call.name != "request_confirmation":
            return

        event = asyncio.Event()
        self._pending[tool_call.id] = (event, "")

        await event.wait()
        _, decision = self._pending.pop(tool_call.id, (None, "deny"))

        if decision == "deny":
            job.data["result"] = "Operation cancelled by user."

    async def _on_request_confirm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        confirm_id = ctx.data.get("confirm_id", "")
        if not confirm_id:
            confirm_id = str(uuid.uuid4())[:8]
            ctx.data["confirm_id"] = confirm_id

        description = ctx.data.get("confirm_description", "")

        if job.output is not None:
            job.output.events.append(
                MessageEvent(
                    type="confirm_request",
                    data={"id": confirm_id, "description": description},
                )
            )
            await ctx.emit("on_output", job)

        event = asyncio.Event()
        self._pending[confirm_id] = (event, "")

        await event.wait()
        _, decision = self._pending.pop(confirm_id, (None, "deny"))
        ctx.data["confirm_decision"] = decision
        ctx.data.pop("confirm_id", None)
        ctx.data.pop("confirm_description", None)

    async def _on_command_confirm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        confirm_id = (
            job.data.get("confirm_id", "")
            or (job.input.data.get("confirm_id", "") if job.input else "")
            or ctx.data.get("confirm_id", "")
        )
        decision = (
            job.data.get("decision", "")
            or (job.input.data.get("decision", "") if job.input else "")
            or "deny"
        )
        if confirm_id in self._pending:
            event, _ = self._pending[confirm_id]
            self._pending[confirm_id] = (event, decision)
            event.set()
