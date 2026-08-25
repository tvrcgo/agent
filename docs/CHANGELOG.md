# CHANGELOG
> 新内容放前面，同一天内容合并；版本号和PR ID、Issue ID没有可省略

## [unreleased] - 2026-08-25

### 核心摘要
新增 `/reset` 会话重置指令，插件 `cmd-reset`（`plugins/cmd_reset.py`）承载，与 pause/cancel 同范式：接收 `cmd_reset` 命令直接处理，不新增领域事件；`pause`/`cancel` 插件同步重命名为 `cmd_pause`/`cmd_cancel`（对齐 cmd_ 前缀）。配套新增 **ctx 插件方法注册机制（gRPC 风格）**：`ctx.register(name, fn)`（如 `ctx.register("reset_session", self.reset)`）把对外方法注册进 `ctx._apis`，`ctx.invoke(name, **args)` 跨插件调用；注册不触碰 ctx 本体，默认成员天然不可被覆盖；**禁止经 `ctx._self` 私有对象访问/操作其他插件实例**。loop 自身注册 `cancel_job`/`drop_queued_messages`，session 插件注册 `reset_session`；cmd-reset 的三个操作（取消在飞 job、丢弃排队消息、清空会话历史）统一经 `ctx.invoke` 调用，同一守卫写法。

### 变更
- 新增：`core/loop.py` `AgentContext.register(name, fn)` + `invoke(name, **args)`——name 即注册键，收进 `_apis` 扁平 dict，重复注册抛 `ValueError`，未注册调用抛 `KeyError`；`AgentContext.job(job_id)` 内置按 job id 获取 job（不走注册机制，job.id 由输入赋值、与会话 id 不默认一致）
- 新增：`plugins/session.py` 注册 `reset_session`（`ctx.register("reset_session", self.reset)`，清内存态 + 删 JSONL，后续同 id 输入冷启动为全新会话）
- 新增：`plugins/cmd_cancel.py` 注册 `cancel_job` API（经 `ctx.job(job_id)` 定位 job → `Task.cancel()` + 等有序收尾）；`cmd_cancel` 指令 handler 同步改用 `ctx.job(id)`，不再直接访问 `ctx._self._jobs`
- 变更：`plugins/subjob.py` 不再访问 loop 私有 `_jobs`——job 树由插件自身经 `job_start`/`job_end` 维护 `_jobs` 快照；`ctx.subjob = ...` 自由挂载改为 `ctx.register("subjob", ...)`；subjob 工具经 `ctx.invoke("subjob", ...)` 调用（不再 `getattr(ctx, "subjob")`）
- 新增：`plugins/cmd_reset.py` `/reset` 指令——`cmd_reset` 经 `ctx.invoke("cancel_job", ...)` 取消目标会话在飞 job 并等其有序收尾（排队消息随 job 收尾由 loop 清理，无需单独 API）、`ctx.invoke("reset_session", ...)` 清空会话历史（统一未注册 warning 兜底）、回执 `Session reset`；支持 `session_id` 路由（可重置子 job 会话）
- 变更：`plugins/pause.py`/`plugins/cancel.py` 重命名为 `cmd_pause.py`/`cmd_cancel.py`，插件名同步为 `cmd-pause`/`cmd-cancel`（类名 `CmdPausePlugin`/`CmdCancelPlugin`）
- 变更：`config.yml` plugins 追加 `- cmd_pause`、`- cmd_cancel`、`- cmd_reset`
- 变更：`playground/index.html` 输入框 placeholder 补充 `/reset`
- 测试：新增 `tests/scripts/test_reset.py` 6 用例（ctx.register/invoke 机制 + 仅显式方法 + KeyError + 重复注册 + 默认成员隔离、历史清空 + 重置后全新上下文、在飞 job 取消、session_id 路由、未知会话 no-op + 幂等、未加载 session 插件时 reset 不崩），6/6 通过；pause/cancel 插件重命名后 test_pause/test_cancel/test_e2e 同步更新；存量回归全绿（cancel 3/3、pause 7/7、e2e 11/11、subjob 6/6、followup 7/7、scene_registry 9/9）
- 文档：`AGENTS.md` 补充 ctx 插件方法注册机制（AgentContext 段 + 架构规范 + plugin 清单）；`tests/README.md` 补充 test_reset.py

