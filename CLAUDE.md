# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API（DeepSeek）。

## 分层

- **入口** (`__main__.py`)：组装各组件并启动 WebSocket 服务
- **推理循环** (`loop.py`)：Think→Act→Observe 循环，每个 WebSocket 会话一个 asyncio Task。loop.py 只做核心调度，功能扩展通过插件钩子实现，禁止在其中添加业务逻辑
- **插件** (`plugin.py`)：基于钩子的事件系统，插件在 `load` 时注册 handler，`emit` 按注册顺序同步调用；`command:<action>` 命名钩子处理 UI 操作
- **工具** (`tool.py`)：可执行工具的抽象基类和注册表，从 `agent/tools/` 加载 Tool 子类，传入 AgentLoop 供 LLM 调用
- **技能** (`skill.py`)：SKILL.md 指令模板，从 `agent/skills/` 和 `skills/` 两个目录加载，注入系统提示词
- **传输** (`ws.py`)：类型化 WebSocket 消息协议，HeartbeatEvent 用于连接保活（type=heartbeat），StatusEvent 承载业务状态和思维内容

## 核心概念

### AgentContext
贯穿 session 和 job 生命周期的可变上下文。`_ensure_ctx` 维护每 session 的持久化实例，`_fork_ctx` 从中派生出 job/command 独立副本（合并 base data + extra）。插件通过 `ctx.data` 字典通信，通过 `ctx.emit()` 触发钩子。

### 消息排队
job 运行中收到的 chat 消息排队，下轮迭代开始前由 loop 写入 `ctx.data`，SessionPlugin 在 `before_llm` 消费为 user 消息。

### 上下文压缩
存储无上限，冷启只加载尾部若干条。token 超阈值时通过独立 LLM 调用压缩旧消息，保留最近原文。压缩在内存完成，JSONL 保留完整历史。

### Job 树
复杂任务可通过 `sub_job` 工具并行执行。`loop.spawn()` 创建子 Job：子 Job 通过 `ClientSession.is_silent` 抑制个体事件，通过 `JobTreeEvent` 广播树结构（id、parent_id、depth、status、content）给客户端。所有 Job 共享同一 AgentLoop 的 LLM、tools 和 skills，通过 `asyncio.gather` 并发执行。`max_sub_job_depth` 限制递归深度。

### 插件生命周期
`on_connect` → `before_job` → `before_llm` → `after_llm` → `before_tool` → 工具执行 → `after_tool` → 循环 → `on_complete` / `on_disconnect`。`command:cancel` 由 loop 自身处理（核心行为）。JobAborted 异常中断 job。

## 约定

- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件
- 默认配置从 `agent/AGENTS.md` 读取系统提示词
- 测试清单见 `tests/README.md`，集成测试见 `tests/test_ws.py`
- 一次性的临时文件写入系统的临时目录

### 架构规范

- 按架构分层，模块只能向上或同级引用，不能 core 中的模块引用 plugins, skills 中的模块
- agent/tools 中 tool 的依赖要和项目依赖隔离
- 对 loop 功能的扩展，都用 hook+plugin 的方式实现；如果 hook 不够可新增，但 hook—name 要符合 loop 流程的语义，可复用

### 流程要求

- 改代码前先出简单的RFC，确认后再执行（除非是非常简单的问题，一两行代码能解决的明显错误，没有其它影响面，可以直接执行）
- 执行完成后，要再追一层，看看有没其它地方有相同的问题；最后按 `编码规范` 的要求检查一遍
- 在本机测试和验证（不要用 docker-compose.yml 中用的端口），测试完成后马上停掉进程
- 测试后要更新 tests/ 中的用例，保证后续测试能覆盖本次的新情况
- 测试后更新 `CLAUDE.md`，主要概括重要机制，帮助 AI 对项目有宏观理解；不要描述技术细节或罗列代码，AI 能从代码中读懂
- 每次改完等用户审查，不要直接 commmit 或 push remote

### 编码规范

- 编码风格要保持一致（如同样是响应事件，不能有的是 on_xxx, 有的是 handle_xxx）
- 不要随意生造新的概念，尽量对齐现有的范式
- 在一个文件中，从上到下是：模块导入、全局变量定义、公共对象（方法、接口、数据类等）定义、类、模块导出；同类型的放一起
- 在一个类中，针对同一对象的变量或方法放在一起（如 fork_ctx, ensure_ctx 都是操作 context）
- 代码做重构后，如果概念或对象发生变化，要全面检查相关的变量名、方法名的语义是否一致，及时更新
- 从同一个包中 import 多个对象时，不要分散多行 import；多行 import 间不要留空行，保持整洁
- 问题要找到根因，从源头解决，不要因为上游一个可能的异常，下游各个分支到处做兜底和防御
- 一个方法可能出现的异常，要在方法内部去处理好，不要在外层调用方去做多种异常捕获和处理
- 不能为了方便，绕过复杂问题，用打补丁或兜底的方式处理
- 不写 docstring，除非行为出人意料；注释要简洁清晰，只在必要的地方添加
- 不要提前抽象，不要多余的中间层；逻辑清晰的前提下保持精简；一段代码逻辑只在一处使用时不要抽成公共函数
- 不需要的代码和死代码及时清除干净
- README 只包含：项目概述、主要特性、部署方式、配置说明，技术细节不展开
- commit message 格式：`类型: 概括描述改动点`；commit 详情用列表格式逐行列出主要改动点，不要罗列代码（通过 claude 提交的 commit 加上 `Co-authored-by: claude <noreply@anthropic.com>`；通过 codex 提交的 commit 加上 `Co-authored-by: codex <codex@openai.com>`）；语言和历史记录一致

### 红线

- 密钥、token、账密等信息禁止添加到 git
