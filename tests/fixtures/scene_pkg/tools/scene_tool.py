from __future__ import annotations

from typing import Any

from agent.core.loop import AgentContext, Job
from agent.core.tool import Tool


class SceneTool(Tool):
    name = "scene_hello"
    description = "Test tool from a scene package."

    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        return "scene-ok"
