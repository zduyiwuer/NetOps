"""gNMI Telemetry 采集器。

支持两种工作模式：
- **collector 模式**（推荐）：设备主动连接平台并推送遥测数据（dial-out）。
  平台作为 gNMI 服务端监听 57400 端口，接收设备的 Subscribe 推流。
- **client 模式**：平台主动连接设备的 gNMI 服务，订阅指定路径并接收推送。

遥测数据统一归一化为平台内部指标模型：
- 接口计数器 → 接口速率 / 利用率
- CPU / 内存 / 温度 → 设备资源指标
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import grpc

from app.proto import gnmi_pb2, gnmi_pb2_grpc

logger = logging.getLogger("netops.telemetry")

DEFAULT_GNMI_PORT = 57400


# ---------------------------------------------------------------- 路径映射
@dataclass
class PathMapping:
    """一条遥测路径 → 平台指标的映射规则。"""

    path: str
    metric: str
    # 数值缩放（如千分之一 → 实际值）
    scale: float = 1.0
    # 接口标识提取用的 key 名称
    interface_key: str = "name"
    # 叶子名匹配时必须同时包含的路径片段（避免 `used`/`total` 等歧义叶子误配）
    require_segment: str = ""


# 内置映射：覆盖 OpenConfig 标准模型 + 主流厂商私有模型
DEFAULT_MAPPINGS: List[PathMapping] = [
    # ---- 接口计数器（OpenConfig 标准）----
    PathMapping("interfaces/interface/state/counters/in-octets", "if_in_octets"),
    PathMapping("interfaces/interface/state/counters/out-octets", "if_out_octets"),
    PathMapping("interfaces/interface/state/counters/in-pkts", "if_in_pkts"),
    PathMapping("interfaces/interface/state/counters/out-pkts", "if_out_pkts"),
    PathMapping("interfaces/interface/state/counters/in-errors", "if_in_errors"),
    PathMapping("interfaces/interface/state/counters/out-errors", "if_out_errors"),
    PathMapping("interfaces/interface/state/counters/in-discards", "if_in_discards"),
    PathMapping("interfaces/interface/state/counters/out-discards", "if_out_discards"),
    # ---- 接口属性 ----
    PathMapping("interfaces/interface/state/oper-status", "if_oper_status"),
    PathMapping("interfaces/interface/state/admin-status", "if_admin_status"),
    PathMapping("interfaces/interface/state/description", "if_descr"),
    PathMapping("interfaces/interface/state/name", "if_name"),
    PathMapping("interfaces/interface/state/mac-address", "if_mac"),
    PathMapping("interfaces/interface/state/if-index", "if_index"),
    PathMapping("interfaces/interface/state/speed", "if_speed"),
    PathMapping("interfaces/interface/state/type", "if_type"),
    PathMapping("interfaces/interface/ethernet/state/duplex-mode", "if_duplex"),
    PathMapping("interfaces/interface/ethernet/state/mtu", "if_mtu"),
    # ---- CPU ----
    PathMapping("system/cpu/state/utilization", "cpu_usage"),
    PathMapping("system/cpu/state/cpu-utilization", "cpu_usage"),
    PathMapping("system/cpu/state/active", "cpu_active"),
    PathMapping("system/cpu/state/idle", "cpu_idle"),
    PathMapping("components/component/state/cpu-utilization", "cpu_usage"),
    # ---- 内存 ----
    PathMapping("system/memory/state/used", "mem_used", require_segment="memory"),
    PathMapping("system/memory/state/available", "mem_available", require_segment="memory"),
    PathMapping("system/memory/state/free", "mem_free", require_segment="memory"),
    PathMapping("system/memory/state/total", "mem_total", require_segment="memory"),
    # ---- 其他系统指标 ----
    PathMapping("system/state/hostname", "hostname"),
    PathMapping("system/state/uptime", "uptime"),
    PathMapping("lldp/interfaces/interface/state/port-id", "lldp_port_id"),
]

# 数值型指标 → 单位换算（统一到平台内部单位）
_SCALE_TO_MB = 1.0 / (1024 * 1024)


@dataclass
class TelemetrySample:
    """一条归一化后的遥测样本。"""

    device_id: Optional[int]
    metric: str
    value: Any
    interface: str = ""
    timestamp: Optional[datetime] = None
    source_path: str = ""
    raw: Any = None
    # 接口计数器类指标需要按接口分组
    per_interface: bool = False


@dataclass
class DeviceTelemetryState:
    """单台设备的遥测会话状态。"""

    device_id: Optional[int]
    peer: str = ""
    mode: str = "collector"
    connected_at: Optional[datetime] = None
    last_update: Optional[datetime] = None
    subscriptions: List[str] = field(default_factory=list)
    received: int = 0
    status: str = "connected"
    sample_interval_sec: int = 10


class TelemetryPathMapper:
    """遥测路径 → 指标映射器。

    路径匹配采用「最长前缀优先」，同时兼容带 origin 前缀的路径
    （如 `openconfig-interfaces:interfaces/interface/...`）。
    """

    def __init__(self, extra_mappings: Optional[List[PathMapping]] = None) -> None:
        self._mappings: List[Tuple[str, PathMapping]] = []
        # 叶子名索引：末段名 → 映射规则（用于前缀形态不一的场景）
        self._leaf_index: Dict[str, PathMapping] = {}
        for m in list(DEFAULT_MAPPINGS) + list(extra_mappings or []):
            self.add(m)

    def add(self, mapping: PathMapping) -> None:
        self._mappings.append((mapping.path.lower(), mapping))
        # 长路径优先匹配
        self._mappings.sort(key=lambda x: len(x[0]), reverse=True)
        leaf = mapping.path.rstrip("/").split("/")[-1]
        if leaf not in self._leaf_index:
            self._leaf_index[leaf] = mapping

    def match(self, full_path: str) -> Optional[PathMapping]:
        """匹配路径，返回映射规则。

        分三级匹配，兼容不同厂商各异的路径形态：
        1. 完整路径匹配
        2. 后缀匹配（尾部若干段与规则一致）
        3. 叶子名匹配（末段名一致，如 in-octets / oper-status / utilization）
        """
        p = normalize_path(full_path)
        if not p:
            return None

        # 1) 完整路径匹配
        for prefix, mapping in self._mappings:
            if p == prefix or p.startswith(prefix + "/"):
                return mapping

        # 2) 后缀匹配
        segments = p.split("/")
        for prefix, mapping in self._mappings:
            rule_segments = prefix.split("/")
            n = len(rule_segments)
            if n <= len(segments) and segments[-n:] == rule_segments:
                return mapping

        # 3) 叶子名匹配（末段），带约束片段的规则需同时满足
        leaf = segments[-1]
        mapping = self._leaf_index.get(leaf)
        if mapping is not None:
            if not mapping.require_segment or mapping.require_segment in segments:
                return mapping
        return None

    def all_paths(self) -> List[str]:
        return [m.path for m in DEFAULT_MAPPINGS]


_ORIGIN_RE = re.compile(r"^[a-zA-Z0-9_\-]+:(.+)$")
_KEY_RE = re.compile(r"\[([^\]]+)\]")


def normalize_path(path: str) -> str:
    """规范化遥测路径：去掉 origin 前缀、去掉 key 方括号内容、小写。"""
    if not path:
        return ""
    p = path.strip()
    # 去掉 origin（如 openconfig-interfaces:interfaces/...）
    m = _ORIGIN_RE.match(p)
    if m:
        p = m.group(1)
    # 去掉 key 部分（interfaces/interface[name=X]/state → interfaces/interface/state）
    p = _KEY_RE.sub("", p)
    # 去掉开头的斜杠与多余的空段
    p = p.lstrip("/")
    while "//" in p:
        p = p.replace("//", "/")
    p = p.rstrip("/")
    return p.lower()


def extract_interface_key(path: str) -> str:
    """从路径中提取接口标识（interface[name=Ethernet1/1] → Ethernet1/1）。"""
    m = _KEY_RE.search(path or "")
    if not m:
        return ""
    body = m.group(1)
    if "=" in body:
        return body.split("=", 1)[1].strip()
    return body.strip()


def build_full_path(prefixes: List[Any], update_path: Any) -> str:
    """拼接 prefix + update path 得到完整路径。

    key 需内联到所属元素之后（`interface[name=X]`），
    不能作为独立路径段，否则规范化时会丢失接口标识。
    """
    parts: List[str] = []

    def _segment(element: str, origin: str = "", keys: Optional[List[str]] = None) -> str:
        seg = f"{origin}:{element}" if origin else element
        if keys:
            seg += "[" + ",".join(keys) + "]"
        return seg

    for prefix in prefixes or []:
        element = getattr(prefix, "element", "")
        if element:
            origin = getattr(prefix, "origin", "")
            keys = list(getattr(prefix, "key", []) or [])
            parts.append(_segment(element, origin, keys))

    if update_path is not None:
        element = getattr(update_path, "element", "")
        if element:
            origin = getattr(update_path, "origin", "")
            keys = list(getattr(update_path, "key", []) or [])
            parts.append(_segment(element, origin, keys))
    return "/".join(parts)


def decode_typed_value(tv: Any) -> Any:
    """把 gNMI TypedValue 解码为原生类型。"""
    if tv is None:
        return None
    which = tv.WhichOneof("value")
    if which is None:
        return None
    if which == "string_val":
        return tv.string_val
    if which == "ascii_val":
        return tv.ascii_val
    if which == "int_val":
        return tv.int_val
    if which == "uint_val":
        return tv.uint_val
    if which == "bool_val":
        return tv.bool_val
    if which == "decimal_val":
        digits = tv.decimal_val.digits
        precision = tv.decimal_val.precision or 0
        return digits / (10 ** precision)
    if which == "bytes_val":
        return tv.bytes_val
    if which in ("json_val", "json_ietf_val"):
        raw = tv.json_val if which == "json_val" else tv.json_ietf_val
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return raw.decode("utf-8", errors="replace")
    if which == "leaflist_val":
        return [decode_typed_value(e) for e in tv.leaflist_val.element]
    if which == "proto_bytes":
        return tv.proto_bytes
    return None


def _extract_field_bytes(raw: bytes, field_number: int) -> bytes:
    """从原始 protobuf 字节流中提取指定字段号（length-delimited）的 payload。

    gRPC 传输的每个 message 都是「字段号(varint) + 长度(varint) + payload」，
    这里做最小化的 wire-format 解析，用于在 protobuf 反序列化丢失原始字节后
    仍能取回嵌套的 message 内容。
    """
    i = 0
    n = len(raw)
    while i < n:
        # 读 tag（varint）
        tag = 0
        shift = 0
        while i < n:
            byte = raw[i]
            i += 1
            tag |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        field_no = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 0:  # varint
            while i < n and raw[i] & 0x80:
                i += 1
            i += 1
        elif wire_type == 1:  # 64-bit
            i += 8
        elif wire_type == 5:  # 32-bit
            i += 4
        elif wire_type == 2:  # length-delimited
            length = 0
            shift = 0
            while i < n:
                byte = raw[i]
                i += 1
                length |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            payload = raw[i : i + length]
            i += length
            if field_no == field_number:
                return payload
        elif wire_type == 3:  # start group（已废弃）
            return b""
        elif wire_type == 4:  # end group
            return b""
        else:
            return b""
    return b""


# ---------------------------------------------------------------- 服务端
class GnmiServicer(gnmi_pb2_grpc.gNMIServicer):
    """gNMI 服务端（collector 模式）。

    设备连接后主动推送 SubscribeResponse 流；本服务负责解析并归一化。
    """

    def __init__(self, on_samples: Callable[[List[TelemetrySample], Optional[int]], None],
                 device_resolver: Callable[[str], Optional[int]],
                 mapper: Optional[TelemetryPathMapper] = None) -> None:
        self.on_samples = on_samples
        self.device_resolver = device_resolver
        self.mapper = mapper or TelemetryPathMapper()
        self.sessions: Dict[str, DeviceTelemetryState] = {}

    async def Subscribe(self, request_iterator, context):
        """gNMI Subscribe 双向流。

        gNMI 规范中该 RPC 是 `stream SubscribeRequest → stream SubscribeResponse`：
        - **dial-in**（平台订阅设备）：设备是服务端，平台是客户端，由平台的 client 模式发起。
        - **dial-out**（设备推送）：设备作为客户端连到平台（collector），
          发送 SubscribeRequest 描述订阅内容，平台需以 SubscribeResponse 回送数据。

        部分厂商的 dial-out 实现会直接以 SubscribeRequest 的载荷形式推送
        SubscribeResponse（复用请求流），这里额外兼容该形态。
        """
        peer = "unknown"
        try:
            peer = context.peer() if hasattr(context, "peer") else "unknown"
        except Exception:
            pass

        device_id = self.device_resolver(peer)
        state = DeviceTelemetryState(
            device_id=device_id,
            peer=peer,
            mode="collector",
            connected_at=datetime.now(),
        )
        self.sessions[peer] = state
        logger.info("gNMI 设备接入: peer=%s device_id=%s", peer, device_id)

        try:
            async for request in request_iterator:
                logger.debug("gNMI 收到请求: kind=%s", request.WhichOneof("request"))
                kind = request.WhichOneof("request")

                # 兼容：设备把 SubscribeResponse 塞在请求流里推送（厂商自定义行为）
                pushed = await self._parse_pushed_response(request, state)
                if pushed:
                    continue

                if kind == "subscribe":
                    sub = request.subscribe
                    for s in sub.subscription.subscription:
                        path = build_full_path([sub.subscription.prefix], s.path)
                        state.subscriptions.append(path)
                    logger.info("gNMI 订阅建立: peer=%s paths=%s", peer, state.subscriptions[:5])
                    resp = gnmi_pb2.SubscribeResponse()
                    resp.sync_response.CopyFrom(gnmi_pb2.SyncResponse(sync_response=True))
                    yield resp
                    # 设备连上后，平台按订阅周期采集该设备数据并回送（collector 即数据源）
                    async for data_resp in self._stream_device_data(state, device_id):
                        yield data_resp
                elif kind == "poll":
                    resp = gnmi_pb2.SubscribeResponse()
                    resp.sync_response.CopyFrom(gnmi_pb2.SyncResponse(sync_response=True))
                    yield resp
                    async for data_resp in self._stream_device_data(state, device_id):
                        yield data_resp
                else:
                    logger.debug("gNMI 收到未知请求类型: %s", kind)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("gNMI 会话结束: %s", exc)

        logger.info("gNMI 设备断开: peer=%s", peer)
        self.sessions.pop(peer, None)

    async def _stream_device_data(self, state: DeviceTelemetryState, device_id: Optional[int]):
        """按订阅周期采集设备数据，并以 gNMI SubscribeResponse 回送。

        collector 模式下设备是订阅方（数据消费者），平台承担数据源角色：
        平台通过 SNMP/SSH 采集该设备的指标，转成 gNMI 格式回送，
        同时这些指标也会写入平台自身的指标表（供看板使用）。
        """
        if not device_id:
            return
        interval = max(2, state.sample_interval_sec or 10)
        try:
            while True:
                sample = await self._collect_device(device_id)
                if sample:
                    resp = self._build_response(sample)
                    if resp is not None:
                        yield resp
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("gNMI 数据推送结束: %s", exc)

    async def _collect_device(self, device_id: Optional[int]):
        """采集设备指标（复用 SNMP/SSH 采集引擎）。"""
        from app.db.session import SessionLocal
        from app.models.models import Device
        from app.collectors.engine import Collector

        db = SessionLocal()
        try:
            device = db.get(Device, device_id)
            if device is None:
                return None
            collector = Collector(device)
            return await collector.collect(collect_interface=True, collect_system=True)
        except Exception as exc:
            logger.debug("gNMI 设备采集失败: %s", exc)
            return None
        finally:
            db.close()

    def _build_response(self, sample: Any):
        """把采集样本转换为 gNMI SubscribeResponse。"""
        updates = []
        ts = int(datetime.now().timestamp() * 1e9)
        system = sample.system
        if system.cpu_usage is not None:
            updates.append(
                gnmi_pb2.Update(
                    path=gnmi_pb2.Path(element="utilization"),
                    val=gnmi_pb2.TypedValue(int_val=int(system.cpu_usage)),
                )
            )
        for itf in sample.interfaces:
            if not itf.in_octets and not itf.out_octets:
                continue
            updates.append(
                gnmi_pb2.Update(
                    path=gnmi_pb2.Path(element="in-octets"),
                    val=gnmi_pb2.TypedValue(uint_val=int(itf.in_octets)),
                )
            )
            updates.append(
                gnmi_pb2.Update(
                    path=gnmi_pb2.Path(element="out-octets"),
                    val=gnmi_pb2.TypedValue(uint_val=int(itf.out_octets)),
                )
            )
        if not updates:
            return None
        notif = gnmi_pb2.Notification(
            timestamp=ts,
            prefix=[
                gnmi_pb2.Path(element="interfaces"),
                gnmi_pb2.Path(element="interface", key=["name=unknown"]),
                gnmi_pb2.Path(element="state"),
                gnmi_pb2.Path(element="counters"),
            ],
            update=updates,
        )
        resp = gnmi_pb2.SubscribeResponse()
        resp.update.CopyFrom(gnmi_pb2.SubscriptionResponse(notification=notif))
        return resp

    async def _parse_pushed_response(self, request: Any, state: DeviceTelemetryState) -> bool:
        """尝试把请求当作 SubscribeResponse 解析（兼容厂商 dial-out 推送行为）。

        Cisco IOS-XR MDT 等设备在 dial-out 模式下，会把 SubscribeResponse 的
        字节流塞进 SubscribeRequest 的 subscribe 字段（field 1）里发送。

        由于 protobuf 会按 SubscribeRequest 的 schema 重新解释这些字节，
        这里必须在**原始字节层面**提取 field 1 的 payload，再按
        SubscribeResponse 的 schema 解析。
        """
        try:
            raw = request.SerializeToString()
            payload = _extract_field_bytes(raw, 1)
            if not payload:
                return False
            resp = gnmi_pb2.SubscribeResponse()
            resp.ParseFromString(payload)
            if resp.WhichOneof("response") != "update":
                return False
            samples = self.parse_response(resp)
            if samples:
                for s in samples:
                    s.device_id = state.device_id
                await self._dispatch_samples(samples, state.device_id)
                state.received += len(samples)
                state.last_update = datetime.now()
                logger.info("gNMI 收到推送遥测: peer=%s samples=%s", state.peer, len(samples))
            return True
        except Exception as exc:
            logger.debug("gNMI 推送解析异常: %s", exc, exc_info=True)
            return False

    async def _dispatch_samples(self, samples: List[TelemetrySample], device_id: Optional[int]) -> None:
        """分发样本；回调既可以是同步函数也可以是协程。"""
        result = self.on_samples(samples, device_id)
        if inspect.isawaitable(result):
            await result

    async def Get(self, request, context):
        return gnmi_pb2.GetResponse(error=gnmi_pb2.Error(code=12, message="not implemented"))

    async def Set(self, request, context):
        return gnmi_pb2.SetResponse(error=gnmi_pb2.Error(code=12, message="not implemented"))

    def parse_response(self, response: Any) -> List[TelemetrySample]:
        """解析设备推送的 SubscribeResponse，抽取样本。"""
        samples: List[TelemetrySample] = []
        kind = response.WhichOneof("response")
        if kind != "update":
            return samples
        sub = response.update
        notif = sub.notification
        ts = None
        if notif.timestamp:
            ts = datetime.fromtimestamp(notif.timestamp / 1e9)

        for update in notif.update:
            full_path = build_full_path(notif.prefix, update.path)
            mapping = self.mapper.match(full_path)
            if mapping is None:
                continue
            value = decode_typed_value(update.val)
            if value is None:
                continue
            iface = extract_interface_key(full_path)
            samples.append(
                TelemetrySample(
                    device_id=None,
                    metric=mapping.metric,
                    value=value,
                    interface=iface,
                    timestamp=ts or datetime.now(),
                    source_path=full_path,
                    per_interface=mapping.metric.startswith("if_"),
                )
            )
        return samples


class TelemetryCollectorServer:
    """gNMI collector 服务端管理器。"""

    def __init__(self, on_samples: Callable[[List[TelemetrySample], Optional[int]], None],
                 device_resolver: Callable[[str], Optional[int]],
                 mapper: Optional[TelemetryPathMapper] = None) -> None:
        self.servicer = GnmiServicer(on_samples, device_resolver, mapper)
        self._server: Optional[grpc.aio.Server] = None
        self.port = DEFAULT_GNMI_PORT

    async def start(self, port: int = DEFAULT_GNMI_PORT) -> bool:
        self.port = port
        try:
            server = grpc.aio.server(options=[
                ("grpc.max_receive_message_length", 16 * 1024 * 1024),
                ("grpc.max_send_message_length", 16 * 1024 * 1024),
                ("grpc.keepalive_time_ms", 30000),
                # 允许快速重启时复用端口（Windows 默认禁止）
                ("grpc.so_reuseport", 1),
            ])
            gnmi_pb2_grpc.add_gNMIServicer_to_server(self.servicer, server)
            bound = server.add_insecure_port(f"0.0.0.0:{port}")
            if not bound:
                logger.warning("gNMI 端口 %s 绑定失败（可能已被占用）", port)
                return False
            await server.start()
            self._server = server
            logger.info("gNMI Collector 监听 0.0.0.0:%s", port)
            return True
        except Exception as exc:
            logger.warning("gNMI Collector 启动失败: %s", exc)
            return False

    async def stop(self) -> None:
        if self._server:
            try:
                await self._server.stop(grace=1)
            except Exception as exc:
                logger.debug("gNMI Collector 停止异常: %s", exc)
            self._server = None
            # 等待端口释放，避免重启时绑定失败
            await asyncio.sleep(0.3)

    @property
    def sessions(self) -> Dict[str, DeviceTelemetryState]:
        return self.servicer.sessions


# ---------------------------------------------------------------- 客户端
class TelemetryClientSession:
    """gNMI 客户端会话：订阅设备遥测。"""

    def __init__(self, device_id: int, target: str, port: int,
                 paths: List[str], sample_interval_ms: int,
                 on_samples: Callable[[List[TelemetrySample], Optional[int]], None],
                 mapper: Optional[TelemetryPathMapper] = None,
                 username: str = "", password: str = "") -> None:
        self.device_id = device_id
        self.target = target
        self.port = port
        self.paths = paths
        self.sample_interval_ms = sample_interval_ms
        self.on_samples = on_samples
        self.mapper = mapper or TelemetryPathMapper()
        self.username = username
        self.password = password
        self._channel: Optional[grpc.aio.Channel] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.status = "disconnected"
        self.last_error = ""
        self.received = 0
        self.connected_at: Optional[datetime] = None

    def _build_request(self) -> gnmi_pb2.SubscribeRequest:
        subs = []
        for p in self.paths:
            path = gnmi_pb2.Path(element=p)
            subs.append(
                gnmi_pb2.Subscription(
                    path=path,
                    mode=gnmi_pb2.SAMPLE,
                    sample_interval=self.sample_interval_ms * 1_000_000,
                )
            )
        # SubscribeRequest.request 是 oneof：protobuf 7.x 需直接给成员赋值
        # （访问 oneof 容器 r.request 会抛 AttributeError）
        req = gnmi_pb2.SubscribeRequest()
        req.subscribe.CopyFrom(
            gnmi_pb2.SubscriptionRequest(
                subscription=gnmi_pb2.SubscriptionList(subscription=subs)
            )
        )
        return req

    async def _run(self) -> None:
        try:
            creds = None
            if self.username:
                creds = grpc.aio.insecure_channel_options = None  # 占位，实际用 metadata 传认证
            options = [
                ("grpc.max_receive_message_length", 16 * 1024 * 1024),
                ("grpc.max_send_message_length", 16 * 1024 * 1024),
                ("grpc.keepalive_time_ms", 30000),
            ]
            self._channel = grpc.aio.insecure_channel(f"{self.target}:{self.port}", options=options)
            stub = gnmi_pb2_grpc.gNMIStub(self._channel)

            async def _request_stream():
                yield self._build_request()
                while not self._stop.is_set():
                    await asyncio.sleep(1)
                    # 保持流存活（周期性空心跳由设备侧处理）

            metadata = []
            if self.username:
                metadata.append(("username", self.username))
                metadata.append(("password", self.password))

            self.status = "connected"
            self.connected_at = datetime.now()
            logger.info("gNMI 客户端已连接 %s:%s", self.target, self.port)

            async for response in stub.Subscribe(_request_stream(), metadata=metadata or None):
                samples = self._parse(response)
                if samples:
                    self.received += len(samples)
                    result = self.on_samples(samples, self.device_id)
                    if inspect.isawaitable(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = "error"
            self.last_error = str(exc)
            logger.warning("gNMI 客户端会话异常 %s:%s — %s", self.target, self.port, exc)
        finally:
            self.status = "disconnected"
            if self._channel:
                await self._channel.close()
                self._channel = None

    def _parse(self, response: Any) -> List[TelemetrySample]:
        samples: List[TelemetrySample] = []
        kind = response.WhichOneof("response")
        if kind != "update":
            return samples
        notif = response.update.notification
        ts = datetime.fromtimestamp(notif.timestamp / 1e9) if notif.timestamp else datetime.now()
        for update in notif.update:
            full_path = build_full_path(notif.prefix, update.path)
            mapping = self.mapper.match(full_path)
            if mapping is None:
                continue
            value = decode_typed_value(update.val)
            if value is None:
                continue
            samples.append(
                TelemetrySample(
                    device_id=self.device_id,
                    metric=mapping.metric,
                    value=value,
                    interface=extract_interface_key(full_path),
                    timestamp=ts,
                    source_path=full_path,
                    per_interface=mapping.metric.startswith("if_"),
                )
            )
        return samples

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.status = "disconnected"
