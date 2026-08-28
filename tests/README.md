# 测试

## 测试用例

- [单元测试 — SessionPlugin](cases/unit-session.md)
- [单元测试 — ConfirmPlugin](cases/unit-plugins.md)
- [单元测试 — MCP](cases/unit-mcp.md)
- [单元测试 — SkillPlugin](cases/unit-skill.md)
- [单元测试 — Follow-up 多轮输出](cases/unit-followup.md)
- [集成测试 — WebSocket 协议](cases/integration-ws.md)
- [E2E — playground 全链路](cases/e2e-playground.md)

## 测试脚本

- [test_subjob.py](scripts/test_subjob.py) — 子任务测试
- [test_ws.py](scripts/test_ws.py) — WebSocket 测试
- [test_loop_followup.py](scripts/test_loop_followup.py) — follow-up 多轮输出回归测试
- [test_scene_registry.py](scripts/test_scene_registry.py) — 基座场景化单元测试（场景包工具/插件加载、内置回退、依赖定位、session 路径配置化）
- [test_pause.py](scripts/test_pause.py) — 暂停/恢复单元测试（挂载点阻塞、serial 顺序、cancel 中断、job_end 清理、session_id 路由）
- [test_cancel.py](scripts/test_cancel.py) — 取消插件单元测试（cmd_cancel → cancelled、session_id 路由、未知 job no-op）
- [test_reset.py](scripts/test_reset.py) — ctx.register/invoke（gRPC 风格，`register(name, fn)`）插件方法注册机制 + 会话重置插件单元测试（cmd_reset → 历史清空 + JSONL 删除 + 回执、在飞 job 取消、session_id 路由、未知会话 no-op、未加载 session 插件不崩）
- [test_waterfall.py](scripts/test_waterfall.py) — waterfall 事件分发模式单元测试（顺序流水线、None 透传、链尾 tail 兜底观察/修正、tail 整数控制链尾顺序、异常隔离、tail 仅 waterfall 生效、serial/parallel 回归）
- [test_e2e.py](scripts/test_e2e.py) — 端到端集成测试（fake LLM，无网络依赖）
  - msg_input → LLM → 工具/subjob 递归 → msg_output 基础链路
  - 流式（llm_chunk → stream 消息）
  - tool_guard + confirm（approve/deny 决策回传）
  - 取消（cmd_cancel → cancelled，经 CancelPlugin）、截断（finish_reason=length）、max_iterations、LLM 异常
  - max_concurrent 排队、follow-up steering 多轮
  - 取消后孤儿 tool_calls 清理（回归：DeepSeek 400）
