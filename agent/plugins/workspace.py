from __future__ import annotations

import logging
from pathlib import Path

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext, Job


logger = logging.getLogger(__name__)


class WorkspacePlugin(Plugin):

    name = "workspace"

    def __init__(self) -> None:
        self._base_path = Path("./workspace")

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        registry.on("before_job", self._on_before_job)
        registry.on("before_llm", self._on_before_llm)
        logger.info("WorkspacePlugin initialized, base_path=%s", self._base_path)

    def unload(self) -> None:
        logger.info("WorkspacePlugin shut down")

    async def _on_before_job(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        # Only initialize workspace for root jobs (id equals session_id)
        if job.id != job.session_id:
            return
        ws_dir = (self._base_path / job.session_id).resolve()
        ws_dir.mkdir(parents=True, exist_ok=True)
        job.data["workspace"] = ws_dir
        logger.info("Workspace ready: %s", ws_dir)

    async def _on_before_llm(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        if job.data.get("_ws_injected"):
            return
        ws_dir = job.data.get("workspace")
        if ws_dir is None:
            return
        messages = job.data.get("messages")
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
        job.data["_ws_injected"] = True