### 上下文
- 机制：插件间调用从「经 `ctx._self` 私有对象操作实例」收敛为「`ctx.register(name, fn)` → `ctx.invoke(name, **args)`」gRPC 风格；注册只挂显式方法、全部收进 `_apis`，不承载运行时状态（状态仍走事件/`job.data`）
- 取消语义：reset 先 cancel 在飞 job 并 await 其 `job_end` 收尾，保证重置后会话干净、不会被并发收尾路径污染；不清理 workspace 工作目录（会话重置 ≠ 文件清理，用户已确认）
- 影响范围：`core/loop.py`、`plugins/cmd_reset.py`、`plugins/session.py`、`config.yml`、`playground/index.html`、`tests/`、`docs/`、`AGENTS.md`

## [unreleased] - 2026-08-24

### 核心摘要
Job 控制指令下沉为独立插件并新增暂停/恢复能力：`pause`（暂停/恢复）与 `cancel`（取消）两个插件承载 `cmd_*` 指令，core 只保留执行机制（`CancelledError` 有序收尾），不新增事件、不改 `EVENT_MODES`。pause 复用 `turn_start`/`tools_start` 作为挂载点，与 tool_guard 守卫链的顺序由 plugins 配置顺序决定；门闩为插件私有 `_gates[job.id]`（不写 job.data，无覆盖风险），生命周期与 job 同步。新增真实 E2E 验证（本地起 agent + DeepSeek 驱动 WS 链路），并修复 `test_ws.py` 两个存量测试 bug（heartbeat 超时逻辑、persistence 平台依赖）。

### 变更
- 新增：`plugins/pause.py` job 暂停/恢复——复用 `turn_start`/`tools_start` 挂载点阻塞（软暂停：在飞 LLM/工具自然完成后在下一安全点生效），`cmd_pause`/`cmd_resume` 支持 `session_id` 路由（可暂停子 job），状态经 `msg_output` 直发 `paused`/`running`
- 新增：`plugins/cancel.py` job 取消指令触发——`cmd_cancel` 经 `ctx._self._jobs` 定位目标 task → `Task.cancel()`，支持 `session_id` 路由；`CancelledError` 有序收尾保留在 core
- 变更：`core/loop.py` 移除 `_on_command_cancel` 与注册，`start()` 仅注册 `msg_input`（`cmd_*` 全由插件承载）
- 变更：`config.yml` plugins 追加 `- pause`、`- cancel`（pause 在 tool_guard 前：先暂停后审查）
- 变更：`plugins/subjob.py` 监听 `turn_start`/`tools_start` 刷新 job 树（暂停中的子 job 在 UI 显示 paused）
- 测试：新增 `tests/scripts/test_pause.py` 7 用例（挂载点阻塞、serial 顺序双向、cancel 中断、job_end 清理、session_id 路由）、`tests/scripts/test_cancel.py` 3 用例（运行中取消、session_id 路由、未知 job no-op）
- 测试：`tests/scripts/test_ws.py` 新增 pause_resume / pause_noop / cancel_while_paused 3 个真实 WS E2E 场景；修复两个存量 bug——heartbeat 场景超时逻辑（`try/except` 包住整个 for 导致首次 2s 超时即退出，改为每次迭代独立超时）、persistence 场景平台依赖（`ls`/`stat` → `os.path.getsize`）
- 测试：真实 E2E 通过（本地 agent + DeepSeek：pause→resume→done、pause→cancel→cancelled、no-op；test_ws 16 场景、test_pause 7/7、test_cancel 3/3、test_e2e 11/11、test_subjob 6/6、test_loop_followup 7/7、test_scene_registry 9/9）
- 文档：`AGENTS.md` plugin 清单补充 pause.py/cancel.py；`docs/job-pause-resume.md` 机制设计

### 上下文
- 遵循「功能扩展走事件+插件、core 只承载执行机制」：`cmd_*` 指令全部下沉插件，core `start()` 仅注册 `msg_input`
- pause 复用现有事件（不新增事件、不改 `EVENT_MODES`）；`tools_start` 为 serial，pause 与 tool_guard 同挂该点，顺序由 plugins 配置顺序决定，pause handler 始终返回 None 不短路守卫链
- 门闩语义：`asyncio.Event` 初始 set（放行），`cmd_pause` → clear（阻塞），`cmd_resume` → set；`Event.set` 粘性（set 后 wait 立即返回）故暂停=clear
- 影响范围：`core/loop.py`、`plugins/pause.py`、`plugins/cancel.py`、`plugins/subjob.py`、`config.yml`、`tests/`、`docs/`、`AGENTS.md`

## [unreleased] - 2026-08-21

### 核心摘要
基座场景化：agent 作为**基座**支持垂直场景扩展，场景与基座**分层**。registry 模块路径解析兼容完整模块路径（名称含 `.` 直接 import，否则回退内置前缀，存量 config.yml 零改动）；场景 = 独立目录（plugins/tools/AGENTS.md/skills），COPY 到容器 `/app` 下即可运行，不侵入 `agent/` 包，每场景一个容器 `FROM agent-base`。场景能力按配置项显式声明（工具写 `tools` 列表、资产写文件路径），无全局开关。新增场景扩展说明文档。

