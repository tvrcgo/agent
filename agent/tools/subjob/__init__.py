from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job


class SubJobTool(Tool):
    name = "subjob"
    description = (
        "Decompose a complex task into independent sub-jobs and execute "
        "them in parallel (max 5 sub-jobs). Each sub-job runs independently "
        "with full tool access. Results are aggregated and returned."
    )
    parameters = {
        "type": "object",
        "properties": {
            "jobs": {
                "type": "array",
                "description": "List of independent sub-jobs to execute in parallel (max 5)",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Specific, self-contained job description",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        "required": ["jobs"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        jobs_to_run = arguments.get("jobs", [])
        if not jobs_to_run:
            return "Error: no jobs provided"
        if len(jobs_to_run) > 5:
            return f"Error: at most 5 sub-jobs allowed, got {len(jobs_to_run)}"

        subjob = getattr(ctx, "subjob", None)
        if subjob is None:
            return "Error: subjob not available"

        async def run_one(j: dict, i: int) -> tuple[int, str, str]:
            content = j.get("content", f"job-{i + 1}")
            future = subjob(content, job, ctx)
            result = await future
            return (i, content, result)

        results = await asyncio.gather(*[run_one(j, i) for i, j in enumerate(jobs_to_run)])
        results.sort(key=lambda x: x[0])

        lines = [f"## Sub-job {i + 1}: {content}\n\n{result}" for i, content, result in results]
        return "\n\n---\n\n".join(lines)
