from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class Plugin(ABC):
    """Base class for all plugins."""

    name: str = ""

    async def on_init(self, ctx: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class Skill(Plugin):
    """A plugin that exposes tools to the LLM."""

    @property
    @abstractmethod
    def tools(self) -> list[ToolDefinition]:
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        ...


class PluginRegistry:
    """Manages plugin/skill registration and tool dispatch."""

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []
        self._skills: dict[str, Skill] = {}
        self._tool_map: dict[str, Skill] = {}

    def register(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)
        if isinstance(plugin, Skill):
            self._skills[plugin.name] = plugin
            for tool in plugin.tools:
                self._tool_map[tool.name] = plugin

    async def init_all(self, ctx: Any) -> None:
        for plugin in self._plugins:
            await plugin.on_init(ctx)

    async def shutdown_all(self) -> None:
        for plugin in reversed(self._plugins):
            await plugin.on_shutdown()

    def get_tool_definitions(self) -> list[ToolDefinition]:
        defs: list[ToolDefinition] = []
        for skill in self._skills.values():
            defs.extend(skill.tools)
        return defs

    async def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        skill = self._tool_map.get(tool_name)
        if skill is None:
            return f"Error: unknown tool '{tool_name}'"
        return await skill.execute(tool_name, arguments)
