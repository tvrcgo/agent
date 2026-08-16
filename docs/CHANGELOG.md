# CHANGELOG
> 新内容放前面，同一天内容合并；版本号和PR ID、Issue ID没有可省略

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