### 变更
- 变更：`core/plugin.py` `PluginRegistry.load_modules` 名称含 `.` 按完整模块路径加载，否则回退 `agent.plugins.{name}`
- 变更：`core/tool.py` `ToolRegistry.load_modules` 同上；`_check_deps` 按模块实际位置（内置目录 / 外部模块 `find_spec` 定位）找 requirements.txt，缺失依赖报错提示重建镜像（依赖只在镜像构建时安装，运行时不做安装）；requirements.txt 读取指定 UTF-8
- 修复：`core/tool.py` 存量 bug——`importlib.metadata` 异常名写错（`PackageNotFoundException` → `PackageNotFoundError`），真实缺依赖时会抛 AttributeError 掩盖 DependencyError
- 变更：`plugins/session.py` 路径配置化——`session_root`（默认 `./data/sessions`）、`workspace_root`（默认 `./workspace`，按 session id 建工作子目录）；config 读取指定 UTF-8
- 新增：`docs/scene-extension.md` 场景扩展说明（机制介绍，无实际代码）
- 测试：新增 `tests/scripts/test_scene_registry.py` 9 用例（场景工具/插件加载、内置回退、模块缺失跳过、场景依赖定位、session 路径配置化）+ `tests/fixtures/scene_pkg/` 场景 fixture；存量回归全绿（e2e 11/11、follow-up 7/7、subjob 6/6、protocol 3/3）

### 上下文
- 垂直场景形态：每场景独立容器运行；agent 提供基座按场景扩展；场景间不共享工具；场景是独立目录（不打包、与基座分层、不侵入 agent/ 包），场景能力经 config.yml 显式声明，基座包名不变走内部私有 index
- `AgentLoop` 自包含（自有 EventBus/ToolRegistry/PluginRegistry），每容器一实例天然成立，`loop.py` 与 `EVENT_MODES` 契约零改动
- 影响范围：`core/plugin.py`、`core/tool.py`、`plugins/session.py`、`docs/scene-extension.md`、`tests/`

## [unreleased] - 2026-08-20

### 核心摘要
事件系统引入**分发模式登记表**：分发模式（parallel / serial / fire 预留）由 `core/events.py` 的 `EVENT_MODES` 集中登记，事件契约化；API 保持单一 `ctx.emit`，监听方经 `Event.mode` 感知模式。`req:` 前缀废弃，confirm 通道迁移为 serial 事件 `confirm_request`。websocket 输出改 per-session 发送队列，解除慢客户端对流式推理的反压。

### 变更
- 变更：`core/events.py` 新增 `DispatchMode` 枚举与 `EVENT_MODES` 登记表（存量事件全量登记；`tools_start`/`confirm_request` 为 serial，其余 parallel）；`Event` 增 `mode` 字段（分发时注入），删除 `Request` 类与 `req:` 前缀分支
- 变更：`EventBus.emit` 按登记表分发——serial 顺序执行、首个非 None 短路并作为返回值、异常记日志继续；parallel 并发观察、异常隔离；未登记事件（`cmd_<action>` 除外）按 parallel 分发并打 warning；fire 登记条目抛 NotImplementedError 预留
- 变更：`loop.py` `AgentContext.emit` 移除 `timeout` 参数
- 变更：`plugins/confirm.py` 监听 `confirm_request`（serial），第二层 `req:confirm_ui` 自请求结构内部化为 `_ask_ui`（future + `wait_for` 超时，一次性 `cmd_confirm` 监听 finally 卸载）
- 变更：`plugins/tool_guard.py` 确认请求改发 `confirm_request`
- 变更：`plugins/websocket.py` `ClientSession` 改为 per-session `asyncio.Queue` + 单 writer task：`_on_output` 入队即返回（反压解除），confirm 输出入队后 `flush()` 保证送达，heartbeat 同队（消除并发 send），`agent_stop` 前全 session flush
- 修复：`plugins/session.py` 存 `tool_calls` 快照（`list()` 拷贝）——E2E 发现 tool_guard 原地剔除共享列表导致 assistant 消息 tool_calls 变空、deny 后第二轮 LLM 请求 400
- 修复：`core/model.py` `chat_stream` 错误处理——错误 body 在流上下文内 `aread()` 读取（原先访问流式响应 `.text` 抛 ResponseNotRead，掩盖真实 400 信息）
- 修复：`plugins/session.py` 监听 `job_end` 清理内存态会话尾部孤儿 tool_calls——E2E 发现 /cancel 打断工具执行后，assistant 消息已带 tool_calls 但 tool 结果缺失，下一次消息触发 DeepSeek 400（insufficient tool messages）；冷启动路径由 `_load_session` 兜底，热内存路径由 `_on_job_end` 及时清理
- 文档：`AGENTS.md` 请求-响应原语段、Confirm Plugin 段、plugin 清单同步更新
- 测试：`tests/cases/unit-plugins.md` 更新分发模式原语与 confirm 用例；分发模式原语、websocket 队列（FIFO/反压/close/heartbeat）验证通过，follow-up（7/7）与 subjob（6/6）回归全绿；playground E2E 全链路验证（流式渲染/confirm 批准拒绝/会话持久化/多轮上下文）通过
- 测试：`tests/scripts/test_e2e.py` 扩展至 11 场景（fake LLM 无网络依赖）——echo 工具链路、subjob 递归聚合、流式、tool_guard+confirm（approve/deny）、取消、截断（length）、max_iterations、LLM 异常、max_concurrent 排队、follow-up steering 多轮、取消后孤儿 tool_calls 清理；新增 `tests/cases/e2e-playground.md`（playground 11 场景：会话连接/流式问答/工具渲染/confirm 批准拒绝/持久化/多轮上下文/cancel/compress/subjob 任务树/孤儿清理回归），全部通过

