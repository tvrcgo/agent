# 测试

## 测试用例

- [单元测试 — SessionPlugin](cases/unit-session.md)
- [单元测试 — ConfirmPlugin](cases/unit-plugins.md)
- [单元测试 — MCP](cases/unit-mcp.md)
- [单元测试 — SkillPlugin](cases/unit-skill.md)
- [单元测试 — Follow-up 多轮输出](cases/unit-followup.md)
- [集成测试 — WebSocket 协议](cases/integration-ws.md)

## 测试脚本

- [test_subjob.py](scripts/test_subjob.py) — 子任务测试
- [test_ws.py](scripts/test_ws.py) — WebSocket 测试
- [test_loop_followup.py](scripts/test_loop_followup.py) — follow-up 多轮输出回归测试
- [test_e2e.py](scripts/test_e2e.py) — 端到端集成测试（msg_input → LLM → 工具/subjob 递归 → msg_output）
