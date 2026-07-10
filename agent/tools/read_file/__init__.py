from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool
from agent.core.loop import MessageEvent

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

MAX_READ_SIZE = 50 * 1024 * 1024

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


def _check_read_size(path: Path, max_size: int | None = None) -> None:
    limit = max_size if max_size is not None else MAX_READ_SIZE
    try:
        size = path.stat().st_size
        if size > limit:
            raise ValueError(
                f"File too large ({size} bytes, max {limit} bytes). "
                f"Use offset/limit to read in chunks."
            )
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {path}")


async def _request_confirm(ctx: AgentContext, job: Job, description: str) -> bool:
    confirm_id = str(uuid.uuid4())[:8]
    ctx.data["confirm_id"] = confirm_id
    ctx.data["confirm_description"] = description

    if job.output is not None:
        job.output.events.append(
            MessageEvent(
                type="confirm_request",
                data={"id": confirm_id, "description": description},
            )
        )
        await ctx.emit("on_output", job)

    await ctx.emit("request_confirm", job)

    ctx.data.pop("confirm_id", None)
    ctx.data.pop("confirm_description", None)
    decision = ctx.data.pop("confirm_decision", "deny")
    return decision == "approve"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the local filesystem. Returns content with line numbers. "
        "Use offset/limit for large files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to read (absolute or relative to work_dir)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, default 1)",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (default 2000)",
                "default": 2000,
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
        "required": ["file_path"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        file_path = arguments.get("file_path", "")
        offset = max(1, int(arguments.get("offset", 1)))
        limit = int(arguments.get("limit", 2000))
        force = bool(arguments.get("force", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config)

        if force:
            path = _sanitize_path(file_path, work_dir, force=True)
        else:
            try:
                path = _sanitize_path(file_path, work_dir)
            except PermissionError as e:
                if "retry with force=true" in str(e):
                    if await _request_confirm(ctx, job, f"Read file outside work_dir: {file_path}"):
                        path = _sanitize_path(file_path, work_dir, force=True)
                    else:
                        return "Operation cancelled by user."
                else:
                    raise
        _check_read_size(path)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except UnicodeDecodeError:
            return f"Error: '{file_path}' is not a text file or uses an unsupported encoding"

        lines = content.splitlines()
        total_lines = len(lines)

        if offset > total_lines:
            return f"Error: offset {offset} exceeds total lines {total_lines}"

        end = min(offset + limit - 1, total_lines)
        selected = lines[offset - 1 : end]

        output = [f"{offset + i}: {line}" for i, line in enumerate(selected)]

        if end < total_lines:
            output.append(f"... (truncated, {total_lines} lines total, use offset/limit to read more)")

        return "\n".join(output)
