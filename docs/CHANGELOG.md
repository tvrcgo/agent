# CHANGELOG
> 新内容放前面，同一天内容合并；版本号和PR ID、Issue ID没有可省略

## [unreleased] - 2026-08-10

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
