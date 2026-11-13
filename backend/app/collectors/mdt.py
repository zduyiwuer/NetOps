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
