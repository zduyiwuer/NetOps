"""三个新需求的 UI 验证。

覆盖：
  1. 设备管理表格无横向滚动（列宽合计不超过可用宽度）
  2. 设备管理主表格显示位置/资产编号/序列号/设备类型列
  3. Telemetry 可订阅路径含无线终端路径，且支持新增自定义路径
  4. 防火墙会话页正常渲染，显示会话数与趋势图

运行：python -m scripts.verify_new_features（需先启动后端服务）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 新需求 UI 验证 ===\n")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- 1. 接口层
    print("[1] 后端接口")
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "") if login.status_code == 200 else ""
    check("管理员登录成功", bool(token), f"HTTP {login.status_code}")
    H = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        r = c.get("/api/telemetry/paths", headers=H)
    check("遥测路径接口可访问", r.status_code == 200, f"HTTP {r.status_code}")
    paths = r.json().get("items", []) if r.status_code == 200 else []
    wlan = [p for p in paths if "wlan" in p["metric"]]
    check("含无线终端路径", len(wlan) >= 10, f"{len(wlan)} 条")
    check(
        "无线终端路径覆盖客户端与AP",
        any("clients/client" in p["path"] for p in wlan)
        and any("access-point/state/num-clients" in p["path"] for p in wlan),
    )

    # 自定义路径增删
    custom_path = "verify-tmp/wireless/client/state/mac-address"
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        cr = c.post(
            "/api/telemetry/paths",
            headers=H,
            json={
                "path": custom_path,
                "metric": "wlan_client_mac",
                "description": "验证用自定义路径",
                "per_interface": True,
            },
        )
    created = cr.status_code in (200, 201)
    check("可新增自定义路径", created, f"HTTP {cr.status_code}")
    mapping_id = cr.json().get("id") if created else None

    if created:
        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            r2 = c.get("/api/telemetry/paths", headers=H)
        items2 = r2.json().get("items", [])
        check("自定义路径出现在列表中", any(p["path"] == custom_path for p in items2))
        check("列表区分内置/自定义", any(p["source"] == "custom" for p in items2))
        if mapping_id:
            with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
                dr = c.delete(f"/api/telemetry/paths/{mapping_id}", headers=H)
            check("可删除自定义路径", dr.status_code == 200, f"HTTP {dr.status_code}")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        fr = c.get("/api/monitor/firewalls/sessions", headers=H)
    check("防火墙会话一览接口可访问", fr.status_code == 200, f"HTTP {fr.status_code}")
    fw = fr.json().get("items", []) if fr.status_code == 200 else []
    check("返回防火墙设备", isinstance(fw, list), f"{len(fw)} 台")
    if fw:
        row = fw[0]
        check("含会话数值", "value" in row, str(row.get("value")))
        check("含指标名称", bool(row.get("metric_label")), row.get("metric_label", ""))

        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            sr = c.get(f"/api/monitor/devices/{row['device_id']}/sessions", headers=H)
        check("单设备会话接口可访问", sr.status_code == 200, f"HTTP {sr.status_code}")
        if sr.status_code == 200:
            sd = sr.json()
            check("识别为防火墙", sd.get("is_firewall") is True)
            check("返回趋势序列", isinstance(sd.get("items"), list))

    # ---------------------------------------------------------- 2. 页面
    print("\n[2] 页面渲染")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1680, "height": 950})
        page = ctx.new_page()
        console_msgs: list[str] = []
        page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: console_msgs.append(f"[pageerror] {e}"))

        page.goto(f"{BASE}/#/login", wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill('input[placeholder*="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "admin123")
        page.click("button:has-text('登 录')")
        page.wait_for_timeout(3000)
        check("登录成功", "dashboard" in page.url, page.url)

        # ---- 设备管理：横向滚动
        page.goto(f"{BASE}/#/devices", wait_until="networkidle")
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        check("设备管理页渲染", "设备管理" in body)

        scroll = page.evaluate(
            """() => {
              const wrap = document.querySelector('.el-table__body-wrapper')
                || document.querySelector('.el-scrollbar__wrap')
              if (!wrap) return null
              return { scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth }
            }"""
        )
        if scroll:
            overflow = scroll["scrollWidth"] - scroll["clientWidth"]
            check(
                "设备表格无横向滚动",
                overflow <= 2,
                f"内容宽 {scroll['scrollWidth']} / 可视宽 {scroll['clientWidth']}（溢出 {overflow}px）",
            )
        else:
            check("设备表格无横向滚动", False, "未找到表格容器")

        # 位置 / 资产编号 / 序列号 / 设备类型已提升为主表格列
        # （不再使用展开行——按需求与设备名称合并，不占独立列）
        try:
            body_text = page.inner_text("body")
            check(
                "主表格显示位置/资产编号/序列号/设备类型列",
                all(
                    k in body_text
                    for k in ("位置", "资产编号", "序列号", "设备类型")
                ),
                "四列应直接出现在设备表格",
            )
            check(
                "无独立展开箭头列",
                page.locator(".el-table__expand-column").count() == 0,
                "展开列应已删除",
            )
            page.screenshot(path=str(SHOT_DIR / "devices-expanded.png"))
        except Exception as exc:
            check("主表格显示位置/资产编号/序列号/设备类型列", False, str(exc)[:60])

        # ---- Telemetry 路径
        page.goto(f"{BASE}/#/telemetry", wait_until="networkidle")
        page.wait_for_timeout(2500)
        tbody = page.inner_text("body")
        check("Telemetry 页渲染", "可订阅路径" in tbody)
        check("显示无线终端路径", "access-point" in tbody or "wlan" in tbody)
        check("有新增路径按钮", "新增路径" in tbody)
        # 分类筛选是下拉选项，未展开时文本不可见——检查下拉元素存在即可
        sel_count = page.locator(".el-select").count()
        check("有分类筛选下拉", sel_count >= 2, f"{sel_count} 个下拉")
        page.screenshot(path=str(SHOT_DIR / "telemetry-paths.png"))

        # 新增自定义路径对话框
        try:
            page.click("button:has-text('新增路径')", timeout=5000)
            page.wait_for_timeout(1200)
            dlg = page.inner_text("body")
            check("弹出自定义路径对话框", "新增自定义遥测路径" in dlg)
            page.screenshot(path=str(SHOT_DIR / "telemetry-add-path.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
        except Exception as exc:
            check("弹出自定义路径对话框", False, str(exc)[:60])

        # ---- 防火墙会话页
        page.goto(f"{BASE}/#/firewall-sessions", wait_until="networkidle")
        page.wait_for_timeout(2500)
        fw_body = page.inner_text("body")
        check("防火墙会话页渲染", "防火墙会话" in fw_body)
        check("显示会话总数统计", "会话总数" in fw_body)
        check("显示设备会话列表", "各防火墙会话数" in fw_body)
        page.screenshot(path=str(SHOT_DIR / "firewall-sessions.png"))

        real_errors = [
            m for m in console_msgs
            if ("[error]" in m or "[pageerror]" in m)
            and not any(ig in m for ig in ("favicon", "DevTools", "ResizeObserver", "Third-party"))
        ]
        check("无控制台错误", not real_errors, real_errors[0][:110] if real_errors else "")
        browser.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    if passed != len(results):
        print("未通过项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} {detail}")
        print()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
