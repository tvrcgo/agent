from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent.core.io import InputMessage
from agent.core.loop import AgentContext, AgentLoop, Job
from agent.core.events import Event
from agent.core.model import ModelResponse
from agent.core.plugin import Plugin
from agent.plugins.session import SessionPlugin


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

    def messages(self) -> list[str]:
        return [o.content for o in self.outputs if o.type == "message"]


class SlowFake:
    async def chat(self, messages, tools=None):
        await asyncio.sleep(30)
        return ModelResponse(text="too late")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


class FreshFake:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    async def chat(self, messages, tools=None):
        user_msgs = [
            m.content for m in messages
            if hasattr(m, "role") and getattr(m, "role") == "user" and m.content
        ]
        self.seen.append(user_msgs)
        return ModelResponse(text=f"reply-{len(user_msgs)}")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


def _make_loop(stream: bool = False) -> AgentLoop:
    loop = AgentLoop()
    loop._config.agent.stream = stream
    loop._config.tools = []
    loop._config.plugins = []
    loop._config.agent.max_iterations = 20
    return loop


async def _start(loop: AgentLoop, fake, extra_plugins: tuple = ()) -> tuple[SessionPlugin, Path]:
    loop._models.get = lambda scene: fake
    tmp = Path(tempfile.mkdtemp(prefix="reset-"))
    loop._config.plugins = [
        {"session": {
            "system_prompt_path": "nonexistent.md",
            "session_root": str(tmp / "sessions"),
            "workspace_root": str(tmp / "workspace"),
        }},
        "cmd_cancel",
        "cmd_reset",
        *extra_plugins,
    ]
    await loop.start()
    assert "reset_session" in loop.ctx._apis
    assert "cancel_job" in loop.ctx._apis
    assert callable(getattr(loop.ctx, "job", None)) and loop.ctx.job("ghost") is None
    return loop._plugins._plugins["session"], tmp


def _session_file(session: SessionPlugin, session_id: str) -> Path:
    return session._base_path / f"{session_id}.jsonl"


async def test_reset_clears_history_and_starts_fresh() -> None:
    loop = _make_loop()
    session, tmp = await _start(loop, FreshFake())
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="hello-1", session_id="r-1"))
    await asyncio.sleep(0.3)
    assert _session_file(session, "r-1").exists(), "session file should exist"

    await loop.ctx.emit("cmd_reset", job=Job(id="r-1", status="idle"))
    await asyncio.sleep(0.2)

    assert not _session_file(session, "r-1").exists(), "session file should be removed"
    assert "r-1" not in session._sessions, "in-memory state should be cleared"
    assert "Session reset" in spy.messages(), f"expected ack, got {spy.messages()}"

    await loop.ctx.emit("msg_input", input=InputMessage(content="hello-2", session_id="r-1"))
    await asyncio.sleep(0.3)
    fake = loop._models.get("main")
    assert fake.seen[-1] == ["hello-2"], f"post-reset context leaked: {fake.seen}"
    assert _session_file(session, "r-1").exists(), "new session file should be recreated"
    content = _session_file(session, "r-1").read_text(encoding="utf-8")
    assert "hello-2" in content and "hello-1" not in content, "old history leaked to file"


async def test_reset_cancels_running_job() -> None:
    loop = _make_loop()
    session, tmp = await _start(loop, SlowFake(), extra_plugins=("message",))
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="r-2"))
    await asyncio.sleep(0.4)
    assert loop._is_running("r-2"), "job should be running"
    assert _session_file(session, "r-2").exists(), "session file should exist"

    await loop.ctx.emit("cmd_reset", job=Job(id="r-2", status="thinking"))
    await asyncio.sleep(0.8)

    assert "cancelled" in spy.statuses(), f"expected cancelled, got {spy.statuses()}"
    assert not loop._is_running("r-2"), "job should have stopped"
    assert not _session_file(session, "r-2").exists(), "session file should be removed"
    assert "r-2" not in session._sessions, "in-memory state should be cleared"
    assert "Session reset" in spy.messages(), f"expected ack, got {spy.messages()}"


async def test_reset_session_id_routing() -> None:
    loop = _make_loop()
    session, tmp = await _start(loop, SlowFake())

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="r-3"))
    await asyncio.sleep(0.4)
    assert loop._is_running("r-3"), "job should be running"

    await loop.ctx.emit("cmd_reset", job=Job(id="other", status="idle"), session_id="r-3")
    await asyncio.sleep(0.8)

    assert not loop._is_running("r-3"), "target job should have stopped"
    assert not _session_file(session, "r-3").exists(), "target session file should be removed"


async def test_reset_noop_for_unknown() -> None:
    loop = _make_loop()
    session, tmp = await _start(loop, SlowFake())
    spy = OutputSpy(loop)

    await loop.ctx.emit("cmd_reset", job=Job(id="never-started", status="idle"))
    await loop.ctx.emit("cmd_reset", job=Job(id="never-started", status="idle"), session_id="ghost")
    await asyncio.sleep(0.2)

    assert not loop._is_running("never-started")
    assert spy.messages().count("Session reset") == 2, f"expected 2 acks, got {spy.messages()}"


async def test_reset_without_session_api() -> None:
    loop = _make_loop()
    fake = SlowFake()
    loop._models.get = lambda scene: fake
    loop._config.plugins = ["cmd_cancel", "cmd_reset"]
    await loop.start()
    spy = OutputSpy(loop)

    await loop.ctx.emit("msg_input", input=InputMessage(content="slow task", session_id="r-5"))
    await asyncio.sleep(0.4)
    assert loop._is_running("r-5"), "job should be running"

    await loop.ctx.emit("cmd_reset", job=Job(id="r-5", status="thinking"))
    await asyncio.sleep(0.8)

    assert not loop._is_running("r-5"), "job should have stopped"
    assert "Session reset" in spy.messages(), f"expected ack, got {spy.messages()}"


class _ApiProbePlugin(Plugin):
    name = "api-probe"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def load(self, ctx: AgentContext, config: dict = {}) -> None:
        ctx.register("ping", self.ping)

    def ping(self, value: str) -> str:
        self.calls.append(value)
        return f"pong-{value}"


async def test_ctx_register_api() -> None:
    loop = _make_loop()
    probe = _ApiProbePlugin()
    probe.load(loop.ctx, {})

    assert loop.ctx.invoke("ping", value="hello") == "pong-hello"
    assert probe.calls == ["hello"]
    assert "ping" in loop.ctx._apis, "only explicitly registered methods"
    assert loop.ctx.job("ghost") is None, "ctx.job(id) returns None for unknown id"

    try:
        loop.ctx.invoke("probe.calls")
        raise AssertionError("invoke should raise KeyError for unregistered api")
    except KeyError:
        pass

    try:
        loop.ctx.register("ping", lambda: None)
        raise AssertionError("register should raise on duplicate name")
    except ValueError:
        pass

    loop.ctx.register("bad", lambda: None)
    assert loop.ctx.models is loop._models, "default member must not be overwritten"
    assert loop.ctx.invoke("bad") is None, "registered method lives in _apis only"


async def main() -> None:
    results = {}
    scenarios = [
        ("ctx_register_api", test_ctx_register_api),
        ("reset_clears_history_and_starts_fresh", test_reset_clears_history_and_starts_fresh),
        ("reset_cancels_running_job", test_reset_cancels_running_job),
        ("reset_session_id_routing", test_reset_session_id_routing),
        ("reset_noop_for_unknown", test_reset_noop_for_unknown),
        ("reset_without_session_api", test_reset_without_session_api),
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
