# Confirm 单元测试

## ConfirmPlugin

- [ ] `register` 注册 `before_tool` 和 `command:confirm` 两个钩子
- [ ] `_on_before_tool`：非 `request_confirmation` 工具调用直接放过
- [ ] `_on_before_tool`：匹配时 emit `StatusEvent(status="waiting")` 并等待
- [ ] `_on_command_confirm`：收到 deny → `job.data["abort"] = True`
- [ ] `_on_command_confirm`：收到 approve → 正常继续
- [ ] 超时未响应 → 默认 deny


---

```bash
uv run python -c "
from agent.plugins.confirm import ConfirmPlugin
cp = ConfirmPlugin()
assert cp.name == 'confirm' and cp._pending == {}
print('[PASS] confirm init')

print('All passed')
"
```
