from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from agent.core.events import Event
from agent.core.plugin import Plugin

if TYPE_CHECKING:
    from agent.core.loop import AgentContext

logger = logging.getLogger(__name__)

DEFAULT_SKILL_DIRS = ["agent/skills", "skills"]


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


class SkillRegistry:

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_skills(self, *dirs: str | Path) -> None:
        for skills_dir in dirs:
            base = Path(skills_dir)
            if not base.is_dir():
                continue
            for entry in sorted(base.iterdir()):
                if not entry.is_dir():
                    continue
                md = entry / "SKILL.md"
                if not md.is_file():
                    continue
                try:
                    sk = Skill.from_skill_md(md)
                except Exception:
                    logger.error("Failed to parse SKILL.md in %s", entry, exc_info=True)
                    continue
                if sk is None:
                    continue
                self._skills[sk.name] = sk
                logger.info("Skill loaded: %s (%s)", sk.name, entry)

    def clear(self) -> None:
        self._skills.clear()

    def get_skills_prompt(self) -> str:
        if not self._skills:
            return ""
        return "\n\n---\n\n".join(sk.as_prompt() for sk in self._skills.values())


class SkillPlugin(Plugin):
    name = "skill"

    def __init__(self) -> None:
        self._registry = SkillRegistry()

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        dirs = config.get("dirs", DEFAULT_SKILL_DIRS)
        self._registry.load_skills(*dirs)
        ctx.on("turn_start", self._on_turn_start)
        logger.info("SkillPlugin loaded, dirs=%s", dirs)

    def unload(self) -> None:
        self._registry.clear()

    async def _on_turn_start(self, ctx: AgentContext, evt: Event) -> Any:
        prompt = self._registry.get_skills_prompt()
        if not prompt:
            return None
        evt.data.setdefault("prompts", []).append(prompt)
        return evt.data
