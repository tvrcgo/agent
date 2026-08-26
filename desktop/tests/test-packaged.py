# 打包版（win-unpacked）UI 快速验证（CDP 9224）
# 通过 CDP 连接运行中的打包版 Electron（--remote-debugging-port=9224）
# 验证：主界面渲染（状态+日志合并页）、agent 启停、聊天真实链路
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9224"
RESULT = Path(__file__).parent / "packaged-result.txt"


def main() -> None:
    buf = io.StringIO()
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        page.set_default_timeout(15000)
        page.wait_for_timeout(1200)

        # 主界面渲染（默认状态+日志合并页 + 侧边栏设置）
        body = page.inner_text("body")
        for nav in ["运行状态", "设置", "Agent 状态"]:
            if nav not in body:
                ok = False
                buf.write(f"[FAIL] 主界面元素 {nav} 缺失\n")
        buf.write("[PASS] 主界面渲染（状态+日志合并页）\n")

        # bridge 可用
        has_bridge = page.evaluate("() => Boolean(window.agentDesktop)")
        if not has_bridge:
            ok = False
            buf.write("[FAIL] window.agentDesktop bridge 缺失\n")
        else:
            buf.write("[PASS] 桌面桥接可用\n")

        # 启动 agent（状态页启动按钮）
        start_btn = page.get_by_role("button", name="启动 Agent")
        if start_btn.count() > 0:
            start_btn.first.click()
        running = False
        for _ in range(40):
            page.wait_for_timeout(1000)
            body = page.inner_text("body")
            if "停止 Agent" in body and "运行中" in body:
                running = True
                break
        if not running:
            ok = False
            buf.write("[FAIL] agent 未进入运行中\n")
        else:
            buf.write("[PASS] 打包版 agent 启动运行\n")

        # 聊天：新建会话（侧边栏）→ 发消息 → 回复
        new_btn = page.locator("aside button[title*='会话']")
        if new_btn.count() > 0:
            new_btn.first.click()
            page.wait_for_timeout(600)
        input_el = page.locator("textarea.inputbar-input")
        if input_el.count() == 0 or input_el.first.is_disabled():
            ok = False
            buf.write("[FAIL] 聊天输入框不可用\n")
        else:
            input_el.first.fill("1+1=?")
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
            body = page.inner_text("body")
            if "1+1=?" not in body:
                ok = False
                buf.write("[FAIL] 用户消息未显示\n")
            else:
                buf.write("[PASS] 用户消息已发送\n")
            # 等 agent 回复（回合结束）
            replied = False
            for _ in range(60):
                page.wait_for_timeout(1000)
                t = page.locator("textarea.inputbar-input")
                if t.count() > 0 and t.first.is_enabled() and page.locator(".chatview-turnstatus").count() == 0:
                    replied = True
                    break
            if not replied:
                ok = False
                buf.write("[FAIL] agent 未回复\n")
            else:
                buf.write("[PASS] 打包版 agent 回复\n")

        # 停止 agent（点状态点按钮回状态页 → 停止）
        logs_btn = page.locator("aside button[aria-label='运行状态']")
        if logs_btn.count() > 0:
            logs_btn.first.click()
            page.wait_for_timeout(400)
        stop_btn = page.get_by_role("button", name="停止 Agent")
        if stop_btn.count() > 0:
            stop_btn.first.click()
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            if "启动 Agent" not in body:
                ok = False
                buf.write("[FAIL] agent 未停止\n")
            else:
                buf.write("[PASS] 打包版 agent 停止\n")
        else:
            buf.write("[WARN] agent 未运行，跳过停止验证\n")

    buf.write(("=== PACKAGED APP TEST PASSED ===\n" if ok else "=== PACKAGED APP TEST FAILED ===\n"))
    RESULT.write_text(buf.getvalue(), encoding="utf-8")
    print(buf.getvalue())
    sys.exit(0 if ok else 1)


main()
