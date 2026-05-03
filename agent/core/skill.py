from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """Base class for LLM-callable tools."""

    name: str = ""

    @property
    @abstractmethod
    def tools(self) -> list[ToolDefinition]:
        """Return tool definitions this skill provides."""
        ...

    @abstractmethod
    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool and return the result."""
        ...


class SkillRegistry:
    """Skill/tool registry."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._tool_map: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill and its tools."""
        self._skills[skill.name] = skill
        for tool in skill.tools:
            self._tool_map[tool.name] = skill
        logger.info("Skill registered: %s (tools: %s)", skill.name, [t.name for t in skill.tools])

    def load_modules(self, names: list[str]) -> None:
        """Load skill modules by short name (e.g. 'websearch' → 'agent.skills.websearch')."""
        for name in names:
            module_path = f"agent.skills.{name}"
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                logger.error("Failed to import skill module: %s", module_path, exc_info=True)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Skill)
                    and attr is not Skill
                ):
                    try:
                        skill = attr()
                        self.register(skill)
                    except Exception:
                        logger.error("Failed to instantiate skill %s.%s", module_path, attr_name, exc_info=True)

    def get_definitions(self) -> list[ToolDefinition]:
        """Return all tool definitions."""
        defs: list[ToolDefinition] = []
        for skill in self._skills.values():
            defs.extend(skill.tools)
        return defs

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name."""
        skill = self._tool_map.get(tool_name)
        if skill is None:
            return f"Error: unknown tool '{tool_name}'"
        return await skill.execute(tool_name, arguments)
