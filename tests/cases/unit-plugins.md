# Confirm 单元测试

## ConfirmPlugin

- [ ] `load(ctx, config)` 注册 `request_confirm`、`confirm_ui` 两个事件
- [ ] `_on_request_confirm`：无 req/job 时直接返回
- [ ] `_on_request_confirm`：发起第二层请求 `await ctx.emit("req:confirm_ui", job, timeout=self._timeout, confirm_id=req.id, ...)`，拿到决策后**隐式返回** `{"decision": ...}`
- [ ] `_on_confirm_ui`：emit `msg_output`，载荷 `output` 携带 `OutputMessage(type="confirm")`，data 带 id/description（直接构造 core 端口，插件间零依赖）
- [ ] `_on_confirm_ui`：`cmd_confirm` 闭包匹配 `job + confirm_id` 后 `ui_req.done(...)`，超时卸载监听
- [ ] 第一层超时（`ctx.emit("req:request_confirm")` 返回 None）→ 隐式返回 `{"decision": "deny"}`
- [ ] 卸载后 `_ctx is None`，无 pending 残留

## ToolGuardPlugin

- [ ] `load(ctx, config)` 注册 `tools_start` 事件，解析 `review_tools` 清单
- [ ] `_on_tools_start`：清单外工具直接放行，不调 LLM
- [ ] `_on_tools_start`：flash 判 `safe` 放行 / `dangerous` 发 `req:request_confirm` 请求事件
- [ ] deny（返回 None 或 decision=deny）→ 预填失败 `ToolResult` + 从 `tool_calls` 剔除 + emit `tool_error`
- [ ] approve（decision=approve）→ 不阻断

## Request 原语

- [ ] `req.done(result)` 后 `await req.wait(timeout)` 返回 result
- [ ] `await req.wait(timeout)` 超时返回 None
- [ ] `ctx.emit("req:<name>")` 无响应方时超时返回 None（fail-closed）

---

```bash
uv run python -c "
import asyncio
from agent.core.events import Request

async def main():
    req = Request(name='t', data={'x': 1})
    req.done({'ok': True})
    assert await req.wait(1) == {'ok': True}

    req2 = Request(name='t')
    assert await req2.wait(0.05) is None
    print('[PASS] request primitives')

asyncio.run(main())
print('All passed')
"
```
