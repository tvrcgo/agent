from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

MAX_OUTPUT_SIZE = 100 * 1024
DEFAULT_TIMEOUT = 120

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


async def _request_confirm(ctx: AgentContext, job: Job, description: str) -> bool:
    evt = await ctx.emit("request_confirm", job=job, confirm_description=description)
    return evt.data.get("confirm_decision", "deny") == "approve"


class ShellTool(Tool):
    name = "shell"
    description = (
        "Execute a shell command. Commands run in the work_dir by default. "
        "Output is truncated if it exceeds 100KB. "
        "Dangerous: always request confirmation before running destructive commands."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "work_dir": {
                "type": "string",
                "description": "Override the working directory for this command",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {DEFAULT_TIMEOUT})",
                "default": DEFAULT_TIMEOUT,
            },
            "force": {
                "type": "boolean",
                "description": "Bypass work_dir restriction (requires prior confirmation)",
                "default": False,
            },
        },
        "required": ["command"],
    }

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        command = arguments.get("command", "")
        timeout = int(arguments.get("timeout", DEFAULT_TIMEOUT))
        force = bool(arguments.get("force", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config, job)

        if force:
            _sanitize_path(str(work_dir), work_dir, force=True)
        else:
            try:
                _sanitize_path(str(work_dir), work_dir)
            except PermissionError as e:
                if "retry with force=true" in str(e):
                    if await _request_confirm(
                        ctx, job, f"Execute command outside work_dir: {command[:200]}"
                    ):
                        _sanitize_path(str(work_dir), work_dir, force=True)
                    else:
                        return "Operation cancelled by user."
                else:
                    raise

        logger.info(f"Executing command in {work_dir}: {command[:200]}")

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
                env={**os.environ},
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return (
                    f"Error: command timed out after {timeout} seconds.\n"
                    f"Command: {command[:200]}"
                )

            output_parts = []

            if stdout:
                stdout_text = stdout.decode("utf-8", errors="replace")
                if len(stdout_text) > MAX_OUTPUT_SIZE:
                    stdout_text = stdout_text[:MAX_OUTPUT_SIZE] + (
                        f"\n... ({len(stdout_text) - MAX_OUTPUT_SIZE} more bytes truncated)"
                    )
                output_parts.append(f"[stdout]\n{stdout_text.rstrip()}")

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if len(stderr_text) > MAX_OUTPUT_SIZE // 2:
                    stderr_text = stderr_text[:MAX_OUTPUT_SIZE // 2] + (
                        f"\n... ({len(stderr_text) - MAX_OUTPUT_SIZE // 2} more bytes truncated)"
                    )
                output_parts.append(f"[stderr]\n{stderr_text.rstrip()}")

            if not output_parts:
                output_parts.append(f"[exit code: {process.returncode}]")

            return "\n".join(output_parts)

        except FileNotFoundError:
            return f"Error: command not found or shell unavailable: {command[:200]}"
        except Exception as e:
            return f"Error executing command: {e}"