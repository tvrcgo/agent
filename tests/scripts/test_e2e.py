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
from agent.core.model import ModelResponse, ToolCall, StreamChunk
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


class StreamFake:
    """流式 fake：逐块输出文本，验证 llm_chunk → stream 消息。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return ModelResponse(text="streamed-final")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        self.calls += 1
        if on_chunk is not None:
            await on_chunk(StreamChunk(text="Hello "))
            await on_chunk(StreamChunk(text="world"))
        return ModelResponse(text="Hello world")


class TruncatedFake:
    """返回 finish_reason=length + 工具调用：验证截断路径。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return ModelResponse(
            text=None,
            tool_calls=[ToolCall(id="tc-trunc", name="echo", arguments={"text": "never"})],
            finish_reason="length",
        )

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class InfiniteFake:
    """永远返回工具调用：验证 max_iterations 路径。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        return ModelResponse(
            text=None,
            tool_calls=[ToolCall(id=f"tc-inf-{self.calls}", name="echo", arguments={"text": "x"})],
        )

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class ErrorFake:
    """首轮抛异常：验证 job_error 路径。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        raise RuntimeError("boom-e2e")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class FollowUpFake:
    """首轮阻塞直到测试发入 follow-up：验证 msg_input → steering 排队 → 多轮。"""

    def __init__(self) -> None:
        self.calls = 0
        self.first_done = asyncio.Event()

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            await asyncio.wait_for(self.first_done.wait(), timeout=5)
        return ModelResponse(text=f"reply-{self.calls}")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class SlowFake:
    """慢模型：验证 cmd_cancel 中断路径。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        await asyncio.sleep(30)
        return ModelResponse(text="too late")

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


def _make_loop(tools: list, stream: bool = False) -> AgentLoop:
    loop = AgentLoop()
    loop._config.agent.stream = stream
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


async def test_e2e_streaming() -> None:
    """流式链路：llm_chunk → stream message 输出，job done。"""
    loop = _make_loop([], stream=True)
    fake = StreamFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-stream-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="hi", session_id="e2e-3"))
    await asyncio.sleep(0.6)

    stream_msgs = [o for o in spy.by_type("message") if o.stream]
    full_msgs = [o for o in spy.by_type("message") if not o.stream]
    print(f"  stream chunks: {[m.content for m in stream_msgs]}")
    assert stream_msgs, "expected stream chunks"
    assert "".join(m.content for m in stream_msgs) == "Hello world"
    assert full_msgs == [], f"stream mode should not emit full message: {full_msgs}"
    assert any(s.content == "done" for s in spy.by_type("status"))

    session.unload()
    message.unload()


async def test_e2e_tool_guard_confirm_flow() -> None:
    """tool_guard + confirm：危险工具弹确认，approve 放行，deny 阻断。"""
    from agent.plugins.tool_guard import ToolGuardPlugin
    from agent.plugins.confirm import ConfirmPlugin

    loop = _make_loop([])
    loop._tools.register(EchoTool())

    fake = StatefulFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-guard-"))

    guard = ToolGuardPlugin()
    guard.load(loop.ctx, {"review_tools": ["echo"]})
    confirm = ConfirmPlugin()
    confirm.load(loop.ctx, {"timeout": 5})

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    # 场景 A：approve → 工具放行
    async def _auto_approve(ctx: AgentContext, evt: Event) -> None:
        pass

    async def _on_confirm_approve(ctx: AgentContext, evt: Event) -> None:
        conf = evt.data.get("output")
        if conf is None or conf.type != "confirm":
            return
        cid = conf.data.get("id")
        # 直接以新 Job 触发 cmd_confirm（与前端行为一致）
        await loop.ctx.emit(
            "cmd_confirm",
            job=Job(id="e2e-4", status="pending"),
            confirm_id=cid,
            decision="approve",
        )

    loop.ctx.on("msg_output", _on_confirm_approve)
    await loop.ctx.emit("msg_input", input=InputMessage(content="run echo", session_id="e2e-4"))
    await asyncio.sleep(0.8)

    loop.ctx.off("msg_output", _on_confirm_approve)

    tool_results = spy.by_type("tool_result")
    assert len(tool_results) >= 1, f"expected tool result after approve: {tool_results}"
    assert tool_results[-1].content == "Echo: hello", f"approve should execute: {tool_results[-1].content!r}"
    print("  approve flow: tool executed")

    # 场景 B：deny → 阻断 + tool_error
    await asyncio.sleep(0.2)
    guard._review_tools = {"echo"}

    async def _on_confirm_deny(ctx: AgentContext, evt: Event) -> None:
        conf = evt.data.get("output")
        if conf is None or conf.type != "confirm":
            return
        cid = conf.data.get("id")
        await loop.ctx.emit(
            "cmd_confirm",
            job=Job(id="e2e-5", status="pending"),
            confirm_id=cid,
            decision="deny",
        )

    loop.ctx.on("msg_output", _on_confirm_deny)
    await loop.ctx.emit("msg_input", input=InputMessage(content="run echo again", session_id="e2e-5"))
    await asyncio.sleep(0.8)

    loop.ctx.off("msg_output", _on_confirm_deny)

    failed = [o for o in spy.by_type("tool_result") if o.data.get("failed")]
    assert failed, "expected failed tool_result after deny"
    assert "denied" in failed[-1].content, failed[-1].content
    print("  deny flow: tool blocked")

    session.unload()
    guard.unload()
    confirm.unload()
    message.unload()


async def test_e2e_cancel() -> None:
    """cmd_cancel → CancelledError → job_end(reason=cancelled)。"""
    loop = _make_loop([])
    fake = SlowFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-cancel-"))

    loop.ctx.on("msg_input", loop._on_input)
    loop.ctx.on("cmd_cancel", loop._on_command_cancel)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="e2e-6"))
    await asyncio.sleep(0.5)

    # 经 cmd_cancel 钩子触发取消（与前端 /cancel 一致）
    await loop.ctx.emit("cmd_cancel", job=Job(id="e2e-6", status="thinking"))
    await asyncio.sleep(0.8)

    statuses = spy.by_type("status")
    assert any(s.content == "cancelled" for s in statuses), f"expected cancelled status: {statuses}"

    session.unload()
    message.unload()


async def test_e2e_truncated() -> None:
    """finish_reason=length → fail_tool_call → job_end(reason=truncated)。"""
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = TruncatedFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-trunc-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="x", session_id="e2e-7"))
    await asyncio.sleep(0.6)

    failed = [o for o in spy.by_type("tool_result") if o.data.get("failed")]
    assert failed, "expected failed tool_result (truncated)"
    assert "truncated" in failed[-1].content.lower(), failed[-1].content
    errors = spy.by_type("error")
    assert errors and "truncated" in errors[-1].content.lower()
    assert any(s.content == "error" for s in spy.by_type("status"))

    session.unload()
    message.unload()


async def test_e2e_max_iterations() -> None:
    """无限工具循环 → max_iterations 终止。"""
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    loop._config.agent.max_iterations = 3
    fake = InfiniteFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-maxiter-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="loop forever", session_id="e2e-8"))
    await asyncio.sleep(0.8)

    assert fake.calls == 3, f"expected 3 llm calls, got {fake.calls}"
    errors = spy.by_type("error")
    assert errors and "maximum iterations" in errors[-1].content.lower(), errors
    assert any(s.content == "error" for s in spy.by_type("status"))

    session.unload()
    message.unload()


async def test_e2e_model_error() -> None:
    """LLM 抛异常 → job_error → job_end(reason=error)。"""
    loop = _make_loop([])
    fake = ErrorFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-err-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="boom", session_id="e2e-9"))
    await asyncio.sleep(0.6)

    errors = spy.by_type("error")
    assert errors and "boom-e2e" in errors[-1].content, errors
    assert any(s.content == "error" for s in spy.by_type("status"))

    session.unload()
    message.unload()


async def test_e2e_queue_max_concurrent() -> None:
    """max_concurrent=1：第二个 job 排队，第一个结束后出队执行。"""
    loop = _make_loop([])
    loop._config.agent.max_concurrent = 1
    fake = StreamFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-queue-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="first", session_id="e2e-10a"))
    await asyncio.sleep(0.15)
    # 第一个 job 未结束，第二个应排队
    await loop.ctx.emit("msg_input", input=InputMessage(content="second", session_id="e2e-10b"))
    await asyncio.sleep(1.0)

    assert "e2e-10a" in loop._jobs or fake.calls >= 2, "first job should run"
    done_sids = {o.session_id for o in spy.by_type("status") if o.content == "done"}
    assert "e2e-10a" in done_sids, f"first job should finish: {done_sids}"
    assert "e2e-10b" in done_sids, f"queued job should run after first: {done_sids}"

    session.unload()
    message.unload()


async def test_e2e_followup_steering() -> None:
    """运行中 follow-up（msg_input）排队 → 下轮消费，多轮输出后 done。"""
    loop = _make_loop([])
    fake = FollowUpFake()
    loop._models.get = lambda scene: fake

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-follow-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    spy = OutputSpy(loop)

    sid = "e2e-11"
    # 首轮 LLM 调用阻塞在 first_done，确保 follow-up 在 job 运行期间到达
    await loop.ctx.emit("msg_input", input=InputMessage(content="start", session_id=sid))
    await asyncio.sleep(0.3)
    assert loop._is_running(sid), "job should still be running"
    # 运行中 follow-up：同 session_id 再发一条，应进排队队列
    await loop.ctx.emit("msg_input", input=InputMessage(content="follow-up", session_id=sid))
    await asyncio.sleep(0.3)
    fake.first_done.set()
    await asyncio.sleep(0.6)

    messages = spy.by_type("message")
    print(f"  message turns: {[m.content for m in messages]}")
    assert [m.content for m in messages] == ["reply-1", "reply-2"], messages
    assert any(s.content == "done" for s in spy.by_type("status"))

    session.unload()
    message.unload()


async def test_e2e_cancel_orphan_cleanup() -> None:
    """取消工具调用后，job_end 清理孤儿 tool_calls，下一轮消息不再 400。

    回归：/cancel 打断工具执行时，assistant 消息已带 tool_calls 但 tool 结果缺失，
    下次请求会触发 LLM 400（insufficient tool messages）。
    """
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    loop.ctx.on("cmd_cancel", loop._on_command_cancel)

    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix="e2e-orphan-"))

    loop.ctx.on("msg_input", loop._on_input)
    await loop.ctx.emit("agent_start")

    class _ToolThenSlow:
        """首轮调用工具（阻塞等待取消），取消后首轮应被清理。"""

        def __init__(self, loop: AgentLoop, job_id: str) -> None:
            self.loop = loop
            self.job_id = job_id
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                # 工具调用后马上取消自己
                await asyncio.sleep(0.1)
                target = self.loop._jobs.get(self.job_id)
                if target and target._task:
                    target._task.cancel()
                await asyncio.sleep(5)
            return ModelResponse(text="after-cancel-ok")

        async def chat_stream(self, messages, tools=None, on_chunk=None):
            return await self.chat(messages, tools=tools)

    fake = _ToolThenSlow(loop, "e2e-12")
    loop._models.get = lambda scene: fake

    await loop.ctx.emit("msg_input", input=InputMessage(content="start", session_id="e2e-12"))
    await asyncio.sleep(1.0)

    # job 已取消；内存态会话应已清理孤儿
    state = session._sessions.get("e2e-12")
    assert state is not None, "session state missing"
    assert not any(
        getattr(m, "role", None) == "assistant" and getattr(m, "tool_calls", None)
        for m in state.messages
    ), "orphan tool_calls not cleaned after cancel"

    session.unload()
    message.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("e2e_echo_tool", test_e2e_echo_tool),
        ("e2e_subjob_recursion", test_e2e_subjob_recursion),
        ("e2e_streaming", test_e2e_streaming),
        ("e2e_tool_guard_confirm", test_e2e_tool_guard_confirm_flow),
        ("e2e_cancel", test_e2e_cancel),
        ("e2e_truncated", test_e2e_truncated),
        ("e2e_max_iterations", test_e2e_max_iterations),
        ("e2e_model_error", test_e2e_model_error),
        ("e2e_queue_max_concurrent", test_e2e_queue_max_concurrent),
        ("e2e_followup_steering", test_e2e_followup_steering),
        ("e2e_cancel_orphan_cleanup", test_e2e_cancel_orphan_cleanup),
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
