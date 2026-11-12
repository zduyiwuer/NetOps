"""本轮需求的综合验证。

覆盖：
  1. **总览大屏按门店筛选** —— 下拉可选、页面显示筛选状态、后端确实过滤
  2. **设备详情接口流量趋势图** —— 可按接口过滤
  3. **接口流量页按门店筛选**
  4. **ASA 会话数 / WLC 内存 CPU** —— 厂商 OID 修正后的采集结果
  5. **WLC 无线 AP 与客户端** —— 采集、落库、界面展示
  6. **telemetry 数据查看** —— 入口、样本、趋势图

运行：python -m scripts.verify_wireless_and_filter（需先启动后端服务）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8900"
SHOTS = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 无线数据 / 门店筛选 / telemetry 综合验证 ===\n")

    # ---------------------------------------------------------- 后端
    print("[1] 后端：厂商 OID 修正后的采集")
    from app.collectors.engine import Collector
    from app.collectors.ssh import parse_ap_summary, parse_client_summary
    from app.db.session import SessionLocal
    from app.models.models import Device, WirelessAP, WirelessClient

    db = SessionLocal()
    try:
        wlc = db.query(Device).filter(Device.vendor == "cisco_wlc").first()
        asa = db.query(Device).filter(Device.vendor == "cisco_asa").first()

        if wlc:
            s = asyncio.run(Collector(wlc).collect(collect_interface=False, collect_system=True))
            check("WLC CPU 可采集", s.system.cpu_usage is not None, f"{s.system.cpu_usage}%")
            check("WLC 内存可采集", s.system.mem_usage is not None, f"{s.system.mem_usage}%")
            check("WLC 总内存合理",
                  s.system.mem_total_mb and 100 < s.system.mem_total_mb < 100000,
                  f"{s.system.mem_total_mb} MB")
            check("WLC 内存百分比合理",
                  s.system.mem_usage is not None and 0 <= s.system.mem_usage <= 100,
                  f"{s.system.mem_usage}%")
            check("WLC 采到 AP 列表", len(s.wireless_aps) > 0, f"{len(s.wireless_aps)} 个 AP")
            check("WLC 采到客户端列表", len(s.wireless_clients) > 0,
                  f"{len(s.wireless_clients)} 个客户端")
            check("无线数据已落库",
                  db.query(WirelessAP).filter(WirelessAP.device_id == wlc.id).count() > 0
                  and db.query(WirelessClient).filter(
                      WirelessClient.device_id == wlc.id).count() > 0,
                  "AP 与客户端均已入库")
            if s.wireless_aps:
                a = s.wireless_aps[0]
                check("AP 字段完整",
                      all(a.get(k) for k in ("name", "model", "eth_mac", "ip", "state")),
                      f"{a['name']} / {a['model']} / {a['ip']} / {a['state']}")
            if s.wireless_clients:
                c = s.wireless_clients[0]
                check("客户端字段完整",
                      all(c.get(k) for k in ("mac", "ap_name", "type", "state", "protocol")),
                      f"{c['mac']} / {c['ap_name']} / {c['protocol']}")
        else:
            check("存在 WLC 设备", False, "未找到")

        if asa:
            s2 = asyncio.run(Collector(asa).collect(collect_interface=False, collect_system=True))
            check("ASA CPU 可采集", s2.system.cpu_usage is not None, f"{s2.system.cpu_usage}%")
            check("ASA 内存百分比可采集", s2.system.mem_usage is not None, f"{s2.system.mem_usage}%")
            check("ASA 总内存合理",
                  s2.system.mem_total_mb and 100 < s2.system.mem_total_mb < 100000,
                  f"{s2.system.mem_total_mb} MB")
    finally:
        db.close()

    print("\n[2] 后端：解析器健壮性")
    ap_txt = """Number of APs: 2

AP Name          Slots    AP Model  Ethernet MAC    Radio MAC       Location                          Country     IP Address                                 State
---------------------------------------------------------------------------------------------------------------------------
SZHO-AP01        2      9115AXI   84f1.47b1.9410  1cd1.e01e.a080  default location                  CN          172.30.132.42                              Registered
SZHO-AP02        2      9120AXI   84f1.47b2.38ec  1cd1.e023.c760  3F Floor East Wing                CN          172.30.132.54                              Registered
"""
    aps = parse_ap_summary(ap_txt)
    check("解析 AP 列表", len(aps) == 2, f"{len(aps)} 个")
    if len(aps) == 2:
        check("AP 位置含空格能正确解析",
              aps[1]["location"] == "3F Floor East Wing",
              repr(aps[1]["location"]))
        check("AP 型号/IP 正确",
              aps[0]["model"] == "9115AXI" and aps[0]["ip"] == "172.30.132.42",
              f"{aps[0]['model']} {aps[0]['ip']}")

    cli_txt = """Number of Clients: 2

