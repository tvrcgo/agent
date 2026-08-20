# Confirm 单元测试

## ConfirmPlugin

- [ ] `load(ctx, config)` 注册 `confirm_request` 事件
- [ ] `_on_confirm_request`：job 为 None 时返回 `{"decision": "deny"}`
- [ ] `_on_confirm_request`：内部 `_ask_ui` 推送确认后等决策，拿到决策**返回非 None** `{"decision": ...}`（serial 短路回填 emit 返回值）
- [ ] `_ask_ui`：emit `msg_output`，载荷 `output` 携带 `OutputMessage(type="confirm")`，data 带 id/description（直接构造 core 端口，插件间零依赖）
- [ ] `_ask_ui`：`cmd_confirm` 闭包匹配 `job + confirm_id` 后 `future.set_result(...)`，超时/异常路径 finally 卸载监听
- [ ] 超时（`wait_for` 超时）→ 返回 None → `_on_confirm_request` 转 `{"decision": "deny"}`
- [ ] 卸载后 `_ctx is None`，无 pending 残留

## ToolGuardPlugin

- [ ] `load(ctx, config)` 注册 `tools_start` 事件，解析 `review_tools` 清单
- [ ] `_on_tools_start`：清单外工具直接放行，不调 LLM
- [ ] `_on_tools_start`：flash 判 `safe` 放行 / `dangerous` 发 `confirm_request` 事件
- [ ] deny（返回 None 或 decision=deny）→ 从 `tool_calls` 剔除 + emit `tool_error`
- [ ] approve（decision=approve）→ 不阻断

## 分发模式原语

- [ ] serial：首个 handler 返回非 None → 短路并作为 emit 返回值，后续 handler 不执行
- [ ] serial：handler 全部返回 None → emit 返回 None
- [ ] serial：handler 抛异常 → 记日志继续下一 handler，不产生短路值
- [ ] serial：按注册顺序依次执行
- [ ] parallel：并发观察，返回值忽略，handler 异常隔离
- [ ] 未登记事件 emit → 按 parallel 分发 + warning 日志
- [ ] 登记 fire 条目后 emit → 抛 NotImplementedError
- [ ] `Event.mode` 分发时注入实际模式（"serial" / "parallel"）

---

```bash
uv run python -c "
import asyncio
from agent.core.events import EventBus, DispatchMode, EVENT_MODES

EVENT_MODES.update({
    's1': DispatchMode.SERIAL, 's2': DispatchMode.SERIAL, 's3': DispatchMode.SERIAL,
    'p1': DispatchMode.PARALLEL, 'm1': DispatchMode.PARALLEL,
})

async def main():
    bus = EventBus()
    order = []

    # serial：顺序 + 短路
    async def h1(ctx, evt):
        order.append(1)
        return 'A'
    async def h2(ctx, evt):
        order.append(2)
        return 'B'
    bus.on('s1', h1)
    bus.on('s1', h2)
    assert await bus.emit('s1') == 'A'
    assert order == [1], f'短路后 h2 不应执行: {order}'
    print('[PASS] serial short-circuit')

    # serial：全 None 返回 None
    async def hn(ctx, evt):
        return None
    bus.on('s2', hn)
    assert await bus.emit('s2') is None
    print('[PASS] serial all-none')

    # serial：异常隔离继续
    async def boom(ctx, evt):
        raise RuntimeError('boom')
    async def hb(ctx, evt):
        return 'B'
    bus.on('s3', boom)
    bus.on('s3', hb)
    assert await bus.emit('s3') == 'B'
    print('[PASS] serial exception isolation')

    # parallel：并发观察，返回值忽略
    async def hp(ctx, evt):
        order.append(3)
        return 'ignored'
    bus.on('p1', hp)
    assert await bus.emit('p1') is None
    assert order[-1] == 3
    print('[PASS] parallel ignore return')

    # 未登记默认 parallel + warning
    await bus.emit('unregistered_event')
    print('[PASS] unregistered defaults to parallel')

    # fire 预留报错
    EVENT_MODES['fire_placeholder'] = DispatchMode.FIRE
    try:
        await bus.emit('fire_placeholder')
        raise AssertionError('fire should raise NotImplementedError')
    except NotImplementedError:
        pass
    finally:
        del EVENT_MODES['fire_placeholder']
    print('[PASS] fire reserved raises')

    # Event.mode 注入
    captured = []
    async def hm(ctx, evt):
        captured.append(evt.mode)
    bus.on('m1', hm)
    await bus.emit('m1')
    assert captured == ['parallel'], f'mode should be parallel: {captured}'
    print('[PASS] event mode injected')

asyncio.run(main())
print('All passed')
"
```
