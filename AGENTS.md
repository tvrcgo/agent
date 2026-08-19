# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API。

## 分层

**core（`agent/core/`）**——执行机制，不感知客户端协议，不依赖 plugin：

- `loop.py`：推理循环（Think→Act→Observe），每个会话一个 asyncio Task。只做核心调度，功能扩展一律走事件+插件，禁止在其中添加业务逻辑
- `events.py`：事件总线（`on`/`off`/`emit`），插件与组件间通信中枢
- `io.py`：I/O 端口契约——`InputMessage`（输入端口，loop 消费）/ `OutputMessage`（输出端口，由 MessagePlugin 从领域事件翻译构造）
- `model.py`：LLM 客户端与消息模型
- `tool.py`：工具基类与注册表，只提供执行机制，不做状态检查
- `config.py`：Pydantic 配置模型

**plugin（`agent/plugins/`）**——业务扩展，通过 `ctx.on`/`ctx.emit` 经事件总线交流，插件间不相互依赖：

- `message.py`：领域事件 → `OutputMessage` → `msg_output`（输出翻译唯一中枢）
- `session.py`：会话数据（存取、压缩、LLM 消息组装），不发 `msg_output`
- `websocket.py` / `queue.py`：外部协议解析/序列化（信任边界），发 `msg_input`、消费 `msg_output`
- `subjob.py`：子任务递归（独立 session_id 经 `msg_input` 建独立 job，结果经 `job_end` 回填）
- `confirm.py`：通用确认通道（两层请求-响应）
- `tool_guard.py`：工具执行守卫（审查→确认→阻断，全在插件内闭环）
- `skill.py`：SKILL.md 指令模板加载，`turn_start` 追加提示段
- `logging.py`：领域事件日志
- `mcp.py`：从 agent-mcp 服务（`services/mcp/`）同步 MCP 工具，命名 `mcp_{server}_{tool}`

**tools（`agent/tools/`）**：内置工具，每目录一个 Tool 子类；**services（`services/`）**：外部服务独立部署，通过共享 Docker 网络通信，agent 不强依赖。

## 核心概念

### AgentContext
AgentLoop 的能力门面，贯穿 session/job 生命周期：`ctx.models`/`ctx.tools`/`ctx.config` 访问组件，`ctx.on`/`ctx.off`/`ctx.emit` 订阅发布事件。事件用 `job` 参数定位具体 job（多 job 并发下 ctx 不持有 job）；per-job 静态数据与执行缓存写 `job.data`，运行时数据一律随事件传递。

### 请求-响应原语
事件名带 `req:` 前缀走请求-响应，否则广播。请求方 `await ctx.emit("req:<name>", job, timeout=120, **data)` 阻塞等结果；响应方从 `evt.request` 取请求，隐式返回非 None 结果即回填。请求事件只允许一个 handler（多个抛 RuntimeError）；超时/无响应方返回 None，处理策略由调用方决定。

### 广播
`asyncio.gather` 并行执行所有 handler 并等待完成，单个 handler 异常被隔离记日志，不中断其他 handler 与 emit 本身。

### 消息排队
job 运行中收到的同 id chat 消息排队，下轮迭代前由 loop 消费为 steering 消息。

### I/O 通道
- `msg_input`：外部输入（websocket/queue 解析后发出，subjob 内部递归同通道），loop 消费后建 Job（`job.id` = `session_id`，loop 不感知 session 概念）
- `msg_output`：对外输出，统一由 MessagePlugin 发出；插件主动推送（confirm、subjob jobs 树）直接构造 `OutputMessage` 发 `msg_output`
- 消息体不承载内部调度概念（无 `job_id`）；输入端口类型用 Literal 注解固化合法取值

### 上下文压缩
存储无上限，冷启只加载尾部若干条。token 超阈值时独立 LLM 调用压缩旧消息，保留最近原文；内存完成，JSONL 保留完整历史。

### 插件生命周期
`agent_start` → `job_start` → `llm_start` → `llm_end` → `tools_start` / `tool_start` → 工具执行 → `tool_end` → `tools_end` → 循环 → `job_end` / `job_error` / `agent_stop`。`job_end` 是 job 结束的唯一钩子（所有终止路径必发），插件资源清理一律监听 `job_end`。工具未执行（被守卫拒绝/截断 fail）走 `tool_error`。`cmd_<action>` 钩子处理 UI 操作。

### 工具执行阻断
core 只提供执行机制，阻断实现交给 plugin：`execute_batch`（core）纯执行；`tool_guard`（plugin）在 `tools_start` 审查→确认→deny 则从 `tool_calls` 剔除并 emit `tool_error`，被阻断的调用不进入 `execute_batch`。取消：`cmd_cancel` 取消单个 job，经 `Task.cancel()` 注入 `CancelledError`，`_run_loop` 捕获后有序收尾。

### Confirm Plugin
两层请求-响应各持各的 req：第一层 `req:request_confirm`（隐式返回决策）；第二层 `req:confirm_ui` 推送确认到前端，注册一次性 `cmd_confirm` 监听等决策，超时返回 None 按 deny 处理。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件
- 默认系统提示词从 `agent/AGENTS.md` 读取
- 测试资源清单见 `tests/README.md`
- 一次性临时文件写入系统临时目录
- tool 的依赖与项目依赖隔离

### 架构规范

- 按架构分层，模块只能向下或同级引用（core 不能引用 plugins）
- **`loop.py` 是核心流程，不能随便修改**；功能扩展一律事件+插件，事件不够可新增，事件名要符合 loop 流程语义
- plugin 之间不能相互依赖，通过事件总线交流
- 组件间共享状态一律走事件（`Event.data` 或 `job.data`），不使用 `ctx.data` 动态属性
- 插件 `load(ctx, config)` 时用 `ctx.on(name, handler)` 注册，`unload` 释放自身资源
- handler 签名统一 `async def handler(ctx, evt: Event)`（不用的参数命名 `_`）
