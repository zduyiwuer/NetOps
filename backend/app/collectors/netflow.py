"""NetFlow / IPFIX 采集器。

支持三种报文版本：
- **NetFlow v5**  ：固定头 24 字节 + 48 字节定长记录，UDP 传统格式
- **NetFlow v9**  ：模板化（Template FlowSet + Data FlowSet），需缓存模板
- **IPFIX (v10)** ：模板化，IE 长度可变，支持 64 位计数与企业自定义 IE

解析后统一归一化为 `FlowRecord` 数据类，再由上层聚合入库。
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("netops.netflow")

# ---------------------------------------------------------------- 常量
NF_V5 = 5
NF_V9 = 9
IPFIX = 10

# UDP 监听端口
DEFAULT_LISTEN_PORTS = {
    2055: "netflow-v9",
    4739: "ipfix",
    9995: "netflow-v5",
}

# TCP 监听端口（部分设备用 TCP 导出）
DEFAULT_TCP_PORTS = {2055: "netflow-v9-tcp"}

# ---------------------------------------------------------------- IE 定义
# IANA IPFIX Information Element 编号 → 字段名
IE_NAMES: Dict[int, str] = {
    1: "octetDeltaCount",
    2: "packetDeltaCount",
    4: "protocolIdentifier",
    5: "ipClassOfService",
    6: "tcpControlBits",
    7: "sourceTransportPort",
    8: "sourceIPv4Address",
    10: "ingressInterface",
    11: "destinationTransportPort",
    12: "destinationIPv4Address",
    14: "egressInterface",
    15: "ipNextHopIPv4Address",
    16: "sourceMacAddress",
    17: "postDestinationMacAddress",
    18: "vlanId",
    21: "flowEndSysUpTime",
    22: "flowStartSysUpTime",
    27: "sourceIPv6Address",
    28: "destinationIPv6Address",
    32: "mplsTopLabelType",
    33: "mplsTopLabelIPv4Address",
    34: "samplerId",
    35: "samplerMode",
    36: "samplerRandomInterval",
    52: "minimumTTL",
    53: "maximumTTL",
    56: "sourceMacAddress",
    61: "flowDirection",
    80: "destinationMacAddress",
    81: "postDestinationMacAddress",
    85: "octetTotalCount",
    86: "packetTotalCount",
    136: "flowEndReason",
    138: "flowEndReasonIPv4",
    144: "flowStartSeconds",
    150: "flowStartSeconds",
    151: "flowEndSeconds",
    152: "flowStartMilliseconds",
    153: "flowEndMilliseconds",
    154: "flowStartMicroseconds",
    155: "flowEndMicroseconds",
    156: "flowStartNanoseconds",
    157: "flowEndNanoseconds",
    160: "destinationMacAddress",
    192: "interfaceName",
    195: "dot1qVlanId",
    209: "flowSelectorAlgorithm",
    344: "samplingPacketInterval",
    345: "flowSamplingTimeInterval",
    346: "flowSamplingTimeSpacing",
    347: "ipTTL",
    348: "ipDiffServCodePoint",
    403: "deltaservCodePoint",
    385: "octetTotalCount",
    386: "packetTotalCount",
    479: "flowSelectorAlgorithm",
    480: "flowSelectorId",
}

# 字节序敏感的 IE（需要特殊处理）
IE_MAC = {16, 56, 17, 80, 81, 160}
IE_IPV4 = {8, 12, 15, 26, 30}
IE_IPV6 = {27, 28, 31}
IE_STRING = {18, 192}

PROTO_NAMES = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 47: "GRE", 89: "OSPF", 132: "SCTP", 137: "MPLS-in-IP"}


@dataclass
class FlowRecord:
    """归一化后的流记录。"""

    src_ip: str = ""
    dst_ip: str = ""
    src_port: int = 0
    dst_port: int = 0
    protocol: int = 0
    bytes_total: int = 0
    packets_total: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_sec: int = 0
    tcp_flags: int = 0
    dscp: int = 0
    src_as: int = 0
    dst_as: int = 0
    src_mac: str = ""
    dst_mac: str = ""
    ingress_interface: int = 0
    egress_interface: int = 0
    vlan_id: int = 0
    sampler_id: int = 0
    flow_direction: int = 0
    exporter_ip: str = ""
    exporter_seq: int = 0
    observation_domain: int = 0
    template_id: int = 0
    version: int = 0
    raw_fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def protocol_name(self) -> str:
        return PROTO_NAMES.get(self.protocol, str(self.protocol))

    @property
    def application(self) -> str:
        """按端口推断应用（常见协议）。"""
        mapping = {
            (6, 20): "FTP-Data", (6, 21): "FTP", (6, 22): "SSH", (6, 23): "Telnet",
            (6, 25): "SMTP", (6, 53): "DNS", (6, 80): "HTTP", (6, 110): "POP3",
            (6, 143): "IMAP", (6, 443): "HTTPS", (6, 445): "SMB", (6, 3389): "RDP",
            (6, 8080): "HTTP-Proxy", (17, 53): "DNS", (17, 123): "NTP",
            (17, 161): "SNMP", (17, 514): "Syslog", (17, 1900): "SSDP",
            (17, 5060): "SIP", (6, 1194): "OpenVPN", (6, 51820): "WireGuard",
        }
        for (proto, port), name in mapping.items():
            if proto == self.protocol and port in (self.src_port, self.dst_port):
                return name
        if self.protocol == 6:
            return "TCP"
        if self.protocol == 17:
            return "UDP"
        return self.protocol_name


# ---------------------------------------------------------------- 工具
def _ip4(raw: bytes) -> str:
    try:
        return str(ipaddress.IPv4Address(raw))
    except Exception:
        return "0.0.0.0"


def _ip6(raw: bytes) -> str:
    try:
        return str(ipaddress.IPv6Address(raw))
    except Exception:
        return "::"


def _mac(raw: bytes) -> str:
    if len(raw) < 6:
        return ""
    return ":".join(f"{b:02x}" for b in raw[:6])


def _ts_from_sysuptime(sys_uptime_ms: int, unix_secs: int) -> Optional[datetime]:
    """NetFlow v5/v9 的 start/end 使用设备 uptime（毫秒），需换算为绝对时间。"""
    if not sys_uptime_ms:
        return None
    try:
        # 设备当前时间 - 已经过的毫秒数
        device_now = datetime.fromtimestamp(unix_secs)
        return device_now - timedelta_ms(sys_uptime_ms)
    except Exception:
        return None


def timedelta_ms(ms: int):
    from datetime import timedelta

    return timedelta(milliseconds=ms)


def _decode_ie(value_bytes: bytes, ie_id: int) -> Any:
    """按 IE 编号解码原始字节。"""
    n = len(value_bytes)
    if ie_id in IE_MAC:
        return _mac(value_bytes)
    if ie_id in IE_IPV4:
        return _ip4(value_bytes)
    if ie_id in IE_IPV6:
        return _ip6(value_bytes)
    if ie_id in IE_STRING:
        return value_bytes.decode("utf-8", errors="replace").rstrip("\x00")
    if n == 1:
        return value_bytes[0]
    if n == 2:
        return struct.unpack("!H", value_bytes)[0]
    if n == 3:
        return int.from_bytes(value_bytes, "big")
    if n == 4:
        return struct.unpack("!I", value_bytes)[0]
    if n == 6:
        return int.from_bytes(value_bytes, "big")
    if n == 8:
        return struct.unpack("!Q", value_bytes)[0]
    if n == 16:
        return int.from_bytes(value_bytes, "big")
    return int.from_bytes(value_bytes, "big") if value_bytes else 0


# ---------------------------------------------------------------- v5 解析
def parse_netflow_v5(payload: bytes, exporter_ip: str = "") -> List[FlowRecord]:
    """解析 NetFlow v5 报文。"""
    records: List[FlowRecord] = []
    if len(payload) < 24:
        return records
    (
        version,
        count,
        sys_uptime,
        unix_secs,
        unix_nsecs,
        flow_sequence,
        engine_type,
        engine_id,
        sampling_interval,
    ) = struct.unpack("!HHIIIIBBH", payload[:24])

    if version != NF_V5:
        return records

    offset = 24
    for _ in range(count):
        if offset + 48 > len(payload):
            break
        (
            src_raw, dst_raw, nexthop_raw, input_if, output_if, d_pkts, d_octets,
            first, last, src_port, dst_port, _pad1, tcp_flags, prot, tos,
            src_as, dst_as, _src_mask, _dst_mask, _pad2,
        ) = struct.unpack("!4s4s4sHHIIIIHHBBBBHHBBH", payload[offset : offset + 48])
        offset += 48

        rec = FlowRecord(
            src_ip=_ip4(src_raw),
            dst_ip=_ip4(dst_raw),
            src_port=src_port,
            dst_port=dst_port,
            protocol=prot,
            bytes_total=d_octets,
            packets_total=d_pkts,
            tcp_flags=tcp_flags,
            dscp=tos >> 2,
            src_as=src_as,
            dst_as=dst_as,
            ingress_interface=input_if,
            egress_interface=output_if,
            duration_sec=max(0, (last - first) // 1000),
            start_time=_ts_from_sysuptime(first, unix_secs),
            end_time=_ts_from_sysuptime(last, unix_secs),
            exporter_ip=exporter_ip,
            exporter_seq=flow_sequence,
            version=NF_V5,
        )
        records.append(rec)
    return records


# ---------------------------------------------------------------- v9 / IPFIX 解析
class TemplateCache:
    """模板缓存：NetFlow v9 / IPFIX 需要先收到模板才能解析数据。"""

    def __init__(self, max_templates: int = 4096) -> None:
        self._templates: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        self.max_templates = max_templates

    def key(self, observation_domain: int, template_id: int) -> Tuple[int, int]:
        return (observation_domain, template_id)

    def put(self, observation_domain: int, template_id: int, fields: List[Tuple[int, int]]) -> None:
        if len(self._templates) >= self.max_templates:
            # 简单淘汰：清空最旧的一半
            for k in list(self._templates.keys())[: self.max_templates // 2]:
                self._templates.pop(k, None)
        self._templates[self.key(observation_domain, template_id)] = fields

    def get(self, observation_domain: int, template_id: int) -> Optional[List[Tuple[int, int]]]:
        return self._templates.get(self.key(observation_domain, template_id))

    def clear(self) -> None:
        self._templates.clear()

    @property
    def count(self) -> int:
        return len(self._templates)


def _parse_v9_template_set(data: bytes, observation_domain: int, cache: TemplateCache) -> None:
    """解析 NetFlow v9 模板流集（id=0）。"""
    offset = 0
    while offset + 4 <= len(data):
        template_id, field_count = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
        fields: List[Tuple[int, int]] = []
        for _ in range(field_count):
            if offset + 4 > len(data):
                break
            field_type, field_length = struct.unpack("!HH", data[offset : offset + 4])
            offset += 4
            fields.append((field_type, field_length))
        if fields:
            cache.put(observation_domain, template_id, fields)
        else:
            # 模板流集以 padding 4 字节对齐结束
            if template_id == 0 and field_count == 0:
                break


def _parse_ipfix_template_set(data: bytes, observation_domain: int, cache: TemplateCache) -> None:
    """解析 IPFIX 模板集（id=2）。

    IPFIX 模板记录格式：
        template_id (2) | field_count (2) | scope_field_count (2) | field_specifier+ ...
    注意 scope_field_count 是 IPFIX 独有的字段，NetFlow v9 没有。
    """
    offset = 0
    while offset + 6 <= len(data):
        template_id, field_count, scope_count = struct.unpack("!HHH", data[offset : offset + 6])
        offset += 6
        if template_id == 2 and field_count == 0:
            # 空模板，模板集结束
            break
        fields: List[Tuple[int, int]] = []
        for _ in range(field_count):
            if offset + 4 > len(data):
                break
            field_type, field_length = struct.unpack("!HH", data[offset : offset + 4])
            offset += 4
            fields.append((field_type, field_length))
        if fields:
            cache.put(observation_domain, template_id, fields)
        else:
            break


def _decode_v9_record(record_bytes: bytes, fields: List[Tuple[int, int]]) -> Dict[str, Any]:
    """按模板解码一条 v9 记录。"""
    values: Dict[str, Any] = {}
    offset = 0
    for field_type, field_length in fields:
        if offset + field_length > len(record_bytes):
            break
        raw = record_bytes[offset : offset + field_length]
        offset += field_length
        name = IE_NAMES.get(field_type, f"ie_{field_type}")
        values[name] = _decode_ie(raw, field_type)
        values[f"_raw_{name}"] = raw
    return values


def _values_to_record(values: Dict[str, Any], version: int, exporter_ip: str,
                      exporter_seq: int, observation_domain: int,
                      template_id: int, unix_secs: int, sys_uptime: int) -> FlowRecord:
    """把解码后的字段映射为 FlowRecord。"""
    rec = FlowRecord(
        src_ip=str(values.get("sourceIPv4Address") or values.get("sourceIPv6Address") or ""),
        dst_ip=str(values.get("destinationIPv4Address") or values.get("destinationIPv6Address") or ""),
        src_port=int(values.get("sourceTransportPort") or 0),
        dst_port=int(values.get("destinationTransportPort") or 0),
        protocol=int(values.get("protocolIdentifier") or 0),
        bytes_total=int(values.get("octetTotalCount") or values.get("octetDeltaCount") or 0),
        packets_total=int(values.get("packetTotalCount") or values.get("packetDeltaCount") or 0),
        tcp_flags=int(values.get("tcpControlBits") or 0),
        dscp=int(values.get("ipClassOfService") or values.get("ipDiffServCodePoint") or 0),
        src_as=int(values.get("bgpSourceAsNumber") or 0),
        dst_as=int(values.get("bgpDestinationAsNumber") or 0),
        src_mac=str(values.get("sourceMacAddress") or ""),
        dst_mac=str(values.get("destinationMacAddress") or ""),
        ingress_interface=int(values.get("ingressInterface") or 0),
        egress_interface=int(values.get("egressInterface") or 0),
        vlan_id=int(values.get("vlanId") or 0),
        sampler_id=int(values.get("samplerId") or 0),
        flow_direction=int(values.get("flowDirection") or 0),
        exporter_ip=exporter_ip,
        exporter_seq=exporter_seq,
        observation_domain=observation_domain,
        template_id=template_id,
        version=version,
    )

    # 时间：优先用绝对时间戳 IE
    for start_key, end_key in (
        ("flowStartSeconds", "flowEndSeconds"),
        ("flowStartMilliseconds", "flowEndMilliseconds"),
        ("flowStartMicroseconds", "flowEndMicroseconds"),
        ("flowStartNanoseconds", "flowEndNanoseconds"),
    ):
        if start_key in values and values[start_key]:
            st = values[start_key]
            et = values.get(end_key) or st
            if start_key.endswith("Seconds"):
                rec.start_time = datetime.fromtimestamp(st)
                rec.end_time = datetime.fromtimestamp(et)
            elif start_key.endswith("Milliseconds"):
                rec.start_time = datetime.fromtimestamp(st / 1000)
                rec.end_time = datetime.fromtimestamp(et / 1000)
            elif start_key.endswith("Microseconds"):
                rec.start_time = datetime.fromtimestamp(st / 1_000_000)
                rec.end_time = datetime.fromtimestamp(et / 1_000_000)
            else:
                rec.start_time = datetime.fromtimestamp(st / 1_000_000_000)
                rec.end_time = datetime.fromtimestamp(et / 1_000_000_000)
            break
    else:
        # 回退：设备 uptime 换算
        st = values.get("flowStartSysUpTime")
        en = values.get("flowEndSysUpTime")
        if st is not None:
            rec.start_time = _ts_from_sysuptime(int(st), unix_secs)
            rec.end_time = _ts_from_sysuptime(int(en), unix_secs) if en is not None else rec.start_time
        else:
            rec.start_time = datetime.fromtimestamp(unix_secs)
            rec.end_time = rec.start_time

    if rec.start_time and rec.end_time:
        rec.duration_sec = max(0, int((rec.end_time - rec.start_time).total_seconds()))

    rec.raw_fields = {k: v for k, v in values.items() if not k.startswith("_raw_")}
    return rec


def parse_netflow_v9(payload: bytes, exporter_ip: str, cache: TemplateCache) -> List[FlowRecord]:
    """解析 NetFlow v9 报文（模板 + 数据流集）。"""
    records: List[FlowRecord] = []
    if len(payload) < 20:
        return records
    version, count, sys_uptime, unix_secs, seq, source_id = struct.unpack("!HHIIII", payload[:20])
    if version != NF_V9:
        return records

    offset = 20
    while offset + 4 <= len(payload):
        flowset_id, flowset_len = struct.unpack("!HH", payload[offset : offset + 4])
        if flowset_len < 4 or offset + flowset_len > len(payload):
            break
        body = payload[offset + 4 : offset + flowset_len]
        offset += flowset_len

        if flowset_id == 0:
            _parse_v9_template_set(body, source_id, cache)
            continue
        if flowset_id == 1:
            continue
        # 数据流集：按模板逐条解析
        fields = cache.get(source_id, flowset_id)
        if not fields:
            continue
        record_len = sum(length for _, length in fields)
        if record_len <= 0:
            continue
        pos = 0
        while pos + record_len <= len(body):
            values = _decode_v9_record(body[pos : pos + record_len], fields)
            records.append(
                _values_to_record(values, NF_V9, exporter_ip, seq, source_id, flowset_id, unix_secs, sys_uptime)
            )
            pos += record_len
    return records


def parse_ipfix(payload: bytes, exporter_ip: str, cache: TemplateCache) -> List[FlowRecord]:
    """解析 IPFIX（NetFlow v10）报文。"""
    records: List[FlowRecord] = []
    if len(payload) < 16:
        return records
    version, msg_len, export_time, seq, observation_domain = struct.unpack("!HHIII", payload[:16])
    if version != IPFIX:
        return records
    if msg_len > len(payload):
        msg_len = len(payload)

    offset = 16
    while offset + 4 <= msg_len:
        set_id, set_len = struct.unpack("!HH", payload[offset : offset + 4])
        if set_len < 4 or offset + set_len > msg_len:
            break
        body = payload[offset + 4 : offset + set_len]
        offset += set_len

        if set_id == 2:
            _parse_ipfix_template_set(body, observation_domain, cache)
            continue
        if set_id == 3:
            continue
        if set_id < 256:
            continue

        fields = cache.get(observation_domain, set_id)
        if not fields:
            continue
        record_len = sum(length for _, length in fields)
        if record_len <= 0:
            continue
        pos = 0
        while pos + record_len <= len(body):
            values = _decode_v9_record(body[pos : pos + record_len], fields)
            records.append(
                _values_to_record(values, IPFIX, exporter_ip, seq, observation_domain, set_id, export_time, 0)
            )
            pos += record_len
    return records


def parse_netflow(payload: bytes, exporter_ip: str = "", cache: Optional[TemplateCache] = None) -> List[FlowRecord]:
    """自动识别版本并解析。"""
    if len(payload) < 2:
        return []
    version = struct.unpack("!H", payload[:2])[0]
    cache = cache if cache is not None else TemplateCache()
    try:
        if version == NF_V5:
            return parse_netflow_v5(payload, exporter_ip)
        if version == NF_V9:
            return parse_netflow_v9(payload, exporter_ip, cache)
        if version == IPFIX:
            return parse_ipfix(payload, exporter_ip, cache)
    except Exception as exc:
        logger.debug("NetFlow 解析失败 (version=%s): %s", version, exc)
    return []


# ---------------------------------------------------------------- 接收端
class NetflowUdpProtocol(asyncio.DatagramProtocol):
    """UDP 导出接收协议。"""

    def __init__(self, collector: "NetflowCollector") -> None:
        self.collector = collector
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        source_ip = addr[0] if addr else ""
        try:
            records = parse_netflow(data, source_ip, self.collector.cache)
        except Exception as exc:
            logger.debug("NetFlow 报文解析异常: %s", exc)
            return
        if not records:
            return
        self.collector.enqueue(records, source_ip)


class NetflowTcpServer:
    """TCP 导出接收（部分设备使用 TCP 传输 NetFlow）。"""

    def __init__(self, collector: "NetflowCollector") -> None:
        self.collector = collector
        self.server: Optional[asyncio.AbstractServer] = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        source_ip = peer[0] if peer else ""
        try:
            while True:
                header = await reader.readexactly(2)
                version = struct.unpack("!H", header)[0]
                if version == NF_V5:
                    rest = await reader.readexactly(22)
                    payload = header + rest
                    # v5 后续还有 count 条 48 字节记录
                    count = struct.unpack("!H", payload[2:4])[0]
                    body = await reader.readexactly(count * 48)
                    payload += body
                elif version == NF_V9:
                    rest = await reader.readexactly(18)
                    payload = header + rest
                    total = struct.unpack("!H", payload[2:4])[0]
                    body = await reader.readexactly(total * 4)
                    payload += body
                else:  # IPFIX
                    rest = await reader.readexactly(14)
                    payload = header + rest
                    msg_len = struct.unpack("!H", payload[2:4])[0]
                    remaining = msg_len - 16
                    if remaining > 0:
                        payload += await reader.readexactly(remaining)

                records = parse_netflow(payload, source_ip, self.collector.cache)
                if records:
                    self.collector.enqueue(records, source_ip)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as exc:
            logger.debug("NetFlow TCP 处理异常: %s", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass


class NetflowCollector:
    """NetFlow 采集器：监听导出报文并交给处理器入库。"""

    def __init__(self, handler=None) -> None:
        self.cache = TemplateCache()
        self.handler = handler
        self._queue: List[Tuple[List[FlowRecord], str]] = []
        self._udp_transports: List[asyncio.DatagramTransport] = []
        self._tcp_servers: List[NetflowTcpServer] = []
        self._udp_ports: List[int] = []
        self.stats = {
            "packets": 0, "records": 0, "errors": 0, "bytes": 0,
            "templates": 0, "started_at": datetime.now(),
        }

    def enqueue(self, records: List[FlowRecord], source_ip: str) -> None:
        self.stats["packets"] += 1
        self.stats["records"] += len(records)
        if self.handler:
            try:
                self.handler(records, source_ip)
            except Exception as exc:
                self.stats["errors"] += 1
                logger.warning("NetFlow 处理失败: %s", exc)
        else:
            self._queue.append((records, source_ip))

    async def start(self, udp_ports: Optional[List[int]] = None, tcp_ports: Optional[List[int]] = None) -> None:
        loop = asyncio.get_running_loop()
        udp_ports = udp_ports or list(DEFAULT_LISTEN_PORTS.keys())
        tcp_ports = tcp_ports if tcp_ports is not None else []

        for port in udp_ports:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: NetflowUdpProtocol(self),
                    local_addr=("0.0.0.0", port),
                )
                self._udp_transports.append(transport)
                self._udp_ports.append(port)
                logger.info("NetFlow UDP 监听 0.0.0.0:%s", port)
            except Exception as exc:
                logger.warning("NetFlow UDP 端口 %s 监听失败: %s", port, exc)

        for port in tcp_ports:
            try:
                srv = NetflowTcpServer(self)
                srv.server = await asyncio.start_server(srv.handle, "0.0.0.0", port)
                self._tcp_servers.append(srv)
                logger.info("NetFlow TCP 监听 0.0.0.0:%s", port)
            except Exception as exc:
                logger.warning("NetFlow TCP 端口 %s 监听失败: %s", port, exc)

    async def stop(self) -> None:
        for t in self._udp_transports:
            t.close()
        self._udp_transports.clear()
        for s in self._tcp_servers:
            if s.server:
                s.server.close()
                await s.server.wait_closed()
        self._tcp_servers.clear()
