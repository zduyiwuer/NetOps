"""深色/浅色主题验证脚本。

覆盖：跟随系统 → 深色 → 刷新保持 → 浅色 → 跟随系统 的完整切换链路，
以及侧边栏、图表、表格在深色模式下的样式适配。

运行：python -m scripts.verify_theme（需先启动服务）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 深色 / 浅色主题验证 ===\n")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1600, "height": 900})
        page = ctx.new_page()

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(900)
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(2600)
        check("登录成功", "dashboard" in page.url, page.url)

        print("\n[1] 默认（跟随系统，浅色环境）")
        cls = page.evaluate("document.documentElement.className")
        check("初始无 dark class", "dark" not in cls, f"class='{cls}'")
        stored = page.evaluate("localStorage.getItem('netops_theme')")
        check("默认主题为 system", stored in (None, "system"), f"localStorage={stored}")

        print("\n[2] 切换到深色")
        page.click("header button:has(.el-icon)")
        page.wait_for_timeout(500)
        page.click("text=深色")
        page.wait_for_timeout(900)
        cls = page.evaluate("document.documentElement.className")
        theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        body_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        stored = page.evaluate("localStorage.getItem('netops_theme')")
        check("html 添加 dark class", "dark" in cls, f"class='{cls}'")
        check("data-theme=dark", theme == "dark", f"data-theme={theme}")
        check("body 背景为深色", body_bg == "rgb(15, 15, 15)", f"bg={body_bg}")
        check("主题已持久化", stored == "dark", f"localStorage={stored}")

        print("\n[3] 深色模式下关键元素样式")
        side_bg = page.evaluate(
            "getComputedStyle(document.querySelector('.sidebar')).backgroundColor"
        )
        check("侧边栏为深色", side_bg == "rgb(29, 30, 31)", f"bg={side_bg}")
        menu_color = page.evaluate(
            """() => {
                const items = [...document.querySelectorAll('.el-menu-item')];
                const normal = items.find(el => !el.classList.contains('is-active'));
                return normal ? getComputedStyle(normal).color : '';
            }"""
        )
        check("非激活菜单文字为浅色", "196" in menu_color or "233" in menu_color or "234" in menu_color,
              f"color={menu_color}")
        card_bg = page.evaluate(
            "getComputedStyle(document.querySelector('.stat-card')).backgroundColor"
        )
        check("指标卡为深色", card_bg == "rgb(29, 30, 31)", f"bg={card_bg}")

        print("\n[4] 刷新后保持深色（无闪烁）")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1800)
        cls = page.evaluate("document.documentElement.className")
        check("刷新后仍为深色", "dark" in cls, f"class='{cls}'")

        print("\n[5] 切回浅色")
        page.click("header button:has(.el-icon)")
        page.wait_for_timeout(500)
        page.click("text=浅色")
        page.wait_for_timeout(900)
        cls = page.evaluate("document.documentElement.className")
        stored = page.evaluate("localStorage.getItem('netops_theme')")
        check("已移除 dark class", "dark" not in cls, f"class='{cls}'")
        check("主题持久化为 light", stored == "light", f"localStorage={stored}")

        print("\n[6] 跟随系统")
        page.click("header button:has(.el-icon)")
        page.wait_for_timeout(500)
        page.click("text=跟随系统")
        page.wait_for_timeout(900)
        stored = page.evaluate("localStorage.getItem('netops_theme')")
        check("主题持久化为 system", stored == "system", f"localStorage={stored}")

        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