### 上下文
- 借鉴 cordis（cordiverse/cordis）的分发模式设计，但按项目哲学收敛：API 单一 `emit`，模式是集中登记的事件元数据而非名字前缀或多 API；fire 与 waterfall 均以"无用例不实现"原则仅留枚举占位
- 反压修复落点选在 websocket I/O 边界（插件内部写循环），不引入 fire 事件与 loop flush——保住 loop 状态机对流程与顺序的完全控制


### 核心摘要
I/O 收敛为**端口契约**：`core/io.py` 是 core 的 I/O 端口（`InputMessage` 输入端口 + `OutputMessage` 输出端口），loop 直接消费 `msg_input`，插件/MessagePlugin 直接构造端口类型发 `msg_output`，无翻译层。真正客户端协议（JSON envelope 线格式）只在 websocket/queue。插件间零相互依赖（发消息 import core 端口，不 import message plugin）。

### 变更
- 新增：`agent/core/io.py`（端口契约）——`InputMessage`/`OutputMessage`/`OutputType` 定义，`InputMessage.type` 用 `Literal["chat", "command"]` 注解
- 变更：`loop.py` 直接监听 `msg_input` 消费 `InputMessage`（Job.input 类型），无翻译层、无 AgentInput
- 变更：MessagePlugin 回归纯输出翻译（领域事件 → `OutputMessage`），删除输入翻译链与 `agent_output` 翻译
- 变更：websocket/queue 输入侧构造 `InputMessage` 发 `msg_input`（外部协议解析边界）；输出侧 asdict 序列化
- 变更：ConfirmPlugin/SubJobPlugin 直接构造 `OutputMessage` 发 `msg_output`（import core 端口，插件间零依赖）
- 变更：`agent/core/__init__.py` 导出 `InputMessage`/`OutputMessage`
- 测试：三个测试脚本回归 msg_input/InputMessage 直连模式，全绿

### 上下文
- 本轮探索了"msg_input→agent_input 翻译层 + agent_output 统一事件"方向后回退：翻译层制造了两态切换（每段一端无约束），且 agent_input/agent_output 与 msg_input/msg_output 字段重复无本质区别。最终按端口契约收敛：端口在 core（插件依赖 core 合法），信任边界在外部协议解析处，校验与类型同源（注解即 schema）。

### 核心摘要
job 错误（达 `max_iterations`、异常）输出从 `status(content=error)` 改为独立 `error` 类型事件：MessagePlugin 的 `_on_job_end`（`job.status=="error"`）与 `_on_job_error` 统一发出 `OutputMessage(type="error", content=<reason>, data={"reason": <reason>})`，与 websocket 协议错误（code/message）共用 `error` 类型。playground 前端在收到达上限错误事件后弹「已达到最大迭代次数，是否继续运行？」确认框；用户选择继续则向同一 session 重发 chat 触发新 job（会话冷加载延续上下文），选择停止则保持收尾。可重复询问。

