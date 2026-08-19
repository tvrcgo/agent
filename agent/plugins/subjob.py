from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from agent.core.plugin import Plugin
from agent.core.io import InputMessage, OutputMessage
from agent.core.loop import AgentContext, Job
from agent.core.events import Event


logger = logging.getLogger(__name__)


class SubJobPlugin(Plugin):

    name = "subjob"

    def __init__(self) -> None:
        self._max_sub_job_depth: int = 1
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._depth: dict[str, int] = {}
        self._parent: dict[str, str] = {}

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.on("agent_start", self._on_agent_start)
        ctx.on("llm_end", self._send_jobs)
        ctx.on("tools_end", self._send_jobs)
        ctx.on("job_end", self._send_jobs)
        ctx.on("job_end", self._on_job_end)
        self._max_sub_job_depth = config.get("max_depth", 2)
        logger.info("SubJobPlugin initialized, max_depth=%d", self._max_sub_job_depth)

    async def _on_agent_start(self, ctx: AgentContext, evt: Event) -> None:
        ctx.subjob = self._create_subjob

    async def _send_jobs(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None or ctx._self is None:
            return
        root_session = self._root_session(job.id)
        jobs_data = [
            {
                "id": j.id,
                "parent_id": self._parent.get(j.id),
                "depth": self._depth.get(j.id, 0),
                "status": j.status,
                "content": j.input.content if j.input else "",
            }
            for j in ctx._self._jobs.values()
            if self._root_session(j.id) == root_session
        ]
        await ctx.emit(
            "msg_output",
            output=OutputMessage(type="data", session_id=root_session, data={"name": "jobs", "jobs": jobs_data}),
        )

    def _root_session(self, session_id: str) -> str:
        """沿父子映射找到最顶层 session（root 调用方的会话）。"""
        seen: set[str] = set()
        cur = session_id
        while cur in self._parent and cur not in seen:
            seen.add(cur)
            cur = self._parent[cur]
        return cur

    async def _on_job_end(self, ctx: AgentContext, evt: Event) -> None:
        job = evt.job
        if job is None:
            return
        key = job.id
        future = self._pending.pop(key, None)
        if future and not future.done():
            future.set_result(job.turn.content if job.turn else "")
        self._depth.pop(key, None)
        self._parent.pop(key, None)

    async def _create_subjob(self, content: str, parent_job: Job, ctx: AgentContext) -> asyncio.Future[str]:
        parent_session = parent_job.id
        depth = self._depth.get(parent_session, 0)

        if depth >= self._max_sub_job_depth:
            future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
            future.set_result(f"Error: maximum sub-job depth ({self._max_sub_job_depth}) reached")
            return future

        sub_session = f"{parent_job.id}:{uuid.uuid4().hex[:8]}"
        future = asyncio.get_event_loop().create_future()
        self._pending[sub_session] = future
        self._depth[sub_session] = depth + 1
        self._parent[sub_session] = parent_job.id

        input_msg = InputMessage(
            content=content,
            session_id=sub_session,
        )

        await ctx.emit("msg_input", input=input_msg)
        return future

    def unload(self) -> None:
        logger.info("SubJobPlugin shut down")
