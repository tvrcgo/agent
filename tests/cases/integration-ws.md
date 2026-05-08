# WebSocket 集成测试

Agent 需在运行中（含 SearXNG 服务）：`docker compose up -d --build`

## 场景列表

| 场景 | 说明 |
|---|---|
| status_structure | StatusEvent 使用 `status` 字段，不含废弃的 `state` |
| persistence | 会话消息写入 `data/sessions/` JSONL |
| multi_session | 两个不同 session_id 并发执行，上下文不交叉 |
| error_handling | 非法 JSON 返回 `parse_error` |
| cancel | `/cancel` 命令停止运行中的 job |
| tool_call_protocol | ToolCallEvent / ToolResultEvent 结构正确，id 配对 |
| command_routing | CommandMessage 路由到对应钩子，不崩溃 |

## 验证点

- [ ] 每个场景不抛异常
- [ ] StatusEvent 的 `status` 字段存在，无 `state` 字段
- [ ] 工具调用与结果 id 一一对应
- [ ] 多 session 并发无错误
- [ ] 取消后 job 正常停止

---

```bash
uv run python tests/test_ws.py
```
