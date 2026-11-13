"""Cisco MDT（Model-Driven Telemetry）dial-out 采集器。

与 gNMI 的区别：
- **gNMI**：平台是客户端（dial-in）或服务端（dial-out），RPC 是 Subscribe，
  载荷是 gNMI TypedValue；
- **Cisco MDT**（`telemetry ietf subscription ... protocol grpc-tcp`）：
  设备是 gRPC 客户端，主动调用平台的 MdtDialout 双向流，
  每条 MdtDialoutArgs.data 是一条 KV-GPB 编码的 Telemetry 消息。

两种服务**注册在同一个 gRPC 服务器**上（互不冲突），
因此设备只需要配一个 receiver 端口。

数据流：
  设备 --MdtDialout(MdtDialoutArgs{data})--> 平台
    -> 解析 KV-GPB Telemetry（encoding_path + 数据树）
    -> 路径映射（wireless-client-oper 等无线路径 -> 无线表；
       其余 -> 归一化遥测样本，与 gNMI 同管线）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import grpc

from app.proto import mdt_pb2, mdt_pb2_grpc

logger = logging.getLogger("netops.mdt")


# ---------------------------------------------------------------- KV-GPB 解析
def _field_value(f: mdt_pb2.TelemetryField) -> Any:
    """取 TelemetryField 的标量值（oneof value_by_type）。"""
    which = f.WhichOneof("value_by_type")
    if which is None:
        return None
    return getattr(f, which)


def _walk_fields(
    fields: List[mdt_pb2.TelemetryField],
    prefix: str = "",
) -> Dict[str, Any]:
    """把 KV-GPB 数据树展平成 {路径: 值}。

    列表节点（如 client-oper-data 下的多个客户端条目）会出现
    重名路径——展平时同名键聚合为列表，由调用方按需处理。
    """
    out: Dict[str, Any] = {}
    for f in fields:
        key = f"{prefix}.{f.name}" if prefix else f.name
        val = _field_value(f)
        if val is not None:
            out[key] = val
        if f.fields:
            sub = _walk_fields(f.fields, key)
            for k, v in sub.items():
                if k in out and not isinstance(out[k], list):
                    out[k] = [out[k], v]
                elif k in out:
                    out[k].append(v)
                else:
                    out[k] = v
    return out


def parse_telemetry_msg(raw: bytes) -> Optional[mdt_pb2.Telemetry]:
    """解析一条 KV-GPB Telemetry 消息。"""
    try:
        msg = mdt_pb2.Telemetry()
        msg.ParseFromString(raw)
        return msg
    except Exception as exc:
        logger.debug("解析 KV-GPB Telemetry 失败: %s", exc)
        return None


def _norm_mac(v: Any) -> str:
    """MAC 统一为 xx:xx:xx:xx:xx:xx 小写格式。

    设备可能推 aabb.ccdd.eeff / AA-BB-CC-DD-EE-FF / AABBCCDDEEFF
    等形式，归一化后才能做表间关联（bssid 关联 AP 表）。
    """
    s = str(v).strip().lower()
    s = s.replace(":", "").replace("-", "").replace(".", "").replace(" ", "")
    if len(s) == 12 and all(ch in "0123456789abcdef" for ch in s):
        return ":".join(s[i : i + 2] for i in range(0, 12, 2))
    return str(v)  # 非标准形式原样返回


def telemetry_to_samples(msg: mdt_pb2.Telemetry) -> List[Dict[str, Any]]:
    """把 Telemetry 消息转成归一化样本列表。

    KV-GPB 的数据树是「路径 → 重复条目 → 字段」结构
    （client-oper-data → N 个 common-oper-data → client-mac / ap-name / …）。
    展平会因重复路径互相覆盖/聚合成列表，这里按条目遍历：
    条目的身份键（如 client-mac）放进 interface 字段，
    叶子字段的相对路径做 metric 名。

    返回的样本结构（与 gNMI 管线一致）：
    {"metric", "value", "interface", "source_path", "ts"}
    """
    ts = datetime.now()
    # proto3 标量字段没有 presence，不能用 HasField，直接判值
    if msg.msg_timestamp > 0:
        try:
            ts = datetime.fromtimestamp(msg.msg_timestamp / 1000)
        except (ValueError, OSError, OverflowError):
            pass

    path = msg.encoding_path or msg.sensor_path or ""
    samples: List[Dict[str, Any]] = []
    if not msg.HasField("data_gpb"):
        return samples

    def _is_ident(name: str) -> bool:
        """条目的身份字段：mac / name / id 类的字符串键。"""
        n = name.lower()
        return ("mac" in n or n.endswith("-id") or n == "name"
                or n.endswith("-name")) and not n.startswith("ms-ap")

    def _walk(field: mdt_pb2.TelemetryField, prefix: str, ident: str) -> None:
        """递归遍历数据树。

        prefix：当前字段路径（做 metric 名）。
        ident：条目身份（如 client-mac 的值），进 interface。
        重复的兄弟容器（列表条目）各走一遍，互不覆盖。
        """
        for f in field.fields:
            key = f"{prefix}.{f.name}" if prefix else f.name
            val = _field_value(f)
            if f.fields:
                # 容器 / 条目：进入前找身份字段（本层第一个 mac/id 类字符串）
                sub_ident = ident
                if not sub_ident or f.name != prefix.rsplit(".", 1)[-1]:
                    for sub in f.fields:
                        if not sub.fields and _is_ident(sub.name):
                            v = _field_value(sub)
                            if isinstance(v, str) and v:
                                sub_ident = v
                                break
                _walk(f, key, sub_ident)
            elif val is not None:
                samples.append(
                    {
                        "metric": key,
                        "value": val,
                        "interface": ident,
                        "source_path": path,
                        "ts": ts,
                    }
                )

    _walk(msg.data_gpb, "", "")
    # 上限保护：一条消息最多取 2000 个叶子（1MB 级消息可能上万个）
    return samples[:2000]


# ---------------------------------------------------------------- 服务端
class MdtDialoutServicer(mdt_pb2_grpc.gRPCMdtDialoutServicer):
    """接收设备的 dial-out 推流。

    on_samples 回调与 gNMI 的 on_samples 相同签名：
    (samples, device_id) -> None，其中 samples 是归一化样本列表。
    device_resolver：按 node_id（设备名 / IP）解析设备 ID。
    """

    def __init__(
        self,
        on_samples: Callable[[List[Dict[str, Any]], Optional[int]], None],
        device_resolver: Callable[[str], Optional[int]],
        debug_dump: bool = False,
    ) -> None:
        self.on_samples = on_samples
        self.device_resolver = device_resolver
        self.debug_dump = debug_dump
        self.sessions: Dict[str, Dict[str, Any]] = {}

    async def MdtDialout(self, request_iterator, context):
        """设备侧发起的双向流：接收 MdtDialoutArgs，**逐条回 MdtDialoutReply**。

        实测（Catalyst 9800L）：设备每发一条数据就等服务端的回执，
        不回执就不发下一条——双向流必须按批处理模式实现。"""
        peer = ""
        try:
            peer = context.peer() or ""
        except Exception:
            pass
        session = {
            "peer": peer,
            "received": 0,
            "last_ts": None,
            "status": "connected",
        }
        # 用完整 peer（含源端口）做 key：同一设备的多条订阅 RPC
        # （每订阅一条流）用 IP 做主键会互相覆盖
        self.sessions[peer or "unknown"] = session
        # peer 形如 "ipv4:172.30.132.2:60389"——解析出 IP 供设备匹配
        ip = peer.split(":")[1] if ":" in peer else peer
        # 清理已断开超过 10 分钟的旧会话记录
        now = datetime.now()
        stale = [
            k
            for k, st in self.sessions.items()
            if st["status"] == "disconnected"
            and st.get("last_ts")
            and (now - st["last_ts"]).total_seconds() > 600
        ]
        for k in stale:
            self.sessions.pop(k, None)

        try:
            async for args in request_iterator:
                if args.errors:
                    logger.warning("MDT 设备侧错误（%s）: %s", peer, args.errors)
                    continue
                if not args.data:
                    logger.info("MDT 收到空载荷（%s）type=%s", peer, args.type)
                    continue
                session["received"] += 1
                session["last_ts"] = datetime.now()
                # 首条与每 100 条记一次（设备 5 秒周期推送，全量记会刷屏）
                if session["received"] == 1 or session["received"] % 100 == 0:
                    logger.info(
                        "MDT 收到数据（%s）累计 %d 条，本条 %d 字节",
                        peer, session["received"], len(args.data),
                    )
                self._last_raw = args.data

                msg = parse_telemetry_msg(args.data)
                if msg is None:
                    # 可能是 JSON 编码（encode-json）
                    try:
                        text = args.data.decode("utf-8", errors="replace")
                        logger.info("MDT 收到非 KV-GPB 载荷（前 200 字）: %s", text[:200])
                    except Exception:
                        pass
                    continue

                node = msg.node_id_str or ""
                did = self.device_resolver(node) if node else None
                if did is None and ip:
                    did = self.device_resolver(ip)

                if self.debug_dump:
                    self._dump(msg, did)

                samples = telemetry_to_samples(msg)
                if session["received"] == 1 or session["received"] % 100 == 0:
                    logger.info(
                        "MDT 解析样本数=%d（node=%s path=%s）",
                        len(samples), msg.node_id_str, msg.encoding_path,
                    )
                if samples:
                    # 入库放线程池：无线推送单条可达 1MB+，解析后几百条
                    # DB 写是同步操作——直接在事件循环里跑会阻塞 gRPC
                    # 服务（设备下一批推送收不到响应就停推了）
                    try:
                        await asyncio.to_thread(self.on_samples, samples, did)
                    except Exception as exc:
                        logger.exception("MDT 样本入库失败: %s", exc)

                # 无线数据实时合并进 wireless_clients（telemetry 优先：
                # 5-30 秒粒度比 SNMP 轮询新；失败不影响样本入库）
                if did:
                    try:
                        await asyncio.to_thread(
                            update_wireless_from_msg, msg, did
                        )
                    except Exception as exc:
                        logger.exception("MDT 无线合并调度失败: %s", exc)
                else:
                    logger.info("MDT 未匹配到设备，跳过无线合并（node=%s）", msg.node_id_str)

                # 逐条回执：设备等确认后才发下一条（9800L 实测）
                yield mdt_pb2.MdtDialoutReply(RespId=args.ReqId)

        except Exception as exc:
            logger.info("MDT 会话结束（%s）: %s", peer, exc)
        finally:
            session["status"] = "disconnected"
            session["last_ts"] = datetime.now()

    def status(self) -> Dict[str, Any]:
        """活跃会话状态（供遥测页展示）。"""
        return {
            "sessions": [
                {
                    "peer": s["peer"],
                    "received": s["received"],
                    "last_ts": s["last_ts"].isoformat() if s["last_ts"] else None,
                    "status": s["status"],
                }
                for s in self.sessions.values()
            ]
        }

    def _dump(self, msg: mdt_pb2.Telemetry, did: Optional[int]) -> None:
        """调试：把消息结构打到日志（前几层）。"""
        try:
            head = (
                f"MDT DEBUG node={msg.node_id_str!r} sub={msg.subscription_id_str!r} "
                f"path={msg.encoding_path!r} ts={msg.msg_timestamp}"
            )
            logger.info(head)
            # 定义的 Telemetry 字段号全不匹配时（解析后全空），
            # 用通用 wire 解码器 dump 真实字段号
            if not msg.node_id_str and not msg.encoding_path:
                logger.info("MDT DEBUG >>> 字段号不匹配，通用 wire 解码：")
                self._wire_dump(self._last_raw or b"", depth=0, max_depth=2)
                return

            def _show(fields, depth=0, max_depth=3, limit=6):
                for f in fields[:limit]:
                    val = _field_value(f)
                    logger.info(
                        "MDT DEBUG   %s%s = %r (%d 子节点)",
                        "  " * depth, f.name, val, len(f.fields),
                    )
                    if depth < max_depth and f.fields:
                        _show(f.fields, depth + 1, max_depth, limit)

            _show(msg.data_gpb.fields)
        except Exception as exc:
            logger.debug("MDT dump 失败: %s", exc)

    _last_raw: Optional[bytes] = None

    @staticmethod
    def _wire_dump(data: bytes, depth: int = 0, max_depth: int = 2, limit: int = 8) -> None:
        """通用 protobuf wire-format 解码：显示 (字段号, wire类型) 结构。

        字段号与已知消息不匹配时用来逆向真实布局。
        """
        import io as _io

        def _read_varint(buf: _io.BytesIO) -> int:
            result = 0
            shift = 0
            while True:
                b = buf.read(1)
                if not b:
                    raise ValueError("varint 越界")
                result |= (b[0] & 0x7F) << shift
                if not (b[0] & 0x80):
                    return result
                shift += 7

        try:
            buf = _io.BytesIO(data)
            shown = 0
            while shown < limit:
                pos = buf.tell()
                try:
                    key = _read_varint(buf)
                except ValueError:
                    break
                field_no = key >> 3
                wire = key & 0x07
                if wire == 0:  # varint
                    v = _read_varint(buf)
                    logger.info(
                        "MDT WIRE   %s字段 %d (varint) = %d @%d",
                        "  " * depth, field_no, v, pos,
                    )
                elif wire == 2:  # length-delimited
                    ln = _read_varint(buf)
                    payload = buf.read(ln)
                    is_text = all(32 <= c < 127 for c in payload[:24]) and len(payload) > 0
                    preview = (
                        payload[:40].decode("ascii", "replace")
                        if is_text
                        else payload[:16].hex()
                    )
                    logger.info(
                        "MDT WIRE   %s字段 %d (bytes, %d) = %r%s @%d",
                        "  " * depth, field_no, ln, preview,
                        " …" if ln > 40 else "", pos,
                    )
                    if depth < max_depth and ln > 8:
                        MdtDialoutServicer._wire_dump(payload, depth + 1, max_depth, 6)
                elif wire == 5:  # 32-bit
                    buf.read(4)
                    logger.info("MDT WIRE   %s字段 %d (fixed32) @%d", "  " * depth, field_no, pos)
                elif wire == 1:  # 64-bit
                    buf.read(8)
                    logger.info("MDT WIRE   %s字段 %d (fixed64) @%d", "  " * depth, field_no, pos)
                else:
                    break
                shown += 1
        except Exception as exc:
            logger.debug("wire dump 失败: %s", exc)


class MdtCollector:
    """Cisco MDT dial-out 服务：注册到已有的 gRPC 服务器上。

    与 gNMI collector 共用同一个 server 实例（同端口双协议），
    设备按需选择 dial-out 方式。
    """

    def __init__(
        self,
        on_samples: Callable[[List[Dict[str, Any]], Optional[int]], None],
        device_resolver: Callable[[str], Optional[int]],
        debug_dump: bool = False,
    ) -> None:
        self.servicer = MdtDialoutServicer(on_samples, device_resolver, debug_dump)
        self.registered = False

    def attach(self, server: grpc.aio.Server) -> bool:
        """把 MdtDialout 服务注册到现有 gRPC 服务器。

        gRPC 支持同一端口多个服务，设备调 MdtDialout 走这里的 servicer，
        调 gNMI Subscribe 走原来的——互不影响。
        """
        try:
            mdt_pb2_grpc.add_gRPCMdtDialoutServicer_to_server(self.servicer, server)
            self.registered = True
            logger.info("Cisco MDT dial-out 服务已注册")
            return True
        except Exception as exc:
            logger.warning("注册 MDT 服务失败: %s", exc)
            return False

    def status(self) -> Dict[str, Any]:
        """活跃会话状态（供遥测页展示）——委托给 servicer。"""
        if self.servicer is None:
            return {"sessions": []}
        return self.servicer.status()


class _RpcLogger(grpc.aio.ServerInterceptor):
    """诊断用：记录所有进来的 gRPC 调用（含未知方法）。"""

    def __init__(self) -> None:
        self.calls: Dict[str, int] = {}

    async def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method or "?"
        self.calls[method] = self.calls.get(method, 0) + 1
        if self.calls[method] <= 3 or self.calls[method] % 50 == 0:
            logger.info(
                "gRPC 调用 %s（第 %d 次）", method, self.calls[method]
            )
        return await continuation(handler_call_details)


class MdtCollectorServer:
    """Cisco MDT 专用服务器（传统 57000 端口）。

    gNMI collector 占用 57400 时，设备配了 `receiver ... 57000 protocol
    grpc-tcp` 的 dial-out 由这里接收。与 TelemetryCollectorServer 的
    双协议端口互补，两个端口都能收 MDT。
    """

    def __init__(
        self,
        on_samples: Callable[[List[Dict[str, Any]], Optional[int]], None],
        device_resolver: Callable[[str], Optional[int]],
        debug_dump: bool = False,
    ) -> None:
        self.servicer = MdtDialoutServicer(on_samples, device_resolver, debug_dump)
        self._server: Optional[grpc.aio.Server] = None
        self.port = 0

    async def start(self, port: int = 57000) -> bool:
        self.port = port
        try:
            server = grpc.aio.server(
                options=[
                    ("grpc.max_receive_message_length", 16 * 1024 * 1024),
                    ("grpc.max_send_message_length", 16 * 1024 * 1024),
                    ("grpc.keepalive_time_ms", 30000),
                    ("grpc.so_reuseport", 1),
                ],
                interceptors=[_RpcLogger()],
            )
            mdt_pb2_grpc.add_gRPCMdtDialoutServicer_to_server(self.servicer, server)
            bound = server.add_insecure_port(f"0.0.0.0:{port}")
            if not bound:
                logger.warning("MDT 端口 %s 绑定失败", port)
                return False
            await server.start()
            self._server = server
            logger.info("Cisco MDT Collector 监听 0.0.0.0:%s", port)
            return True
        except Exception as exc:
            logger.warning("MDT Collector 启动失败: %s", exc)
            return False

    async def stop(self) -> None:
        if self._server:
            try:
                await self._server.stop(grace=1)
            except Exception as exc:
                logger.debug("MDT Collector 停止异常: %s", exc)
            self._server = None
            await asyncio.sleep(0.3)

    def status(self) -> Dict[str, Any]:
        return self.servicer.status() if self.servicer else {"sessions": []}


# ---------------------------------------------------------------- 无线表合并
def update_wireless_from_msg(msg: mdt_pb2.Telemetry, device_id) -> int:
    """把 MDT 推送的无线数据实时合并进 wireless_ap / wireless_clients 表。

    展示优先级：遥测 > SNMP > SSH——MDT 是 5-30 秒粒度（远快于
    SNMP 轮询），合并时把行标成 telemetry 来源；SNMP 轮询的
    save_wireless 遇到 telemetry 行不覆盖。
    - client-oper-data：ap-name / slot / state（co-state/client-type）
    - access-point-oper-data：AP 主数据（ap-name / wtp-mac / ip /
      model / serial 等，键名按常见 YANG 宽松匹配）→ 无线 AP 表
    - location-oper-data：每客户端 RSSI / SNR（<-120 是设备的
      「无效」标记，丢弃）

    返回更新的行数（AP + 客户端）。
    """
    if not device_id or not msg.HasField("data_gpb"):
        return 0
    path = (msg.encoding_path or "").lower()
    if "wireless" not in path:
        return 0
    is_location = "location" in path
    # AP 射频数据条目：access-point-oper（主数据/CDP）与
    # rrm-oper（ap-auto-rf-dot11-data，按 wtp-mac 关联）两类
    is_ap = "access-point-oper" in path or "rrm-oper" in path

    def _leaves(field: mdt_pb2.TelemetryField, prefix: str) -> Dict[str, Any]:
        """深度优先收集条目内全部叶子（路径相对条目容器）。"""
        out: Dict[str, Any] = {}
        for f in field.fields:
            key = f"{prefix}.{f.name}" if prefix else f.name
            if f.fields:
                out.update(_leaves(f, key))
            else:
                v = _field_value(f)
                if v is not None:
                    out[key] = v
        return out

    # 数据树：.../<条目容器>/<重复条目>。一棵树里可能有**多个**条目组
    # （access-point-oper-data 下既有 ap-radio-neighbor × N，
    # 也有 AP 主数据条目 × N）——要全部收集，不能找到第一个组就停
    updates: List[Tuple[str, Dict[str, Any]]] = []
    root = msg.data_gpb  # noqa: F841（条目提取见下方 _iter_entries）

    def _find_entry_groups(
        field: mdt_pb2.TelemetryField,
    ) -> List[List[mdt_pb2.TelemetryField]]:
        groups: List[List[mdt_pb2.TelemetryField]] = []
        names = [f.name for f in field.fields]
        for name in set(names):
            group = [f for f in field.fields if f.name == name]
            # ≥2 个同名兄弟才算列表；只有 1 个时也要（设备上只有
            # 1 个客户端的场景），但要排除 keys 这类单例键容器
            if len(group) >= 1 and all(g.fields for g in group) and name not in (
                "keys", "content", "oper-data", "capwap-data",
            ):
                groups.append(group)
        # 深一层找（keys / content 等单例容器要穿透）
        for f in field.fields:
            if f.fields and not any(f in g for g in groups):
                groups.extend(_find_entry_groups(f))
        return groups

    def _iter_entries(msg_root: mdt_pb2.TelemetryField):
        for group in _find_entry_groups(msg_root):
            for entry in group[:300]:
                yield entry

    ap_updates: List[Tuple[str, Dict[str, Any]]] = []
    for entry in _iter_entries(root):
        leaves = _leaves(entry, "")
        if not leaves:
            continue
        mac = None
        for k, v in leaves.items():
            if ("client-mac" in k or "ms-mac" in k) and isinstance(v, str):
                # 归一化：location 订阅的 client-mac-addr 可能是
                # 裸 12 位格式，统一成 xx:xx:xx:xx:xx:xx 才能与
                # client-oper 订阅的行对上（否则创建重复行）
                mac = _norm_mac(v)
                break
        if mac:
            updates.append((mac, leaves))
            continue
        # AP 主数据条目：有 ap-name（或 wtp-mac）而无客户端 MAC
        if is_ap:
            ap_name = None
            for k, v in leaves.items():
                if k.endswith("ap-name") and isinstance(v, str) and v:
                    ap_name = v
                    break
            if not ap_name:
                # rrm 树（ap-auto-rf-dot11-data）没有 ap-name——
                # 用 wtp-mac 定位（稍后按 radio_mac 关联 AP 行）
                wtp = leaves.get("wtp-mac") or leaves.get("wtp-mac-addr")
                if wtp:
                    ap_name = f"__mac__{_norm_mac(wtp)}"
            if ap_name:
                if len(ap_updates) == 0:
                    # 首个 AP 条目的字段名（debug 级，诊断用）
                    logger.debug(
                        "MDT AP 条目[%s] 字段: %s", ap_name[:20],
                        {k: str(v)[:20] for k, v in list(leaves.items())[:25]},
                    )
                ap_updates.append((ap_name, leaves))

    if not updates and not ap_updates:
        logger.info(
            "MDT 无线合并: path=%s 未提取到条目",
            (msg.encoding_path or "")[:60],
        )
        return 0

    from app.db.session import SessionLocal
    from app.models.models import WirelessAP, WirelessClient

    touched = 0
    db = SessionLocal()
    try:
        now = datetime.now()

        # ---- 无线 AP 表（遥测优先 upsert）----
        if ap_updates:
            aps = {
                a.name: a
                for a in db.query(WirelessAP)
                .filter(WirelessAP.device_id == device_id)
                .all()
            }
            # radio_mac 索引（rrm 条目按 wtp-mac 关联用）
            aps_by_mac = {
                _norm_mac(a.radio_mac): a
                for a in aps.values()
                if a.radio_mac
            }
            for ap_name, leaves in ap_updates[:200]:
                if ap_name.startswith("__mac__"):
                    # rrm 条目：按 radio_mac 找已有 AP 行；找不到跳过
                    # （噪底等 RF 数据挂到 AP 行上，不新建无名字的行）
                    ap_row = aps_by_mac.get(ap_name[len("__mac__"):])
                    if ap_row is None:
                        continue
                else:
                    ap_row = aps.get(ap_name)
                    if ap_row is None:
                        ap_row = WirelessAP(
                            device_id=device_id, name=ap_name, ts=now
                        )
                        aps[ap_name] = ap_row
                        db.add(ap_row)
                # 键名按常见 YANG 宽松匹配（设备版本差异容忍）
                # 键名按 9800L 实测报文（16:15 批 dump）：
                # mac-addr / wtp-mac-addr / cdp-cache-ap-address。
                # 注意：ip-address.ip-address-value 实测是 **WLC
                # 自己的管理地址**（15 个 AP 全同一个 IP 的根因），
                # AP 的真实地址在 CDP 缓存键 cdp-cache-ap-address
                for lk, col, conv in (
                    ("mac-addr", "radio_mac", _norm_mac),
                    ("wtp-mac-addr", "radio_mac", _norm_mac),
                    ("wtp-mac", "radio_mac", _norm_mac),
                    ("cdp-cache-ap-address", "ip", str),
                    ("serial-number", "serial_number", str),
                    ("serial-num", "serial_number", str),
                    ("model", "model", str),
                    ("model-id", "model", str),
                    ("description", "location", str),
                    ("location", "location", str),
                ):
                    v = leaves.get(lk)
                    if v is None:
                        # 嵌套路径（如 oper-data.wtp-mac）
                        for k2, v2 in leaves.items():
                            if k2.endswith("." + lk) and v2:
                                v = v2
                                break
                    if v is not None and v != getattr(ap_row, col):
                        setattr(ap_row, col, conv(v))
                # rrm 的噪底（noise-floor 键，dBm）——订阅数据里
                # 有该字段时自动填充 AP 行
                for nk in ("noise-floor", "oper-data.noise-floor"):
                    v = leaves.get(nk)
                    if v is not None:
                        try:
                            ap_row.noise_floor = int(v)
                        except (ValueError, TypeError):
                            pass
                        break
                ap_row.source = "telemetry"
                ap_row.ts = now
                touched += 1

        clients = {
            c.mac: c
            for c in db.query(WirelessClient)
            .filter(WirelessClient.device_id == device_id)
            .all()
        }
        for mac, leaves in updates:
            # 纯遥测设备没有 SNMP 基础行——遇到新 MAC 直接建行
            # （upsert），否则合并器只更新已有行、详情页永远空
            c = clients.get(mac)
            if c is None:
                c = WirelessClient(device_id=device_id, mac=mac, ts=now)
                clients[mac] = c
                db.add(c)
            changed = False
            if is_location:
                snr = leaves.get("radio-measurements.snr")
                if isinstance(snr, (int, float)) and snr > -120 and snr != c.snr:
                    c.snr = int(snr)
                    changed = True
                rssi = (
                    leaves.get("radio-measurements.sample.rssi-value-a")
                    or leaves.get("radio-measurements.sample.rssi-value-b")
                )
                if isinstance(rssi, (int, float)) and rssi > -120:
                    if c.rssi != int(rssi):
                        c.rssi = int(rssi)
                        c.signal_strength = int(rssi)
                        changed = True
            else:
                # 客户端条目字段全量映射（9800L 实测键名）——
                # 遥测优先填满详情页的客户端信息
                for lk, attr, conv in (
                    ("ap-name", "ap_name", str),
                    ("ms-ap-slot-id", "slot", int),
                    ("co-state", "state", str),
                    ("dot11-state", "state", str),
                    ("username", "username", str),
                    ("ms-mac-address", "bssid", _norm_mac),
                    ("ms-bssid", "bssid", _norm_mac),
                    ("bssid", "bssid", _norm_mac),
                    ("ssid", "ssid", str),
                    ("vap-ssid", "ssid", str),
                    ("ap-mac-address", "bssid", _norm_mac),
                ):
                    v = leaves.get(lk)
                    if v is None:
                        continue
                    try:
                        v = conv(v) if conv is not str else str(v)
                    except (ValueError, TypeError):
                        continue
                    if v in (None, ""):
                        continue
                    # state 允许多个来源键先后覆盖（后面的键
                    # dot11-state 比 co-state 更详细）
                    if v != getattr(c, attr):
                        setattr(c, attr, v)
                        changed = True
                # 信道：客户端条目不含信道（由 AP radio 决定），
                # wlan-id / vlan 类数值字段暂无展示列，忽略
            # 遥测来源：SNMP 轮询不再覆盖此行的遥测字段
            c.source = "telemetry"
            if changed:
                c.ts = now
                touched += 1
            elif c.ts is None:
                c.ts = now
        if touched:
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("MDT 无线合并失败: %s", exc)
    finally:
        db.close()
    if touched or updates:
        logger.info(
            "MDT 无线合并: path=%s 条目=%d 更新=%d",
            (msg.encoding_path or "")[:60], len(updates), touched,
        )
    return touched
