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
from agent.plugins.cmd_pause import CmdPausePlugin


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


class SlowEcho(Tool):
    name = "echo"
    description = "Echo the input text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo"}},
        "required": ["text"],
    }

    async def execute(self, arguments: dict[str, Any], ctx: AgentContext, job: Job) -> str:
        await asyncio.sleep(0.6)
        return f"Echo: {arguments.get('text', '')}"


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

    def statuses(self) -> list[str]:
        return [o.content for o in self.by_type("status")]


class PausableFake:
    def __init__(self) -> None:
        self.first_gate = asyncio.Event()
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            await self.first_gate.wait()
            return ModelResponse(
                text=None,
                tool_calls=[ToolCall(id="tc-p", name="echo", arguments={"text": "hello"})],
            )
        return ModelResponse(text="final-answer")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class GateFake:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools=None):
        self.calls += 1
        tool_results = [m for m in messages if hasattr(m, "role") and getattr(m, "role") == "tool"]
        if tool_results:
            return ModelResponse(text="final-answer")
        return ModelResponse(
            text=None,
            tool_calls=[ToolCall(id="tc-p", name="echo", arguments={"text": "hello"})],
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


def _install_base(loop: AgentLoop) -> None:
    loop.ctx.on("msg_input", loop._on_input)
    from agent.plugins.cmd_cancel import CmdCancelPlugin
    cancel = CmdCancelPlugin()
    cancel.load(loop.ctx, {})
    return cancel


def _install_message(loop: AgentLoop):
    from agent.plugins.message import MessagePlugin
    message = MessagePlugin()
    message.load(loop.ctx, {})
    return message


def _install_session(loop: AgentLoop, prefix: str) -> SessionPlugin:
    session = SessionPlugin()
    session.load(loop.ctx, {"system_prompt_path": "nonexistent.md"})
    session._base_path = Path(tempfile.mkdtemp(prefix=prefix))
    return session


async def test_pause_at_tools_start() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = PausableFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-e2e-")
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})
    message = _install_message(loop)

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-1"))
    await asyncio.sleep(0.3)
    assert loop._is_running("p-1"), "job should still be running"
    assert "paused" not in spy.statuses(), "should not be paused yet (still in LLM)"

    await loop.ctx.emit("cmd_pause", job=Job(id="p-1", status="thinking"))
    await asyncio.sleep(0.1)
    fake.first_gate.set()
    await asyncio.sleep(0.3)

    assert "paused" in spy.statuses(), f"expected paused, got {spy.statuses()}"
    assert not any(o.type == "tool_result" for o in spy.outputs), "tool executed while paused"

    await loop.ctx.emit("cmd_resume", job=Job(id="p-1", status="paused"))
    await asyncio.sleep(0.5)

    assert any(o.type == "tool_result" for o in spy.outputs), "tool did not execute after resume"
    assert "done" in spy.statuses(), f"expected done, got {spy.statuses()}"

    session.unload()
    message.unload()
    pause.unload()


async def test_pause_at_turn_start() -> None:
    loop = _make_loop([])
    loop._tools.register(SlowEcho())
    fake = GateFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-turn-")
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})
    message = _install_message(loop)

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-2"))
    await asyncio.sleep(0.2)
    await loop.ctx.emit("cmd_pause", job=Job(id="p-2", status="acting"))
    await asyncio.sleep(0.9)

    assert "paused" in spy.statuses(), f"expected paused, got {spy.statuses()}"
    assert any(o.type == "tool_result" for o in spy.outputs), "tool should have completed before pause"

    await loop.ctx.emit("cmd_resume", job=Job(id="p-2", status="paused"))
    await asyncio.sleep(0.5)

    assert "done" in spy.statuses(), f"expected done, got {spy.statuses()}"

    session.unload()
    message.unload()
    pause.unload()


async def test_tools_start_serial_order_pause_first() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = PausableFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-order-")
    probe_ran: list[str] = []

    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})

    async def _probe(ctx: AgentContext, evt: Event) -> None:
        probe_ran.append(evt.job.id if evt.job else "?")

    loop.ctx.on("tools_start", _probe)   # 模拟"后注册的守卫"，在 pause 之后

    message = _install_message(loop)

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-3"))
    await asyncio.sleep(0.3)
    await loop.ctx.emit("cmd_pause", job=Job(id="p-3", status="thinking"))
    await asyncio.sleep(0.1)
    fake.first_gate.set()
    await asyncio.sleep(0.3)

    assert "paused" in spy.statuses(), f"expected paused, got {spy.statuses()}"
    assert not probe_ran, f"probe (after pause) should not run while paused: {probe_ran}"

    await loop.ctx.emit("cmd_resume", job=Job(id="p-3", status="paused"))
    await asyncio.sleep(0.5)

    assert "p-3" in probe_ran, f"probe should run after resume: {probe_ran}"
    assert any(o.type == "tool_result" for o in spy.outputs), "tool should execute after resume"

    session.unload()
    message.unload()
    pause.unload()


