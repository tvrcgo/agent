"""waterfall/serial 事件分发 order 排序单元测试。

覆盖：顺序流水线（非 None 返回值替换 evt.data、None 透传）、order 排序
（负数队头、0 即注册顺序、正数队尾、同值按注册顺序、与注册时机解耦）、
serial 上 order 生效（短路前串行到链尾、短路后跳过）、异常隔离、mode 标记、
off 移除、parallel 回归。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import asyncio

from agent.core.events import DispatchMode, EVENT_MODES, EventBus


# 注册到登记表的测试事件（唯一命名，避免污染其他事件）
EVENT_MODES["wf_pipe"] = DispatchMode.WATERFALL
EVENT_MODES["wf_pipe_none"] = DispatchMode.WATERFALL
EVENT_MODES["wf_order_tail"] = DispatchMode.WATERFALL
EVENT_MODES["wf_order_fix"] = DispatchMode.WATERFALL
EVENT_MODES["wf_order_mixed"] = DispatchMode.WATERFALL
EVENT_MODES["wf_empty"] = DispatchMode.WATERFALL
EVENT_MODES["wf_error"] = DispatchMode.WATERFALL
EVENT_MODES["wf_mode"] = DispatchMode.WATERFALL
EVENT_MODES["serial_order"] = DispatchMode.SERIAL


async def test_pipeline() -> None:
    """纯流水线：h1 输出成为 h2 输入，最终结果作为 emit 返回值。"""
    bus = EventBus()
    seen = []

    async def h1(ctx, evt):
        seen.append(("h1", dict(evt.data)))
        return {"v": evt.data["v"] + 1, "tag": "h1"}

    async def h2(ctx, evt):
        seen.append(("h2", dict(evt.data)))
        return {"v": evt.data["v"] * 10, "tag": "h2"}

    bus.on("wf_pipe", h1)
    bus.on("wf_pipe", h2)
    result = await bus.emit("wf_pipe", v=1)

    assert result == {"v": 20, "tag": "h2"}, result
    assert seen == [("h1", {"v": 1}), ("h2", {"v": 2, "tag": "h1"})], seen


async def test_none_pass_through() -> None:
    """返回 None 的 handler 透传，保留上游最后非 None 结果。"""
    bus = EventBus()

    async def h1(ctx, evt):
        return {"v": 1}

    async def h2(ctx, evt):
        return None  # 不改变结果

    async def h3(ctx, evt):
        return {"v": evt.data["v"] + 1}

    bus.on("wf_pipe_none", h1)
    bus.on("wf_pipe_none", h2)
    bus.on("wf_pipe_none", h3)
    result = await bus.emit("wf_pipe_none")

    assert result == {"v": 2}, result


async def test_order_tail_observe() -> None:
    """正数 order 队尾观察：order=1 返回 None 不改最终结果，且保证在主体之后执行。"""
    bus = EventBus()
    order = []

    async def main1(ctx, evt):
        order.append("main1")
        return {"v": 1}

    async def tail_obs(ctx, evt):
        order.append("tail")
        assert evt.data == {"v": 1}, evt.data  # 看到主体最终结果
        return None  # 观察：不改结果

    # order=1 先注册，仍应最后执行
    bus.on("wf_order_tail", tail_obs, order=1)
    bus.on("wf_order_tail", main1)
    result = await bus.emit("wf_order_tail")

    assert order == ["main1", "tail"], order
    assert result == {"v": 1}, result


async def test_order_fix() -> None:
    """正数 order 队尾修正：返回新值覆盖最终结果，与注册顺序解耦。"""
    bus = EventBus()
    order = []

    async def main1(ctx, evt):
        order.append("main1")
        return {"v": 1}

    async def main2(ctx, evt):
        order.append("main2")
        return {"v": 2}

    async def tail_fix(ctx, evt):
        order.append("tail")
        return {"v": evt.data["v"] + 100, "fixed": True}

    bus.on("wf_order_fix", tail_fix, order=1)  # 注册在主体之前
    bus.on("wf_order_fix", main1)
    bus.on("wf_order_fix", main2)
    result = await bus.emit("wf_order_fix")

    assert order == ["main1", "main2", "tail"], order
    assert result == {"v": 102, "fixed": True}, result


async def test_order_same_value_registration() -> None:
    """同值 order 按注册顺序执行（稳定排序），尾尾依次透传。"""
    bus = EventBus()
    order = []

    async def main1(ctx, evt):
        order.append("main1")
        return {"v": 1}

    async def main2(ctx, evt):
        order.append("main2")
        return {"v": 2}

    async def tail_a(ctx, evt):
        order.append("tail_a")
        return {"v": evt.data["v"] + 10}

    async def tail_b(ctx, evt):
        order.append("tail_b")
        return {"v": evt.data["v"] + 100}

    bus.on("wf_order_fix", main1)
    bus.on("wf_order_fix", main2)
    bus.on("wf_order_fix", tail_a, order=1)
    bus.on("wf_order_fix", tail_b, order=1)
    result = await bus.emit("wf_order_fix")

    assert order == ["main1", "main2", "tail_a", "tail_b"], order
    assert result == {"v": 112}, result  # tail_a 输出进 tail_b，尾尾依次


async def test_order_value_control() -> None:
    """order 整数显式控制顺序：与注册顺序解耦。"""
    bus = EventBus()
    order = []

    async def main1(ctx, evt):
        order.append("main1")
        return {"v": 1}

    async def tail_last(ctx, evt):
        order.append("tail_last")
        return {"v": evt.data["v"] + 100}

    async def tail_first(ctx, evt):
        order.append("tail_first")
        return {"v": evt.data["v"] + 1}

    # tail_last 先注册但 order 大，仍应后执行
    bus.on("wf_order_fix", main1)
    bus.on("wf_order_fix", tail_last, order=100)
    bus.on("wf_order_fix", tail_first, order=1)
    result = await bus.emit("wf_order_fix")

    assert order == ["main1", "tail_first", "tail_last"], order
    assert result == {"v": 102}, result


async def test_order_head() -> None:
    """负数 order 排队头：注册在主体之后仍最先执行。"""
    bus = EventBus()
    order = []

    async def main1(ctx, evt):
        order.append("main1")
        return {"v": 1}

    async def head(ctx, evt):
        order.append("head")
        return None  # 纯位置观察：不改结果

    bus.on("wf_order_tail", main1)
    bus.on("wf_order_tail", head, order=-1)
    result = await bus.emit("wf_order_tail")

    assert order == ["head", "main1"], order
    assert result == {"v": 1}, result


async def test_order_mixed() -> None:
    """-1/0/1 混合按 order 升序执行，同档按注册顺序，流水线逐级透传。"""
    bus = EventBus()
    order = []

    async def make(name, step):
        async def h(ctx, evt):
            order.append(name)
            return {"v": evt.data["v"] + step}
        return h

    # 交错注册，执行顺序应完全由 order 决定
    bus.on("wf_order_mixed", await make("tail_a", 100), order=1)
    bus.on("wf_order_mixed", await make("main_a", 1))
    bus.on("wf_order_mixed", await make("head", 1), order=-1)
    bus.on("wf_order_mixed", await make("tail_b", 1), order=1)
    bus.on("wf_order_mixed", await make("main_b", 10))
    result = await bus.emit("wf_order_mixed", v=0)

    assert order == ["head", "main_a", "main_b", "tail_a", "tail_b"], order
    assert result == {"v": 113}, result  # 0→1→2→12→112→113


async def test_empty_returns_initial_data() -> None:
    """无 handler 的 waterfall 事件返回初始 data。"""
    bus = EventBus()
    result = await bus.emit("wf_empty", a=1, b=2)
    assert result == {"a": 1, "b": 2}, result


async def test_exception_isolation() -> None:
    """中间 handler 抛错被隔离，后续 handler 正常执行。"""
    bus = EventBus()

    async def bad(ctx, evt):
        raise RuntimeError("boom")

    async def ok(ctx, evt):
        return {"fixed": True}

    bus.on("wf_error", bad)
    bus.on("wf_error", ok)
    result = await bus.emit("wf_error")

    assert result == {"fixed": True}, result


async def test_mode_flag() -> None:
    """handler 能读到 evt.mode == waterfall。"""
    bus = EventBus()
    modes = []

    async def m1(ctx, evt):
        modes.append(evt.mode)
        return {"x": 1}

    bus.on("wf_mode", m1)
    await bus.emit("wf_mode")

    assert modes == ["waterfall"], modes


async def test_serial_order_chain() -> None:
    """serial 事件上 order 生效：主体返回 None 时按序走到正数 handler。"""
    bus = EventBus()
    ran = []

    async def head(ctx, evt):
        ran.append("head")
        return None

    async def main_serial(ctx, evt):
        ran.append("main")
        return None

    async def tail_serial(ctx, evt):
        ran.append("tail")
        return "tail-decision"

    bus.on("serial_order", tail_serial, order=1)
    bus.on("serial_order", main_serial)
    bus.on("serial_order", head, order=-1)
    result = await bus.emit("serial_order")

    assert result == "tail-decision", result
    assert ran == ["head", "main", "tail"], ran


async def test_serial_order_shortcircuit() -> None:
    """serial 事件上主体短路时，更大 order 的 handler 不执行。"""
    bus = EventBus()
    ran = []

    async def main_serial(ctx, evt):
        ran.append("main")
        return "decision"

    async def tail_serial(ctx, evt):
        ran.append("tail")
        return "tail-decision"

    bus.on("serial_order", main_serial)
    bus.on("serial_order", tail_serial, order=1)
    result = await bus.emit("serial_order")

    assert result == "decision", result  # serial 仍短路
    assert ran == ["main"], ran  # 短路后 order=1 未被调度


async def test_off_removes_ordered() -> None:
    """off 能从排序列表移除带 order 的 handler。"""
    bus = EventBus()

    async def main1(ctx, evt):
        return {"v": 1}

    async def tail1(ctx, evt):
        return {"v": 99}

    bus.on("wf_order_tail", main1)
    bus.on("wf_order_tail", tail1, order=1)
    bus.off("wf_order_tail", tail1)
    result = await bus.emit("wf_order_tail")

    assert result == {"v": 1}, result


async def test_serial_parallel_regression() -> None:
    """回归：serial 仍短路，parallel 仍返回 None。"""
    bus = EventBus()
    EVENT_MODES["wf_reg_serial"] = DispatchMode.SERIAL
    EVENT_MODES["wf_reg_parallel"] = DispatchMode.PARALLEL

    async def s1(ctx, evt):
        return "first"

    async def s2(ctx, evt):
        return "second"

    bus.on("wf_reg_serial", s1)
    bus.on("wf_reg_serial", s2)
    assert await bus.emit("wf_reg_serial") == "first"

    async def p1(ctx, evt):
        return "ignored"

    bus.on("wf_reg_parallel", p1)
    assert await bus.emit("wf_reg_parallel") is None


async def main() -> None:
    results = {}
    scenarios = [
        ("pipeline", test_pipeline),
        ("none_pass_through", test_none_pass_through),
        ("order_tail_observe", test_order_tail_observe),
        ("order_fix", test_order_fix),
        ("order_same_value_registration", test_order_same_value_registration),
        ("order_value_control", test_order_value_control),
        ("order_head", test_order_head),
        ("order_mixed", test_order_mixed),
        ("empty_initial_data", test_empty_returns_initial_data),
        ("exception_isolation", test_exception_isolation),
        ("mode_flag", test_mode_flag),
        ("serial_order_chain", test_serial_order_chain),
        ("serial_order_shortcircuit", test_serial_order_shortcircuit),
        ("off_removes_ordered", test_off_removes_ordered),
        ("serial_parallel_regression", test_serial_parallel_regression),
    ]
    for name, fn in scenarios:
        try:
            await fn()
            results[name] = True
            print(f"  {name}: PASS")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            results[name] = False
            import traceback
            traceback.print_exc()

    passed = sum(1 for v in results.values() if v)
    print(f"\nPassed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    asyncio.run(main())
