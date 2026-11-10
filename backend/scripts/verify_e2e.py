"""端到端链路验证：导入 → 采集 → 告警 → 日志 → 巡检 → 报告。

直接调用后端 API（HTTP），走真实运行中的服务。
运行：python -m scripts.verify_e2e
"""
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def send_syslog(host: str, port: int, message: str) -> None:
    """向平台发送一条 syslog（RFC3164）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    sock.sendto(message.encode("utf-8"), (host, port))
    sock.close()


def main() -> int:
    print("\n=== NetOps 端到端链路验证 ===\n")
    client = httpx.Client(base_url=BASE, timeout=90)

    # ---------------------------------------------------------------- 认证
    print("[1] 认证与权限")
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    ok = r.status_code == 200 and "access_token" in r.json()
    check("管理员登录", ok, f"status={r.status_code}")
    if not ok:
        print(r.text[:400])
        return 1
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/devices", headers=H)
    check("带 token 访问受保护接口", r.status_code == 200, f"status={r.status_code}")
    r = client.get("/api/devices")
    check("无 token 被拒绝", r.status_code == 401, f"status={r.status_code}")
    r = client.get("/api/auth/config")
    check("公开配置接口", r.status_code == 200, f"ldap_enabled={r.json().get('ldap_enabled')}")

    # ---------------------------------------------------------------- 设备
    print("\n[2] 设备与门店管理")
    r = client.get("/api/stores", headers=H)
    stores = r.json().get("items", [])
    check("门店列表", r.status_code == 200 and len(stores) > 0, f"{len(stores)} 个门店")

    r = client.get("/api/devices", headers=H)
    devices = r.json()
    check("设备列表", r.status_code == 200 and devices["total"] > 0, f"{devices['total']} 台设备")
    dev = devices["items"][0]
    # 门店关联由导入流程保证（见下方「导入设备已关联门店」），
    # 存量设备可能未分配门店，此处只校验字段可用
    check("设备含门店字段", "store_name" in dev, dev.get("store_name") or "未分配")

    # 开启模拟模式便于验证
    client.put(f"/api/devices/{dev['id']}", headers=H, json={"mock_mode": True})
    r = client.get(f"/api/devices/{dev['id']}", headers=H)
    check("模拟模式已开启", r.json().get("mock_mode") is True)

    # ---------------------------------------------------------------- 采集
    print("\n[3] 指标采集")
    r = client.post(f"/api/devices/{dev['id']}/collect", headers=H)
    body = r.json()
    check("手动采集成功", body.get("ok") is True, f"cpu={body.get('cpu_usage')} mem={body.get('mem_usage')}")
    check("采集到接口数据", body.get("interfaces", 0) > 0, f"{body.get('interfaces')} 个接口")

    time.sleep(1.2)
    client.post(f"/api/devices/{dev['id']}/collect", headers=H)
    r = client.get("/api/monitor/interfaces/realtime", headers=H, params={"device_id": dev["id"]})
    items = r.json().get("items", [])
    check("实时接口速率有数据", len(items) > 0, f"{len(items)} 条")
    if items:
        it = items[0]
        check("接口速率已计算", it["in_bps"] > 0 or it["out_bps"] > 0,
              f"in={it['in_bps']}bps out={it['out_bps']}bps util={it['utilization']}%")
        check("协商速率正确", it["speed_mbps"] > 0, f"{it['speed_mbps']} Mbps")

    r = client.get(f"/api/monitor/devices/{dev['id']}/metrics", headers=H, params={"hours": 1})
    check("设备指标历史", len(r.json().get("items", [])) > 0)

    # ---------------------------------------------------------------- 告警
    print("\n[4] 告警引擎")
    r = client.get("/api/alerts/rules", headers=H)
    rules = r.json().get("items", [])
    check("默认告警规则已初始化", len(rules) > 0, f"{len(rules)} 条规则")

    # 造一条必然触发的规则：CPU > 0
    r = client.post(
        "/api/alerts/rules",
        headers=H,
        json={
            "name": "E2E验证-CPU触发",
            "metric": "cpu",
            "operator": ">",
            "threshold": 0.5,
            "severity": "warning",
            "silence_minutes": 0,
        },
    )
    rule_id = r.json().get("id")
    check("创建告警规则", bool(rule_id), f"id={rule_id}")

    client.post(f"/api/devices/{dev['id']}/collect", headers=H)
    time.sleep(0.6)
    r = client.get("/api/alerts/events", headers=H, params={"page_size": 50})
    events = r.json().get("items", [])
    fired = [e for e in events if e.get("rule_id") == rule_id]
    check("告警已触发", len(fired) > 0, f"{len(fired)} 条事件")

    if fired:
        ev = fired[0]
        check("告警含设备与门店", bool(ev.get("device_name")), f"{ev.get('device_name')} / {ev.get('store_name')}")
        r = client.post(f"/api/alerts/events/{ev['id']}/ack", headers=H)
        check("告警确认", r.status_code == 200 and r.json().get("status") == "acked")
        r = client.post(f"/api/alerts/events/{ev['id']}/resolve", headers=H)
        check("告警恢复", r.status_code == 200 and r.json().get("status") == "resolved")

    r = client.get("/api/alerts/statistics", headers=H, params={"days": 1})
    check("告警统计", r.status_code == 200, f"total={r.json().get('total')}")

    # 清理测试规则
    client.delete(f"/api/alerts/rules/{rule_id}", headers=H)

    # ---------------------------------------------------------------- 日志
    print("\n[5] Syslog 日志接收")
    msg = "<134>Oct 10 10:30:00 E2E-TEST %%01SHELL/5/LOGIN: e2e verification login message"
    send_syslog("127.0.0.1", 5514, msg)
    time.sleep(1.5)
    r = client.get("/api/logs", headers=H, params={"keyword": "e2e verification", "page_size": 10})
    logs = r.json().get("items", [])
    check("Syslog 日志已入库", len(logs) > 0, f"{len(logs)} 条")
    if logs:
        lg = logs[0]
        check("日志级别解析正确", lg.get("severity") == "notice", f"severity={lg.get('severity')}")
        check("日志来源标记", lg.get("protocol") == "syslog", lg.get("protocol"))

    r = client.get("/api/logs/statistics", headers=H, params={"hours": 1})
    check("日志统计", r.status_code == 200, f"total={r.json().get('total')}")

    # ---------------------------------------------------------------- TopN
    print("\n[6] 流量 TopN")
    r = client.get("/api/monitor/topn/traffic", headers=H, params={"dimension": "interface", "limit": 5})
    topn = r.json().get("items", [])
    check("接口维度 TopN", r.status_code == 200 and len(topn) > 0, f"{len(topn)} 条")
    r = client.get("/api/monitor/topn/traffic", headers=H, params={"dimension": "store", "limit": 5})
    check("门店维度 TopN", r.status_code == 200, f"{len(r.json().get('items', []))} 条")
    r = client.get("/api/monitor/traffic/timeseries", headers=H, params={"hours": 6})
    check("流量时序", r.status_code == 200)

    # ---------------------------------------------------------------- 导入导出
    print("\n[7] 导入导出")
    r = client.get("/api/import/template.xlsx", headers=H)
    check("Excel 模板下载", r.status_code == 200 and len(r.content) > 3000, f"{len(r.content)} bytes")
    r = client.get("/api/import/template.csv", headers=H)
    check("CSV 模板下载", r.status_code == 200 and len(r.content) > 100)
    r = client.get("/api/export/devices.xlsx", headers=H)
    check("Excel 导出", r.status_code == 200 and len(r.content) > 1000, f"{len(r.content)} bytes")
    r = client.get("/api/export/devices.csv", headers=H)
    check("CSV 导出", r.status_code == 200 and "name,ip" in r.text)

    csv_import = (
        "设备名称,管理IP,厂商,门店编码,型号\n"
        "E2E导入设备,10.99.0.1,cisco,SH001,TEST-2960\n"
    ).encode("utf-8")
    r = client.post(
        "/api/import/devices",
        headers={**H},
        files={"file": ("devices.csv", csv_import, "text/csv")},
    )
    imp = r.json()
    check("CSV 设备导入", imp.get("created") == 1, f"created={imp.get('created')}")
    new_id = None
    r = client.get("/api/devices", headers=H, params={"keyword": "E2E导入设备"})
    if r.json().get("items"):
        new_id = r.json()["items"][0]["id"]
        check("导入设备已关联门店", r.json()["items"][0].get("store_name") == "上海总部")
    # 清理
    if new_id:
        client.delete(f"/api/devices/{new_id}", headers=H)

    # ---------------------------------------------------------------- 巡检
    print("\n[8] 基线巡检与报告")
    r = client.get("/api/inspection/templates", headers=H)
    templates = r.json().get("items", [])
    check("巡检模板已初始化", len(templates) > 0, f"{len(templates)} 个模板")

    tpl = next((t for t in templates if t["vendor"] == "h3c"), templates[0])
    r = client.post(
        "/api/inspection/tasks",
        headers=H,
        json={
            "name": "E2E 巡检任务",
            "template_id": tpl["id"],
            "scope_type": "device",
            "scope_value": str(dev["id"]),
        },
    )
    task = r.json()
    task_id = task.get("id")
    check("创建并执行巡检", bool(task_id) and task.get("ok") is True, f"id={task_id}")
    check("巡检产出结果", task.get("devices", 0) > 0, f"{task.get('devices')} 台设备")
    check("巡检有通过项", task.get("passed", 0) > 0, f"passed={task.get('passed')} failed={task.get('failed')}")

    r = client.get(f"/api/inspection/tasks/{task_id}", headers=H)
    detail = r.json()
    check("巡检明细含检查项", len(detail.get("results", [])) > 0)
    if detail.get("results"):
        checks = detail["results"][0].get("checks", [])
        check("检查项结构完整", len(checks) > 0 and all("status" in c for c in checks), f"{len(checks)} 项")

    r = client.get(f"/api/inspection/tasks/{task_id}/report", headers=H)
    check("HTML 报告生成", r.status_code == 200 and "巡检报告" in r.text, f"{len(r.content)} bytes")
    r = client.get(f"/api/inspection/tasks/{task_id}/report.xlsx", headers=H)
    check("Excel 报告生成", r.status_code == 200 and len(r.content) > 3000, f"{len(r.content)} bytes")

    # ---------------------------------------------------------------- 资产
    print("\n[9] 资产管理")
    r = client.get("/api/assets/summary", headers=H)
    summary = r.json()
    check("资产汇总", r.status_code == 200 and summary.get("total", 0) > 0, f"{summary.get('total')} 台")

    # ---------------------------------------------------------------- 备份
    print("\n[10] 配置备份")
    r = client.post(f"/api/backups/{dev['id']}", headers=H)
    body = r.json()
    r = client.get("/api/backups", headers=H, params={"device_id": dev["id"]})
    backups = r.json().get("items", [])
    check("备份记录已生成", len(backups) > 0, f"{len(backups)} 条")
    if backups:
        b = backups[0]
        check("备份结果有明确状态", b.get("status") in ("success", "failed"), f"status={b.get('status')}")
        if b.get("status") == "success":
            check("备份文件已落盘", bool(b.get("filename")) and b.get("size_bytes", 0) > 0,
                  f"{b.get('filename')} ({b.get('size_kb')} KB)")
            r = client.get(f"/api/backups/download/{b['id']}", headers=H)
            check("备份文件可下载", r.status_code == 200 and len(r.content) > 50, f"{len(r.content)} bytes")
        else:
            check("失败原因已记录", bool(b.get("message")), b.get("message", "")[:60])

    # ---------------------------------------------------------------- 总览
    print("\n[11] 总览聚合")
    r = client.get("/api/monitor/overview", headers=H)
    ov = r.json()
    check("总览数据", r.status_code == 200 and ov.get("device_total", 0) > 0,
          f"设备={ov.get('device_total')} 在线={ov.get('online')} 告警={ov.get('alerts_open')}")

    # 清理巡检任务
    client.delete(f"/api/inspection/tasks/{task_id}", headers=H)

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
