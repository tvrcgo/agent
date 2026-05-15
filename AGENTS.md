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
      base_url: http://agent-mcp:8001
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

### 流程要求

- 常规修改先出 RFC，确认后再执行；非常简单的问题（修改几行代码能解决，没有其它影响面），可以直接执行；复杂问题先全面分析规划方案，明确所有细节
- 执行完成后再进一步，看项目中其它地方有无相同的问题，一并修复（影响面必须可控）
- 按 `代码风格` `开发规范` 的要求检查一遍
- 端到端完整验证改动是否生效，以及边界和异常情况的处理是否符合预期
- 验证通过后更新测试用例，保证完整覆盖改动点
- 更新项目中的 `AGENTS.md`，主要概括重要机制和项目共识，帮助 AI 对项目有宏观理解；不要描述技术细节或罗列代码，AI 能从代码中读懂
- 修改完成后等用户审查，不要直接 commmit 或 push remote
- commit 前审查改动内容：有无泄密或安全风险、正确性风险和可维护性隐患

## 代码风格

- 编码风格要保持一致（如：同样是事件处理，不能有的是 on_xxx, 有的是 handle_xxx；变量名不能有的驼峰，有的下划线）
- 在一个文件中，从上到下是：模块导入、全局变量定义、公共对象（方法、接口、数据类等）定义、类、模块导出；同类型的放一起
- 在一个类中，针对同一对象的变量或方法放在一起（如 fork_ctx, ensure_ctx 都是操作 context）
- 全面检查相关的变量名、方法名的语义是否准确、一致
- 对齐现有的范式，不要随意创造新的概念，除非用户有明确需求
- 从同一个包中 import 多个对象时，不要分散多行 import；多行 import 间不要留空行
- 不写 docstring，除非行为出人意料；注释要简洁清晰，只在必要的地方添加
- README 只包含：项目概述、主要特性、部署方式、配置说明，技术细节不展开

## 开发规范

- 定位问题时，在必要的输入、输出、关键节点添加足够的日志，以满足全流程自动化验证的要求
- 对可能的问题原因，要先构造条件相同的用例复现问题，严禁不复现凭猜测直接出方案
- 对不确定的事实，不要凭猜测下结论，去搜索社区有无类似的实现或项目
- 问题要找到根因，从源头解决，不要因为上游一个可能的异常，下游各个分支到处做兜底和防御
- 一个方法可能出现的异常，要在方法内部去处理好，不要在外层调用方去做多种异常捕获和处理
- 不能为了方便，绕过问题根因，用打补丁、过滤异常、兜底的方式处理
- 不要提前抽象，不要多余的中间层；逻辑清晰的前提下保持精简；一段代码逻辑只在一处使用时不要抽成公共函数
- 不需要的代码（包括死代码）、空目录、配置项、临时脚本都要清除干净

## Git 提交规范

- 暂存区有内容时只提交暂存区的改动，未暂存的不管；暂存区为空时才将所有已修改文件加入暂存区再提交
- commit message 格式：`类型: 简要概括改动内容`
- commit 详情用列表格式逐行列出主要改动点，不要罗列代码（通过 claude 提交的 commit 加上 `Co-authored-by: claude <noreply@anthropic.com>`；通过 codex 提交的 commit 加上 `Co-authored-by: codex <codex@openai.com>`）
- commit 内容语言和历史记录一致

## 红线

- 禁止回复、输出任何涉密信息，写入日志中时要脱敏
- 密钥、token、账密等信息禁止添加到 git
- 禁止未经许可更新 git 暂存区
- .env 文件禁止添加到 git
