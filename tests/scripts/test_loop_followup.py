"""Unit tests for AgentLoop + SessionPlugin follow-up output.

回归验证：多轮文本轮（用户 follow-up/纠正确认场景）时，
- job.turn.content 保持"最后一个 turn 输出"语义（不拼接、不覆盖历史）
- 非流式下每轮文本作为独立 message 事件推送（体现先后关系）
- job 状态语义不变
"""
from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent.core.io import InputMessage, OutputMessage
from agent.core.loop import AgentContext, AgentLoop, Job
from agent.core.events import Event
from agent.core.model import ModelResponse, ToolCall
from agent.plugins.session import SessionPlugin
from agent.plugins.subjob import SubJobPlugin


@dataclass
class _FakeMain:
    """Simulate main model. On first call, queue a follow-up message."""
    loop: AgentLoop
    job: Job
    call: int = 0

    async def chat(self, messages, tools=None):
        self.call += 1
        if self.call == 1:
            # 模拟第一轮模型调用期间用户 follow-up 消息排队
            self.loop._message_queues[self.job.id].push("follow-up")
        return ModelResponse(text=f"reply {self.call}")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


def _make_loop(stream: bool) -> AgentLoop:
    loop = AgentLoop()
    loop._config.agent.stream = stream
    loop._config.tools = []
    loop._config.plugins = []
    return loop


def _make_job(loop: AgentLoop, sid: str = "j1") -> Job:
    job = Job(
        id=sid,
        status="pending",
        input=InputMessage(content="hello", session_id=sid),
    )
    loop._jobs[job.id] = job
    return job


class _Plugins:
    """测试用插件容器：统一 unload。"""

    def __init__(self, *plugins) -> None:
        self._plugins = plugins

    def unload(self) -> None:
        for p in self._plugins:
            p.unload()


def _load_plugin(loop: AgentLoop) -> _Plugins:
    """加载 MessagePlugin + SessionPlugin。

    MessagePlugin 负责把领域事件翻译成 msg_output（loop 自身不再发），
    SessionPlugin 持久化指向临时目录避免污染 data/sessions/。
    """
    from agent.plugins.message import MessagePlugin

    message = MessagePlugin()
    message.load(loop.ctx, {})

    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    # _session_file 基于 _base_path，指向临时目录即可绕过真实持久化
    session._base_path = Path(tempfile.mkdtemp(prefix="session-test-"))

    return _Plugins(message, session)


class _OutputCollector:
    """监听 msg_output，按增量外发语义收集 evt.event。"""

    def __init__(self, loop: AgentLoop) -> None:
        self.events: list[OutputMessage] = []
        self._loop = loop

    async def _on_msg_output(self, ctx: AgentContext, evt: Event) -> None:
        event = evt.data.get("output")
        if event is not None:
            self.events.append(event)

    def __enter__(self) -> "_OutputCollector":
        self._loop.ctx.on("msg_output", self._on_msg_output)
        return self

    def __exit__(self, *args: Any) -> None:
        self._loop.ctx.off("msg_output", self._on_msg_output)


def _collect_message_events(events: list[OutputMessage]) -> list[str]:
    """从收集到的输出事件中过滤 message 事件内容。"""
    return [
        e.content
        for e in events
        if isinstance(e, OutputMessage) and e.type == "message"
    ]


async def test_multiple_turns_content_semantics():
    """非流式 + follow-up：turn.content 保持末轮文本（不拼接，字段语义不变）。"""
    loop = _make_loop(stream=False)
    job = _make_job(loop)
    fake = _FakeMain(loop, job)
    loop._models.get = lambda scene: fake

    await loop._run_loop(job)

    assert fake.call == 2, f"expected 2 turns, got {fake.call}"
    # turn.content 保持"最后一个 turn 输出"，字段语义不变
    assert job.turn.content == "reply 2", f"content={job.turn.content!r}"
    assert job.status == "done", job.status


