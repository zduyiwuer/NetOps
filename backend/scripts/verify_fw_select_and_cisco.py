"""验证：防火墙会话设备选择 + Cisco 交换机采集。

覆盖：
  1. **防火墙会话可选择设备** —— 下拉选设备后趋势图与统计随之切换
  2. **点击表格行可切换选中**
  3. **Cisco 交换机采集** —— SNMP community 容错、表型 OID 的 WALK 取值
  4. **community 自动修正** —— 修正后数据库里存的是清洗后的值

运行：python -m scripts.verify_fw_select_and_cisco（需先启动后端服务）
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 防火墙选择 + Cisco 采集验证 ===\n")

    # -------------------------------------------------- 1. Cisco 采集
    print("[1] Cisco 交换机采集")
    from app.collectors.engine import Collector
    from app.collectors.snmp import _strip_access_suffix
    from app.db.session import SessionLocal
    from app.models.models import Device

    check("community 后缀清洗正确",
          _strip_access_suffix("abc ro") == "abc"
          and _strip_access_suffix("abc RW") == "abc"
          and _strip_access_suffix("abc") == "",
          "ro / RW / 无后缀")

    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.vendor == "cisco").first()
        if dev is None:
            check("存在 Cisco 设备", False, "未找到")
            return 1
        print(f"  设备 {dev.name} ({dev.ip})")

        # 故意写入带冗余后缀的 community，验证容错 + 自动修正
        original = dev.snmp_community
        broken = f"{original} ro"
        dev.snmp_community = broken
        db.commit()
        db.close()

        collector = Collector(dev)
        sample = asyncio.run(collector.collect(collect_interface=True, collect_system=True))
        s = sample.system
        check("SNMP 采集成功", s.reachable is True, f"source={s.source}")
        check("CPU 采到值", s.cpu_usage is not None, f"cpu={s.cpu_usage}")
        check("内存使用率采到值", s.mem_usage is not None, f"mem={s.mem_usage}%")
        check("总内存采到值", s.mem_total_mb is not None, f"total={s.mem_total_mb} MB")
        check("接口数据采到", len(sample.interfaces) > 0, f"{len(sample.interfaces)} 个接口")
        check("运行时长正确（Timeticks 已换算）",
              s.uptime_sec is not None and s.uptime_sec < 10 * 365 * 86400,
              f"{s.uptime_sec} 秒")

        db2 = SessionLocal()
        try:
            dev2 = db2.query(Device).filter(Device.id == dev.id).first()
            check("community 已自动修正",
                  dev2.snmp_community == original,
                  f"{broken!r} -> {dev2.snmp_community!r}")
        finally:
            db2.close()
    finally:
        try:
            db.close()
        except Exception:
            pass

    # -------------------------------------------------- 2. 防火墙选择
    print("\n[2] 防火墙会话设备选择")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    check("管理员登录成功", bool(token), f"HTTP {login.status_code}")
    H = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        fw = c.get("/api/monitor/firewalls/sessions", headers=H).json()
    items = fw.get("items", [])
    check("有防火墙设备", len(items) > 0, f"{len(items)} 台")
    check("列表含设备 ID", all("device_id" in i for i in items))

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

        page.goto(f"{BASE}/#/firewall-sessions", wait_until="networkidle")
        page.wait_for_timeout(2500)

        # 设备下拉
        selects = page.locator(".page-header .el-select")
        check("顶部有设备选择下拉", selects.count() >= 1, f"{selects.count()} 个")
        if selects.count():
            selects.first.click(timeout=4000)
            page.wait_for_timeout(900)
            opts = page.locator(".el-select-dropdown__item:visible")
            opt_texts = [opts.nth(i).inner_text().replace("\n", " ") for i in range(opts.count())]
            check("下拉列出各防火墙", opts.count() > 0, f"{opts.count()} 项: {opt_texts}")
            # 选第二台（如果有）
            if opts.count() >= 1:
                target = items[0]
                opts.first.click(timeout=4000)
                page.wait_for_timeout(2500)
                body = page.inner_text("body")
                check("选中后显示该设备名", target["name"] in body, target["name"])
                check("选中后卡片文案切换", "当前选中防火墙" in body)
                page.screenshot(path=str(SHOTS / "firewall-sessions-selected.png"))

        # 点击表格行切换
        rows = page.locator(".el-table__body-wrapper tbody tr")
        if rows.count():
            before = page.inner_text("body")
            rows.first.click(timeout=4000)
            page.wait_for_timeout(2500)
            after = page.inner_text("body")
            check("点击行可切换选中状态", before != after, "内容有变化")
            page.screenshot(path=str(SHOTS / "firewall-sessions-row-click.png"))

        real = [e for e in errors if "favicon" not in e and "ResizeObserver" not in e]
        check("无控制台错误", not real, real[0][:120] if real else "")
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
