from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext
from agent.core.ws import JobTreeEvent

if TYPE_CHECKING:
    from agent.core.config import Config

logger = logging.getLogger(__name__)


class SubJobPlugin(Plugin):

    name = "subjob"

    def load(self, registry: PluginRegistry, config: Config) -> None:
        registry.on("before_job", self._broadcast)
        registry.on("before_llm", self._broadcast)
        registry.on("before_tools", self._broadcast)
        registry.on("on_complete", self._broadcast)
        logger.info("SubJobPlugin initialized, max_depth=%d", config.agent.max_sub_job_depth)

    def unload(self) -> None:
        logger.info("SubJobPlugin shut down")

    async def _broadcast(self, ctx: AgentContext) -> None:
        loop = ctx._loop
        if loop is None:
            return
        job = loop._jobs.get(ctx.session_id)
        if job is None:
            return
        root_id = job.id
        while job.parent_id is not None:
            job = loop._jobs[job.parent_id]
            root_id = job.id
        root_ctx = loop._contexts.get(root_id)
        if root_ctx is None or root_ctx.client.is_silent:
            return
        jobs_data = [
            {"id": j.id, "parent_id": j.parent_id, "depth": j.depth,
             "status": j.status, "content": j.content, "result": j.result}
            for j in loop._jobs.values()
            if self._in_tree(loop._jobs, j.id, root_id)
        ]
        if jobs_data:
            await root_ctx.client.emit(JobTreeEvent(jobs=jobs_data))

    @staticmethod
    def _in_tree(jobs: dict, job_id: str, root_id: str) -> bool:
        if job_id == root_id:
            return True
        current = jobs.get(job_id)
        while current and current.parent_id is not None:
            if current.parent_id == root_id:
                return True
            current = jobs.get(current.parent_id)
        return False