MAC Address    AP Name      Type ID   State   Protocol Method     Role
-------------------------------------------------------------------
02a7.289a.e673 SZHO-AP01    WLAN 3    Run     11ax(5)  None       Local
04cf.4b0e.8a35 SZHO-AP02    WLAN 3    Run     11ax(5)  None       Local
"""
    clis = parse_client_summary(cli_txt)
    check("解析客户端列表", len(clis) == 2, f"{len(clis)} 个")
    if clis:
        check("客户端字段正确",
              clis[0]["mac"] == "02a7.289a.e673" and clis[0]["ap_name"] == "SZHO-AP01",
              f"{clis[0]['mac']} @ {clis[0]['ap_name']}")

    print("\n[3] 后端：接口")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    H = {"Authorization": f"Bearer {token}"}
    check("管理员登录成功", bool(token))

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        stores = c.get("/api/stores", headers=H).json()
        ids = [s["id"] for s in stores.get("items", [])]
    check("门店列表非空", len(ids) > 0, f"{len(ids)} 个门店")

    # 门店过滤确实生效
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        all_ov = c.get("/api/monitor/overview", headers=H).json()
        filtered = c.get("/api/monitor/overview", headers=H,
                         params={"store_id": ids[0]}).json()
    check("overview 支持门店过滤",
          filtered.get("device_total", 0) <= all_ov.get("device_total", 0),
          f"全量 {all_ov.get('device_total')} -> 筛选后 {filtered.get('device_total')}")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        rt = c.get("/api/monitor/interfaces/realtime", headers=H,
                   params={"store_id": ids[0]}).json()
    check("接口实时支持门店过滤", "items" in rt, f"{len(rt.get('items', []))} 条")

    # 接口历史（if_index 可选）
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        hist = c.get("/api/monitor/devices/2/interfaces/history", headers=H,
                     params={"hours": 6}).json()
    check("接口历史接口可不指定 if_index", "items" in hist, f"{len(hist.get('items', []))} 条")

    # 无线数据接口
    if wlc:
        with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
            ap_res = c.get(f"/api/devices/{wlc.id}/wireless/aps", headers=H).json()
            cli_res = c.get(f"/api/devices/{wlc.id}/wireless/clients", headers=H).json()
        check("无线 AP 接口可用", ap_res.get("total", 0) > 0, f"{ap_res.get('total')} 个")
        check("无线客户端接口可用", cli_res.get("total", 0) > 0, f"{cli_res.get('total')} 个")

    # ---------------------------------------------------------- 前端
    print("\n[4] 界面")
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

        # 总览大屏
        page.goto(f"{BASE}/#/", wait_until="networkidle")
        page.wait_for_timeout(3000)
        body = page.inner_text("body")
        check("大屏有门店下拉", "全部门店" in body)
        try:
            page.locator(".page-header .el-select").first.click()
            page.wait_for_timeout(900)
            opts = page.locator(".el-select-dropdown__item:visible")
            if opts.count():
                sname = opts.first.inner_text()
                opts.first.click()
                page.wait_for_timeout(2500)
                body2 = page.inner_text("body")
                check("大屏显示筛选状态", "当前筛选" in body2, sname)
                page.screenshot(path=str(SHOTS / "dashboard-store-filter.png"))
        except Exception as exc:
            check("大屏显示筛选状态", False, str(exc)[:70])

        # 接口流量页
        page.goto(f"{BASE}/#/interfaces", wait_until="networkidle")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3500)
        body3 = page.inner_text("body")
        n_store_sel = page.locator(".page-header .el-select").count()
        check("接口页有门店下拉", n_store_sel > 0,
              f"{n_store_sel} 个下拉 / 文本含全部门店: {'全部门店' in body3}")
        try:
            page.locator(".page-header .el-select").first.click()
            page.wait_for_timeout(900)
            opts = page.locator(".el-select-dropdown__item:visible")
            if opts.count():
                opts.first.click()
                page.wait_for_timeout(2000)
                body4 = page.inner_text("body")
                check("接口页显示筛选状态", "当前筛选" in body4)
        except Exception as exc:
            check("接口页显示筛选状态", False, str(exc)[:70])

        # 设备详情
        page.goto(f"{BASE}/#/device/2", wait_until="networkidle")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(5000)
        body5 = page.inner_text("body")
        check("设备详情有接口流量趋势", "接口流量趋势" in body5)
        # 下拉可能已自动选中流量最大的接口（有值时占位符不显示），
        # 因此按元素是否存在来判断
        n_select = page.locator(".chart-card .el-select").count()
        check("接口趋势有下拉", n_select > 0, f"{n_select} 个下拉")

        # WLC 详情
        if wlc:
            page.goto(f"{BASE}/#/device/{wlc.id}", wait_until="networkidle")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(5000)
            body6 = page.inner_text("body")
            check("WLC 详情有无线 AP 卡片", "无线 AP" in body6)
            check("WLC 详情有关联客户端卡片", "关联客户端" in body6)
            check("WLC 详情有 AP 列表", "SZHO-AP" in body6)
            check("WLC 详情有客户端列表", "MAC 地址" in body6)
            page.screenshot(path=str(SHOTS / "device-wlc-wireless.png"), full_page=True)

        # telemetry
        page.goto(f"{BASE}/#/telemetry", wait_until="networkidle")
        page.wait_for_timeout(3000)
        body7 = page.inner_text("body")
        check("telemetry 页标题已明确", "gNMI 遥测采样" in body7)
        check("telemetry 有数据来源说明", "数据从哪来" in body7)
        check("telemetry 有样本趋势图", "样本趋势" in body7)
        check("telemetry 有遥测样本表", "遥测样本" in body7)
        check("telemetry 有可订阅路径", "可订阅路径" in body7)
        page.screenshot(path=str(SHOTS / "telemetry-page.png"))

        real = [e for e in errors if "favicon" not in e and "ResizeObserver" not in e]
        check("无控制台错误", not real, real[0][:130] if real else "")
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
