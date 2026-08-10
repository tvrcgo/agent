# Confirm 单元测试

## ConfirmPlugin

- [ ] `load(ctx, config)` 注册 `before_tool`、`request_confirm`、`cmd_confirm` 三个事件
- [ ] `_on_before_tool`：非 `request_confirmation` 工具调用直接放过
- [ ] `_on_before_tool`：匹配时阻塞等待 `cmd_confirm` 唤醒
- [ ] `_on_request_confirm`：向 `job.output` 追加 `confirm_request` 事件并等待决策，结果回填 `event.data["confirm_decision"]`
- [ ] `cmd_confirm` deny → `_on_before_tool` 在 `event.data` 写 `abort=True` + 取消消息，工具跳过执行
- [ ] `cmd_confirm` approve → 正常继续执行工具
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
