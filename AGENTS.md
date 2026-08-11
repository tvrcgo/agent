# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API。

## 分层

- **入口** (`__main__.py`)：加载配置、创建 `AgentLoop` 并启动/停止；所有组件（models/总线/tools/skills/plugins）由 `AgentLoop.start()` 统一加载
- **推理循环** (`loop.py`)：Think→Act→Observe 循环，每个 WebSocket 会话一个 asyncio Task。loop.py 只做核心调度，功能扩展通过事件钩子实现，禁止在其中添加业务逻辑
- **事件总线** (`events.py`)：agent 级基础消息机制。`Event{name, job, data}` 数据类（`data` 读写对称：`evt.field` 读 / `evt.field = value` 写都代理到 data）+ `EventBus`（订阅 `on`、退订 `off`、发布 `emit`），插件和任何组件通过 `ctx.on`/`ctx.emit` 交流
- **插件** (`plugin.py`)：插件容器，插件在 `load` 时通过 `ctx.on` 注册事件 handler，`PluginRegistry` 只负责插件实例的加载与卸载
- **工具** (`tool.py`)：可执行工具的抽象基类和注册表，从 `agent/tools/` 加载 Tool 子类。`config.yml` 中 tools 列表支持字符串或带参数的 dict 格式，参数通过 `Tool.config` 传递给工具实例
- **技能** (`skill.py`)：SKILL.md 指令模板，从 `agent/skills/` 和 `skills/` 加载，注入系统提示词

## 核心概念

### AgentContext
贯穿 session 和 job 生命周期的可变上下文，是 AgentLoop 的能力门面：`models`/`tools`/`config` 经 `_self` 代理，`ctx.on`/`ctx.emit` 订阅发布事件。插件通过 `ctx.on` 订阅事件、`ctx.emit` 发布事件，通过 `job.data` 写 per-job 静态数据/执行缓存。

### 事件总线
`EventBus` 是 agent 的通信中枢，`AgentLoop` 持有实例并通过 `ctx` 暴露 `on/off/emit`。事件 `Event{name, job, data}` 扁平结构：`job` 定位到具体 job（多 job 并发下 ctx 不持有 job），`data` 携带业务字段。`emit(name, job=None, **data)` 返回 Event，订阅者可写入 `evt.field` 回填（如 `request_confirm` 的决策结果）。运行时数据（LLM 响应、工具结果、reason/error、confirm 决策）一律随事件传递，`job.data` 只存静态数据（`work_dir`）与执行缓存（`messages`）。

### 消息排队
job 运行中收到的 chat 消息排队，下轮迭代开始前由 loop 写入 `job.data`，SessionPlugin 在 `llm_start` 消费为 user 消息。

### 上下文压缩
存储无上限，冷启只加载尾部若干条。token 超阈值时通过独立 LLM 调用压缩旧消息，保留最近原文。压缩在内存完成，JSONL 保留完整历史。

### Job 树
复杂任务可通过 `sub_job` 工具并行执行。子 Job 共享同一 AgentLoop 的 LLM、tools 和 skills，通过 `asyncio.gather` 并发执行。`max_sub_job_depth` 限制递归深度。

### 插件生命周期
`agent_start` → `job_start` → `llm_start` → `llm_end` → `tool_start` → 工具执行 → `tool_end` → `tools_start` / `tools_end` → 循环 → `job_end` / `job_error` → `job_complete` / `agent_stop`。`msg_output` 由插件在需要推送事件时主动触发。`cmd_<action>` 钩子处理 UI 操作。

### MCP Plugin
MCP 作为插件，通过 HTTP 从 agent-mcp 服务同步工具。agent-mcp 运行在独立容器（`services/mcp/`），管理 Node.js MCP servers。插件每 10 分钟同步一次，移除失效 tools、注册新增 tools。工具命名格式为 `mcp_{server}_{tool}`。

```yaml
plugins:
  - mcp:
      base_url: http://mcp:8001
```

### Queue Plugin
Redis 队列插件，BLPOP 监听输入队列触发 `msg_input`，`msg_output` 时 RPUSH 到输出队列。连接失败自动重试，不阻塞 agent 启动。

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
- **`loop.py` 是核心流程，不能随便修改**，对 loop 功能的扩展，都用事件+插件的方式实现；如果事件不够可新增，但事件名要符合 loop 流程的语义，可复用
- plugin 之间不能相互依赖，通过 `ctx.on`/`ctx.emit` 经事件总线交流
- 组件间共享状态一律走事件（`Event.data` 或 per-job 的 `job.data`），不使用 `ctx.data` 动态属性
- 插件 `load(ctx, config)` 时用 `ctx.on(name, handler)` 注册，`unload` 时释放自身资源；单个 handler 可用 `ctx.off(name, handler)` 手动移除
- handler 签名统一为 `async def handler(ctx, evt: Event)`，从 `evt.job` 取作用对象、`evt.data`/`evt.<field>` 取载荷
- tool 的依赖要和项目依赖隔离