### 变更
- 变更：`agent/plugins/message.py` `_on_job_end` 的 `job.status=="error"` 分支与 `_on_job_error` 输出从 `status` 改为 `error` 类型；`content` 与 `data.reason` 承载错误原因（max_iterations→"Reached maximum iterations"，异常→异常文本）
- 变更：`playground/index.html` `case 'error'` 扩展为同时处理协议错误（code/message）与 job 错误（reason）；达上限错误事件（`data.reason="Reached maximum iterations"`）弹确认框，确认后向同一 session 重发 `chat`（"请继续"）触发新 job，并追加「继续运行」user 消息到会话历史
- 变更：`_maxIterPrompting` 标记防止同一 session 弹窗期间重复弹（每次达上限可重新询问）
- 测试：`test_ws.py`/`test_protocol.py`/`test_mcp.py` 终态判断与 error 断言兼容 `type="error"`（原来错误从 status 改到 error 类型）

### 上下文
- 长任务常在一个会话内跑不完 `max_iterations`（默认 100）轮，此前直接以错误收尾，用户无法继续
- 达上限 → `job_end(reason=max_iterations)` → MessagePlugin 翻译 `error` 类型；会话按 session_id 持久化，新 job 冷加载历史延续上下文
- 影响范围：`agent/plugins/message.py`（错误输出类型）、`playground/index.html`（error 分支）、集成测试脚本

## [unreleased] - 2026-08-18

### 核心摘要
输出消息统一由 MessagePlugin 翻译发出：loop/core/tool 只 emit 领域事件，不感知输出细节。I/O 消息模型（`InputMessage`/`OutputMessage`）独立为 `core/io.py` 共享模块，插件间零相互依赖。`Event{name, job, data, request}` 扁平结构，`input`/`output` 均经 `data` 传递。内容类输出统一 `thinking`/`message`（流式用 `stream` 属性），工具调用/结果独立 `tool_call`/`tool_result` 类型。`Job` 不持有 `session_id`，`job.id` 即 session 身份，loop 不感知 session 概念。subjob 递归复用 msg_input/msg_output：子任务独立 session_id 经 `msg_input` 创建独立 job；父子关联由 SubJobPlugin 自维护（以 `job.id` 为 key）。

### 变更
- 新增：`agent/plugins/message.py`（MessagePlugin）——监听领域事件（llm_start/llm_chunk/llm_end/tools_start/tool_start/tool_end/job_end/job_error）统一构造并发出 `msg_output`；`thinking`/`message`/`status`/`tool_call`/`tool_result` 全由此翻译
- 新增：`agent/core/io.py`——`InputMessage`/`OutputMessage`/`OutputType` 独立共享模块，供 core 与各插件引用，避免插件间依赖
- 变更：`Job` 移除 `session_id` 字段（`job.id` 即 session 身份，loop 不感知 session）；各输出点 `session_id` 统一取 `job.id`；subjob 父子映射 `_pending`/`_depth`/`_parent` 以 `job.id` 为 key
- 变更：`loop.py` 移除所有 `msg_output` 发出——流式 chunk 经 on_chunk 转发 `llm_chunk` 事件，非流式 thinking/message 与终态 status 由 MessagePlugin 从 `llm_end`/`tools_start`/`job_end`/`job_error` 翻译
- 变更：`tool.py` 工具执行只 emit `tool_start`/`tool_end`（纯执行，无状态检查），`tool_call`/`tool_result` 输出由 MessagePlugin 翻译
- 变更：`OutputMessage.type` 用 Literal 固化类型集合；`tool_call`/`tool_result` 的 `content` 承载展示文本、`data` 承载 id/tool/arguments/error；`confirm` 类型精简
- 变更：日志归属——有现成事件承载的日志（job_start/llm_start/llm_end/tool_start/tool_end/job_end/job_error）由 LoggingPlugin 输出；无事件承载的调度日志（queued/truncated/dequeued）直接在 loop 写 logger，不新增事件
- 变更：`InputMessage`/`OutputMessage` 移除 `job_id` 字段（消息规范不泄漏内部调度概念）
- 变更：websocket/queue 发 `msg_input` 只带 `input`，不构造伪造 `Job`
- 变更：`SubJobPlugin` 子任务用独立 session_id（`f"{parent.id}:{uuid}"`）；`_on_job_end` 按 `job.id` 匹配回填结果；`_send_jobs` 沿父子映射找 root session 收集整棵 job 树
- 测试：`test_loop_followup.py` 新增 msg_input 构造验证 + subjob 独立 session 集成验证；`test_e2e.py`/`test_ws.py`/`test_protocol.py`/`test_mcp.py` 更新 tool_call/tool_result 断言为顶层类型
- 文档：`AGENTS.md` 更新 I/O 消息规范、MessagePlugin 输出归属、Event 结构与 Job 树说明
- 前端：`playground/index.html` 修复 thinking/message 渲染顺序与切换会话后的消息持久化