async def test_session_pushes_independent_messages():
    """SessionPlugin 非流式下每轮推送独立 message，先后顺序保留。"""
    loop = _make_loop(stream=False)
    job = _make_job(loop)

    plugin = _load_plugin(loop)
    try:
        fake = _FakeMain(loop, job)
        loop._models.get = lambda scene: fake

        collector = _OutputCollector(loop)
        with collector:
            await loop._run_loop(job)

        msgs = _collect_message_events(collector.events)
        print(f"  message events: {msgs!r}")
        assert msgs == ["reply 1", "reply 2"], f"messages={msgs!r}"
        # OutputMessage 自包含 session_id
        for ev in collector.events:
            assert ev.session_id == job.id, f"session_id={ev.session_id!r}"
        assert job.status == "done", job.status
    finally:
        plugin.unload()


async def test_streaming_no_duplicate_message():
    """流式下 turn_end 不推送 message（stream 事件已实时渲染），job_end 也不兜底。"""
    loop = _make_loop(stream=True)
    job = _make_job(loop)

    plugin = _load_plugin(loop)
    try:
        fake = _FakeMain(loop, job)
        loop._models.get = lambda scene: fake

        collector = _OutputCollector(loop)
        with collector:
            await loop._run_loop(job)

        msgs = _collect_message_events(collector.events)
        full_messages = [
            e for e in collector.events
            if isinstance(e, OutputMessage) and e.type == "message" and not e.stream
        ]
        print(f"  message events: {msgs!r}, full message events: {len(full_messages)}")
        # 流式：message 块带 stream=True，无非流式 message 兜底（否则前端会重复展示）
        assert msgs == [], f"unexpected message events in stream mode: {msgs!r}"
        assert full_messages == [], f"unexpected full message in stream mode: {full_messages!r}"
        assert job.status == "done", job.status
    finally:
        plugin.unload()


async def test_single_turn_content():
    """单轮文本轮：turn.content 即该轮文本，job_end 不发 message（已由 turn_end 推）。"""
    class _OneShot:
        async def chat(self, messages, tools=None):
            return ModelResponse(text="hello")

    loop = _make_loop(stream=False)
    job = _make_job(loop)

    plugin = _load_plugin(loop)
    try:
        loop._models.get = lambda scene: _OneShot()

        collector = _OutputCollector(loop)
        with collector:
            await loop._run_loop(job)
        assert job.turn.content == "hello", job.turn.content
        assert job.status == "done", job.status

        msgs = _collect_message_events(collector.events)
        assert msgs == ["hello"], f"messages={msgs!r}"
    finally:
        plugin.unload()


async def test_tool_turn_then_text_turn():
    """工具轮后接文本轮：工具轮不产生 message，文本轮才推一条，无残留误推。"""
    class _ToolThenText:
        def __init__(self, loop, job):
            self.loop = loop
            self.job = job
            self.call = 0

        async def chat(self, messages, tools=None):
            self.call += 1
            if self.call == 1:
                # 第一轮：工具调用（无文本），同时用户 follow-up 排队
                self.loop._message_queues[self.job.id].push("follow-up")
                return ModelResponse(
                    text=None,
                    tool_calls=[ToolCall(id="tc1", name="echo", arguments={"text": "x"})],
                )
            # 第二轮：纯文本回复
            return ModelResponse(text="final answer")

        async def chat_stream(self, messages, tools=None, on_chunk=None):
            return await self.chat(messages, tools=tools)

    loop = _make_loop(stream=False)
    job = _make_job(loop)

    plugin = _load_plugin(loop)
    try:
        fake = _ToolThenText(loop, job)
        loop._models.get = lambda scene: fake

        collector = _OutputCollector(loop)
        with collector:
            await loop._run_loop(job)

        msgs = _collect_message_events(collector.events)
        print(f"  message events: {msgs!r}")
        # 工具轮不推，仅文本轮推一条（tools 未注册时 unknown tool 记 error，不影响收敛）
        assert msgs == ["final answer"], f"messages={msgs!r}"
        assert job.status == "done", job.status
        assert fake.call == 2, f"expected 2 turns, got {fake.call}"
    finally:
        plugin.unload()


