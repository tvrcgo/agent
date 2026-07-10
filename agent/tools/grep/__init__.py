from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)

MAX_GREP_RESULTS = 500

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


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents using regular expressions. "
        "Returns matching lines in format: file_path:line_number: content"
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for in file contents",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in (defaults to work_dir)",
            },
            "include": {
                "type": "string",
                "description": "File pattern filter (e.g. '*.py', '*.{ts,tsx}')",
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
        include = arguments.get("include", "")
        force = bool(arguments.get("force", False))

        work_dir = _resolve_work_dir(ctx, arguments, self.config)

        if search_path:
            base = _sanitize_path(search_path, work_dir, force=force)
        else:
            base = work_dir

        if not base.exists():
            return f"Error: path not found: {base}"

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex pattern: {e}"

        results: list[str] = []
        files = self._collect_files(base, include) if include else base.rglob("*")

        for file_path in files:
            if not file_path.is_file():
                continue
            if file_path.name.startswith(".") and not include:
                continue
            if file_path.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".zip", ".tar", ".gz"):
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    try:
                        rel_path = str(file_path.relative_to(work_dir))
                    except ValueError:
                        rel_path = str(file_path)
                    results.append(f"{rel_path}:{i}: {line}")
                    if len(results) >= MAX_GREP_RESULTS:
                        return "\n".join(results) + f"\n... (truncated at {MAX_GREP_RESULTS} results)"

        if not results:
            return f"No matches for '{pattern}' in {base}"

        return "\n".join(results)

    def _collect_files(self, base: Path, include: str) -> list[Path]:
        if "{" in include:
            prefix, _, suffixes = include.partition("{")
            suffixes = suffixes.rstrip("}")
            ext_list = suffixes.replace("{", "").split(",")
            patterns = [f"**/{prefix}{ext.strip()}" for ext in ext_list]
        else:
            patterns = [f"**/{include}"]

        files: list[Path] = []
        for p in patterns:
            files.extend(base.glob(p))
        return sorted(set(files))
