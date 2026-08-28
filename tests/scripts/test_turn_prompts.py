"""turn_start 提示段组装（waterfall）单元/集成测试。

覆盖：emit 返回组装结果（注册顺序确定性）、无贡献者时返回初始 data、
loop 作为 Turn 持有者把返回值落盘 job.turn.prompts 并经 _on_llm_start 合并进 system prompt。
"""
from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio

from agent.core.io import InputMessage
from agent.core.loop import AgentContext, AgentLoop, Job, Turn
from agent.core.events import Event
from agent.core.model import ModelResponse
from agent.plugins.session import SessionPlugin
from agent.plugins.skill import SkillPlugin


def _make_loop() -> AgentLoop:
    loop = AgentLoop()
    loop._config.plugins = []
    return loop


def _job(job_id: str = "t-1") -> Job:
    job = Job(id=job_id, status="running")
    job.turn = Turn()
    return job


def _make_run_job(loop: AgentLoop, sid: str = "t-1") -> Job:
    job = Job(id=sid, status="pending", input=InputMessage(content="hello", session_id=sid))
    loop._jobs[job.id] = job
    return job


def _install_session(loop: AgentLoop) -> SessionPlugin:
    session = SessionPlugin()
    session.load(loop.ctx, {
        "system_prompt_path": "nonexistent.md",
        "session_root": tempfile.mkdtemp(prefix="turn-session-"),
        "workspace_root": tempfile.mkdtemp(prefix="turn-ws-"),
    })
    return session


def _install_skill(loop: AgentLoop) -> SkillPlugin:
    skill_dir = Path(tempfile.mkdtemp(prefix="turn-skill-"))
    (skill_dir / "demo").mkdir()
    (skill_dir / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nbody text\n",
        encoding="utf-8",
    )
    skill = SkillPlugin()
    skill.load(loop.ctx, {"dirs": [str(skill_dir)]})
    return skill


@dataclass
class _CapturingMain:
    """捕获每次 LLM 调用的 messages，返回纯文本收尾。"""

    calls: list = field(default_factory=list)

    async def chat(self, messages, tools=None):
        self.calls.append(messages)
        return ModelResponse(text="final-answer")

    async def chat_stream(self, messages, tools=None, on_chunk=None):
        return await self.chat(messages, tools=tools)


async def test_turn_start_emit_returns_prompts_in_order() -> None:
    """emit 返回组装结果：按注册顺序确定（时间在前、技能在后）。"""
    loop = _make_loop()
    session = _install_session(loop)
    skill = _install_skill(loop)

    job = _job()
    result = await loop.ctx.emit("turn_start", job=job)

    assert isinstance(result, dict) and "prompts" in result, result
    prompts = result["prompts"]
    assert len(prompts) == 2, prompts
    assert prompts[0].startswith("Current time:"), prompts
    assert "demo" in prompts[1], prompts

    session.unload()
    skill.unload()


async def test_turn_start_without_skill() -> None:
    """仅 session（无 skill 贡献）时只有时间段。"""
    loop = _make_loop()
    session = _install_session(loop)

    job = _job()
    result = await loop.ctx.emit("turn_start", job=job)

    assert len(result.get("prompts", [])) == 1, result
    assert result["prompts"][0].startswith("Current time:"), result

    session.unload()


async def test_turn_start_no_contributors() -> None:
    """无任何 turn_start handler：emit 返回初始 data。"""
    loop = _make_loop()
    job = _job()
    result = await loop.ctx.emit("turn_start", job=job)

    assert result == {}, result


async def test_loop_persists_prompts_into_messages() -> None:
    """集成：loop 落盘返回值到 job.turn.prompts，_on_llm_start 合并进 system prompt。"""
    loop = _make_loop()
    fake = _CapturingMain()
    loop._models.get = lambda scene: fake
    job = _make_run_job(loop)

    session = _install_session(loop)
    skill = _install_skill(loop)
    try:
        await loop._run_loop(job)

        assert job.status == "done", job.status
        # loop 落盘：job.turn.prompts 含两个段（注册顺序确定）
        assert len(job.turn.prompts) == 2, job.turn.prompts
        assert job.turn.prompts[0].startswith("Current time:"), job.turn.prompts
        assert "demo" in job.turn.prompts[1], job.turn.prompts
        # 消费方：提示段已并入 system prompt
        assert fake.calls, "LLM 未被调用"
        content = getattr(fake.calls[0][0], "content", "")
        assert "Current time:" in content, content
        assert "demo" in content, content
    finally:
        session.unload()
        skill.unload()


async def test_loop_skips_invalid_prompts() -> None:
    """集成：贡献者产出非 list / 非 dict 时，loop 不落盘，job.turn.prompts 保持空。"""

    async def run_with_bad(bad_result) -> tuple[list, str]:
        loop = _make_loop()
        fake = _CapturingMain()
        loop._models.get = lambda scene: fake
        job = _make_run_job(loop)
        session = _install_session(loop)

        async def bad(ctx: AgentContext, evt: Event):
            return bad_result  # 整体替换 evt.data

        loop.ctx.on("turn_start", bad)  # 注册在 session 贡献者之后，覆盖最终 data
        try:
            await loop._run_loop(job)
            return job.turn.prompts, getattr(fake.calls[0][0], "content", "")
        finally:
            session.unload()

    # 非 list 的 prompts：不落盘
    prompts, content = await run_with_bad({"prompts": "not-a-list"})
    assert prompts == [], prompts
    assert "Current time:" not in content, content

    # 非 dict 的返回值：不落盘
    prompts, content = await run_with_bad("scalar")
    assert prompts == [], prompts
    assert "Current time:" not in content, content


async def main() -> None:
    results = {}
    scenarios = [
        ("emit_returns_prompts_in_order", test_turn_start_emit_returns_prompts_in_order),
        ("without_skill", test_turn_start_without_skill),
        ("no_contributors", test_turn_start_no_contributors),
        ("loop_persists_prompts_into_messages", test_loop_persists_prompts_into_messages),
        ("loop_skips_invalid_prompts", test_loop_skips_invalid_prompts),
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
