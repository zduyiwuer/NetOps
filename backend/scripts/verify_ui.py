"""前端 UI 渲染验证：逐页打开、捕获控制台错误、截图。

运行：python -m scripts.verify_ui
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"

PAGES = [
    ("login", "/#/login", False),
    ("dashboard", "/#/dashboard", True),
    ("devices", "/#/devices", True),
    ("interfaces", "/#/interfaces", True),
    ("topn", "/#/topn", True),
    ("netflow", "/#/netflow", True),
    ("telemetry", "/#/telemetry", True),
    ("topology", "/#/topology", True),
    ("assistant", "/#/assistant", True),
    ("audit", "/#/audit", True),
    ("alerts", "/#/alerts", True),
    ("logs", "/#/logs", True),
    ("backups", "/#/backups", True),
    ("approval", "/#/approval", True),
    ("device-access", "/#/device-access", True),
    ("inspection", "/#/inspection", True),
    ("stores", "/#/stores", True),
    ("assets", "/#/assets", True),
    ("system", "/#/system", True),
]

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"

# 忽略的非致命噪声（Element Plus / 浏览器自身提示）
IGNORE = ("favicon", "DevTools", "ResizeObserver loop", "Third-party cookie")


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    errors: dict[str, list[str]] = {}
    ok_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()

        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        # 登录
        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(SHOT_DIR / "01-login.png"))
        print(f"  [{PASS}] 登录页渲染")

        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(2500)
        url = page.url
        logged_in = "dashboard" in url
        print(f"  [{PASS if logged_in else FAIL}] 登录跳转 — {url}")
        if not logged_in:
            browser.close()
            return 1

        for name, path, _auth in PAGES:
            if name == "login":
                continue
            console_msgs.clear()
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            page.wait_for_timeout(2000)

            # 抓取页面文本，确认有实际内容渲染
            body_text = page.inner_text("body")
            has_content = len(body_text.strip()) > 80

            # 过滤噪声错误
            real_errors = [
                m
                for m in console_msgs
                if ("[error]" in m or "[pageerror]" in m)
                and not any(ig in m for ig in IGNORE)
            ]
            errors[name] = real_errors

            shot = SHOT_DIR / f"{name}.png"
            page.screenshot(path=str(shot), full_page=False)

            status = PASS if (has_content and not real_errors) else FAIL
            if status == PASS:
                ok_count += 1
            detail = f"{len(body_text)} 字符"
            if real_errors:
                detail += f" | {real_errors[0][:110]}"
            print(f"  [{status}] {name:12} {detail}")

        browser.close()

    total = len(PAGES) - 1
    print(f"\n=== UI 验证：{ok_count}/{total} 页面正常 ===")
    if errors:
        print("\n详细错误：")
        for name, errs in errors.items():
            for e in errs[:3]:
                print(f"  [{name}] {e[:200]}")
    print(f"\n截图目录：{SHOT_DIR}\n")
    return 0 if ok_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