async def test_tools_start_serial_order_probe_first() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = PausableFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-order2-")
    probe_ran: list[str] = []

    async def _probe(ctx: AgentContext, evt: Event) -> None:
        probe_ran.append(evt.job.id if evt.job else "?")

    loop.ctx.on("tools_start", _probe)
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})

    message = _install_message(loop)

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-4"))
    await asyncio.sleep(0.3)
    await loop.ctx.emit("cmd_pause", job=Job(id="p-4", status="thinking"))
    await asyncio.sleep(0.1)
    fake.first_gate.set()
    await asyncio.sleep(0.3)

    assert "paused" in spy.statuses(), f"expected paused, got {spy.statuses()}"
    assert "p-4" in probe_ran, f"probe (before pause) should have run: {probe_ran}"

    await loop.ctx.emit("cmd_resume", job=Job(id="p-4", status="paused"))
    await asyncio.sleep(0.5)

    assert any(o.type == "tool_result" for o in spy.outputs), "tool should execute after resume"
    assert "done" in spy.statuses(), f"expected done, got {spy.statuses()}"

    session.unload()
    message.unload()
    pause.unload()


async def test_cancel_while_paused() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = PausableFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-cancel-")
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})
    message = _install_message(loop)

    await loop.ctx.emit("agent_start")
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-5"))
    await asyncio.sleep(0.3)
    await loop.ctx.emit("cmd_pause", job=Job(id="p-5", status="thinking"))
    await asyncio.sleep(0.1)
    fake.first_gate.set()
    await asyncio.sleep(0.3)
    assert "paused" in spy.statuses(), f"expected paused, got {spy.statuses()}"

    await loop.ctx.emit("cmd_cancel", job=Job(id="p-5", status="paused"))
    await asyncio.sleep(0.6)

    assert "cancelled" in spy.statuses(), f"expected cancelled, got {spy.statuses()}"
    assert not loop._is_running("p-5"), "job should have stopped"
    assert "p-5" not in pause._gates, "gate should be cleaned after cancel"

    session.unload()
    message.unload()
    pause.unload()


async def test_job_end_cleans_gate() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = GateFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-clean-")
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})
    message = _install_message(loop)

    await loop.ctx.emit("agent_start")

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-6"))
    await asyncio.sleep(0.8)
    assert not loop._is_running("p-6"), "job should have finished"

    assert "p-6" not in pause._gates, "gate not cleaned up after job end"

    await loop.ctx.emit("cmd_pause", job=Job(id="p-6", status="done"))
    await loop.ctx.emit("cmd_resume", job=Job(id="p-6", status="done"))
    await asyncio.sleep(0.2)

    session.unload()
    message.unload()
    pause.unload()


async def test_cmd_pause_session_id_routing() -> None:
    loop = _make_loop([])
    loop._tools.register(EchoTool())
    fake = PausableFake()
    loop._models.get = lambda scene: fake
    _install_base(loop)

    session = _install_session(loop, "pause-route-")
    pause = CmdPausePlugin()
    pause.load(loop.ctx, {})
    message = _install_message(loop)

    await loop.ctx.emit("agent_start")

    await loop.ctx.emit("msg_input", input=InputMessage(content="do it", session_id="p-7"))
    await asyncio.sleep(0.3)
    assert "p-7" in pause._gates, "gate not created"

    await loop.ctx.emit("cmd_pause", job=Job(id="other", status="idle"), session_id="p-7")
    await asyncio.sleep(0.1)
    assert not pause._gates["p-7"].is_set(), "gate should be cleared (paused) via session_id"

    await loop.ctx.emit("cmd_resume", job=Job(id="other", status="idle"), session_id="p-7")
    await asyncio.sleep(0.1)
    assert pause._gates["p-7"].is_set(), "gate should be set (resumed) via session_id"

    session.unload()
    message.unload()
    pause.unload()


async def main() -> None:
    results = {}
    scenarios = [
        ("pause_at_tools_start", test_pause_at_tools_start),
        ("pause_at_turn_start", test_pause_at_turn_start),
        ("tools_start_order_pause_first", test_tools_start_serial_order_pause_first),
        ("tools_start_order_probe_first", test_tools_start_serial_order_probe_first),
        ("cancel_while_paused", test_cancel_while_paused),
        ("job_end_cleanup", test_job_end_cleans_gate),
        ("cmd_pause_session_id_routing", test_cmd_pause_session_id_routing),
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
