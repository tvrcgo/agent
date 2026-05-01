from __future__ import annotations

import asyncio

from agent.core.plugin import Plugin, PluginRegistry, PluginContext
from agent.core.loop import JobAborted
from agent.core.ws import StatusEvent


class ConfirmPlugin(Plugin):
    """Intercepts ``request_confirmation`` tool calls via the ``before_tool`` hook.

    The UI recognizes the ``tool_call`` with ``name: "request_confirmation"``
    and renders approve/deny buttons.  On deny it raises ``JobAborted``.
    """

    name = "confirm"

    def __init__(self) -> None:
        self._pending: dict[str, tuple[asyncio.Event, str]] = {}

    def register(self, registry: PluginRegistry) -> None:
        registry.on("before_tool", self._on_before_tool)
        registry.on("command:confirm", self._on_command_confirm)

    async def _on_before_tool(self, ctx: PluginContext) -> None:
        tool_call = ctx.data.get("tool_call")
        if tool_call is None or tool_call.name != "request_confirmation":
            return

        await ctx.client.emit(StatusEvent(state="waiting"))

        event = asyncio.Event()
        self._pending[tool_call.id] = (event, "")

        await event.wait()
        _, decision = self._pending.pop(tool_call.id, (None, "deny"))

        if decision == "deny":
            raise JobAborted("Operation cancelled by user.")

    async def _on_command_confirm(self, ctx: PluginContext) -> None:
        confirm_id = ctx.data.get("confirm_id", "")
        decision = ctx.data.get("decision", "deny")
        if confirm_id in self._pending:
            event, _ = self._pending[confirm_id]
            self._pending[confirm_id] = (event, decision)
            event.set()
