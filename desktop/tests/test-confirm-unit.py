# 单元验证：desktop 场景下的 confirm 链路（不依赖真实 LLM 决策）
# 用 fake 模型返回 dangerous 工具调用，强制触发 tool_guard → confirm_request → 决策回填。
# 验证：confirm 事件发出、approve 放行（工具执行）、deny 阻断（tool_error）。
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core.io import InputMessage, OutputMessage
from agent.core.loop import AgentContext, AgentLoop, Job
from agent.core.events import Event
from agent.core.model import ModelResponse, ToolCall
from agent.core.tool import Tool
from agent.plugins.message import MessagePlugin
from agent.plugins.confirm import ConfirmPlugin
from agent.plugins.tool_guard import ToolGuardPlugin


class WriteLikeTool(Tool):
    name = "write_file"
    description = "Write content to a file"
    parameters = {
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        return f"wrote {arguments.get('file_path')}"


class DangerousFake:
    """主模型：第一个调用返回工具调用，第二个返回最终文本。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                text=None,
                tool_calls=[ToolCall(id="tc-1", name="write_file", arguments={"file_path": "x.txt", "content": "x"})],
            )
        return ModelResponse(text="final-answer")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class FlashSafeFake(DangerousFake):
    async def chat(self, messages, tools=None):
        return ModelResponse(text="safe")


class FlashDangerousFake(DangerousFake):
    async def chat(self, messages, tools=None):
        return ModelResponse(text="dangerous")


class OutputSpy:
    def __init__(self, loop: AgentLoop) -> None:
        self.outputs: list[OutputMessage] = []
        loop.ctx.on("msg_output", self._on_output)

    async def _on_output(self, ctx: AgentContext, evt: Event) -> None:
        out = evt.data.get("output")
        if out is not None:
            self.outputs.append(out)

    def by_type(self, t: str) -> list[OutputMessage]:
        return [o for o in self.outputs if o.type == t]


async def run_case(loop: AgentLoop, session_id: str, decision: str | None) -> None:
    """并发监听 confirm 输出，按 decision 回填决策。"""
    if decision is None:
        return

    async def responder():
        while True:
            confirms = [o for o in spy.outputs if o.type == "confirm" and not o.data.get("_answered")]
            for c in confirms:
                c.data["_answered"] = True
                await loop.ctx.emit(
                    "cmd_confirm",
                    job=Job(id=session_id, status="pending"),
                    confirm_id=c.data.get("id"),
                    decision=decision,
                )
            await asyncio.sleep(0.02)

    spy = OutputSpy(loop)
    responder_task = asyncio.create_task(responder())
    try:
        await loop.ctx.emit(
            "msg_input",
            input=InputMessage(content="写一个文件", type="chat", session_id=session_id),
        )
        for _ in range(200):
            if not any(j.id == session_id for j in loop._jobs.values()):
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)
    finally:
        responder_task.cancel()
    return spy


async def wait_done(spy: OutputSpy, session_id: str, timeout: float = 10.0) -> None:
    """等待 job 出现终态（done/error/cancelled status）或超时。"""
    start = asyncio.get_event_loop().time()
    while True:
        statuses = [o.content for o in spy.outputs if o.type == "status"]
        if any(s in ("done", "error", "cancelled") for s in statuses):
            return
        if asyncio.get_event_loop().time() - start > timeout:
            return
        await asyncio.sleep(0.05)


async def main() -> None:
    results = []

    # case 1: flash 判 safe → 不触发 confirm，工具执行，done
    loop1 = AgentLoop()
    loop1._config.agent.stream = False
    loop1._config.agent.max_iterations = 10
    loop1._config.tools = []
    loop1._config.plugins = []
    loop1.ctx.on("msg_input", loop1._on_input)
    loop1._tools.register(WriteLikeTool())
    main1 = DangerousFake()
    flash1 = FlashSafeFake()
    loop1._models.get = lambda name: main1 if name == "main" else flash1
    MessagePlugin().load(loop1.ctx, {})
    ConfirmPlugin().load(loop1.ctx, {"timeout": 5})
    ToolGuardPlugin().load(loop1.ctx, {"review_tools": ["write_file"]})
    spy1 = OutputSpy(loop1)
    await loop1.ctx.emit("msg_input", input=InputMessage(content="写文件", type="chat", session_id="case-safe"))
    await wait_done(spy1, "case-safe")
    types1 = [o.type for o in spy1.outputs]
    status1 = [o.content for o in spy1.outputs if o.type == "status"]
    ok1 = len(spy1.by_type("confirm")) == 0 and any(o.type == "tool_result" and not (o.data or {}).get("failed") for o in spy1.outputs) and "done" in status1
    results.append(("safe 放行不弹确认", ok1, f"confirm={len(spy1.by_type('confirm'))}, tool 执行, done={'done' in status1}"))

    # case 2: flash 判 dangerous + approve → confirm 触发，工具执行，done
    loop2 = AgentLoop()
    loop2._config.agent.stream = False
    loop2._config.agent.max_iterations = 10
    loop2._config.tools = []
    loop2._config.plugins = []
    loop2.ctx.on("msg_input", loop2._on_input)
    loop2._tools.register(WriteLikeTool())
    main2 = DangerousFake()
    loop2._models.get = lambda name: main2 if name == "main" else FlashDangerousFake()
    MessagePlugin().load(loop2.ctx, {})
    ConfirmPlugin().load(loop2.ctx, {"timeout": 5})
    ToolGuardPlugin().load(loop2.ctx, {"review_tools": ["write_file"]})
    spy2 = OutputSpy(loop2)

    async def responder2():
        while True:
            for c in spy2.by_type("confirm"):
                if not c.data.get("_answered"):
                    c.data["_answered"] = True
                    await loop2.ctx.emit("cmd_confirm", job=Job(id="case-approve", status="pending"),
                                         confirm_id=c.data.get("id"), decision="approve")
            await asyncio.sleep(0.02)

    task2 = asyncio.create_task(responder2())
    await loop2.ctx.emit("msg_input", input=InputMessage(content="写文件", type="chat", session_id="case-approve"))
    await wait_done(spy2, "case-approve")
    task2.cancel()
    types2 = [o.type for o in spy2.outputs]
    status2 = [o.content for o in spy2.outputs if o.type == "status"]
    ok2 = len(spy2.by_type("confirm")) >= 1 and any(o.type == "tool_result" and not (o.data or {}).get("failed") for o in spy2.outputs) and "done" in status2
    results.append(("dangerous+批准→执行", ok2, f"confirm={len(spy2.by_type('confirm'))}, tool 执行, done={'done' in status2}"))

    # case 3: flash 判 dangerous + deny → confirm 触发，工具阻断 tool_error
    loop3 = AgentLoop()
    loop3._config.agent.stream = False
    loop3._config.agent.max_iterations = 10
    loop3._config.tools = []
    loop3._config.plugins = []
    loop3.ctx.on("msg_input", loop3._on_input)
    loop3._tools.register(WriteLikeTool())
    main3 = DangerousFake()
    loop3._models.get = lambda name: main3 if name == "main" else FlashDangerousFake()
    MessagePlugin().load(loop3.ctx, {})
    ConfirmPlugin().load(loop3.ctx, {"timeout": 5})
    ToolGuardPlugin().load(loop3.ctx, {"review_tools": ["write_file"]})
    spy3 = OutputSpy(loop3)

    async def responder3():
        while True:
            for c in spy3.by_type("confirm"):
                if not c.data.get("_answered"):
                    c.data["_answered"] = True
                    await loop3.ctx.emit("cmd_confirm", job=Job(id="case-deny", status="pending"),
                                         confirm_id=c.data.get("id"), decision="deny")
            await asyncio.sleep(0.02)

    task3 = asyncio.create_task(responder3())
    await loop3.ctx.emit("msg_input", input=InputMessage(content="写文件", type="chat", session_id="case-deny"))
    await wait_done(spy3, "case-deny")
    task3.cancel()
    types3 = [o.type for o in spy3.outputs]
    failed3 = [o for o in spy3.outputs if o.type == "tool_result" and (o.data or {}).get("failed")]
    ok3 = len(spy3.by_type("confirm")) >= 1 and len(failed3) >= 1
    results.append(("dangerous+拒绝→阻断", ok3, f"confirm={len(spy3.by_type('confirm'))}, tool_error={len(failed3)}"))

    all_pass = True
    for desc, ok, detail in results:
        all_pass = all_pass and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}: {detail}")
    print("=== CONFIRM UNIT TEST PASSED ===" if all_pass else "=== CONFIRM UNIT TEST FAILED ===")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    asyncio.run(main())
