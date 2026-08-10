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


def _check_write_size(content: str, max_size: int | None = None) -> int:
    limit = max_size if max_size is not None else MAX_WRITE_SIZE
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ValueError(f"Content too large ({size} bytes, max {limit} bytes)")
    return size


async def _request_confirm(ctx: AgentContext, job: Job, description: str) -> bool:
    evt = await ctx.emit("request_confirm", job=job, confirm_description=description)
    return evt.data.get("confirm_decision", "deny") == "approve"


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write content to a file. Creates parent directories automatically. "
        "Requires user confirmation before overwriting existing files."
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
                "description": "Allow overwriting an existing file (requires confirmation)",
                "default": False,
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
        "required": ["file_path", "content"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        file_path = arguments.get("file_path", "")
        content = arguments.get("content", "")
        overwrite = bool(arguments.get("overwrite", False))
        force = bool(arguments.get("force", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config, job)

        if force:
            path = _sanitize_path(file_path, work_dir, force=True)
        else:
            try:
                path = _sanitize_path(file_path, work_dir)
            except PermissionError as e:
                if "retry with force=true" in str(e):
                    if await _request_confirm(ctx, job, f"Write file outside work_dir: {file_path}"):
                        path = _sanitize_path(file_path, work_dir, force=True)
                    else:
                        return "Operation cancelled by user."
                else:
                    raise

        _check_write_size(content)

        if path.exists() and not overwrite:
            return (
                f"Error: file '{path}' already exists. "
                f"Retry with overwrite=true to overwrite after confirmation."
            )

        if path.exists() and overwrite:
            if not await _request_confirm(ctx, job, f"Overwrite existing file: {path}"):
                return "Operation cancelled by user."

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"
