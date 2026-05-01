from __future__ import annotations

from agent.core.skill import Skill, ToolDefinition


class RequestConfirmationSkill(Skill):
    """Exposes request_confirmation tool so the LLM can ask for user approval.

    The actual blocking and user interaction is handled by ConfirmPlugin
    via the ``before_tool`` hook. This skill's execute is a trivial pass-through.
    """

    name = "request_confirmation"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="request_confirmation",
                description=(
                    "Request user confirmation before performing a critical operation. "
                    "Use this before destructive actions like deleting files, running commands, "
                    "or making irreversible changes. The job will be cancelled if the user denies."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Clear description of what operation needs confirmation",
                        },
                    },
                    "required": ["description"],
                },
            )
        ]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        return "User approved the operation."