## [unreleased] - 2026-08-17

### 核心摘要
修复 follow-up 多轮文本轮输出覆盖，输出外发收敛为**增量事件外发**，并以 `OutputMessage`/`InputMessage` 定型 loop 状态机的输入输出规范：
1. **迁移**：`job.output.content` 移除，文本统一放 `job.turn.content`（每轮重建、天然清零、工具轮为空；语义为最后一个 turn 的输出）。
2. **逐轮推送**：SessionPlugin 的 `turn_end` 在非流式下每个有文本的 turn 即推送独立 `message` 输出，保留先后关系；流式下跳过（`stream` 已实时渲染）。
3. **职责收敛**：`job_end` 只发终态 status，不再兜底推送 message（删除 `_sent_turn_messages` 集合）。
4. **增量外发**：`msg_output` 事件载荷 `output=<OutputMessage>` 直接承载单个输出消息，移除 `job.output` 缓冲锚点与消费者清空摘取逻辑，消除多消费者并发抢同一缓冲的竞态。
5. **I/O 规范**：`OutputMessage`（输出）与 `InputMessage`（输入）对称，构成 loop 状态机的输入输出消息规范，均含 `session_id` 自包含归属。

subjob 结果回传改读 `job.turn.content`（最后一个 turn 的输出），queue payload 只含单事件。

### 变更
- 新增：`OutputMessage` 输出消息类型（`type`/`content`/`data`/`session_id`，与 `InputMessage` 对应），导出至 `agent.core`；移除旧 `Job.output` 锚点字段及相关恒真守卫
- 变更：`msg_output` 事件载荷改为 `output` 字段携带单个 `OutputMessage`（不用 `event` 名——`ctx.emit` 首参就叫 event）；websocket/queue 消费者直接发 `evt.data["output"]`
- 变更：所有产出点（session / confirm / subjob / loop 流式 on_chunk）构造 `OutputMessage` 时带 `session_id=job.session_id`；websocket 错误响应带当前连接 `session_id`；消费者以 `output.session_id` 为准、`job.session_id` 兜底
- 变更：`queue.py` 输出 payload 只含单事件 `events`
- 测试：`tests/scripts/test_loop_followup.py` 改为监听 `msg_output` 收集 `output` 断言，并断言 `session_id` 填充
- 文档：`tests/README.md` 登记新测试

## [unreleased] - 2026-08-15

### 核心摘要
技能（skill）从 core 组件变为独立插件：`agent/core/skill.py` 整体迁入 `agent/plugins/skill.py`（`SkillPlugin`），core 不再感知技能。新增 `Turn.prompts` 通用提示段收集机制：插件在 `turn_start` 追加提示段，SessionPlugin 在 `llm_start` 将每条提示段各成一条 SystemMessage 插在系统提示词之后（session 不感知 skill 语义，利用事件先后顺序天然保证正确性，规避广播 handler 并发无序的竞态）。

### 变更
- 新增：`skill.py` 插件——`Skill`/`SkillRegistry` 迁入，`load(ctx, config)` 读 `dirs` 配置（默认 `agent/skills`、`skills`）加载 SKILL.md，注册 `turn_start` 追加提示段到 `job.turn.prompts`
- 新增：`Turn.prompts` 通用提示段收集字段（每轮重建），供任意插件在 `turn_start` 追加、session 在 `llm_start` 统一合并拼入单条 system message（保证模型对多条 system 的兼容）
- 移除：`agent/core/skill.py`；`AgentLoop` 删除 `SkillRegistry` 实例与 `load_skills` 调用
- 变更：SessionPlugin 当前时间注入与提示段同机制——`turn_start` 追加 `Current time` 提示段，`llm_start` 与基础系统提示词合并组装
- 变更：`config.yml` plugins 增加 `skill`
- 文档：`tests/cases/unit-mcp.md` 整篇更新为当前 `AgentLoop(config)` 签名，新增 `tests/cases/unit-skill.md`
- 文档：`AGENTS.md` 技能描述改为插件

## [unreleased] - 2026-08-12

### 核心摘要
工具执行守卫拆分为两个独立插件：`tool_guard`（审查+阻断）和 `confirm`（通用确认通道）。审计→确认全在 plugin 内闭环，loop 不感知。新增**请求-响应原语**（API 复用 `ctx.emit`/`on`，请求事件事件名带 `req:` 前缀）：`Request` 对象（`agent/core/events.py`）作为 `Event.request` 正式字段随事件传递，响应方**隐式返回**非 None 结果、由总线自动 `req.done(result)` 回填，结果封装在 Request 内部、不落 `job.data`。core 只做执行机制：`execute_batch` 纯执行、不做状态检查；阻断实现全在 `tool_guard`（审查→确认→剔除调用→emit `tool_error`）。

