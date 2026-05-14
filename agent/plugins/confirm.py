from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import JobAborted, AgentContext, Job



class ConfirmPlugin(Plugin):

    name = "confirm"

    def __init__(self) -> None:
        self._pending: dict[str, tuple[asyncio.Event, str]] = {}

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        registry.on("before_tool", self._on_before_tool)
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
            raise JobAborted("Operation cancelled by user.")

    async def _on_command_confirm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        confirm_id = job.data.get("confirm_id", "")
        decision = job.data.get("decision", "deny")
        if confirm_id in self._pending:
            event, _ = self._pending[confirm_id]
            self._pending[confirm_id] = (event, decision)
            event.set()
