"""End-to-end integration test.

模拟完整 agent 链路：msg_input → loop 建 job → LLM 调用（fake，先工具后文本）
→ 工具执行（echo / subjob 递归子任务）→ 结果回填 → msg_output 增量外发。

不依赖真实 LLM / 网络，通过 fake model + 动态注册工具驱动。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from agent.core.io import InputMessage, OutputMessage
from agent.core.loop import AgentContext, AgentLoop, Job
from agent.core.events import Event
from agent.core.model import ModelResponse, ToolCall
from agent.core.tool import Tool
from agent.plugins.session import SessionPlugin


class EchoTool(Tool):
    """测试用工具：回显输入。"""

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
    """监听 msg_output，按增量外发语义收集 evt.data['output']。"""

    def __init__(self, loop: AgentLoop) -> None:
        self.outputs: list[OutputMessage] = []
        loop.ctx.on("msg_output", self._on_output)

    async def _on_output(self, ctx: AgentContext, evt: Event) -> None:
        out = evt.data.get("output")
        if out is not None:
            self.outputs.append(out)

    def by_type(self, t: str) -> list[OutputMessage]:
        return [o for o in self.outputs if o.type == t]


class StatefulFake:
    """有状态 fake LLM：按消息阶段区分。

    首轮（无 tool result）→ 请求 echo 工具；之后（有 tool result）→ 输出最终文本。
    """

    def __init__(self) -> None:
        self.main_calls = 0

    async def chat(self, messages, tools=None):
        self.main_calls += 1
        tool_results = [m for m in messages if hasattr(m, "role") and getattr(m, "role") == "tool"]
        if tool_results:
            return ModelResponse(text="parent-final")
        return ModelResponse(
            text=None,
            tool_calls=[ToolCall(id="tc-e2e", name="echo", arguments={"text": "hello"})],
        )

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class SubJobFake:
    """父任务调用 subjob 工具、子任务独立运行的 fake。

    以会话输入是否含 'child' 区分父/子：
    - 子会话 → 直接返回子任务结果
    - 父会话首轮 → 请求 subjob 工具；之后 → 聚合子任务结果输出最终文本
    """

    def __init__(self) -> None:
        self.main_calls = 0
        self.sub_calls = 0

    async def chat(self, messages, tools=None):
        user_contents = [
            m.content for m in messages
            if hasattr(m, "role") and getattr(m, "role") == "user" and m.content
        ]
        joined = " | ".join(str(c) for c in user_contents)

        if "child" in joined:
            self.sub_calls += 1
            return ModelResponse(text="child-task-done")

        tool_results = [m for m in messages if hasattr(m, "role") and getattr(m, "role") == "tool"]
        if tool_results:
            self.main_calls += 1
            agg = tool_results[-1].content
            return ModelResponse(text=f"parent-done [{agg[:60]}]")

        self.main_calls += 1
        return ModelResponse(
            text=None,
            tool_calls=[ToolCall(
                id="tc-sub",
                name="subjob",
                arguments={"jobs": [{"content": "child task"}]},
            )],
        )

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


def _make_loop(tools: list) -> AgentLoop:
    loop = AgentLoop()
    loop._config.agent.stream = False
    loop._config.tools = tools
    loop._config.plugins = []
    loop._config.agent.max_iterations = 20
    return loop


async def test_e2e_echo_tool() -> None:
    """msg_input → LLM 工具调用 → echo 工具 → 文本 → msg_output 完整事件流。"""
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = StatefulFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-session-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="hello", session_id="e2e-1"))
    await asyncio.sleep(0.6)

    assert fake.main_calls >= 2, f"expected >=2 llm calls, got {fake.main_calls}"

    types = [o.type for o in spy.outputs]
    print("  output types:", types)
    tool_calls = spy.by_type("tool_call")
    tool_results = spy.by_type("tool_result")
    print(f"  tool_calls: {len(tool_calls)}, tool_results: {len(tool_results)}")
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    assert tool_calls[0].data["tool"] == "echo"
    assert tool_results[0].content == "Echo: hello", f"echo result wrong: {tool_results[0].content!r}"

    msgs = spy.by_type("message")
    statuses = spy.by_type("status")
    assert msgs and msgs[-1].content == "parent-final"
    assert any(s.content == "done" for s in statuses)
    assert all(o.session_id == "e2e-1" for o in spy.outputs)

    session.unload()
    message.unload()


async def test_e2e_subjob_recursion() -> None:
    """父 job 触发 subjob 工具 → 子任务独立 session 运行 → 结果回填父工具。"""
    loop = _make_loop(["subjob"])
    fake = SubJobFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-session-"))

    loop._tools.load_modules(loop._config.tools)
    from agent.plugins.subjob import SubJobPlugin
    subjob_plugin = SubJobPlugin()
    subjob_plugin.load(loop.ctx, {"max_depth": 2})

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="run subjob", session_id="e2e-2"))
    await asyncio.sleep(1.5)

    assert fake.sub_calls >= 1, f"subjob llm not called: {fake.sub_calls}"
    assert fake.main_calls >= 2, f"parent llm calls: {fake.main_calls}"

    tool_results = spy.by_type("tool_result")
    print("  subjob tool_results:", [(d.data.get("tool"), d.content[:80]) for d in tool_results])
    assert any(
        d.data.get("tool") == "subjob" and "child-task-done" in d.content
        for d in tool_results
    ), "child result not aggregated into parent tool result"

    msgs = spy.by_type("message")
    assert msgs and "parent-done" in msgs[-1].content, f"final message: {msgs[-1].content if msgs else None}"

    session.unload()
    subjob_plugin.unload()
    message.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("e2e_echo_tool", test_e2e_echo_tool),
        ("e2e_subjob_recursion", test_e2e_subjob_recursion),
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
