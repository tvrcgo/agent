from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent.core.tool import Tool

logger = logging.getLogger(__name__)


class SubJobTool(Tool):
    name = "sub_job"
    description = (
        "Decompose a complex task into independent sub-tasks and execute "
        "them in parallel. Each sub-task runs independently with full tool "
        "access. Results are aggregated and returned."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of independent sub-tasks to execute in parallel",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Specific, self-contained task description",
                        },
                    },
                    "required": ["description"],
                },
            },
        },
        "required": ["tasks"],
    }

    async def execute(self, arguments: dict, ctx: Any = None) -> str:
        tasks = arguments.get("tasks", [])
        if not tasks:
            return "Error: no tasks provided"
        if ctx is None or ctx._loop is None:
            return "Error: sub-job execution unavailable"

        async def run_one(task: dict, i: int) -> tuple[int, str, str]:
            desc = task.get("description", f"task-{i + 1}")
            result = await ctx._loop.spawn(desc, ctx)
            return (i, desc, result)

        results = await asyncio.gather(*[run_one(t, i) for i, t in enumerate(tasks)])
        results.sort(key=lambda x: x[0])

        lines = []
        for i, desc, result in results:
            lines.append(f"## Sub-task {i + 1}: {desc}\n\n{result}")
        return "\n\n---\n\n".join(lines)
