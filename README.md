<div align="center">
  <h1>Agent</h1>
  灵活扩展的 Cloud Agent，AI 工作流的运行底座
</div>

## 主要特性

- 多会话并行，多层子任务
- 上下文自动压缩与会话持久化
- 插件系统：事件驱动与方法注册两种扩展机制
- 工具系统：内置工具 + MCP 工具同步，高风险工具审查确认阻断
- 技能系统：SKILL.md 指令模板
- 会话控制：暂停/恢复、取消、重置
- 场景扩展：基座 + 垂直场景独立部署

## 部署

```bash
uv sync
uv run python -m agent
```

Agent 监听 `ws://localhost:8765`（WebSocket 协议，流式输出 + 命令控制）。

### Docker

```bash
docker compose up -d --build
```

外部服务需要单独启动：

```bash
cd services/mcp && docker compose up -d --build
cd services/searxng_search && docker compose up -d
```

## 配置

`config.yml` 覆盖默认值，支持 `${VAR}` 环境变量展开。`agent/AGENTS.md` 作为 system prompt。

```yaml
model:
  providers:
    deepseek:
      base_url: https://api.deepseek.com
      api_key: ${DEEPSEEK_API_KEY}
      models:
        v4-pro:
          name: deepseek-v4-pro
          max_tokens: 128000
  alias:
    main: deepseek:v4-pro
    flash: deepseek:v4-flash

agent:
  max_concurrent: 10
  max_iterations: 100

tools:
  - read_file
  - write_file
  - edit_file
  - shell
  - subjob
  - websearch:
      search_url: http://searxng:8080

plugins:
  - websocket:
      host: 0.0.0.0
      port: 8765
  - message
  - session:
      max_load_messages: 100
      max_tokens: 128000
      compress_threshold: 0.9
      system_prompt_path: agent/AGENTS.md
  - subjob:
      max_depth: 2
  - cmd_pause
  - cmd_cancel
  - cmd_reset
  - logging
  - mcp:
      base_url: http://mcp:8001
```

## 外部服务

- `services/mcp/` — agent-mcp 服务，管理 Node.js MCP servers，端口 8001
- `services/searxng_search/` — SearXNG 搜索服务，端口 8080
