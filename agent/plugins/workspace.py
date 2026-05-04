from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext

if TYPE_CHECKING:
    from agent.core.config import Config

logger = logging.getLogger(__name__)


class WorkspacePlugin(Plugin):

    name = "workspace"

    def __init__(self) -> None:
        self._base_path = Path("./workspace")

    def load(self, registry: PluginRegistry, config: Config) -> None:
        registry.on("on_connect", self._on_connect)
        registry.on("before_llm", self._on_before_llm)
        logger.info("WorkspacePlugin initialized, base_path=%s", self._base_path)

    def unload(self) -> None:
        logger.info("WorkspacePlugin shut down")

    async def _on_connect(self, ctx: AgentContext) -> None:
        sid = ctx.session_id
        ws_dir = (self._base_path / sid).resolve()
        ws_dir.mkdir(parents=True, exist_ok=True)
        ctx.data["workspace"] = ws_dir
        logger.info("Workspace ready: %s", ws_dir)

    async def _on_before_llm(self, ctx: AgentContext) -> None:
        if ctx.data.get("_ws_injected"):
            return
        ws_dir = ctx.data.get("workspace")
        if ws_dir is None:
            return
        messages = ctx.data.get("messages")
        if not messages:
            return
        system_msg = messages[0]
        if system_msg.role != "system":
            return
        hint = (
            f"\n\nCurrent session workspace: {ws_dir}\n"
            "When you need to create, read, or modify files, use this directory "
            "as the working directory for all file paths."
        )
        system_msg.content += hint
        ctx.data["_ws_injected"] = True
