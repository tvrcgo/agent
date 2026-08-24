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

    await loop.ctx.emit("cmd_cancel", job=Job(id="other", status="idle"), session_id="c-2")
    await asyncio.sleep(0.8)

    assert "cancelled" in spy.statuses(), f"expected cancelled, got {spy.statuses()}"
    assert not loop._is_running("c-2"), "target job should have stopped"

    session.unload()
    message.unload()
    cancel.unload()


async def test_cancel_noop_for_unknown() -> None:
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

    await loop.ctx.emit("cmd_cancel", job=Job(id="never-started", status="idle"))
    await loop.ctx.emit("cmd_cancel", job=Job(id="never-started", status="idle"), session_id="ghost")
    await asyncio.sleep(0.2)
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
