from __future__ import annotations

import importlib
import importlib.metadata as _md
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
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
        skills_dir = Path(__file__).parent.parent / "skills"

        for name in names:
            self._check_deps(name, skills_dir)

            module_path = f"agent.skills.{name}"
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                logger.error("Failed to import skill: %s", module_path, exc_info=True)
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

    class DependencyError(RuntimeError):
        pass

    @staticmethod
    def _check_deps(name: str, skills_dir: Path) -> None:
        req_file = skills_dir / name / "requirements.txt"
        if not req_file.exists():
            return

        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([a-zA-Z0-9_.-]+)", line)
            if not m:
                continue
            pkg = m.group(1)
            try:
                _md.version(pkg)
            except _md.PackageNotFoundError:
                raise SkillRegistry.DependencyError(
                    f"Skill '{name}' requires '{line}' but it is not installed. "
                    f"Run 'pip install -r agent/skills/{name}/requirements.txt' or rebuild the image."
                )

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
