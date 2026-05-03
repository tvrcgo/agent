# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API（DeepSeek）。

## 架构

- **入口** (`__main__.py`)：组装 LLM Provider + SkillRegistry + PluginRegistry + AgentLoop + WebSocketServer
- **推理循环** (`loop.py`)：AgentLoop 管理 Think→Act→Observe 循环，每个 WebSocket 会话一个独立的 asyncio Task。JobContext（可变上下文）贯穿整个 job 生命周期，所有插件通过 `ctx.data` 共享数据。JobAborted 异常由插件抛出以中断 job。
- **插件系统** (`plugin.py`)：Plugin ABC + PluginRegistry，基于钩子的生命周期管理。插件注册到特定钩子点（`on_connect`、`before_llm`、`after_tool` 等），按注册顺序同步调用。`command:<action>` 钩子处理 UI 命令。
- **技能系统** (`skill.py`)：Skill ABC + SkillRegistry，每个 Skill 提供一组 ToolDefinition。LLM 返回 tool_calls 时由对应的 Skill 执行。
- **WebSocket** (`ws.py`)：类型化消息协议，入站 `chat`/`command`，出站 `message`/`tool_call`/`tool_result`/`status`/`error`。StatusEvent 统一承载状态（`status`）和思维内容（`content`）。

## 数据流

1. 客户端通过 WebSocket 连接（可选 `?session_id=xxx`）
2. `AgentLoop` 为每个会话创建 `asyncio.Task`，执行推理循环：
   - **Think:** `before_llm` 钩子 → SessionPlugin 检查压缩、消费排队消息、设置 `ctx.data["messages"]` → LLM 调用 → `after_llm` 钩子
   - **Act:** `ToolCallEvent` → `before_tool` 钩子 → `SkillRegistry.execute()` → `ToolResultEvent` → `after_tool` 钩子 → 回到 Think
   - **Done:** 无工具调用 → 发出 `MessageEvent` → `finally` 中触发 `on_complete` + `StatusEvent`
3. SessionPlugin 以 JSONL 格式追加写入 `./data/sessions/<session_id>.jsonl`

## 关键设计

### JobContext
- 定义在 `loop.py`，是贯穿 job 生命周期的可变上下文，所有插件共享
- 字段：`session_id`、`client`、`data`（插件间通信字典）、`llm`、`status`
- 命名：变量统一用 `ctx`

### WebSocket 消息协议
- 入站：`chat`（用户输入）和 `command`（UI 操作，带 `action` 字段）
- 出站：`{"type": "...", "timestamp": "...", "payload": {...}}`
- 事件类型：`message`、`tool_call`、`tool_result`、`status`、`error`
- `StatusEvent`：统一承载状态和思维内容（`status` + 可选 `content`）
- Session ID 从 URL query string 获取

### 消息排队
- job 运行中收到的 `chat` 消息进入 `_queue_messages`（按 session）
- 下一轮迭代开始时，loop 将排队消息写入 `ctx.data["queue_messages"]`
- SessionPlugin 在 `before_llm` 中消费，追加为 user 消息

### 上下文管理
- 存储无上限，不设 window_size；冷启时仅加载尾部 `max_load_messages` 条
- **基于 token 的压缩：** 估算 token 达 `max_tokens * compress_threshold` 时，通过独立 LLM 调用压缩旧消息，保留最近 `compress_keep_recent` 条原文
- 压缩纯内存操作，不写盘；JSONL 保留完整原始消息
- 冷启后 token 仍超限 → 自动 compact + warning 日志

### 插件生命周期
- 生命周期钩子（6 个）：`on_connect` → `before_job` → `before_llm` → `after_llm` → `before_tool` → skill 执行 → `after_tool` → ...(循环) → `on_complete` / `on_disconnect`
- 命令钩子：`command:<action>`，loop 将 `CommandMessage` 分发到对应钩子
- `command:cancel` 由 loop 自身处理（核心行为，非插件）
- 插件与 loop 仅通过 `ctx.data` 字典通信
- **规则：loop.py 禁止扩展功能逻辑** 功能扩展必须通过插件钩子实现。loop.py 仅允许 bug 修复和钩子点新增。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件
- 默认配置从 `agent/AGENTS.md` 读取系统提示词
- 测试清单见 `tests/README.md`，集成测试见 `tests/test_ws.py`

### 流程要求

- 改代码前先出简单的RFC，确认后再执行
- 测试前要部署代码
- 本地用 `docker-compose up -d --build` 部署，运行日志看 docker 容器日志
- 每次修改完等用户审查，不要直接 commmit 或 push remote
- commit 详情用列表格式逐条列出主要改动点，不要罗列代码

### 编码规范

- 编码风格要保持一致（如同样是响应事件，不能有的是 on_xxx, 有的是 handle_xxx）
- 从同一个包中 import 多个对象时，不要分散多次 import；多行 import 间不要留空行，保持整洁
- agent/skills 中 skill 的依赖要和项目依赖隔离
- 不写 docstring，不写注释，除非行为出人意料
- 不搞提前抽象
- 不需要的代码及时清除干净
