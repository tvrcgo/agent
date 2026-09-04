# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API。

## 分层

**core（`agent/core/`）**——执行机制，不感知客户端协议，不依赖 plugin：

- `loop.py`：推理循环（Think→Act→Observe），每个会话一个 asyncio Task，只做核心调度（修改限制见「架构规范」）
- `events.py`：事件总线（`on`/`off`/`emit`），插件与组件间通信中枢
- `io.py`：I/O 端口契约——`InputMessage`（输入端口，loop 消费）/ `OutputMessage`（输出端口，由 MessagePlugin 从领域事件翻译构造）
- `model.py`：LLM 客户端与消息模型
- `tool.py`：工具基类与注册表，只提供执行机制，不做状态检查
- `config.py`：Pydantic 配置模型

**plugin（`agent/plugins/`）**——业务扩展，通过 `ctx.on`/`ctx.emit` 经事件总线交流，插件间不相互依赖：

- `message.py`：领域事件 → `OutputMessage` → `msg_output`（输出翻译唯一中枢）
- `session.py`：会话数据（存取、压缩、LLM 消息组装），不发 `msg_output`；注册 `reset_session` 供 `ctx.invoke` 调用
- `websocket.py` / `queue.py`：外部协议解析/序列化（信任边界），发 `msg_input`、消费 `msg_output`
- `subjob.py`：子任务递归（独立 session_id 经 `msg_input` 建独立 job，结果经 `job_end` 回填）；插件内定义 SubJob 工具入口（load 时注册进工具注册表，LLM 可直接调用；工具经 `ctx.invoke("subjob")` 与插件交互）；job 树由插件自身经 `job_start`/`job_end` 维护
- `confirm.py`：通用确认通道（`confirm_request` serial 请求用户决策，超时按 deny 处理）
- `tool_guard.py`：工具执行守卫（审查→确认→阻断，全在插件内闭环）
- `cmd_pause.py`：job 暂停/恢复（复用 `turn_start`/`tools_start` 挂载点阻塞；与守卫链顺序由 plugins 配置顺序决定）
- `cmd_cancel.py`：job 取消指令触发（`cmd_cancel` 经 `ctx.job(id)` 定位目标 job → `Task.cancel()`）；注册 `cancel_job` API（取消 + 等有序收尾，供其他插件调用）；`CancelledError` 有序收尾仍在 core
- `cmd_reset.py`：`/reset` 会话重置（`cmd_reset` 直接处理——经 `ctx.invoke("cancel_job", ...)` 取消该会话在飞 job 并等其有序收尾、`ctx.invoke("reset_session", ...)` 清空该会话历史并回执）
- `skill.py`：SKILL.md 指令模板加载，`turn_start` 追加提示段
- `logging.py`：领域事件日志
- `mcp.py`：从 agent-mcp 服务（`services/mcp/`）同步 MCP 工具，命名 `mcp_{server}_{tool}`

**tools（`agent/tools/`）**：内置工具，每目录一个 Tool 子类；**services（`services/`）**：外部服务独立部署，通过共享 Docker 网络通信，agent 不强依赖。

## 桌面客户端（`desktop/`）

Electron 桌面客户端，后台起一个进程运行 agent，前台实现交互逻辑。shell 视觉：侧边栏导航 + 卡片视图 + 对话框/toast；聊天会话区 ui-conversation 视觉：hero 空态、浮动输入胶囊栏、消息流、面包屑顶栏、右侧工具详情面板、底部坞任务面板、审批接管输入坞。顶栏仅标题（agent 启停在概览、语言/主题在设置）。进程模型：

