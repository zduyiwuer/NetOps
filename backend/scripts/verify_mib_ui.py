"""验证 MIB 文件界面：上传、检索、探测。"""
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

# 一份测试用的 MIB（含设备支持的标准指标与不支持的私有指标）
MIB_TEXT = """FORTINET-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32, TimeTicks
        FROM SNMPv2-SMI;

fortinetMIB MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Fortinet enterprise MIB."
    ::= { enterprises 12356 }

fgSystem MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Global system objects."
    ::= { fortinetMIB 101 }

fgSysCpuUtilization OBJECT-TYPE
    SYNTAX Integer32 (0..100)
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "CPU utilization percentage."
    ::= { fgSystem 1 }

fgSysMemUsage OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Memory usage in percentage."
    ::= { fgSystem 3 }

mySysUpTime OBJECT-TYPE
    SYNTAX TimeTicks
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "System uptime, standard MIB object."
    ::= { mib-2 1 3 }

END
"""

MIB_FILE = Path(__file__).resolve().parent / "_test_sample.mib"


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== MIB 界面验证 ===\n")
    MIB_FILE.write_text(MIB_TEXT, encoding="utf-8")

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

        page.goto(f"{BASE}/#/system", wait_until="networkidle")
        page.wait_for_timeout(2000)

        print("[1] MIB 标签页")
        page.click(".el-tabs__item:has-text('MIB 文件')")
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        check("MIB 标签页存在", "MIB 文件" in body)
        check("有上传按钮", "上传 MIB 文件" in body)
        check("有指标检索", "指标检索" in body or "指标清单" in body)
        check("有设备探测", "探测设备支持情况" in body)
        page.screenshot(path=str(SHOTS / "mib-panel.png"))

        print("\n[2] 上传 MIB")
        try:
            page.set_input_files(
                ".el-tab-pane:visible input[type=file]", str(MIB_FILE)
            )
            page.wait_for_timeout(3000)
            body2 = page.inner_text("body")
            check("上传后出现在列表", "FORTINET-MIB" in body2 or "_test_sample" in body2)
            check("显示解析出的指标数", "3" in body2)
            page.screenshot(path=str(SHOTS / "mib-uploaded.png"))
        except Exception as exc:
            check("上传后出现在列表", False, str(exc)[:90])

        print("\n[3] 指标检索")
        try:
            inputs = page.locator(".el-tab-pane:visible input[placeholder*='搜索指标']")
            if inputs.count():
                inputs.first.fill("Mem")
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)
                body3 = page.inner_text("body")
                check("检索到内存指标", "fgSysMemUsage" in body3)
            else:
                check("找到搜索框", False, "未找到")
        except Exception as exc:
            check("检索到内存指标", False, str(exc)[:90])

        print("\n[4] 设备探测")
        try:
            # 清空搜索，探测完整清单
            box = page.locator(".el-tab-pane:visible input[placeholder*='搜索指标']")
            if box.count():
                box.first.fill("")
                box.first.press("Enter")
                page.wait_for_timeout(1500)
            # 选择设备（先取文本再点击——点完下拉会关闭，元素随即消失）
            page.locator(".el-tab-pane:visible .el-select").last.click()
            page.wait_for_timeout(900)
            opts = page.locator(".el-select-dropdown__item:visible")
            if opts.count():
                dev_name = opts.first.inner_text()
                opts.first.click()
                page.wait_for_timeout(1200)
                check("可选择设备", True, dev_name)
            else:
                check("可选择设备", False, "下拉无选项")
            # 点探测
            page.click("button:has-text('探测设备支持情况')")
            page.wait_for_timeout(4000)
            body4 = page.inner_text("body")
            check("显示支持/不支持判定", "支持" in body4)
            check("识别出被支持的指标", "mySysUpTime" in body4)
            page.screenshot(path=str(SHOTS / "mib-probe.png"))
        except Exception as exc:
            check("可选择设备", False, str(exc)[:90])

        real = [e for e in errors if "favicon" not in e and "ResizeObserver" not in e]
        check("无控制台错误", not real, real[0][:120] if real else "")
        browser.close()

    try:
        MIB_FILE.unlink()
    except OSError:
        pass

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    for name, ok, detail in results:
        if not ok:
            print(f"  未通过: {name} {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
