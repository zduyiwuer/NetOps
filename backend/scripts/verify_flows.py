"""NetFlow / IPFIX 与 gNMI Telemetry 验证脚本。

构造真实格式的报文，验证解析、聚合、入库与遥测归一化全链路。
运行：python -m scripts.verify_flows
"""
from __future__ import annotations

import asyncio
import socket
import struct
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.netflow import (
    IPFIX,
    NF_V5,
    NF_V9,
    TemplateCache,
    parse_ipfix,
    parse_netflow,
    parse_netflow_v5,
    parse_netflow_v9,
)
from app.collectors.telemetry import (
    TelemetryPathMapper,
    build_full_path,
    decode_typed_value,
    extract_interface_key,
    normalize_path,
)

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- 报文构造
def build_netflow_v5(records: list[tuple]) -> bytes:
    """构造 NetFlow v5 报文。records: (src, dst, sport, dport, proto, bytes, pkts)"""
    count = len(records)
    header = struct.pack(
        "!HHIIIIBBH", 5, count, 3600000, int(time.time()), 0, 1000, 0, 0, 0
    )
    body = b""
    for src, dst, sport, dport, proto, nbytes, npkts in records:
        body += struct.pack(
            "!4s4s4sHHIIIIHHBBBBHHBBH",
            socket.inet_aton(src),
            socket.inet_aton(dst),
            socket.inet_aton("10.0.0.1"),
            1, 2, npkts, nbytes, 3500000, 3590000,
            sport, dport, 0, 0x1B, proto, 0x28, 65001, 65002, 24, 24, 0,
        )
    return header + body


def _encode_value(val, fl: int) -> bytes:
    """按 IE 长度编码测试数据。"""
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).ljust(fl, b"\x00")[:fl]
    if isinstance(val, str):
        return val.encode("utf-8")[:fl].ljust(fl, b"\x00")
    return int(val).to_bytes(fl, "big")


def build_netflow_v9(templates: dict, data: list[tuple]) -> bytes:
    """构造 NetFlow v9 报文（含模板流集 + 数据流集）。"""
    unix_secs = int(time.time())
    header = struct.pack("!HHIIII", 9, 0, 3600000, unix_secs, 500, 1)

    # 模板流集
    tpl_body = b""
    for tid, fields in templates.items():
        tpl_body += struct.pack("!HH", tid, len(fields))
        for ft, fl in fields:
            tpl_body += struct.pack("!HH", ft, fl)
    # 4 字节对齐
    while len(tpl_body) % 4:
        tpl_body += b"\x00"
    tpl_set = struct.pack("!HH", 0, 4 + len(tpl_body)) + tpl_body

    # 数据流集
    fields = templates[list(templates.keys())[0]]
    data_body = b""
    for rec in data:
        for (ft, fl), val in zip(fields, rec):
            data_body += _encode_value(val, fl)
    while len(data_body) % 4:
        data_body += b"\x00"
    data_set = struct.pack("!HH", 256, 4 + len(data_body)) + data_body

    return header + tpl_set + data_set


def build_ipfix(templates: dict, data: list[tuple]) -> bytes:
    """构造 IPFIX 报文（模板记录含 scope_field_count 字段）。"""
    export_time = int(time.time())
    # 模板集：template_id(2) + field_count(2) + scope_field_count(2) + field_specifier
    tpl_body = b""
    for tid, fields in templates.items():
        tpl_body += struct.pack("!HHH", tid, len(fields), 0)
        for ft, fl in fields:
            tpl_body += struct.pack("!HH", ft, fl)
    while len(tpl_body) % 4:
        tpl_body += b"\x00"
    tpl_set = struct.pack("!HH", 2, 4 + len(tpl_body)) + tpl_body

    fields = templates[list(templates.keys())[0]]
    data_body = b""
    for rec in data:
        for (ft, fl), val in zip(fields, rec):
            data_body += _encode_value(val, fl)
    while len(data_body) % 4:
        data_body += b"\x00"
    # IPFIX 数据集 id 必须与模板 id 一致
    data_set_id = list(templates.keys())[0]
    data_set = struct.pack("!HH", data_set_id, 4 + len(data_body)) + data_body

    body = tpl_set + data_set
    msg_len = 16 + len(body)
    header = struct.pack("!HHIII", 10, msg_len, export_time, 700, 1)
    return header + body


