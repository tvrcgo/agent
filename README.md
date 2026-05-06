# <center>Agent</center>

<center>灵活扩展的 Cloud Agent，AI 工作流的运行底座</center>

## 主要特性

- 多会话并行，多层子任务
- 上下文自动压缩，保留近期完整 loop
- 插件系统：生命周期钩子 + 命令钩子，功能扩展不修改核心代码
- 技能系统：LLM 可调用工具，每个工具独立依赖隔离

## 部署

```bash
uv sync
uv run python -m agent
```

Agent 监听 `ws://localhost:8765`。

### Docker

```bash
docker compose up -d --build
```

## 配置

`config.yml` 覆盖默认值，支持 `${VAR}` 环境变量展开。`agent/AGENTS.md` 作为 system prompt。

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
