# Agent Project

基于 WebSocket 的自主 agent，含插件生命周期、会话记忆和 LLM 上下文压缩。OpenAI 兼容 API（DeepSeek）。

## 架构

```
agent/
├── __main__.py          # 入口：组装 LLM + skills + plugins + loop + WS server
├── core/
│   ├── config.py         # Pydantic 配置，YAML 加载，${VAR} 展开
│   ├── llm.py            # OpenAIProvider: 异步 httpx 客户端，Message/LLMResponse 类型
│   ├── loop.py           # AgentLoop + JobAborted: Think→Act→Observe，每会话一个 asyncio Task
│   ├── plugin.py         # Plugin ABC, PluginRegistry, PluginContext（可变上下文容器）
│   ├── skill.py          # Skill ABC, SkillRegistry, ToolDefinition
│   └── ws.py             # WebSocketServer, ClientSession，类型化消息协议
├── plugins/
│   ├── session.py        # SessionPlugin: 会话记忆 + JSONL 持久化 + 压缩
│   └── confirm.py        # ConfirmPlugin: 拦截 before_tool，等待用户审批
├── skills/
│   ├── websearch.py      # 网络搜索（Google 优先，多引擎回退）
│   └── confirm.py        # RequestConfirmationSkill: 存根，实际逻辑在 ConfirmPlugin
└── AGENTS.md             # Agent 系统提示词
```

## 数据流

1. 客户端通过 WebSocket 连接（可选 `?session_id=xxx`）
2. `AgentLoop` 为每个会话创建 `asyncio.Task`，执行推理循环：
   - **Think:** 发出 `before_llm` 钩子 → SessionPlugin 检查压缩，设置 `ctx.data["messages"]` → LLM 调用
   - **Act:** `ToolCallEvent` → `before_tool` 钩子 → `SkillRegistry.execute()` → `ToolResultEvent` → `after_tool` 钩子 → 回到 Think
   - **Done:** 无工具调用 → 发出 `MessageEvent` → 发出 `on_complete`
3. SessionPlugin 以 JSONL 格式持久化对话到 `./data/sessions/<session_id>.jsonl`

## 关键设计

### WebSocket 消息协议
- 入站：`chat`（用户输入）和 `command`（UI 操作，带 `action` 字段）
- 出站：事件包装为 `{"type": "...", "timestamp": "...", "payload": {...}}`
- Session ID 从 URL query string 获取，不从消息体获取

### 上下文管理
- **`max_load_messages`:** 冷启时从 JSONL 尾部读取的消息条数
- **基于 token 的压缩:** 当估算 token 达到 `max_tokens * compress_threshold` 时，通过独立 LLM 调用压缩旧消息，保留最近 `compress_keep_recent` 条原文
- 压缩纯内存操作，不写盘；JSONL 保留完整原始消息
- 无 `window_size` — 存储无上限，仅加载时有窗口

### 插件生命周期（6 个生命周期钩子 + 命令钩子）
生命周期：`on_connect` → `before_job` → `before_llm` → `after_llm` → `before_tool` → 技能执行 → `after_tool` → ...(循环) → `on_complete`/`on_disconnect`

命令：`command:<action>` 钩子。loop 将 `CommandMessage` 分发到 `command:<action>` 钩子。插件通过注册 `command:<action>` 处理器响应特定操作。`command:cancel` 由 loop 自身处理（核心行为）。

插件与 loop 之间仅通过 `ctx.data` 字典通信。SessionPlugin 设置 `ctx.data["messages"]`；loop 设置 `ctx.data["response"]` 和 `ctx.data["tool_call"]`。

**规则：loop.py 禁止修改。** 所有功能扩展——新命令、新行为、确认流程、上下文管理——必须通过插件钩子实现。loop.py 仅允许 bug 修复和钩子点新增。如果发现自己在 loop 中添加业务逻辑，停下来，重新设计为插件。

## 约定

- 不写 docstring，不写注释，除非行为出人意料
- 每个关注点一个文件，不搞提前抽象
- 配置：Pydantic 模型内置默认值，`config.yml` 覆盖
- 会话存储：`./data/sessions/`，每会话一个 JSONL 文件
- 默认配置从 `agent/AGENTS.md` 读取系统提示词
- 测试清单见 `tests/README.md`，集成测试见 `tests/test_ws.py`
