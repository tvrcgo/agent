from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import AgentContext, Job, InputMessage, MessageEvent


logger = logging.getLogger(__name__)


class SubJobPlugin(Plugin):

    name = "subjob"

    def __init__(self) -> None:
        self._max_sub_job_depth: int = 1
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._depth: dict[str, int] = {}
        self._parent: dict[str, str] = {}

    def load(self, registry: PluginRegistry, config: dict = {}) -> None:
        registry.on("agent_start", self._on_agent_start)
        registry.on("after_llm", self._send_jobs)
        registry.on("after_tools", self._send_jobs)
        registry.on("after_job", self._send_jobs)
        registry.on("after_job", self._on_after_job)
        self._max_sub_job_depth = config.get("max_depth", 2)
        logger.info("SubJobPlugin initialized, max_depth=%d", self._max_sub_job_depth)

    async def _on_agent_start(self, ctx: AgentContext, job: Job | None) -> None:
        ctx.subjob = self._create_subjob

    async def _send_jobs(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None or ctx._self is None:
            return
        session_id = job.session_id
        jobs_data = [
            {
                "id": j.id,
                "parent_id": self._parent.get(j.id),
                "depth": self._depth.get(j.id, 0),
                "status": j.status,
                "content": j.input.content if j.input else "",
            }
            for j in ctx._self._jobs.values()
            if j.session_id == session_id
        ]
        if job.output is not None:
            job.output.events.append(MessageEvent(type="data", data={"name": "jobs", "jobs": jobs_data}))
            await ctx.emit("on_output", job)

    async def _on_after_job(self, ctx: AgentContext, job: Job | None) -> None:
        if job is None:
            return
        future = self._pending.pop(job.id, None)
        if future and not future.done():
            result = job.output.content if job.output else ""
            future.set_result(result)
        self._depth.pop(job.id, None)
        self._parent.pop(job.id, None)

    def _create_subjob(self, content: str, parent_job: Job, ctx: AgentContext) -> asyncio.Future[str]:
        depth = self._depth.get(parent_job.id, 0)

        if depth >= self._max_sub_job_depth:
            future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            future.set_result(f"Error: maximum sub-job depth ({self._max_sub_job_depth}) reached")
            return future

        sub_id = f"{parent_job.id}/{uuid.uuid4().hex[:8]}"
        future = asyncio.get_event_loop().create_future()
        self._pending[sub_id] = future
        self._depth[sub_id] = depth + 1
        self._parent[sub_id] = parent_job.id

        sub_job = Job(
            id=sub_id,
            session_id=parent_job.session_id,
            status="pending",
            input=InputMessage(content=content, session_id=parent_job.session_id),
        )

        asyncio.create_task(ctx.emit("on_input", sub_job))
        return future

    def unload(self) -> None:
        logger.info("SubJobPlugin shut down")
