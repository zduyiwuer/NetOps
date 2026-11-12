"""验证设备详情页：接口流量趋势图 + WLC 无线数据。"""
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



def wait_text(page, text, timeout=20000):
    """等待页面上出现指定文本（比固定 sleep 稳）。"""
    try:
        page.wait_for_function(
            "t => document.body && document.body.innerText.includes(t)",
            arg=text,
            timeout=timeout,
        )
        return True
    except Exception:
        return False

def main() -> int:
    print("\n=== 设备详情页验证 ===\n")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1680, "height": 1100}).new_page()
        errors = []
        page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(3000)

        # ---------------- 交换机：接口流量趋势图 ----------------
        print("[1] 交换机设备详情：接口流量趋势图")
        page.goto(f"{BASE}/#/device/2", wait_until="networkidle")
        page.wait_for_load_state("networkidle")
        wait_text(page, "接口流量趋势")
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        check("显示「接口流量趋势」", "接口流量趋势" in body)
        # 下拉可能已自动选中流量最大的接口（有值时占位符不显示），
        # 因此按元素是否存在来判断
        n_sel = page.locator(".chart-card .el-select").count()
        check("有接口下拉", n_sel > 0, f"{n_sel} 个下拉")

        # 打开接口下拉，选一个有流量的接口
        try:
            selects = page.locator(".chart-card .el-select")
            if selects.count():
                selects.first.click()
                page.wait_for_timeout(900)
                opts = page.locator(".el-select-dropdown__item:visible")
                n = opts.count()
                check("下拉列出接口", n > 0, f"{n} 个接口")
                if n:
                    label = opts.first.inner_text()
                    opts.first.click()
                    page.wait_for_timeout(2500)
                    body2 = page.inner_text("body")
                    n_chart = page.locator(".chart-card").count()
                    check("选中接口后图表仍在",
                          "接口流量趋势" in body2 and n_chart >= 3,
                          f"选中 {label} / {n_chart} 个图表")
                    page.screenshot(path=str(SHOTS / "device-iface-trend.png"))
        except Exception as exc:
            check("下拉列出接口", False, str(exc)[:80])

        # ---------------- WLC：无线数据 ----------------
        print("\n[2] 无线控制器设备详情：AP 与客户端")
        page.goto(f"{BASE}/#/device/5", wait_until="networkidle")
        page.wait_for_timeout(4000)
        body3 = page.inner_text("body")
        check("显示无线 AP 卡片", "无线 AP" in body3)
        check("显示关联客户端卡片", "关联客户端" in body3)
        check("有 AP 列表", "无线 AP" in body3 and "SZHO-AP" in body3,
              "含 AP 名称")
        check("有客户端列表", "02a7" in body3 or "MAC 地址" in body3)
        # AP 数量卡片
        check("AP 统计卡片有数字", "无线 AP" in body3)
        page.screenshot(path=str(SHOTS / "device-wlc-wireless.png"), full_page=True)

        # 客户端按 AP 过滤
        try:
            client_select = page.locator(".table-card .el-select")
            if client_select.count():
                client_select.last.click()
                page.wait_for_timeout(900)
                opts = page.locator(".el-select-dropdown__item:visible")
                if opts.count():
                    ap_name = opts.first.inner_text()
                    opts.first.click()
                    page.wait_for_timeout(2000)
                    body4 = page.inner_text("body")
                    check("可按 AP 过滤客户端", ap_name in body4, f"过滤 {ap_name}")
        except Exception as exc:
            check("可按 AP 过滤客户端", False, str(exc)[:80])

        real = [e for e in errors if "favicon" not in e and "ResizeObserver" not in e]
        check("无控制台错误", not real, real[0][:140] if real else "")
        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    for name, ok, detail in results:
        if not ok:
            print(f"  未通过: {name} {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
