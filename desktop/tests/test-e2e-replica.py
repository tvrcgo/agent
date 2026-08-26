# test-e2e-replica.py — 完整 E2E 测试：复刻 UI 各视图 + 真实 agent 交互
# 通过 CDP 连接运行中的 Electron（--remote-debugging-port=9223）
# 覆盖：状态+日志合并页（启停/状态卡/滚动日志）、聊天（hero/输入/消息流/工具卡/任务面板/审批）、
#        设置切换（主题/语言）、危险操作对话框
import io
from pathlib import Path

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
RESULT = Path(__file__).parent / "e2e-result.txt"


def main():
    buf = io.StringIO()
    passed = 0
    failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            buf.write(f"PASS  {name}\n")
        else:
            failed += 1
            buf.write(f"FAIL  {name}  {extra}\n")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.bring_to_front()
        page.wait_for_timeout(1000)

        # ---- 0. 前置：确保中文界面 + 回状态页 + agent 未运行 ----
        body0 = page.inner_text("body")
        if "设置" not in body0:
            # 当前是英文，切到设置并改语言为中文
            page.get_by_text("Settings", exact=True).first.click()
            page.wait_for_timeout(400)
            try:
                lang_btn0 = page.locator("text=Interface language").locator("xpath=../..").get_by_role("button").first
                lang_btn0.click()
                page.wait_for_timeout(300)
                page.get_by_text("简体中文", exact=True).click()
                page.wait_for_timeout(500)
            except Exception:
                pass
        # 点状态点回状态+日志合并页（运行状态页）
        logs_btn0 = page.locator("aside button[aria-label='运行状态']")
        if logs_btn0.count() > 0:
            logs_btn0.first.click()
            page.wait_for_timeout(500)
        # 若 agent 正在运行，先停止
        for _ in range(3):
            body0 = page.inner_text("body")
            if "停止 Agent" in body0:
                try:
                    page.get_by_role("button", name="停止 Agent").first.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
            else:
                break
        page.wait_for_timeout(500)

        # ---- 1. 状态+日志合并页（默认视图）----
        body = page.inner_text("body")
        check("status: Agent 状态 Metric", "Agent 状态" in body)
        check("status: WebSocket Metric", "WebSocket" in body)
        check("status: 启动按钮", "启动 Agent" in body)
        check("status: 运行状态标题", "运行状态" in body)
        check("status: 页大小控件", "每页条数" in body)
        check("status: 清除按钮", "清除日志" in body)

        # 页大小切换（按钮文本可能是持久化的任意值，用包含匹配）
        page.locator("button:has-text('每页条数')").first.click()
        page.wait_for_timeout(300)
        body = page.inner_text("body")
        check("status: 页大小下拉打开", "1000" in body)
        page.get_by_text("1000", exact=True).click()
        page.wait_for_timeout(300)
        body = page.inner_text("body")
        check("status: 页大小切换为1000", "每页条数: 1000" in body)

        # ---- 2. 启动 Agent（状态页）----
        start_btn = page.get_by_role("button", name="启动 Agent").first
        start_btn.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        start_btn.click()
        page.wait_for_timeout(3000)
        body = page.inner_text("body")
        check("status: 启动后显示停止", "停止 Agent" in body, f"body={body[:100]}")

        # 等 agent 完全运行（端口就绪）
        agent_ready = False
        for _ in range(30):
            page.wait_for_timeout(1000)
            body = page.inner_text("body")
            if "运行中" in body and "停止 Agent" in body:
                agent_ready = True
                break
        check("status: agent 运行中", agent_ready)
        body = page.inner_text("body")
        check("status: 有日志内容", "条日志" in body)

        # ---- 3. 聊天视图（复刻 UI；经侧边栏新建会话进入）----
        new_btn = page.locator("aside button[title*='会话']")
        check("chat: 侧边栏新建会话按钮", new_btn.count() > 0)
        if new_btn.count() > 0:
            new_btn.first.click()
            page.wait_for_timeout(600)

        body = page.inner_text("body")
        check("chat: 侧边栏会话列表标题", "会话" in body)

        # hero 空态 / 输入栏（textarea）
        ta = page.locator("textarea.inputbar-input")
        check("chat: 输入栏 textarea", ta.count() > 0)
        if ta.count() > 0:
            check("chat: hero 空态输入可用", ta.first.is_enabled())
            # 输入并发送
            ta.first.fill("1+1=?")
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
            body = page.inner_text("body")
            check("chat: 发送后显示用户消息", "1+1=?" in body)
            check("chat: 回合活动行", page.locator(".chatview-turnstatus").count() > 0)

            # 等待 agent 回复（回合结束，输入栏重新可用）
            replied = False
            for _ in range(90):
                page.wait_for_timeout(1000)
                t = page.locator("textarea.inputbar-input")
                if t.count() > 0 and t.first.is_enabled() and page.locator(".chatview-turnstatus").count() == 0:
                    replied = True
                    break
            check("chat: agent 回复(回合结束)", replied)
            body = page.inner_text("body")
            check("chat: 助手消息 markdown 渲染", page.locator(".md-body").count() > 0)

        # ---- 4. 点状态点按钮 → 回到状态+日志页 ----
        logs_btn = page.locator("aside button[aria-label='运行状态']")
        check("status: 状态点按钮存在", logs_btn.count() > 0)
        if logs_btn.count() > 0:
            logs_btn.first.click()
            page.wait_for_timeout(600)
        body = page.inner_text("body")
        check("status: 状态点按钮打开状态页", "条日志" in body)

        # ---- 5. 设置视图（语言/主题移入此处）----
        page.get_by_text("设置", exact=True).first.click()
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        check("settings: 启动标题", "启动" in body)
        check("settings: 登录启动开关", "登录时启动" in body)
        check("settings: 静默启动开关", "静默启动" in body)
        check("settings: 启动agent开关已移除", "启动时启动 agent" not in body)
        check("settings: Python 路径", "Python 可执行文件" in body)
        check("settings: 项目目录", "项目目录" in body)
        check("settings: 本地文件", "本地文件" in body)
        check("settings: 外观", "外观" in body)
        check("settings: 危险操作标题", "危险操作" in body)
        check("settings: 恢复出厂设置", "恢复出厂设置" in body)

        # 主题切换（设置在设置视图）
        theme_btn = page.locator("text=桌面应用的外观").locator("xpath=../..").get_by_role("button").first
        theme_btn.click()
        page.wait_for_timeout(300)
        body = page.inner_text("body")
        check("settings: 主题下拉打开", "浅色" in body)
        page.get_by_text("浅色", exact=True).click()
        page.wait_for_timeout(500)
        is_light = page.evaluate("!document.documentElement.classList.contains('dark')")
        check("settings: 浅色主题生效", is_light)
        theme_btn2 = page.locator("text=桌面应用的外观").locator("xpath=../..").get_by_role("button").first
        theme_btn2.click()
        page.wait_for_timeout(300)
        page.get_by_text("深色", exact=True).click()
        page.wait_for_timeout(500)
        is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
        check("settings: 深色主题生效", is_dark)
        # 主题恢复为"系统"（跟随系统），避免测试后停在深色模式
        theme_btn3 = page.locator("text=桌面应用的外观").locator("xpath=../..").get_by_role("button").first
        theme_btn3.click()
        page.wait_for_timeout(300)
        page.get_by_text("系统", exact=True).click()
        page.wait_for_timeout(500)
        is_system = page.evaluate("!document.documentElement.classList.contains('dark')")
        check("settings: 恢复系统主题", is_system)

        # 语言切换：切到 English
        lang_btn = page.locator("text=桌面应用的界面语言").locator("xpath=../..").get_by_role("button").first
        lang_btn.click()
        page.wait_for_timeout(300)
        page.get_by_text("English", exact=True).click()
        page.wait_for_timeout(600)
        body = page.inner_text("body")
        check("settings: 语言切到英文", "Settings" in body and "Startup" in body)

        # 危险操作对话框（清除会话）
        page.get_by_role("button", name="Clear all sessions").last.click()
        page.wait_for_timeout(400)
        body = page.inner_text("body")
        check("settings: 清除会话确认框(英文)", "Clear all sessions?" in body)
        page.get_by_role("button", name="Cancel").click()
        page.wait_for_timeout(300)
        body = page.inner_text("body")
        check("settings: 取消后确认框关闭", "Clear all sessions?" not in body)

        # 切回中文
        lang_btn2 = page.locator("text=Interface language").locator("xpath=../..").get_by_role("button").first
        lang_btn2.click()
        page.wait_for_timeout(300)
        page.get_by_text("简体中文", exact=True).click()
        page.wait_for_timeout(500)

        # ---- 6. 停止 Agent（点状态点按钮回状态页，点停止）----
        logs_btn2 = page.locator("aside button[aria-label='运行状态']")
        if logs_btn2.count() > 0:
            logs_btn2.first.click()
            page.wait_for_timeout(400)
        page.get_by_role("button", name="停止 Agent").first.click()
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        check("status: 停止后回到未运行", "启动 Agent" in body)

    buf.write(f"\n===== 结果: {passed} 通过, {failed} 失败 =====\n")
    with open(RESULT, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    print(f"done: {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
