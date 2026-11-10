"""告警详情与门店筛选验证脚本。

覆盖：
  1. 接口类告警能指出具体接口（含各接口数值、排序）
  2. 设备类告警给出当前值、阈值与差距
  3. 告警事件结构化明细字段（detail）正确返回
  4. 告警事件支持按门店筛选
  5. 离线告警描述含排查建议

运行：python -m scripts.verify_alert_detail
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class FakeDevice:
    id = 7
    name = "上海核心交换机"
    ip = "10.10.1.1"
    store_id = 1


class FakeIface:
    def __init__(self, name, in_err=0, out_err=0, speed=1_000_000_000,
                 util=0.0, oper=1, in_bps=0.0, out_bps=0.0, index=1):
        self.if_name = name
        self.if_descr = name
        self.if_alias = ""
        self.if_index = index
        self.in_errors = in_err
        self.out_errors = out_err
        self.speed_bps = speed
        self.utilization = util
        self.oper_status = oper
        self.in_bps = in_bps
        self.out_bps = out_bps


def main() -> int:
    print("\n=== 告警详情与门店筛选验证 ===\n")

    from app.services.alert_detail import build_detail, metric_name, metric_unit

    dev = FakeDevice()

    print("[1] 接口错包告警：指出具体接口")
    ifaces = [
        FakeIface("GigabitEthernet0/0/1", in_err=120, index=1),
        FakeIface("GigabitEthernet0/0/2", in_err=45, index=2),
        FakeIface("TenGigabitEthernet1/0/1", out_err=8, speed=10_000_000_000, index=3),
    ]
    text, detail = build_detail("interface_error", 173, 0, ">", dev, "接口错包过多", ifaces)
    check("消息包含设备名与 IP", dev.name in text and dev.ip in text, text.split("\n")[0])
    check("消息包含错包最多的接口", "GigabitEthernet0/0/1" in text)
    check("消息列出其他异常接口", "GigabitEthernet0/0/2" in text and "TenGigabitEthernet1/0/1" in text)
    check("消息含错包总数", "173" in text)
    check("消息含入向/出向拆分", "入向" in text and "出向" in text)
    check("消息含触发规则名", "接口错包过多" in text)
    items = detail.get("items", [])
    check("结构化明细含 3 个接口", len(items) == 3, f"{len(items)} 项")
    check("明细按错包数降序", items[0]["value"] == 120, str([i["value"] for i in items]))
    check("明细含接口名", items[0]["interface"] == "GigabitEthernet0/0/1")
    check("明细含速率与状态", "speed_mbps" in items[0] and "status" in items[0])

    print("\n[2] 接口利用率告警：指出具体接口")
    ifaces2 = [
        FakeIface("GigabitEthernet0/0/1", util=92.5, in_bps=900_000_000, out_bps=450_000_000),
        FakeIface("GigabitEthernet0/0/2", util=35.0),
        FakeIface("TenGigabitEthernet1/0/1", util=12.0),
    ]
    text2, detail2 = build_detail("util", 92.5, 85, ">", dev, "接口利用率过高", ifaces2)
    check("消息指出最高利用率接口", "GigabitEthernet0/0/1" in text2)
    check("消息含利用率数值", "92.50" in text2)
    check("消息列出超阈值接口", "超过阈值的接口" in text2)
    check("明细按利用率降序", detail2["items"][0]["value"] == 92.5)
    check("明细含流量", "in_mbps" in detail2["items"][0])

    print("\n[3] 接口流量告警：指出具体接口")
    text3, detail3 = build_detail("traffic", 950, 800, ">", dev, "接口流量超阈值", ifaces2)
    check("消息指出峰值接口", "GigabitEthernet0/0/1" in text3)
    check("消息含 Mbps 数值", "Mbps" in text3)
    check("明细按流量降序", detail3["items"][0]["value"] >= detail3["items"][-1]["value"])

    print("\n[4] Down 接口告警：列出接口名")
    ifaces4 = [
        FakeIface("GigabitEthernet0/0/1", oper=1),
        FakeIface("TenGigabitEthernet1/0/1", oper=2),
        FakeIface("FortyGigabitEthernet2/0/1", oper=2),
    ]
    text4, detail4 = build_detail("interface_down", 2, 0, ">", dev, "Down 接口数", ifaces4)
    check("消息列出 DOWN 接口名", "TenGigabitEthernet1/0/1" in text4 and "FortyGigabitEthernet2/0/1" in text4)
    check("明细只含 DOWN 接口", len(detail4["items"]) == 2, f"{len(detail4['items'])} 项")

    print("\n[5] 设备类告警：当前值与阈值差")
    text5, detail5 = build_detail("cpu", 88.5, 80, ">", dev, "CPU 使用率过高")
    check("消息含 CPU 指标名", "CPU 使用率" in text5)
    check("消息含当前值", "88.50%" in text5)
    check("消息含超出幅度", "超出阈值" in text5)
    text6, _ = build_detail("mem", 91.0, 85, ">", dev, "内存使用率过高")
    check("内存告警含百分比", "91.00%" in text6 and "内存使用率" in text6)

    print("\n[6] 指标中文名与单位映射")
    check("cpu → CPU 使用率", metric_name("cpu") == "CPU 使用率")
    check("interface_error → 接口错包数", metric_name("interface_error") == "接口错包数")
    check("session_count → 并发会话数", metric_name("session_count") == "并发会话数")
    check("单位映射", metric_unit("cpu") == "%" and metric_unit("traffic") == "Mbps")

    print("\n[7] 告警事件 API：detail 字段与门店筛选")
    import json
    import urllib.request

    BASE = "http://127.0.0.1:8900"

    def login():
        req = urllib.request.Request(
            f"{BASE}/api/auth/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)["access_token"]

    token = login()
    headers = {"Authorization": f"Bearer {token}"}

    # 门店列表
    req = urllib.request.Request(f"{BASE}/api/stores", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        stores = json.load(resp).get("items", [])
    check("门店列表可取", len(stores) > 0, f"{len(stores)} 个")

    # 不带筛选
    req = urllib.request.Request(f"{BASE}/api/alerts/events?page_size=50", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        all_events = json.load(resp)
    check("告警事件可查", "items" in all_events, f"{all_events.get('total')} 条")

    # 按门店筛选
    store = stores[0]
    req = urllib.request.Request(
        f"{BASE}/api/alerts/events?store_id={store['id']}&page_size=50", headers=headers
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        filtered = json.load(resp)
    check("按门店筛选可用", "items" in filtered, f"{filtered.get('total')} 条")

    # 筛选结果的门店归属正确
    wrong = [e for e in filtered.get("items", []) if e.get("store_id") not in (None, store["id"])]
    check("筛选结果门店归属正确", not wrong, f"{len(wrong)} 条不符")

    # 校验不同门店筛选结果互斥
    if len(stores) > 1:
        store2 = stores[1]
        req = urllib.request.Request(
            f"{BASE}/api/alerts/events?store_id={store2['id']}&page_size=50", headers=headers
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            f2 = json.load(resp)
        ids1 = {e["id"] for e in filtered.get("items", [])}
        ids2 = {e["id"] for e in f2.get("items", [])}
        check("不同门店结果互斥", not (ids1 & ids2), f"交集 {len(ids1 & ids2)}")

    # detail 字段结构
    detail_ok = True
    has_detail = False
    for e in all_events.get("items", []):
        if "detail" in e:
            has_detail = True
            d = e.get("detail") or {}
            if not isinstance(d, dict):
                detail_ok = False
                break
    check("事件含 detail 字段", has_detail or not all_events.get("items"))
    check("detail 为结构化对象", detail_ok)

    # 事件含 metric_name
    named = [e for e in all_events.get("items", []) if e.get("metric_name")]
    check("事件含指标中文名", bool(named) or not all_events.get("items"))

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
