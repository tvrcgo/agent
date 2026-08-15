# 单元测试 — SkillPlugin

## 测试范围

- SkillPlugin 加载/卸载
- SKILL.md 解析
- turn_start 追加提示段到 job.turn.prompts

## 测试用例

### 1. SkillPlugin 加载技能

```python
from agent.core.loop import AgentLoop
from agent.plugins.skill import SkillPlugin

def test_skill_plugin_load():
    loop = AgentLoop()
    plugin = SkillPlugin()
    plugin.load(loop.ctx, {"dirs": ["agent/skills", "skills"]})

    assert plugin.name == "skill"
    assert plugin._registry.get_skills_prompt()

    plugin.unload()
    assert plugin._registry.get_skills_prompt() == ""
```

### 2. SKILL.md 解析

```python
from agent.plugins.skill import Skill

def test_skill_from_md(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\n正文内容"
    )

    sk = Skill.from_skill_md(d / "SKILL.md")
    assert sk.name == "demo"
    assert sk.body == "正文内容"

    # 无 frontmatter 返回 None
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter")
    assert Skill.from_skill_md(bad / "SKILL.md") is None
```

### 3. turn_start 追加提示段

```python
from agent.core.loop import AgentLoop, Job, Turn
from agent.plugins.skill import SkillPlugin

async def test_skill_prompt_injected():
    loop = AgentLoop()
    plugin = SkillPlugin()
    plugin.load(loop.ctx, {"dirs": ["agent/skills", "skills"]})

    job = Job(id="j1", session_id="j1", status="pending")
    job.turn = Turn()
    await loop.ctx.emit("turn_start", job=job)

    assert job.turn.prompts
    assert "##" in job.turn.prompts[0]

    plugin.unload()
```

### 4. 空技能目录

```python
from agent.core.loop import AgentLoop
from agent.plugins.skill import SkillPlugin

def test_skill_empty_dirs():
    loop = AgentLoop()
    plugin = SkillPlugin()
    plugin.load(loop.ctx, {"dirs": ["/nonexistent"]})

    assert plugin._registry.get_skills_prompt() == ""
```
