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


def _check_write_size(content: str, max_size: int | None = None) -> int:
    limit = max_size if max_size is not None else MAX_WRITE_SIZE
    size = len(content.encode("utf-8"))
    if size > limit:
        raise ValueError(f"Content too large ({size} bytes, max {limit} bytes)")
    return size


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


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing exact text. "
        "Finds old_string in the file and replaces it with new_string. "
        "If multiple matches found with replace_all=false, returns an error with match locations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to edit (absolute or relative to work_dir)",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to find and replace",
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace it with (must differ from old_string)",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences of old_string (default false)",
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
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        file_path = arguments.get("file_path", "")
        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        replace_all = bool(arguments.get("replace_all", False))
        force = bool(arguments.get("force", False))

        if old_string == new_string:
            return "Error: old_string and new_string must differ"

        if not old_string:
            return "Error: old_string must not be empty"

        work_dir = _resolve_work_dir(ctx, arguments, self.config)

        if force:
            path = _sanitize_path(file_path, work_dir, force=True)
        else:
            try:
                path = _sanitize_path(file_path, work_dir)
            except PermissionError as e:
                if "retry with force=true" in str(e):
                    if await _request_confirm(ctx, job, f"Edit file outside work_dir: {file_path}"):
                        path = _sanitize_path(file_path, work_dir, force=True)
                    else:
                        return "Operation cancelled by user."
                else:
                    raise

        _check_read_size(path)

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return f"Error: file not found: {path}"

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"

        if count > 1 and not replace_all:
            positions = []
            idx = 0
            for i in range(count):
                found = content.index(old_string, idx)
                line_num = content[:found].count("\n") + 1
                positions.append(f"line {line_num}")
                idx = found + len(old_string)
            return (
                f"Error: old_string found {count} times in {path}. "
                f"Matches at: {', '.join(positions)}. "
                f"Use replace_all=true or provide more context to make old_string unique."
            )

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        _check_write_size(new_content)

        path.write_text(new_content, encoding="utf-8")
        return f"Successfully edited {path}: {count} replacement(s) made"
