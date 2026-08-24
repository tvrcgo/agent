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
- `confirm.py`：通用确认通道（`confirm_request` serial 事件请求决策，内部推送确认到前端并注册一次性 `cmd_confirm` 监听等决策，超时按 deny 处理）
- `tool_guard.py`：工具执行守卫（审查→确认→阻断，全在插件内闭环）
- `skill.py`：SKILL.md 指令模板加载，`turn_start` 追加提示段
- `logging.py`：领域事件日志
- `mcp.py`：从 agent-mcp 服务（`services/mcp/`）同步 MCP 工具，命名 `mcp_{server}_{tool}`

**tools（`agent/tools/`）**：内置工具，每目录一个 Tool 子类；**services（`services/`）**：外部服务独立部署，通过共享 Docker 网络通信，agent 不强依赖。

## 场景扩展（基座 + 场景目录）

本仓库是**基座**：core 执行机制 + 内置插件/工具，每场景一个容器 `FROM agent-base`。垂直场景是**独立目录**（自己的 plugins/tools/AGENTS.md/skills），COPY 到容器 `/app` 下与基座分层存放（不侵入 `agent/` 包），无需打包。场景间不共享工具。

- 加载机制：无特殊开关，按配置项显式声明——场景 tool/plugin 写进 `tools`/`plugins` 列表（含 `.` 的完整模块路径，场景目录名即包名），资产写普通文件路径（`system_prompt_path`、skill `dirs`）
- 保留名：场景目录名禁止 `agent`（会覆盖基座包）
- 注册规则：`config.yml` 的 `tools`/`plugins` 项名称**不含 `.`** 回退内置前缀（`agent.tools.{name}` / `agent.plugins.{name}`），**含 `.`** 视为完整模块路径直接 import
- 工具第三方依赖：`requirements.txt` 放在工具模块目录（内置或场景均可），镜像构建时安装；`_check_deps` 按模块实际位置定位
- session 插件路径可配置：`session_root`（默认 `./data/sessions`）、`workspace_root`（默认 `./workspace`，按 session id 建工作子目录）、`system_prompt_path`（默认 `agent/AGENTS.md`）
- 场景扩展机制详见 `docs/scene-extension.md`

## 核心概念

### AgentContext
AgentLoop 的能力门面，贯穿 session/job 生命周期：`ctx.models`/`ctx.tools`/`ctx.config` 访问组件，`ctx.on`/`ctx.off`/`ctx.emit` 订阅发布事件。事件用 `job` 参数定位具体 job（多 job 并发下 ctx 不持有 job）；per-job 静态数据与执行缓存写 `job.data`，运行时数据一律随事件传递。

### 请求-响应原语
事件的分发模式由 `agent/core/events.py` 的 `EVENT_MODES` 登记表决定（`parallel` 并发观察 / `serial` 顺序执行、首个非 None 短路并作为 emit 返回值）。未登记的事件按 `parallel` 分发并打 warning 日志。模式是事件契约的一部分：新事件应登记；监听方可从 `evt.mode` 读取当前模式。

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
`confirm_request`（serial）请求决策；confirm 插件内部推送确认到前端并注册一次性 `cmd_confirm` 监听等决策，超时返回 None 按 deny 处理。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件（`session` 插件 `session_root` 可配）
- 默认系统提示词从 `agent/AGENTS.md` 读取（`session` 插件 `system_prompt_path` 可配）
- 测试资源清单见 `tests/README.md`；场景包测试 fixture 在 `tests/fixtures/scene_pkg/`
- 一次性临时文件写入系统临时目录
- tool 的依赖与项目依赖隔离

### 架构规范

- 按架构分层，模块只能向下或同级引用（core 不能引用 plugins）
- **`loop.py` 是核心流程，不能随便修改**；功能扩展一律事件+插件，事件不够可新增，事件名要符合 loop 流程语义
- plugin 之间不能相互依赖，通过事件总线交流
- 组件间共享状态一律走事件（`Event.data` 或 `job.data`），不使用 `ctx.data` 动态属性
- 插件 `load(ctx, config)` 时用 `ctx.on(name, handler)` 注册，`unload` 释放自身资源
- handler 签名统一 `async def handler(ctx, evt: Event)`（不用的参数命名 `_`）
