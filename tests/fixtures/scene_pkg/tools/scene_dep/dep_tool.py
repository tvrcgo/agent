from __future__ import annotations

from typing import Any

from agent.core.loop import AgentContext, Job
from agent.core.tool import Tool


class SceneDepTool(Tool):
    name = "scene_dep"
    description = "Test tool with a requirements.txt next to it."

    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        return "scene-dep-ok"
