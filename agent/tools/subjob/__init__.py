from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.core.tool import Tool

logger = logging.getLogger(__name__)


class SubJobTool(Tool):
    name = "sub_job"
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

    async def execute(self, arguments: dict, ctx: Any = None) -> str:
        jobs = arguments.get("jobs", [])
        if not jobs:
            return "Error: no jobs provided"
        if ctx is None or ctx._loop is None:
            return "Error: sub-job execution unavailable"
        if len(jobs) > 5:
            return f"Error: at most 5 sub-jobs allowed, got {len(jobs)}"

        async def run_one(job: dict, i: int) -> tuple[int, str, str]:
            content = job.get("content", f"job-{i + 1}")
            result = await ctx._loop.spawn(content, ctx)
            return (i, content, result)

        results = await asyncio.gather(*[run_one(j, i) for i, j in enumerate(jobs)])
        results.sort(key=lambda x: x[0])

        lines = []
        for i, content, result in results:
            lines.append(f"## Sub-job {i + 1}: {content}\n\n{result}")
        return "\n\n---\n\n".join(lines)
