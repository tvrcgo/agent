from __future__ import annotations

from typing import Any

from agent.core.plugin import Skill, ToolDefinition


class EchoSkill(Skill):
    """A simple echo skill for testing the plugin system."""

    name = "echo"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="echo",
                description="Echoes back the input message. Useful for testing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to echo back",
                        }
                    },
                    "required": ["message"],
                },
            )
        ]

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        return arguments.get("message", "")