async def test_msg_input_job_construction():
    """msg_input 消息体不含内部调度信息：外部消息以 session_id 为 job.id 构造 root job。"""
    loop = _make_loop(stream=False)

    # 捕获 _on_input 构造的 job 内部分配（不触发完整循环）
    captured = {}
    real_handle_chat = loop._handle_chat
    real_handle_command = loop._handle_command

    async def spy_handle_chat(job):
        captured["job"] = job
        # 不真正启动循环，避免网络调用
        job.status = "pending"
        # 不挂 _jobs，避免并发污染

    async def spy_handle_command(job):
        captured["job"] = job

    loop._handle_chat = spy_handle_chat
    loop._handle_command = spy_handle_command

    created = {}
    real_on_input = loop._on_input

    async def tracking_on_input(ctx, evt):
        created["input_msg"] = evt.data.get("input")
        await real_on_input(ctx, evt)

    loop.ctx.on("msg_input", tracking_on_input)

    # 外部风格：无事件级 job_id → job.id = session_id
    await loop.ctx.emit("msg_input", input=InputMessage(content="root chat", session_id="sid-A"))
    job = captured.get("job")
    assert job is not None, "_on_input did not construct a job"
    assert job.id == "sid-A"
    assert job.input.content == "root chat"

    # 消息体不含 job_id（消息规范不泄漏内部调度）
    input_msg = created["input_msg"]
    assert not hasattr(input_msg, "job_id"), "InputMessage leaked job_id"
    assert input_msg.session_id == "sid-A"

    # 恢复（避免污染后续用例）
    loop._handle_chat = real_handle_chat

    print(f"  root job id={job.id}")
    print(f"  input fields={[a for a in ('content','type','action','data','session_id') if hasattr(input_msg, a)]}")


async def test_subjob_independent_session():
    """subjob 用独立 session_id 创建子任务：loop 按此建独立 job，结果经 job_end 回填。"""
    loop = _make_loop(stream=False)

    class _FakeMain:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None):
            self.calls += 1
            return ModelResponse(text="sub done")

        async def chat_stream(self, messages, tools=None, on_chunk=None):
            return await self.chat(messages, tools=tools)

    fake = _FakeMain()
    loop._models.get = lambda scene: fake

    # 注册 msg_input handler（start 会加载 plugins；这里手动注册以便精准控制）
    loop.ctx.on("msg_input", loop._on_input)

    plugin = SubJobPlugin()
    plugin.load(loop.ctx, {"max_depth": 2})

    parent = Job(
        id="parent-s",
        status="thinking",
        input=InputMessage(content="root", session_id="parent-s"),
    )
    loop._jobs[parent.id] = parent

    future = await plugin._create_subjob("sub task", parent, loop.ctx)
    await asyncio.sleep(0.3)

    assert future.done(), "subjob future not completed"
    result = future.result()
    print(f"  subjob result: {result!r}")
    assert result == "sub done", f"unexpected result: {result!r}"
    assert fake.calls == 1, f"expected 1 llm call, got {fake.calls}"
    # 父任务仍在，子任务已结束并从 _jobs 清理
    assert loop._jobs.get("parent-s") is not None
    plugin.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("content_semantics", test_multiple_turns_content_semantics),
        ("session_independent_messages", test_session_pushes_independent_messages),
        ("streaming_no_duplicate", test_streaming_no_duplicate_message),
        ("single_turn", test_single_turn_content),
        ("tool_then_text", test_tool_turn_then_text_turn),
        ("msg_input_job_construction", test_msg_input_job_construction),
        ("subjob_independent_session", test_subjob_independent_session),
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
