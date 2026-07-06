# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API。

## 分层

- **入口** (`__main__.py`)：组装各组件并启动 WebSocket 服务
- **推理循环** (`loop.py`)：Think→Act→Observe 循环，每个 WebSocket 会话一个 asyncio Task。loop.py 只做核心调度，功能扩展通过插件钩子实现，禁止在其中添加业务逻辑
- **插件** (`plugin.py`)：基于钩子的事件系统，插件在 `load` 时注册 handler，`emit` 按注册顺序调用
- **工具** (`tool.py`)：可执行工具的抽象基类和注册表，从 `agent/tools/` 加载 Tool 子类。`config.yml` 中 tools 列表支持字符串或带参数的 dict 格式，参数通过 `Tool.config` 传递给工具实例
- **技能** (`skill.py`)：SKILL.md 指令模板，从 `agent/skills/` 和 `skills/` 加载，注入系统提示词

## 核心概念

### AgentContext
贯穿 session 和 job 生命周期的可变上下文，持有 `models` 和 `tools` 引用。插件通过 `ctx.data` 字典通信，通过 `ctx.emit()` 触发钩子。

### 消息排队
job 运行中收到的 chat 消息排队，下轮迭代开始前由 loop 写入 `ctx.data`，SessionPlugin 在 `before_llm` 消费为 user 消息。

### 上下文压缩
存储无上限，冷启只加载尾部若干条。token 超阈值时通过独立 LLM 调用压缩旧消息，保留最近原文。压缩在内存完成，JSONL 保留完整历史。

### Job 树
复杂任务可通过 `sub_job` 工具并行执行。子 Job 共享同一 AgentLoop 的 LLM、tools 和 skills，通过 `asyncio.gather` 并发执行。`max_sub_job_depth` 限制递归深度。

### 插件生命周期
`agent_start` → `before_job` → `before_llm` → `after_llm` → `before_tool` → 工具执行 → `after_tool` → `before_tools` / `after_tools` → 循环 → `after_job` / `on_error` → `on_complete` / `agent_stop`。`on_output` 由插件在需要推送事件时主动触发。`command:<action>` 钩子处理 UI 操作。

### MCP Plugin
MCP 作为插件，通过 HTTP 从 agent-mcp 服务同步工具。agent-mcp 运行在独立容器（`services/mcp/`），管理 Node.js MCP servers。插件每 10 分钟同步一次，移除失效 tools、注册新增 tools。工具命名格式为 `mcp_{server}_{tool}`。

```yaml
plugins:
  - mcp:
      base_url: http://mcp:8001
```

### Queue Plugin
Redis 队列插件，BLPOP 监听输入队列触发 `on_input`，`on_output` 时 RPUSH 到输出队列。连接失败自动重试，不阻塞 agent 启动。

```yaml
plugins:
  - queue:
      redis_url: redis://localhost:6379
      input_queue: agent:input
      output_queue: agent:output
```

### 工具
内置工具以目录形式存放在 `agent/tools/`，每个目录一个 `__init__.py` 导出 Tool 子类。`ToolRegistry.register` 支持单个和数组，`unregister` 支持按名移除。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件
- 默认配置从 `agent/AGENTS.md` 读取系统提示词
- 测试资源清单见 `tests/README.md`
- 一次性的临时文件写入系统的临时目录
- 外部服务独立部署在 `services/` 下，通过共享 Docker 网络与 agent 通信，agent 不强依赖这些服务

### 架构规范

- 按架构分层，模块只能向下或同级引用，不能上向引用（core 中的模块引用 plugins, skills 中的模块）
- **`loop.py` 是核心流程，不能随便修改**，对 loop 功能的扩展，都用 hook+plugin 的方式实现；如果 hook 不够可新增，但 hook name 要符合 loop 流程的语义，可复用
- plugin 之间不能相互依赖
- tool 的依赖要和项目依赖隔离
