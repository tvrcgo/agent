# Confirm / Workspace 单元测试

## ConfirmPlugin

- [ ] `register` 注册 `before_tool` 和 `command:confirm` 两个钩子
- [ ] `_on_before_tool`：非 `request_confirmation` 工具调用直接放过
- [ ] `_on_before_tool`：匹配时 emit `StatusEvent(status="waiting")` 并等待
- [ ] `_on_command_confirm`：收到 deny → `JobAborted`
- [ ] `_on_command_confirm`：收到 approve → 正常继续
- [ ] 超时未响应 → 默认 deny

## WorkspacePlugin

- [ ] `on_connect` 创建 `workspace/<session_id>/` 目录
- [ ] 目录路径写入 `ctx.data["workspace"]`
- [ ] `before_llm` 将 workspace 路径注入 system prompt
- [ ] `_ws_injected` 标记防止重复注入
- [ ] session_id 为空时使用 `__default__`

---

```bash
uv run python -c "
from agent.plugins.confirm import ConfirmPlugin
cp = ConfirmPlugin()
assert cp.name == 'confirm' and cp._pending == {}
print('[PASS] confirm init')

from agent.plugins.workspace import WorkspacePlugin
wp = WorkspacePlugin()
assert wp.name == 'workspace'
wp.shutdown()
print('[PASS] workspace init/shutdown')

print('All passed')
"
```
