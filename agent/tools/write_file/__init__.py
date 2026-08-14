from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

MAX_WRITE_SIZE = 10 * 1024 * 1024

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


def _resolve_work_dir(ctx: AgentContext, arguments: dict, config: dict, job: Job) -> Path:
    work_dir = (
        arguments.get("work_dir")
        or job.data.get("work_dir")
        or config.get("work_dir")
        or "."
    )
    return Path(work_dir).resolve()


def _sanitize_path(file_path: str, work_dir: Path) -> Path:
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
        raise PermissionError(
            f"Access denied: '{path_str}' is outside work_dir '{work_dir}'."
        )

    return path


def _check_write_size(content: str, max_size: int | None = None) -> int:
    limit = max_size if max_size is not None else MAX_WRITE_SIZE
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ValueError(f"Content too large ({size} bytes, max {limit} bytes)")
    return size


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write content to a file. Creates parent directories automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to write to (absolute or relative to work_dir)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Allow overwriting an existing file",
                "default": False,
            },
            "work_dir": {
                "type": "string",
                "description": "Override the working directory for this operation",
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")
        overwrite = bool(arguments.get("overwrite", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config, job)
        path = _sanitize_path(file_path, work_dir)

        _check_write_size(content)

        if path.exists() and not overwrite:
            return (
                f"Error: file '{path}' already exists. "
                f"Retry with overwrite=true to overwrite."
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"
