from __future__ import annotations

import importlib
import importlib.metadata as _md
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)


class Tool(ABC):

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def as_tool_def(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        ...


class ToolRegistry:

    class DependencyError(RuntimeError):
        pass

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def load_modules(self, tools: list[str | dict[str, Any]]) -> None:
        tools_dir = Path(__file__).parent.parent / "tools"

        for item in tools:
            if isinstance(item, str):
                name, config = item, {}
            else:
                name = next(iter(item))
                config = item[name] or {}

            self._check_deps(name, tools_dir)

            module_path = f"agent.tools.{name}"
            try:
                module = importlib.import_module(module_path)
            except ImportError:
                logger.error("Failed to import tool: %s", module_path, exc_info=True)
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    try:
                        tool = attr(config=config)
                        self._tools[tool.name] = tool
                        logger.info("Tool registered: %s", tool.name)
                    except Exception:
                        logger.error("Failed to instantiate tool %s.%s", module_path, attr_name, exc_info=True)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_defs(self) -> list[dict[str, Any]]:
        return [t.as_tool_def() for t in self._tools.values()]

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
            except _md.PackageNotFoundException:
                raise ToolRegistry.DependencyError(
                    f"Tool '{name}' requires '{line}' but it is not installed. "
                    f"Run 'pip install -r agent/tools/{name}/requirements.txt' or rebuild the image."
                )
