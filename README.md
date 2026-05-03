# Agent

容器化运行的 agent，支持插件生命周期、会话记忆和 LLM 上下文压缩。兼容 OpenAI API（DeepSeek）。

## 快速开始

```bash
uv sync
export DEEPSEEK_API_KEY=sk-...
uv run python -m agent
```

Agent 监听 `ws://localhost:8765`。

### Docker

```bash
DEEPSEEK_API_KEY=sk-... docker compose up -d --build
```

## 配置

所有配置项有默认值，`config.yml` 覆盖。支持 `${VAR}` 环境变量展开。

```yaml
model:
  name: deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key: ${DEEPSEEK_API_KEY}

agent:
  max_tokens: 128000
  compress_threshold: 0.9
  compress_keep_recent: 10
  max_load_messages: 100

skills:
  - websearch
  - confirm

plugins:
  - session
  - confirm
  - workspace
```

## 扩展

Skill 给 LLM 提供可调用的工具，Plugin 介入生命周期钩子。详见 `CLAUDE.md`。

### 添加 Skill

```python
from agent.core.skill import Skill, ToolDefinition

class MySkill(Skill):
    name = "my_skill"

    @property
    def tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(name="my_tool", description="...", parameters={...})]

    async def execute(self, tool_name: str, arguments: dict) -> str:
        ...
```

### 添加 Plugin

```python
from agent.core.plugin import Plugin, PluginRegistry
from agent.core.loop import JobContext

class MyPlugin(Plugin):
    name = "my_plugin"

    def register(self, registry: PluginRegistry) -> None:
        registry.on("before_llm", self._on_before_llm)

    async def _on_before_llm(self, ctx: JobContext) -> None:
        ...
```

在 `config.yml` 对应列表中添加模块名即可生效。

## Agent 定义

编辑 `agent/AGENTS.md` 定义 agent 的身份和能力，作为 system prompt 注入。