# ---------------------------------------------------------------- 主流程
async def main() -> int:
    print("\n=== NetFlow / IPFIX / Telemetry 验证 ===\n")

    # 确保表结构存在
    import app.models.models  # noqa: F401  触发模型注册到 Base.metadata
    from app.db.session import Base, engine

    Base.metadata.create_all(bind=engine)

    # ---------------------------------------------------------------- v5
    print("[1] NetFlow v5 解析")
    payload = build_netflow_v5([
        ("192.168.1.100", "10.0.0.5", 54321, 443, 6, 1500000, 1200),
        ("192.168.1.101", "8.8.8.8", 45678, 53, 17, 800, 10),
    ])
    recs = parse_netflow_v5(payload, "10.0.0.1")
    check("v5 记录数正确", len(recs) == 2, f"{len(recs)} 条")
    if recs:
        r = recs[0]
        check("v5 源地址解析", r.src_ip == "192.168.1.100", r.src_ip)
        check("v5 目的地址解析", r.dst_ip == "10.0.0.5", r.dst_ip)
        check("v5 端口解析", r.src_port == 54321 and r.dst_port == 443, f"{r.src_port}→{r.dst_port}")
        check("v5 协议解析", r.protocol == 6, f"proto={r.protocol}")
        check("v5 字节数解析", r.bytes_total == 1500000, f"{r.bytes_total}")
        check("v5 包数解析", r.packets_total == 1200, f"{r.packets_total}")
        check("v5 应用识别", r.application == "HTTPS", r.application)
        check("v5 时长计算", r.duration_sec == 90, f"{r.duration_sec}s")

    # ---------------------------------------------------------------- v9
    print("\n[2] NetFlow v9 模板化解析")
    # IE: 8=srcIPv4, 12=dstIPv4, 4=proto, 7=srcPort, 11=dstPort,
    #     1=octetDelta, 2=packetDelta, 10=ingressIf, 14=egressIf, 56=srcMac
    templates = {
        256: [
            (8, 4), (12, 4), (4, 1), (7, 2), (11, 2),
            (1, 4), (2, 4), (10, 2), (14, 2), (56, 6),
        ]
    }
    data = [
        (socket.inet_aton("172.16.0.10"), socket.inet_aton("172.16.0.20"), 6, 40000, 443,
         2400000, 1800, 1, 2, b"\xaa\xbb\xcc\xdd\xee\xff"),
        (socket.inet_aton("172.16.0.11"), socket.inet_aton("1.1.1.1"), 17, 53000, 53,
         600, 6, 1, 3, b"\x11\x22\x33\x44\x55\x66"),
    ]
    payload = build_netflow_v9(templates, data)
    cache = TemplateCache()
    recs = parse_netflow_v9(payload, "10.0.0.1", cache)
    check("v9 记录数正确", len(recs) == 2, f"{len(recs)} 条")
    check("v9 模板已缓存", cache.count >= 1, f"{cache.count} 个模板")
    if recs:
        r = recs[0]
        check("v9 源地址解析", r.src_ip == "172.16.0.10", r.src_ip)
        check("v9 目的地址解析", r.dst_ip == "172.16.0.20", r.dst_ip)
        check("v9 端口解析", r.src_port == 40000 and r.dst_port == 443, f"{r.src_port}→{r.dst_port}")
        check("v9 字节数解析", r.bytes_total == 2400000, f"{r.bytes_total}")
        check("v9 包数解析", r.packets_total == 1800, f"{r.packets_total}")
        check("v9 MAC 解析", r.src_mac == "aa:bb:cc:dd:ee:ff", r.src_mac)
        check("v9 接口解析", r.ingress_interface == 1 and r.egress_interface == 2,
              f"in={r.ingress_interface} out={r.egress_interface}")

    print("\n[2b] v9 数据流集依赖模板（无模板应跳过）")
    # 只保留数据流集、去掉模板流集，模拟「设备重启后平台丢模板」场景
    # v9 header 为 20 字节；流集从 20 开始，长度字段在 22..24
    tpl_set_len = struct.unpack("!H", payload[22:24])[0]
    data_only = payload[:20] + payload[20 + tpl_set_len :]
    empty_cache = TemplateCache()
    recs2 = parse_netflow_v9(data_only, "10.0.0.1", empty_cache)
    check("无模板时不产生脏数据", len(recs2) == 0, f"{len(recs2)} 条")

    # ---------------------------------------------------------------- IPFIX
    print("\n[3] IPFIX (NetFlow v10) 解析")
    ipfix_templates = {
        300: [(8, 4), (12, 4), (4, 1), (7, 2), (11, 2), (85, 8), (86, 4), (150, 4), (151, 4)],
    }
    now_secs = int(time.time())
    ipfix_data = [
        (socket.inet_aton("10.1.1.1"), socket.inet_aton("10.2.2.2"), 6, 1234, 443,
         9_000_000_000, 50000, now_secs - 60, now_secs),
    ]
    payload = build_ipfix(ipfix_templates, ipfix_data)
    cache = TemplateCache()
    recs = parse_ipfix(payload, "10.0.0.1", cache)
    check("IPFIX 记录数正确", len(recs) == 1, f"{len(recs)} 条")
    if recs:
        r = recs[0]
        check("IPFIX 源/目的解析", r.src_ip == "10.1.1.1" and r.dst_ip == "10.2.2.2",
              f"{r.src_ip}→{r.dst_ip}")
        check("IPFIX 64 位字节数解析", r.bytes_total == 9_000_000_000, f"{r.bytes_total}")
        check("IPFIX 包数解析", r.packets_total == 50000, f"{r.packets_total}")
        check("IPFIX 绝对时间戳解析", r.start_time is not None and r.end_time is not None,
              f"{r.start_time} → {r.end_time}")
        check("IPFIX 时长计算", r.duration_sec == 60, f"{r.duration_sec}s")

    print("\n[3b] 版本自动识别")
    check("自动识别 v5", len(parse_netflow(build_netflow_v5([("1.1.1.1", "2.2.2.2", 1, 2, 6, 10, 1)]))) == 1)
    check("自动识别 v9", len(parse_netflow(payload if False else build_netflow_v9(templates, data))) == 2)
    check("自动识别 IPFIX", len(parse_netflow(build_ipfix(ipfix_templates, ipfix_data))) == 1)
    check("空报文不崩溃", parse_netflow(b"") == [] and parse_netflow(b"\x00") == [])
    check("畸形报文不崩溃", parse_netflow(b"\xff\xff\x01\x02\x03") == [])

    # ---------------------------------------------------------------- gNMI 路径
    print("\n[4] gNMI 路径映射")
    check("origin 前缀剥离", normalize_path("openconfig-interfaces:interfaces/interface/state") ==
          "interfaces/interface/state", normalize_path("openconfig-interfaces:interfaces/interface/state"))
    check("key 方括号剥离", normalize_path("interfaces/interface[name=X]/state") ==
          "interfaces/interface/state", normalize_path("interfaces/interface[name=X]/state"))
    check("接口标识提取", extract_interface_key("interfaces/interface[name=Ethernet1/1]/state/counters/in-octets")
          == "Ethernet1/1", extract_interface_key("interfaces/interface[name=Ethernet1/1]/state/counters/in-octets"))
    check("多 key 提取", extract_interface_key("interfaces/interface[name=Eth1,index=3]/state") == "Eth1,index=3")

    mapper = TelemetryPathMapper()
    m1 = mapper.match("openconfig-interfaces:interfaces/interface[name=Ethernet1/1]/state/counters/in-octets")
    check("计数器路径匹配", m1 is not None and m1.metric == "if_in_octets", m1.metric if m1 else "-")
    m2 = mapper.match("Cisco-IOS-XR-gnmi-system:system/cpu/state/utilization")
    check("CPU 路径匹配", m2 is not None and m2.metric == "cpu_usage", m2.metric if m2 else "-")
    m3 = mapper.match("openconfig-interfaces:interfaces/interface/state/oper-status")
    check("接口状态路径匹配", m3 is not None and m3.metric == "if_oper_status", m3.metric if m3 else "-")
    check("未知路径不匹配", mapper.match("foo/bar/baz") is None)

    print("\n[5] gNMI TypedValue 解码")
    from app.proto import gnmi_pb2

    check("int_val 解码", decode_typed_value(gnmi_pb2.TypedValue(int_val=-42)) == -42)
    check("uint_val 解码", decode_typed_value(gnmi_pb2.TypedValue(uint_val=42)) == 42)
    check("string_val 解码", decode_typed_value(gnmi_pb2.TypedValue(string_val="UP")) == "UP")
    check("bool_val 解码", decode_typed_value(gnmi_pb2.TypedValue(bool_val=True)) is True)
    dv = gnmi_pb2.TypedValue(decimal_val=gnmi_pb2.Decimal64(digits=4250, precision=2))
    check("decimal_val 解码", abs(decode_typed_value(dv) - 42.5) < 1e-9, decode_typed_value(dv))
    check("空 TypedValue 返回 None", decode_typed_value(gnmi_pb2.TypedValue()) is None)

    print("\n[6] gNMI 完整推送报文解析")
    # 真实 gNMI 分层推送：prefix 用多个 Path 表达容器层级，
    # update.path.element 为叶子名
    #   interfaces/interface[name=Ethernet1/1]/state/counters/in-octets
    notif = gnmi_pb2.Notification(
        timestamp=int(time.time() * 1e9),
        prefix=[
            gnmi_pb2.Path(element="interfaces"),
            gnmi_pb2.Path(element="interface", key=["name=Ethernet1/1"]),
            gnmi_pb2.Path(element="state"),
            gnmi_pb2.Path(element="counters"),
        ],
        update=[
            gnmi_pb2.Update(
                path=gnmi_pb2.Path(element="in-octets"),
                val=gnmi_pb2.TypedValue(uint_val=123456789),
            ),
            gnmi_pb2.Update(
                path=gnmi_pb2.Path(element="out-octets"),
                val=gnmi_pb2.TypedValue(uint_val=987654321),
            ),
        ],
    )
    sub = gnmi_pb2.SubscriptionResponse(notification=notif)
    resp = gnmi_pb2.SubscribeResponse(update=sub)

    from app.collectors.telemetry import GnmiServicer

    svc = GnmiServicer(lambda s, d: None, lambda p: None, mapper)
    samples = svc.parse_response(resp)
    check("推送报文解析出样本", len(samples) == 2, f"{len(samples)} 条")
    if samples:
        s = samples[0]
        check("样本路径正确", "in-octets" in s.source_path, s.source_path[:80])
        check("样本接口名正确", s.interface == "Ethernet1/1", s.interface)
        check("样本值正确", s.value == 123456789, f"{s.value}")
        check("样本标记为接口级", s.per_interface is True)
        check("出向样本独立解析", samples[1].metric == "if_out_octets", samples[1].metric)

    print("\n[6b] 相对路径（设备未给完整 prefix）")
    from app.collectors.telemetry import build_full_path as _bfp

    p_rel = _bfp(
        [gnmi_pb2.Path(element="interface", key=["name=Ethernet1/1"])],
        gnmi_pb2.Path(element="counters"),
    ) + "/state/in-octets"
    m_rel = mapper.match(p_rel)
    check("相对路径可匹配", m_rel is not None and m_rel.metric == "if_in_octets",
          m_rel.metric if m_rel else "-")

    # ---------------------------------------------------------------- 入库
    print("\n[7] 流记录入库与聚合")
    from app.db.session import SessionLocal
    from app.models.models import Device, FlowAggregate, FlowRecord
    from app.services.netflow_service import store_flows, top_conversations, top_talkers

    db = SessionLocal()
    device = db.query(Device).order_by(Device.id).first()
    device_id = device.id if device else None
    db.close()

    recs = parse_netflow(build_netflow_v5([
        ("192.168.10.1", "10.0.0.1", 1111, 80, 6, 500000, 400),
        ("192.168.10.1", "10.0.0.2", 2222, 443, 6, 300000, 300),
        ("192.168.10.2", "10.0.0.1", 3333, 53, 17, 1000, 12),
    ]), device.ip if device else "")
    n = store_flows(recs, device.ip if device else "")
    check("流记录入库", n == 3, f"{n} 条")

    db = SessionLocal()
    total = db.query(FlowRecord).count()
    check("流记录表可查", total >= 3, f"{total} 条")
    agg = db.query(FlowAggregate).count()
    check("聚合表已生成", agg >= 2, f"{agg} 个会话聚合")
    db.close()

    talkers = top_talkers(SessionLocal(), hours=24, limit=5, device_id=device_id)
    check("Top 通信源查询", len(talkers) > 0, f"{len(talkers)} 项")
    if talkers:
        check("Top 源按字节降序", talkers[0]["bytes_total"] >= (talkers[-1]["bytes_total"] or 0),
              f"{talkers[0]['ip']} {talkers[0]['bytes_total']}")
    convs = top_conversations(SessionLocal(), hours=24, limit=5, device_id=device_id)
    check("Top 会话对查询", len(convs) > 0, f"{len(convs)} 项")

    # ---------------------------------------------------------------- 遥测入库
    print("\n[8] Telemetry 样本归一化入库")
    from app.collectors.telemetry import TelemetrySample
    from app.services.telemetry_service import store_telemetry_samples

    samples = [
        TelemetrySample(device_id=device_id, metric="cpu_usage", value=55.5,
                        timestamp=datetime.now(), source_path="system/cpu/state/utilization"),
        TelemetrySample(device_id=device_id, metric="if_in_octets", value=5_000_000_000,
                        interface="TenGigabitEthernet1/0/1", timestamp=datetime.now(),
                        source_path="interfaces/interface[name=TenGigabitEthernet1/0/1]/state/counters/in-octets",
                        per_interface=True),
        TelemetrySample(device_id=device_id, metric="if_out_octets", value=4_000_000_000,
                        interface="TenGigabitEthernet1/0/1", timestamp=datetime.now(),
                        source_path="interfaces/interface[name=TenGigabitEthernet1/0/1]/state/counters/out-octets",
                        per_interface=True),
        TelemetrySample(device_id=device_id, metric="if_oper_status", value="UP",
                        interface="TenGigabitEthernet1/0/1", timestamp=datetime.now(),
                        source_path="interfaces/interface[name=TenGigabitEthernet1/0/1]/state/oper-status",
                        per_interface=True),
    ]
    stored = store_telemetry_samples(samples, device_id, source="push")
    check("遥测样本入库", stored == 4, f"{stored} 条")

    from app.models.models import DeviceMetric, InterfaceMetric, TelemetrySample as TsModel

    db = SessionLocal()
    dm = db.query(DeviceMetric).filter(DeviceMetric.device_id == device_id).order_by(DeviceMetric.id.desc()).first()
    check("CPU 指标已归一化", dm is not None and dm.cpu_usage == 55.5,
          f"cpu={dm.cpu_usage if dm else '-'}")
    im = (
        db.query(InterfaceMetric)
        .filter(InterfaceMetric.device_id == device_id, InterfaceMetric.if_name == "TenGigabitEthernet1/0/1")
        .order_by(InterfaceMetric.id.desc())
        .first()
    )
    check("接口指标已归一化", im is not None, f"if={im.if_name if im else '-'}")
    if im:
        check("接口计数器入库", im.in_octets == 5_000_000_000, f"{im.in_octets}")
        check("接口状态入库", im.oper_status == 1, f"oper={im.oper_status}")
    ts = db.query(TsModel).filter(TsModel.device_id == device_id).count()
    check("遥测样本审计入库", ts >= 4, f"{ts} 条")
    db.close()

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