- **主进程（`desktop/electron/main.cjs`）**：创建窗口（`autoHideMenuBar` + `Menu.setApplicationMenu(null)` 彻底移除系统菜单栏）、IPC（agent 启停/状态、配置读写、设置持久化、日志环形缓冲、会话文件存储、危险操作、本地文件打开）、启动时迁移清空旧扁平会话、退出清理
- **node 子进程（`desktop/agent-manager.cjs`）**：spawn Python agent（`python -m agent`，固定读默认 `config.yml`）、TCP 健康检查端口就绪、退出/崩溃清理
- **renderer（`desktop/src/`）**：Vite + React + TSX 构建，产物到 `desktop/dist/`。视图：`运行状态页`（状态+日志合并，默认页：上方紧凑状态栏显示 agent/ws 状态 + 启停按钮，下方滚动日志）、`聊天`（`components/chat/`：ConversationRoot 组装 HeroShell/InputBar/ChatView/MessageItem/ReasoningRow/AssistantMarkdown/ToolCallCard/DetailsPanel/TodoPanel/ApprovalPanel，**直连** `ws://127.0.0.1:<port>?session_id=...`，事件流聚合为会话节点树；无会话/空会话显示整屏 hero 空态，无会话时不显示标题栏）、`设置`（启动/Agent 配置/本地文件/外观主题与语言/危险操作）。复刻 shadcn 风格组件（`src/components/ui/`）+ harness 语义 token（`components/chat/tokens.css` 映射 `--dsw-*`/`--dsh-*`）

要点：
- 后端 agent 用 **`config.yml` 单一配置**（复用核心插件/工具，主模型 DeepSeek 云端，保留 websearch/cloud_file/mcp 外部依赖与 `todo` 工具；无独立桌面配置）
- 会话持久化：主进程写 userData `sessions/<id>.json`（**节点树格式** `{id,title,created_at,updated_at,nodes[]}`，nodes 含 user 节点与 assistant 回合 reasoning/tool/text blocks；旧扁平格式启动时清空）；配置存 userData `config.json`；设置（主题/语言/登录自启）存 userData `settings.json`；**打开应用即自动启动 agent**（固定行为，无开关）
- 侧边栏：仅常驻**会话列表区**（新建/切换/删除，`components/desktop-shell.tsx` 管理，每项仅在会话进行中显示左侧方形 loading 动画指示，由各会话自己的 WS 连接收到的 `status` 事件上报汇总，无后端广播、空闲不显示）与底部**设置**按钮 + 右侧窄**状态点**按钮（无文字，`StatusDot`，点击打开运行状态页）；无概览/聊天导航按钮（聊天经会话列表进入，运行状态页为默认页）
- 聊天交互：hero 空态输入自动建会话；Enter 发送（无会话先建）；`/pause` `/resume` `/cancel` 命令；confirm 事件 → 底部坞接管为 ApprovalPanel（允许/拒绝 → `cmd_confirm`）；工具调用 → ToolCallCard + 点击联动右侧 DetailsPanel 抽屉（Input/Output）；**TodoPanel 仅当模型调用 `todo` 工具**（`agent/tools/todo/`，返回完整清单 JSON）生成任务清单时展示（完成划线+绿勾/未完成灰虚线）；markdown 用 react-markdown + remark-gfm
- 会话保存：节点树写入 userData `sessions/<id>.json`；保存目标一律取 `currentIdRef.current`（实时会话）而非 WS 回调闭包捕获的 `currentId`，避免回复写到旧会话导致切换后内容丢失；切换会话时先固化并保存旧会话进行中（未完成）的回复
- 主题/语言：light/dark/system 三态 + en/zh 双语，存 settings.json 持久化，设置页修改经 `agent:state` 推送即时生效
- 启动命令：`cd desktop && npm run dev`（vite watch 自动重建 + Electron 热重载）；正式构建 `npm run build` 后 `npm start`；打包：`npm run pack`/`npm run dist`（electron-builder，需先 build renderer）
- `desktop/tests/` 自带测试：`test-agent-manager.cjs`（生命周期）、`test-ws-flow.cjs`/`test-ws-tool.cjs`（WS 链路）、`test-confirm-unit.py`（confirm 决策）、`test-storage.cjs`（存储语义）、`test-e2e-replica.py`（CDP 驱动完整 UI E2E 38 项）、`test-packaged.py`（打包版 CDP 9224 验证），结果文件输出到本目录

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
AgentLoop 的能力门面，贯穿 session/job 生命周期：`ctx.models`/`ctx.tools`/`ctx.config` 访问组件，`ctx.on`/`ctx.off`/`ctx.emit` 订阅发布事件，跨插件方法注册与调用见「架构规范」；`ctx.job(job_id)` 内置按 job id 获取 job（不走注册机制；job.id 由输入赋值，与会话 id 不默认一致）；领域操作由对应插件注册。事件用 `job` 参数定位具体 job（多 job 并发下 ctx 不持有 job）；per-job 静态数据与执行缓存写 `job.data`，运行时数据一律随事件传递。

