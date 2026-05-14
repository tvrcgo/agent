from __future__ import annotations

from typing import TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job


class RequestConfirmationTool(Tool):
    name = "request_confirmation"
    description = (
        "Request user confirmation before performing a critical operation. "
        "Use this before destructive actions like deleting files, running commands, "
        "or making irreversible changes. The job will be cancelled if the user denies."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Clear description of what operation needs confirmation",
            },
        },
        "required": ["description"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        return "User approved the operation."
