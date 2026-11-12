"""验证日志中心：系统日志标签页 + 门店筛选。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def visible_table_rows(page, pane_name):
    """只取可见 tab-pane 内的表格行。"""
    pane = page.locator(f'.el-tab-pane:visible:has(.el-tab-pane__content)').nth(
        0 if pane_name == "device" else 1
    )
    return pane.locator(".el-table__body-wrapper tbody tr")


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_context(viewport={"width": 1680, "height": 950}).new_page()
    errors = []
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    page.goto(f"{BASE}/#/login", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.fill('input[placeholder*="用户名"]', "admin")
    page.fill('input[placeholder="密码"]', "admin123")
    page.click("button:has-text('登 录')")
    page.wait_for_timeout(3000)

    page.goto(f"{BASE}/#/logs", wait_until="networkidle")
    page.wait_for_timeout(2500)

    print("\n[1] 标签页")
    tabs = page.locator(".el-tabs__item")
    labels = [tabs.nth(i).inner_text().strip() for i in range(tabs.count())]
    check("有两个标签页", len(labels) == 2, str(labels))
    check("含「系统日志」标签", any("系统日志" in x for x in labels), str(labels))
    check("含「设备日志」标签", any("设备日志" in x for x in labels), str(labels))
    page.screenshot(path=str(SHOTS / "logs-device-tab.png"))

    print("\n[2] 门店筛选（设备日志）")
    device_pane = page.locator(".el-tab-pane").nth(0)
    store_selects = device_pane.locator(".el-select")
    check("设备日志有门店下拉", store_selects.count() >= 1, f"{store_selects.count()} 个下拉")
    # 打开门店下拉看选项
    try:
        device_pane.locator(".el-select").first.click(timeout=4000)
        page.wait_for_timeout(900)
        opts = page.locator(".el-select-dropdown__item:visible")
        txt = [opts.nth(i).inner_text() for i in range(min(opts.count(), 6))]
        check("门店下拉有选项", opts.count() > 0, f"{opts.count()} 项: {txt}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception as exc:
        check("门店下拉有选项", False, str(exc)[:60])

    print("\n[3] 系统日志标签页")
    page.click(".el-tabs__item:has-text('系统日志')")
    page.wait_for_timeout(2500)
    body = page.inner_text("body")
    check("显示系统日志总量", "系统日志总量" in body)
    check("显示端口问题统计", "端口问题" in body)
    check("显示级别筛选", "全部级别" in body)
    check("显示分类筛选", "全部分类" in body)

    sys_pane = page.locator(".el-tab-pane").nth(1)
    sys_selects = sys_pane.locator(".el-select")
    check("系统日志有门店下拉", sys_selects.count() >= 3, f"{sys_selects.count()} 个下拉")

    rows = sys_pane.locator(".el-table__body-wrapper tbody tr")
    check("系统日志表格有数据行", rows.count() > 0, f"{rows.count()} 行")
    if rows.count():
        first = rows.first.inner_text().replace("\n", " | ")
        check("首行是系统日志内容", "Syslog" not in first and "通知" not in first, first[:90])
    page.screenshot(path=str(SHOTS / "logs-system-tab.png"))

    print("\n[4] 系统日志详情")
    try:
        sys_pane.locator(".el-table__body-wrapper tbody tr").first.locator(
            "button:has-text('详情')"
        ).click(timeout=4000)
        page.wait_for_timeout(1500)
        dlg = page.inner_text("body")
        check("详情弹窗打开", "系统日志详情" in dlg)
        check("详情含来源", "来源" in dlg)
        page.screenshot(path=str(SHOTS / "logs-system-detail.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
    except Exception as exc:
        check("详情弹窗打开", False, str(exc)[:60])

    print("\n[5] 控制台")
    real = [e for e in errors if "favicon" not in e and "ResizeObserver" not in e]
    check("无控制台错误", not real, real[0][:120] if real else "")
    browser.close()

passed = sum(1 for _, c, _ in results if c)
print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
for name, ok, detail in results:
    if not ok:
        print(f"  未通过: {name} {detail}")