### 请求-响应原语
事件的分发模式由 `agent/core/events.py` 的 `EVENT_MODES` 登记表决定（`parallel` 并发观察 / `serial` 顺序执行、首个非 None 短路并作为 emit 返回值 / `waterfall` 顺序流水线、非 None 返回值写入 evt.data 供下游读取、最终结果作为 emit 返回值，`on` 可带 `order` 排序 handler：按 `(order, 注册顺序)` 升序执行，负数队头、0 即注册顺序、正数队尾，serial/waterfall 均生效，parallel 并发不消费）。未登记的事件按 `parallel` 分发并打 warning 日志。模式是事件契约的一部分：新事件应登记；监听方可从 `evt.mode` 读取当前模式。emit 用 `asyncio.gather` 等待所有 handler 完成，单个 handler 异常被隔离记日志，不中断其他 handler 与 emit 本身。

### 消息排队
job 运行中收到的同 id chat 消息排队，下轮迭代前由 loop 消费为 steering 消息。

### I/O 通道
- `msg_input`：外部输入（协议插件解析后发出，内部递归产生的输入同通道），loop 消费后建 Job（`job.id` = `session_id`，loop 不感知 session 概念）
- `msg_output`：对外输出，由领域事件翻译后统一发出（输出翻译唯一中枢见「分层」）；插件主动推送时直接构造 `OutputMessage` 发 `msg_output`
- 消息体不承载内部调度概念（无 `job_id`）；输入端口类型用 Literal 注解固化合法取值

### 上下文压缩
存储无上限，冷启只加载尾部若干条。token 超阈值时独立 LLM 调用压缩旧消息，保留最近原文；内存完成，JSONL 保留完整历史。

### 插件生命周期
`agent_start` → `job_start` → `llm_start` → `llm_end` → `tools_start` / `tool_start` → 工具执行 → `tool_end` → `tools_end` → 循环 → `job_end` / `job_error` / `agent_stop`。`job_end` 是 job 结束的唯一钩子（所有终止路径必发），插件资源清理一律监听 `job_end`。工具未执行（被拒绝/截断 fail）走 `tool_error`。`cmd_<action>` 钩子处理 UI 操作（指令 `data` 可带 `session_id` 路由到目标会话）。

### 工具执行阻断
core 只提供执行机制，阻断实现交给 plugin：`execute_batch`（core）纯执行；守卫插件在 `tools_start` 审查→确认→deny 则从 `tool_calls` 剔除并 emit `tool_error`，被阻断的调用不进入 `execute_batch`。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话历史：每会话一个 JSONL 文件（存储路径与系统提示词路径见「场景扩展」）
- 测试资源清单见 `tests/README.md`；场景包测试 fixture 在 `tests/fixtures/scene_pkg/`
- 一次性临时文件写入系统临时目录
- tool 的依赖与项目依赖隔离

### 架构规范

- 按架构分层，模块只能向下或同级引用（core 不能引用 plugins）
- **`loop.py` 是核心流程，不能随便修改**；功能扩展一律事件+插件，事件不够可新增，事件名要符合 loop 流程语义
- plugin 之间不能相互依赖（不 import 对方模块）；对外能力经 `ctx.register(name, fn)` 注册进 `ctx._apis`（重复注册抛 `ValueError`），经 `ctx.invoke(name, **args)` gRPC 风格调用，**禁止经 `ctx._self` 私有对象访问/操作其他插件实例**；注册不触碰 ctx 本体，默认成员（property/方法）天然不可被覆盖
- 组件间共享状态一律走事件（`Event.data` 或 `job.data`），不使用 `ctx.data` 动态属性；`ctx.register` 仅用于插件对外方法注册，不承载运行时状态
- 插件 `load(ctx, config)` 时用 `ctx.on(name, handler)` 注册事件、`ctx.register(name, fn)` 注册对外方法，`unload` 释放自身资源
- handler 签名统一 `async def handler(ctx, evt: Event)`（不用的参数命名 `_`）
