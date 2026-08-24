"""Cancel plugin unit tests.

验证下沉后的 CancelPlugin：
- cmd_cancel → 找到目标 task → Task.cancel() → job_end(reason=cancelled)
- session_id 路由（可取消子 job，命令 job.id 可不同于目标）
- 对未运行/不存在的 job：no-op（不崩溃）
- 暂停中取消（与 PausePlugin 协同）已在 test_pause.py 覆盖

不依赖真实 LLM / 网络，通过 fake model 驱动。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from agent.core.io import InputMessage
from agent.core.loop import AgentContext, AgentLoop, Job
from agent.core.events import Event
from agent.core.model import ModelResponse, ToolCall
from agent.core.tool import Tool
from agent.plugins.cancel import CancelPlugin
from agent.plugins.session import SessionPlugin


class EchoTool(Tool):
    name = "echo"
    description = "Echo the input text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo"}},
        "required": ["text"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        return f"Echo: {arguments.get('text', '')}"


class OutputSpy:
    def __init__(self, loop: AgentLoop) -> None:
        self.outputs = []
        loop.ctx.on("msg_output", self._on_output)

    async def _on_output(self, ctx: AgentContext, evt: Event) -> None:
        out = evt.data.get("output")
        if out is not None:
            self.outputs.append(out)

    def statuses(self) -> list[str]:
        return [o.content for o in self.outputs if o.type == "status"]


class SlowFake:
    """慢模型：首轮阻塞，留出取消窗口。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        await asyncio.sleep(30)
        return ModelResponse(text="too late")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


def _make_loop(tools: list, stream: bool = False) -> AgentLoop:
    loop = AgentLoop()
    loop._config.agent.stream = stream
    loop._config.tools = tools
    loop._config.plugins = []
    loop._config.agent.max_iterations = 20
    return loop


async def test_cancel_running_job() -> None:
    """cmd_cancel → Task.cancel → job_end(reason=cancelled)。"""
    loop = _make_loop([])
    fake = SlowFake()
    loop._models.get = lambda scene: fake

    loop.ctx.on("msg_input", loop._on_input)
    cancel = CancelPlugin()
    cancel.load(loop.ctx, {})

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="cancel-e2e-"))

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="c-1"))
    await asyncio.sleep(0.4)
    assert loop._is_running("c-1"), "job should be running"

    await loop.ctx.emit("cmd_cancel", job=Job(id="c-1", status="thinking"))
    await asyncio.sleep(0.8)

    assert "cancelled" in spy.statuses(), f"expected cancelled, got {spy.statuses()}"
    assert not loop._is_running("c-1"), "job should have stopped"

    session.unload()
    message.unload()
    cancel.unload()


async def test_cancel_session_id_routing() -> None:
    """cmd_cancel 带 session_id 可取消指定 job（命令 job.id 不同也能命中）。"""
    loop = _make_loop([])
    fake = SlowFake()
    loop._models.get = lambda scene: fake

    loop.ctx.on("msg_input", loop._on_input)
    cancel = CancelPlugin()
    cancel.load(loop.ctx, {})

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="cancel-route-"))

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="c-2"))
    await asyncio.sleep(0.4)
    assert loop._is_running("c-2"), "job should be running"

    # 命令连接 job.id = other，但 session_id 指定 c-2
    await loop.ctx.emit("cmd_cancel", job=Job(id="other", status="idle"), session_id="c-2")
    await asyncio.sleep(0.8)

    assert "cancelled" in spy.statuses(), f"expected cancelled, got {spy.statuses()}"
    assert not loop._is_running("c-2"), "target job should have stopped"

    session.unload()
    message.unload()
    cancel.unload()


async def test_cancel_noop_for_unknown() -> None:
    """对未运行/不存在的 job 发 cancel：no-op，不崩溃。"""
    loop = _make_loop([])
    fake = SlowFake()
    loop._models.get = lambda scene: fake

    loop.ctx.on("msg_input", loop._on_input)
    cancel = CancelPlugin()
    cancel.load(loop.ctx, {})

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="cancel-noop-"))

    await loop.ctx.emit("agent_start")

    # 从未启动的 session
    await loop.ctx.emit("cmd_cancel", job=Job(id="never-started", status="idle"))
    await loop.ctx.emit("cmd_cancel", job=Job(id="never-started", status="idle"), session_id="ghost")
    await asyncio.sleep(0.2)
    # 不崩溃即可；确认没有错误输出
    assert not loop._is_running("never-started")

    session.unload()
    message.unload()
    cancel.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("cancel_running_job", test_cancel_running_job),
        ("cancel_session_id_routing", test_cancel_session_id_routing),
        ("cancel_noop_for_unknown", test_cancel_noop_for_unknown),
    ]
    for name, fn in scenarios:
        try:
            await fn()
            results[name] = True
            print(f"  {name}: PASS")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    passed = sum(1 for v in results.values() if v)
    print(f"\nPassed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    asyncio.run(main())
