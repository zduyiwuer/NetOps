"""验证本轮四项需求：告警指标、HTTP 上报、SNMP 无线数据与漫游、样本删除。

运行：python -m scripts.verify_wireless_v2（需先启动后端服务）
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
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


def wait_text(page, text, timeout=20000):
    try:
        page.wait_for_function(
            "t => document.body && document.body.innerText.includes(t)",
            arg=text, timeout=timeout,
        )
        return True
    except Exception:
        return False


def main() -> int:
    print("\n=== 无线 SNMP / 告警指标 / HTTP 上报 / 样本删除 综合验证 ===\n")

    # ------------------------------------------------ 1. SNMP 无线数据
    print("[1] SNMP 无线数据（含信道 / RSSI / SNR / 收发流量）")
    from app.collectors import wireless as wc
    from app.collectors.engine import Collector
    from app.db.session import SessionLocal
    from app.models.models import (
        AlertRule, Device, TelemetrySample, WirelessAP, WirelessClient, WirelessRoaming,
    )

    db = SessionLocal()
    wlc = None
    try:
        wlc = db.query(Device).filter(Device.vendor == "cisco_wlc").first()
        if wlc is None:
            check("存在 WLC 设备", False, "未找到")
        else:
            s = asyncio.run(Collector(wlc).collect(collect_interface=False, collect_system=True))
            check("采集到 AP 列表", len(s.wireless_aps) > 0, f"{len(s.wireless_aps)} 个")
            check("采集到客户端列表", len(s.wireless_clients) > 0, f"{len(s.wireless_clients)} 个")
            with_ch = [a for a in s.wireless_aps if a["channels"]]
            check("AP 有信道信息", len(with_ch) > 0,
                  f"{len(with_ch)}/{len(s.wireless_aps)}，例 {with_ch[0]['channels'] if with_ch else '-'}")
            named = [c for c in s.wireless_clients if c["ap_name"]]
            chd = [c for c in s.wireless_clients if c["channel"]]
            rssi = [c for c in s.wireless_clients if c["rssi"]]
            check("客户端有关联 AP", len(named) > 0, f"{len(named)}/{len(s.wireless_clients)}")
            check("客户端有信道", len(chd) > 0, f"{len(chd)} 个")
            check("客户端有 RSSI", len(rssi) > 0,
                  f"例 {rssi[0]['rssi']} dBm @ {rssi[0]['ap_name']}")
            check("客户端有 SSID", any(c["ssid"] for c in s.wireless_clients),
                  s.wireless_clients[0]["ssid"])
            check("客户端有收发流量", any(c["bytes_in"] or c["bytes_out"] for c in s.wireless_clients))
    finally:
        db.close()

    # 漫游检测（模拟）
    print("\n[2] 漫游检测")
    db = SessionLocal()
    try:
        if wlc:
            db.query(WirelessRoaming).filter(WirelessRoaming.device_id == wlc.id).delete()
            db.commit()
            cli = (
                db.query(WirelessClient)
                .filter(WirelessClient.device_id == wlc.id, WirelessClient.bssid != "")
                .first()
            )
            aps = db.query(WirelessAP).filter(WirelessAP.device_id == wlc.id).all()
            if cli and len(aps) > 1:
                # 确定性单元测试：直接构造新旧快照调 detect_roaming，
                # 不依赖真实客户端的在线状态
                other = [a for a in aps if a.radio_mac != cli.bssid][0]
                fake_clients = [
                    {
                        "mac": "aabbccddeeff",
                        "ip": "10.0.0.1",
                        "username": "",
                        "bssid": other.radio_mac,
                        "ap_name": other.name,
                        "ssid": "test",
                        "slot": 1,
                        "channel": 52,
                        "signal_strength": -60,
                        "rssi": -60,
                        "snr": 30,
                        "bytes_in": 1,
                        "bytes_out": 1,
                        "packets_in": 1,
                        "packets_out": 1,
                        "state": "Run",
                    }
                ]
                # 库里该 MAC 的旧记录指向 cli 原来的 AP，
                # fake 新快照指向另一个 AP（other）→ 应触发漫游
                cli.mac = "aabbccddeeff"
                db.commit()
                evs = wc.detect_roaming(wlc.id, fake_clients)
                check("detect_roaming 能识别 AP 变化", len(evs) >= 1,
                      f"{len(evs)} 条")
                if evs:
                    e = evs[0]
                    check("漫游事件字段完整",
                          all(e.get(k) is not None for k in ("mac", "from_bssid", "to_bssid", "ts")),
                          f"{e['mac']} {e['from_bssid']}->{e['to_bssid']}")
                    check("漫游记录已入库", db.query(WirelessRoaming).count() >= 1)
    finally:
        db.close()

    # ------------------------------------------------ 3. 告警指标
    print("\n[3] 告警规则的新指标")
    from app.models.models import DeviceMetric
    db = SessionLocal()
    try:
        # 拿飞塔的 session_count 做实测
        fg = db.query(Device).filter(Device.vendor == "fortinet").first()
        if fg:
            db.query(AlertRule).filter(AlertRule.name == "__test_session_rule").delete()
            db.commit()
            rule = AlertRule(
                name="__test_session_rule",
                device_id=fg.id,
                metric="session_count",
                operator=">",
                threshold=0,   # 任何会话数都触发，便于验证取值
                severity="info",
                enabled=True,
            )
            db.add(rule)
            db.commit()
            from app.services.alerting import _extract_value
            latest = (
                db.query(DeviceMetric)
                .filter(DeviceMetric.device_id == fg.id)
                .order_by(DeviceMetric.id.desc())
                .first()
            )
            if latest is not None:
                v = _extract_value("session_count", fg, latest, None)
                check("告警取值 session_count", v is not None, f"{v}")
            else:
                check("有飞塔指标数据", False, "无 DeviceMetric")
            db.query(AlertRule).filter(AlertRule.name == "__test_session_rule").delete()
            db.commit()
        # extra_metrics 解析函数
        from app.services.alerting import _extra_metric
        class _Fake:
            extra_metrics = '{"client_count": 129, "session_count": 5165}'
        check("extra_metrics 解析", _extra_metric(_Fake(), "client_count") == 129.0)
    finally:
        db.close()

    # ------------------------------------------------ 4. HTTP 上报 + 样本删除
    print("\n[4] HTTP 遥测上报与样本删除")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    H = {"Authorization": f"Bearer {token}"}
    check("管理员登录", bool(token))

    # 未认证上报应 401
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        r401 = c.post("/api/telemetry/ingest", json={"device": "sw", "samples": []})
    check("未认证上报被拒", r401.status_code == 401, f"HTTP {r401.status_code}")

    # 带用户 token 上报
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        ok = c.post(
            "/api/telemetry/ingest",
            headers=H,
            json={
                "device": "sw",
                "samples": [
                    {"metric": "cpu_usage", "value": 23.5},
                    {"metric": "if_in_octets", "value": 123456, "interface": "Gi1/0/1"},
                ],
            },
        )
    check("HTTP 上报成功", ok.status_code == 200 and ok.json().get("ok"),
          str(ok.json())[:70])
    stored = ok.json().get("stored", 0)
    check("样本已归一化入库", stored >= 1, f"stored={stored}")

    # 样本可查询（source=http）
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        sm = c.get("/api/telemetry/samples", headers=H, params={"limit": 50}).json()
    http_samples = [s for s in sm.get("items", []) if s.get("source") == "http"]
    check("HTTP 样本可查询", len(http_samples) > 0, f"{len(http_samples)} 条")

    # 删除刚才上报的样本（按 older_than 不含刚才 → 用 metric+device 删）
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        before = c.get("/api/telemetry/samples", headers=H, params={"limit": 1000}).json()
        n_before = len(before.get("items", []))
        dele = c.delete(
            "/api/telemetry/samples",
            headers=H,
            params={"metric": "if_in_octets", "device_id": 2},
        )
    check("样本删除接口可用", dele.status_code == 200 and dele.json().get("deleted", 0) > 0,
          f"删除 {dele.json().get('deleted', 0)} 条")

    # ------------------------------------------------ 5. 界面
    print("\n[5] 界面")
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

        # WLC 详情
        if wlc:
            page.goto(f"{BASE}/#/device/{wlc.id}", wait_until="networkidle")
            page.wait_for_load_state("networkidle")
            wait_text(page, "无线 AP")
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            check("WLC 详情有信道列", "信道" in body)
            check("WLC 详情有 RSSI 列", "RSSI" in body)
            check("WLC 详情有漫游记录表", "漫游记录" in body)
            check("AP 表显示信道值", "slot0:" in body or "slot1:" in body)
            check("客户端显示 dBm", "dBm" in body)
            page.screenshot(path=str(SHOTS / "device-wlc-snmp.png"), full_page=True)

        # 告警规则页：通过 API 创建新指标规则，验证列表渲染出可读标签
        with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
            mk = c.post(
                "/api/alerts/rules",
                headers=H,
                json={
                    "name": "__ui_test_client_count",
                    "device_id": (wlc.id if wlc else None),
                    "metric": "client_count",
                    "operator": ">",
                    "threshold": 500,
                    "severity": "warning",
                },
            )
        created = mk.status_code in (200, 201)
        page.goto(f"{BASE}/#/alerts", wait_until="networkidle")
        wait_text(page, "告警规则")
        page.wait_for_timeout(2000)
        # 切到告警规则标签
        try:
            page.click(".el-tabs__item:has-text('告警规则')", timeout=8000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        body_a = page.inner_text("body")
        check("新指标规则创建成功", created, f"HTTP {mk.status_code}")
        check("规则列表渲染无线客户端数标签", "无线客户端数" in body_a)
        # 清理测试规则
        if created:
            rid = mk.json().get("id") or (mk.json().get("item") or {}).get("id")
            if rid:
                with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
                    c.delete(f"/api/alerts/rules/{rid}", headers=H)

        # telemetry 页
        page.goto(f"{BASE}/#/telemetry", wait_until="networkidle")
        wait_text(page, "遥测样本")
        page.wait_for_timeout(2000)
        body3 = page.inner_text("body")
        check("telemetry 页有清理按钮", "清理样本" in body3)

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
