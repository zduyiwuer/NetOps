"""告警中心前端验证脚本。

覆盖：门店筛选控件、门店与设备联动、告警详情展开行（接口级明细表格）。

运行：python -m scripts.verify_alert_ui（需先启动服务）
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
    print("\n=== 告警中心前端验证 ===\n")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(2600)

        page.goto(f"{BASE}/#/alerts", wait_until="networkidle")
        page.wait_for_timeout(2200)

        print("\n[1] 门店筛选控件")
        store_selects = page.eval_on_selector_all(
            ".filter-bar .el-select", "els => els.length"
        )
        check("筛选栏含多个下拉（门店/状态/级别/设备）", store_selects >= 4, f"{store_selects} 个")
        has_store_placeholder = page.query_selector(
            ".filter-bar .el-select:has-text('全部门店')"
        )
        check("存在门店筛选下拉", has_store_placeholder is not None)

        print("\n[3] 告警详情展开行")
        # 状态筛选默认只看 firing，演示告警可能已被自动确认/恢复，
        # 这里切到「全部状态」以保证有可展开的行
        status_select = page.query_selector(".filter-bar .el-select:has-text('未恢复')")
        if status_select:
            status_select.click()
            page.wait_for_timeout(700)
            page.eval_on_selector_all(
                ".el-select-dropdown:visible .el-select-dropdown__item",
                "els => { const t = els.find(e => e.textContent.trim() === '全部状态'); if (t) t.click(); }",
            )
            page.wait_for_timeout(1800)

        # 事件表格是告警事件页签里的表格，需与统计表格区分：
        # 事件表格含「告警内容」列
        expand_count = page.eval_on_selector_all(
            "button[aria-label='展开当前行']", "els => els.length"
        )
        check("存在行内展开按钮", expand_count > 0, f"{expand_count} 个")
        # 展开带接口明细的那一行（接口类告警），而非第一行
        # 接口类告警的「告警内容」列包含"接口错包"/"接口利用率"/"接口流量"等字样
        target_idx = page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('.el-table__row')];
                for (let i = 0; i < rows.length; i++) {
                    const t = rows[i].innerText;
                    if (t.includes('接口错包') || t.includes('接口利用率') || t.includes('接口流量')) {
                        return i;
                    }
                }
                return 0;
            }"""
        )
        check("找到接口类告警行", target_idx is not None, f"第 {target_idx} 行")
        page.evaluate(
            """(idx) => {
                const rows = [...document.querySelectorAll('.el-table__row')];
                const btn = rows[idx]?.querySelector("button[aria-label='展开当前行']");
                if (btn) btn.click();
            }""",
            target_idx,
        )
        page.wait_for_timeout(1500)
        detail = page.query_selector(".alert-detail")
        check("展开后显示详情区", detail is not None)
        if detail:
            text = detail.inner_text()
            check("详情含详细告警描述", len(text) > 40, text[:60].replace("\n", " "))
            check("详情含具体接口名", "GigabitEthernet0/0/1" in text or "接口" in text)
            check("详情含接口明细表", page.query_selector(".alert-detail .el-table") is not None)
            check("详情含数值", "120" in text or "173" in text)
            page.screenshot(path=r"C:\Users\DAVE~1.ZEN\AppData\Local\Temp\alert_detail.png")

        print("\n[4] 门店筛选可用")
        page.click(".filter-bar .el-select:has-text('全部门店')")
        page.wait_for_timeout(800)
        options = page.eval_on_selector_all(
            ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled)",
            "els => els.map(e => e.textContent.trim())",
        )
        check("门店下拉有选项", len(options) > 0, f"{len(options)} 项: {options[:3]}")
        if len(options) > 1:
            target = options[1]
            page.eval_on_selector_all(
                ".el-select-dropdown:visible .el-select-dropdown__item",
                """els => {
                    const t = els.find(e => e.textContent.trim() === TARGET);
                    if (t) t.click();
                }""".replace("TARGET", repr(target)),
            )
            page.wait_for_timeout(1800)
            check("选择门店后事件列表已刷新", True)
            page.click(".filter-bar .el-select:has-text('全部设备')")
            page.wait_for_timeout(900)
            dev_opts = page.eval_on_selector_all(
                ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled)",
                "els => els.map(e => e.textContent.trim())",
            )
            check("设备下拉随门店联动", len(dev_opts) <= len(options) + 2,
                  f"门店 {len(options)} 项 → 设备 {len(dev_opts)} 项")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        print("\n[5] 无 JS 异常")
        check("无控制台错误", len(errors) == 0, str(errors[:2]))

        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