### 变更
- 新增：`tool_guard.py` 插件——审查清单 + flash LLM 风险判断，safe 放行 / dangerous 经 `req:request_confirm` 请求事件委托确认，deny 则从 `tool_calls` 剔除调用并 emit `tool_error`（阻断处理全在 plugin）
- 新增：`confirm.py` 插件——两层请求-响应各持各的 req：第一层隐式返回 `request_confirm`（总线自动 done），第二层发 `confirm_ui` 并注册一次性 `cmd_confirm` 监听（闭包持有 req），收到决策显式 `req.done(...)` 后 `return await req.wait(...)`
- 新增：`Request` 对象（`wait`/`done`）并入 `agent/core/events.py`，请求-响应与广播共享同一 EventBus，仅使用方式区分
- 变更：`req:` 前缀判断、请求组合逻辑下沉 `EventBus.emit`，`ctx.emit` 纯转发，`emit` 时校验请求事件只允许单个 handler
- 变更：`execute_batch` 回归纯执行——并行执行传入调用并各自 emit `tool_start`/`tool_end`，不做任何状态检查
- 新增：`tool_error` 事件——工具未执行的异常情形（被守卫拒绝 / 被截断 fail）以此事件记录失败结果；`tool_start`/`tool_end` 只用于真实执行的生命周期，session 增加 `_on_tool_error` 持久化
- 变更：`cmd_cancel` 改为取消**单个 job**（`Task.cancel()` 注入 `CancelledError`），`_run_loop` 捕获后置 `job.status = "cancelled"` 并 emit `job_end` 收尾；删除 loop 中散落的 `job.status` 检查锚点
- 变更：`cmd_<action>` 事件携带 `job.input.data`（修复 confirm_id/decision 丢失）
- 变更：6 个权限工具纯执行化——移除 `_request_confirm`/`force`/确认分支，保留敏感路径硬拦截
- 新增：`loop.py` 中 `tools_start` emit 时带 `tool_calls`，ToolGuardPlugin 据此审计
- 修复：`playground` 前端新增 `confirm_request` 消息分支，移除旧 `request_confirmation` 工具分支
- 修复：`confirm` 闭包 `on_cmd` 签名补齐 `(ctx, evt)` 双参（此前单参导致 cmd_confirm 到达时抛 TypeError，决策无法回填）
- 修复：`confirm` 闭包按 `job.id` 匹配（此前 `evt2.job is job` 对象同一性比较，cmd_confirm 携带新构造 Job 恒不匹配，approve/deny 只能靠超时兜底）
- 移除：`agent/tools/confirm` 旧确认工具（已被 ConfirmPlugin + tool_guard 取代）
- 文档：`AGENTS.md` 更新 request 原语、Confirm + Tool Guard 插件说明

## [unreleased] - 2026-08-10

### 核心摘要
事件机制独立为 agent 基础消息总线（EventBus）。`PluginRegistry` 退化为纯插件容器；插件/工具/外部组件统一通过 `ctx.on`/`ctx.emit` 交流；`ctx.data` 动态属性废弃；tools/plugins/skills 加载移入 `loop.start()`。

### 变更
- 新增：`agent/core/events.py` 定义 `Event{name, job, data}` 与 `EventBus`（`on`/`off`/`emit` 同步顺序分发）
- 新增：`AgentContext` 持有总线，暴露 `on`/`off`/`emit`；`emit(name, job=None, **data)` 返回 Event 支持订阅者可变回填
- 重构：`PluginRegistry` 删除 `on`/`emit`/`_handlers`，仅保留插件 load/unload；`plugin.load(ctx, config)` 用 `ctx.on` 注册
- 重构：handler 签名统一为 `async def handler(ctx, event: Event)`，从 `event.job`/`event.data` 取值
- 重构：`AgentLoop` 持有 EventBus 并加载 tools/skills/plugins 于 `start()` 内；`__main__.py` 只组装 models 和 config
- 重构：`ModelRegistry` 初始化移入 `AgentLoop`（`__init__(config)` 内部创建），`models.close()` 归入 `stop()`；`__main__.py` 退化为只创建 loop + start/stop
- 移除：`ctx.data` 跨组件共享通道；work_dir 改存 `job.data`，confirm 决策改经事件 `data` 回填
- 重构：`tool.py` 的 `tool_start`/`tool_end` 通过事件 `data` 传 `tool_call`/`result`/`error`，confirm 拒绝经 `abort` 标志短路
- 重构：6 个权限工具 `_request_confirm` 弃用 `ctx.data`，改为 `emit("request_confirm")` 后读返回事件决策
- 移除：`EventBus` 按 owner 分组卸载机制（`_owners`/`unload(owner)`/`ctx.unload`），当前全量生命周期下不必要，避免过度设计
- 新增：`Event.__getattr__`/`__setattr__` 代理到 `data`，事件载荷读写对称（`evt.tool_call` / `evt.abort = True`）
- 重构：`AgentContext.models`/`tools` 改为经 `_self` 代理的 property，消除构造循环依赖与占位回填
- 重构：`ToolRegistry` 与 `PluginRegistry` 对齐，`__init__(ctx)` 注入；`execute_batch(tool_calls, job)` 去掉外部 ctx
- 重构：`job.data` 收敛为静态/cache（`work_dir`/`messages`）；`response`/`result`/`reason`/`error` 运行时数据全部改经事件传递
- 重构：`job_end` 事件带 `reason`（done/max_iterations），`job_error` 事件带 `error`；session 新增 `_on_job_error` 补发异常 error status
- 修复：`queue.py` 插件 `load` 签名漏改为 `load(ctx, config)`

