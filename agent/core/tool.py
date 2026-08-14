from __future__ import annotations

import asyncio
import importlib
import importlib.metadata as _md
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.core.model import ToolCall, ToolResult

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


def _validate_type(name: str, value: Any, schema: dict[str, Any]) -> Any:
    # 按 JSON Schema 校验并强制转换单个值
    expected_type = schema.get("type")

    if expected_type == "string":
        if not isinstance(value, str):
            value = str(value)
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"Argument '{name}' too short (min {schema['minLength']} chars)")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"Argument '{name}' too long (max {schema['maxLength']} chars)")
        if "enum" in schema and value not in schema["enum"]:
            valid = ", ".join(repr(e) for e in schema["enum"])
            raise ValueError(f"Argument '{name}' must be one of: {valid}")

    elif expected_type == "integer":
        if isinstance(value, float):
            if value != int(value):
                raise ValueError(f"Argument '{name}' must be an integer, got float")
            value = int(value)
        elif isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                raise ValueError(f"Argument '{name}' must be an integer, got string")
        elif not isinstance(value, int):
            raise ValueError(f"Argument '{name}' must be an integer, got {type(value).__name__}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Argument '{name}' must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Argument '{name}' must be <= {schema['maximum']}")

    elif expected_type == "number":
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise ValueError(f"Argument '{name}' must be a number, got string")
        elif not isinstance(value, (int, float)):
            raise ValueError(f"Argument '{name}' must be a number, got {type(value).__name__}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Argument '{name}' must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Argument '{name}' must be <= {schema['maximum']}")

    elif expected_type == "boolean":
        if isinstance(value, str):
            value = value.lower() in ("true", "1", "yes")
        elif not isinstance(value, bool):
            raise ValueError(f"Argument '{name}' must be a boolean, got {type(value).__name__}")

    elif expected_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"Argument '{name}' must be an array, got {type(value).__name__}")
        if "items" in schema and "type" in schema["items"]:
            item_schema = schema["items"]
            for i, item in enumerate(value):
                value[i] = _validate_type(f"{name}[{i}]", item, item_schema)

    elif expected_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"Argument '{name}' must be an object, got {type(value).__name__}")

    return value


def validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    # 校验参数：检查必填、校验类型、应用默认值，失败抛 ValueError
    if not schema:
        return arguments

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field in required:
        if field not in arguments or arguments[field] is None:
            raise ValueError(f"Missing required argument: '{field}'")

    result: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        if field_name in arguments:
            value = arguments[field_name]
            validated = _validate_type(field_name, value, field_schema)
            result[field_name] = validated
        elif "default" in field_schema:
            result[field_name] = field_schema["default"]

    for k, v in arguments.items():
        if k not in result:
            result[k] = v

    return result


class ToolRegistry:

    class DependencyError(RuntimeError):
        pass

    def __init__(self, ctx: "AgentContext") -> None:
        self._ctx = ctx
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

    def register(self, tool: Tool | list[Tool]) -> None:
        tools = tool if isinstance(tool, list) else [tool]
        for t in tools:
            self._tools[t.name] = t
            logger.info("Tool registered: %s", t.name)
    def unregister(self, name: str | list[str]) -> None:
        # 按名移除工具
        names = [name] if isinstance(name, str) else name
        for n in names:
            if n in self._tools:
                del self._tools[n]
                logger.info("Tool unregistered: %s", n)


    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_defs(self) -> list[dict[str, Any]]:
        return [t.as_tool_def() for t in self._tools.values()]

    async def execute_batch(
        self,
        tool_calls: list[ToolCall],
        job: Job,
    ) -> None:
        # 纯执行机制：并行执行传入的调用（阻断剔除由 plugin 在 tools_start 完成）
        if not tool_calls:
            return
        logger.debug("Executing %d tool calls in parallel", len(tool_calls))
        await asyncio.gather(*[self._execute_one(tc, job) for tc in tool_calls])

    async def _execute_one(self, tool_call: ToolCall, job: Job) -> None:
        # 单工具执行：通知开始 → 校验 → 执行 → 追加结果 → emit tool_end
        ctx = self._ctx
        await ctx.emit("tool_start", job=job, tool_call=tool_call)

        result = ""
        error = ""

        try:
            tool = self.get(tool_call.name)
            if tool is None:
                result = f"Error: unknown tool '{tool_call.name}'"
                error = result
            else:
                try:
                    validated = validate_arguments(tool_call.arguments, tool.parameters)
                except ValueError as e:
                    result = f"Error: {e}"
                    error = result
                else:
                    result = await tool.execute(validated, ctx=ctx, job=job)
        except Exception as e:
            logger.exception("Tool execution error: %s", tool_call.name)
            result = str(e)
            error = result

        if job.loop is not None:
            job.loop.tool_results.append(ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=result,
                error=error,
            ))

        await ctx.emit("tool_end", job=job, tool_call=tool_call, result=result, error=error)

    async def fail_tool_call(self, tool_call: ToolCall, job: Job, reason: str) -> None:
        # 工具未执行（异常）：记录结果并 emit tool_error，不触发 tool_start/tool_end
        result = f"Error: {reason}"
        if job.loop is not None:
            job.loop.tool_results.append(ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=result,
                error=result,
            ))

        await self._ctx.emit("tool_error", job=job, tool_call=tool_call, error=reason)

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
