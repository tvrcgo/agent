from __future__ import annotations

import importlib
import importlib.metadata as _md
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    body: str

    @classmethod
    def from_skill_md(cls, path: Path) -> Skill | None:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        fm = yaml.safe_load(parts[1])
        if not isinstance(fm, dict):
            return None
        return cls(
            name=fm.get("name", ""),
            description=fm.get("description", ""),
            body=parts[2].strip(),
        )

    def as_prompt(self) -> str:
        return f"## {self.name}\n{self.description}\n\n{self.body}"


class Tool(ABC):

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def as_tool_def(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> str:
        ...


class SkillRegistry:

    class DependencyError(RuntimeError):
        pass

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._skills: dict[str, Skill] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        logger.info("Tool registered: %s", tool.name)

    def load_modules(self, names: list[str]) -> None:
        tools_dir = Path(__file__).parent.parent / "skills"

        for name in names:
            self._check_deps(name, tools_dir)

            module_path = f"agent.skills.{name}"
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                logger.error("Failed to import tool: %s", module_path, exc_info=True)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    try:
                        self.register(attr())
                    except Exception:
                        logger.error("Failed to instantiate tool %s.%s", module_path, attr_name, exc_info=True)

    def load_skills(self, skills_dir: str | Path = "./skills") -> None:
        base = Path(skills_dir)
        if not base.is_dir():
            return

        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            md = entry / "SKILL.md"
            try:
                sk = Skill.from_skill_md(md)
            except Exception:
                logger.error("Failed to parse SKILL.md in %s", entry, exc_info=True)
                continue
            if sk is None:
                continue
            self._skills[sk.name] = sk
            logger.info("Skill loaded: %s (%s)", sk.name, entry)

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_tools_def(self) -> list[dict[str, Any]]:
        return [t.as_tool_def() for t in self._tools.values()]

    def get_skills_prompt(self) -> str:
        if not self._skills:
            return ""
        return "\n\n---\n\n".join(sk.as_prompt() for sk in self._skills.values())

    @staticmethod
    def _check_deps(name: str, tools_dir: Path) -> None:
        req_file = tools_dir / name / "requirements.txt"
        try:
            lines = req_file.read_text().splitlines()
        except FileNotFoundError:
            return

        for line in lines:
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
                    f"Tool '{name}' requires '{line}' but it is not installed. "
                    f"Run 'pip install -r agent/skills/{name}/requirements.txt' or rebuild the image."
                )