### 上下文
- 此前 `PluginRegistry` 一身二任（插件容器+事件分发），插件需逐事件订阅大量钩子，工具/外部组件无法直接交流
- 解耦后事件成为 agent 唯一通信中枢，任何组件都能经 `ctx.on`/`ctx.emit` 交流；`ctx.data` 动态属性存在顺序耦合隐患，一并废弃
- 影响范围：`agent/core/events.py`（新）、`agent/core/loop.py`、`agent/core/plugin.py`、`agent/core/tool.py`、`agent/__main__.py`、7 个插件、6 个权限工具

### 核心摘要
Tool 参数验证 + 并行执行。验证和批量执行逻辑收归 ToolRegistry，loop 精简为核心调度。

### 变更
- 新增：模块级 `validate_arguments()` 基于 JSON Schema 做参数验证（类型检查/必填/默认值/枚举/范围）
- 新增：`ToolRegistry.execute_batch()` 批量执行工具，验证+执行封装在内部，同一轮 tool call 全部并行
- 新增：`ToolRegistry.fail_tool_call()` 记录失败工具调用
- 新增：`ModelResponse.finish_reason` 字段，支持截断保护
- 重构：`loop.py` 删除 `_validate_tool_calls`、`_execute_tools_parallel`、`_execute_tool`、`_fail_tool_call` 四个方法，工具相关逻辑收归 ToolRegistry

### 上下文
- 方案详见 `plan-tool-validation-parallel.md`
- 影响范围：`agent/core/tool.py`、`agent/core/loop.py`、`agent/core/model.py`

### 核心摘要
引入 LLM 消息类型体系（`SystemMessage`/`UserMessage`/`AssistantMessage`/`ToolResult`），统一消息定义到 `model.py`，消除重复类型定义。

### 变更
- 新增：`model.py` 中定义 `ToolCall`、`SystemMessage`、`UserMessage`、`AssistantMessage`、`ToolResult` 消息类型
- 删除：`core/message.py`（已合并到 `model.py`）
- 删除：`loop.py` 中 `ToolCallItem`、`ToolResultItem` 重复定义，改用 `model.ToolCall`、`model.ToolResult`
- 删除：`model.py` 中旧的 `Message` dataclass（session 内部改用 `_SessionState` 直接存 `AgentMessage` 子类型）
- 删除：`session.py` 中 `_SessionMessage`，改为直接使用 `AgentMessage` 子类型
- 重构：`model.py` 中 `_format_messages` 用 `isinstance` 派发替代 `role` 字符串判断
- 重构：`session.py` 中 `isinstance` 替代 `role` 字符串判断
- 新增：`docs/pi-agent-comparison.md` 与 pi-agent-core 的对比分析报告

### 上下文
- 统一消息类型体系，避免底层 `model` 和上层 `session`/`loop` 各自定义类似结构
- 影响范围：`agent/core/model.py`、`agent/core/loop.py`、`agent/plugins/session.py`

## [unreleased] - 2026-07-17

### 核心摘要
新增 `shell` 工具，支持 agent 在工作目录内执行 shell 命令，带安全约束和超时控制。

### 变更
- 新增：`shell` 工具，通过 `asyncio.create_subprocess_shell` 执行命令，含敏感路径拦截、work_dir 限制、超时控制、输出截断

### 上下文
- agent 需要命令执行能力来完成构建、测试、依赖安装等工程操作
- 影响范围：`agent/tools/shell/__init__.py`、`config.yml`
