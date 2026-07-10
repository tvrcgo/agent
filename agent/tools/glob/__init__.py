from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

SENSITIVE_PREFIXES = [
    "/etc/",
    "/root/",
    "/boot/",
    "/sys/",
    "/proc/",
    "/dev/",
    "c:/windows/",
    "c:/windows/system32/",
    "/system/",
    "/library/",
]


def _resolve_work_dir(ctx: AgentContext, arguments: dict, config: dict) -> Path:
    work_dir = (
        arguments.get("work_dir")
        or ctx.data.get("work_dir")
        or config.get("work_dir")
        or "."
    )
    return Path(work_dir).resolve()


def _sanitize_path(file_path: str, work_dir: Path, force: bool = False) -> Path:
    raw_path = Path(file_path)
    raw_str = str(raw_path).replace("\\", "/")

    for prefix in SENSITIVE_PREFIXES:
        if raw_str.lower().startswith(prefix.lower()):
            raise PermissionError(f"Access denied: sensitive path '{raw_str}' is blocked")

    if not raw_path.is_absolute():
        path = (work_dir / raw_path).resolve()
    else:
        path = raw_path.resolve()

    path_str = str(path).replace("\\", "/")

    for prefix in SENSITIVE_PREFIXES:
        if path_str.lower().startswith(prefix.lower()):
            raise PermissionError(f"Access denied: sensitive path '{path_str}' is blocked")

    try:
        path.relative_to(work_dir)
    except ValueError:
        if not force:
            raise PermissionError(
                f"Access denied: '{path_str}' is outside work_dir '{work_dir}'. "
                f"Use request_confirmation first, then retry with force=true."
            )

    return path


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files matching a glob pattern. Returns matching file paths relative to work_dir."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match (e.g. '**/*.py', '*.md')",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (defaults to work_dir)",
            },
            "work_dir": {
                "type": "string",
                "description": "Override the working directory for this operation",
            },
            "force": {
                "type": "boolean",
                "description": "Bypass work_dir restriction (requires prior confirmation)",
                "default": False,
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        pattern = arguments.get("pattern", "")
        search_path = arguments.get("path", "")
        force = bool(arguments.get("force", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config)

        if search_path:
            base = _sanitize_path(search_path, work_dir, force=force)
        else:
            base = work_dir

        if not base.exists():
            return f"Error: path not found: {base}"

        matches = sorted(base.glob(pattern))

        if not matches:
            return f"No files matching '{pattern}' in {base}"

        try:
            relative_matches = [str(m.relative_to(work_dir)) for m in matches]
        except ValueError:
            relative_matches = [str(m) for m in matches]

        return "\n".join(relative_matches)
