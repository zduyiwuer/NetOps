"""Telemetry 服务层：把 gNMI 遥测样本归一化为平台指标。

关键设计：遥测数据写入与 SNMP 采集**相同的** DeviceMetric / InterfaceMetric 表，
因此现有的总览大屏、接口速率、TopN 等看板无需改动即可展示遥测数据。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import grpc

from sqlalchemy.orm import Session

from app.collectors.engine import rate_tracker
from app.collectors.telemetry import (
    DEFAULT_GNMI_PORT,
    PathMapping,
    TelemetryCollectorServer,
    TelemetryClientSession,
    TelemetryPathMapper,
    TelemetrySample,
)
from app.collectors.mdt import MdtCollectorServer
from app.db.session import SessionLocal
from app.models.models import (
    Device,
    DeviceMetric,
    InterfaceMetric,
    TelemetryConfig,
    TelemetryPathMapping,
    TelemetrySample as TelemetrySampleModel,
)

logger = logging.getLogger("netops.telemetry.service")

# 内存中的接口标识映射：(device_id, 接口名) → if_index
_if_index_cache: Dict[Tuple[int, str], int] = {}

# 运行时状态
collector_server: Optional[TelemetryCollectorServer] = None
mdt_server: Optional[Any] = None  # Cisco MDT 专用服务器（57000 端口）
client_sessions: Dict[int, TelemetryClientSession] = {}


def _settings_get(key: str, default: Any) -> Any:
    """轻量读系统设置（无 db 会话时直接给默认值，避免循环依赖）。"""
    try:
        from app.services import settings_service

        db = SessionLocal()
        try:
            return settings_service.get(db, key, default)
        finally:
            db.close()
    except Exception:
        return default


def settings_get(key: str, default: Any) -> Any:
    return _settings_get(key, default)


# ---------------------------------------------------------------- 归一化
def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, (list, tuple)):
        for item in value:
            f = _to_float(item)
            if f is not None:
                return f
    return None


def _alloc_if_index(device_id: int, name: str) -> int:
    """为接口名分配稳定的 if_index。"""
    key = (device_id, name)
    if key in _if_index_cache:
        return _if_index_cache[key]
    db = SessionLocal()
    try:
        row = (
            db.query(InterfaceMetric)
            .filter(InterfaceMetric.device_id == device_id, InterfaceMetric.if_name == name)
            .order_by(InterfaceMetric.id.desc())
            .first()
        )
        if row:
            _if_index_cache[key] = row.if_index
            return row.if_index
        # 取该设备已用的最大 index + 1
        max_idx = (
            db.query(func_max(InterfaceMetric.if_index))
            .filter(InterfaceMetric.device_id == device_id)
            .scalar()
        )
        idx = (max_idx or 0) + 1
        _if_index_cache[key] = idx
        return idx
    finally:
        db.close()


def func_max(col):
    from sqlalchemy import func as _f

    return _f.max(col)


def store_telemetry_samples(
    samples: List[TelemetrySample],
    device_id: Optional[int] = None,
    source: str = "push",
) -> int:
    """把遥测样本写入指标表。"""
    if not samples:
        return 0

    db = SessionLocal()
    try:
        now = datetime.now()
        device = db.get(Device, device_id) if device_id else None
        did = device.id if device else None

        # 按指标聚合
        system_metrics: Dict[str, Any] = {}
        iface_counters: Dict[str, Dict[str, Any]] = {}

        for s in samples:
            if s.per_interface and s.interface:
                bucket = iface_counters.setdefault(s.interface, {})
                bucket[s.metric] = s.value
            elif s.metric in (
                "cpu_usage", "mem_used", "mem_available", "mem_free",
                "mem_total", "temperature", "uptime",
            ):
                system_metrics[s.metric] = s.value
            elif s.metric.startswith("if_") and s.interface:
                bucket = iface_counters.setdefault(s.interface, {})
                bucket[s.metric] = s.value

        # ---- 设备级指标 ----
        if system_metrics:
            cpu = _to_float(system_metrics.get("cpu_usage"))
            mem_used = _to_float(system_metrics.get("mem_used"))
            mem_total = _to_float(system_metrics.get("mem_total"))
            mem_avail = _to_float(system_metrics.get("mem_available"))
            mem_free = _to_float(system_metrics.get("mem_free"))

            mem_usage = None
            mem_used_mb = None
            mem_total_mb = None
            if mem_total and mem_used is not None:
                mem_total_mb = mem_total / (1024 * 1024)
                mem_used_mb = mem_used / (1024 * 1024)
                mem_usage = round(mem_used / mem_total * 100, 2)
            elif mem_avail is not None and mem_total:
                mem_total_mb = mem_total / (1024 * 1024)
                mem_used_mb = (mem_total - mem_avail) / (1024 * 1024)
                mem_usage = round((mem_total - mem_avail) / mem_total * 100, 2)
            elif mem_free is not None and mem_total:
                mem_total_mb = mem_total / (1024 * 1024)
                mem_used_mb = (mem_total - mem_free) / (1024 * 1024)
                mem_usage = round((mem_total - mem_free) / mem_total * 100, 2)

            db.add(
                DeviceMetric(
                    device_id=did,
                    ts=now,
                    cpu_usage=cpu,
                    mem_usage=mem_usage,
                    mem_total_mb=mem_total_mb,
                    mem_used_mb=mem_used_mb,
                    uptime_sec=int(_to_float(system_metrics.get("uptime")) or 0) or None,
                    reachability=True,
                )
            )

        # ---- 接口级指标 ----
        for iface_name, counters in iface_counters.items():
            if not did:
                continue
            idx = _alloc_if_index(did, iface_name)
            in_octets = _to_float(counters.get("if_in_octets"))
            out_octets = _to_float(counters.get("if_out_octets"))
            if in_octets is None and out_octets is None:
                continue

            in_bps, out_bps = rate_tracker.compute(
                did, idx, int(in_octets or 0), int(out_octets or 0), time.time()
            )
            speed = int(_to_float(counters.get("if_speed")) or 0)
            util = round(min(100.0, max(in_bps, out_bps) / speed * 100), 2) if speed else 0.0

            oper_status = counters.get("if_oper_status")
            oper = 0
            if isinstance(oper_status, str):
                oper = 1 if oper_status.upper() in ("UP", "ENABLED") else 2
            elif isinstance(oper_status, (int, float)):
                oper = 1 if oper_status == 1 else 2

            db.add(
                InterfaceMetric(
                    device_id=did,
                    if_index=idx,
                    if_name=iface_name,
                    if_descr=str(counters.get("if_descr") or ""),
                    if_alias=iface_name,
                    speed_bps=speed,
                    admin_status=1 if not oper_status else oper,
                    oper_status=oper,
                    in_bps=round(in_bps, 2),
                    out_bps=round(out_bps, 2),
                    in_octets=int(in_octets or 0),
                    out_octets=int(out_octets or 0),
                    in_errors=int(_to_float(counters.get("if_in_errors")) or 0),
                    out_errors=int(_to_float(counters.get("if_out_errors")) or 0),
                    utilization=util,
                    ts=now,
                )
            )

        # ---- 样本审计 ----
        for s in samples[:200]:  # 限量存储，避免高频写入拖慢
            num = _to_float(s.value)
            db.add(
                TelemetrySampleModel(
                    device_id=did,
                    ts=s.timestamp or now,
                    metric=s.metric,
                    value_num=num,
                    value_text="" if num is not None else str(s.value)[:250],
                    interface=s.interface,
                    source_path=s.source_path[:500],
                    source=source,
                )
            )

        db.commit()

        # 更新配置状态
        if did:
            cfg = (
                db.query(TelemetryConfig)
                .filter(TelemetryConfig.device_id == did, TelemetryConfig.enabled.is_(True))
                .first()
            )
            if cfg:
                cfg.received_count += len(samples)
                cfg.last_update_at = now
                cfg.status = "connected"
                cfg.status_message = f"最近收到 {len(samples)} 条遥测样本"
                db.commit()
        return len(samples)
    except Exception as exc:
        db.rollback()
        logger.warning("遥测样本入库失败: %s", exc)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------- 设备解析
def resolve_device_by_peer(peer: str) -> Optional[int]:
    """根据 gRPC 对端地址匹配设备（形如 10.0.0.1:41234）。"""
    if not peer:
        return None
    ip = peer.split(":")[0] if ":" in peer else peer
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.ip == ip).first()
        return device.id if device else None
    finally:
        db.close()


def _build_mapper(db: Session) -> TelemetryPathMapper:
    """构造路径映射器：内置映射 + 用户自定义映射。

    自定义映射让用户可以订阅厂商未适配的私有路径，
    无需等平台适配就能拿到数据。
    """
    extra: List[PathMapping] = []
    try:
        rows = (
            db.query(TelemetryPathMapping)
            .filter(TelemetryPathMapping.enabled.is_(True))
            .all()
        )
        for m in rows:
            extra.append(
                PathMapping(
                    path=m.path,
                    metric=m.metric,
                    scale=m.scale or 1.0,
                )
            )
    except Exception as exc:
        logger.warning("加载自定义遥测路径失败: %s", exc)
    return TelemetryPathMapper(extra_mappings=extra)


def _port_bindable(port: int, host: str = "0.0.0.0") -> bool:
    """端口能否被绑定。

    Windows 的「排除端口范围」（Hyper-V / WSL / Docker 动态创建，
    可用 ``netsh int ipv4 show excludedportrange protocol=tcp`` 查看）
    会让落在其中的端口绑定时报 ``Permission denied``——
    这类端口既没有进程占用，netstat 也查不到，很容易误判成「端口空闲」。

    探测时**不设 SO_REUSEADDR**：Windows 上该标志允许抢占已被占用的端口，
    设了就会把「端口已被别的程序占用」误判成可用。
    绑定后再 listen()，确保探测结论与真实绑定行为一致。
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen(1)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def resolve_gnmi_port(preferred: int, search_span: int = 200, auto_fallback: bool = True) -> tuple[int, bool]:
    """解析实际可用的 gNMI 监听端口，返回 (端口, 是否发生回退)。

    - auto_fallback=True（默认端口未显式配置时）：
      优先使用 57400；若被系统排除或被其它进程占用，
      在邻近范围找一个可用端口，保证服务能起来。
    - auto_fallback=False（用户在系统设置里显式配置了端口时）：
      **不自动回退**——用户配置的端口绑定失败就让启动失败，
      并写系统日志说明原因。否则用户会以为在监听自己设的端口，
      实际平台偷偷换了一个，设备怎么推都推不上来。
    """
    if _port_bindable(preferred):
        return preferred, False
    if not auto_fallback:
        return preferred, False
    # 先往下找，再往上找（优先靠近标准端口）
    for offset in range(1, search_span + 1):
        for cand in (preferred - offset, preferred + offset):
            if cand < 1024 or cand > 65535:
                continue
            if _port_bindable(cand):
                return cand, True
    return preferred, False


# ---------------------------------------------------------------- 生命周期
async def start_telemetry_services(gnmi_port: int = 57400) -> bool:
    """启动 gNMI collector 服务端与已启用的 client 会话。"""
    global collector_server

    def on_samples(samples: List[TelemetrySample], device_id: Optional[int]) -> None:
        store_telemetry_samples(samples, device_id, source="push")

    # 映射器：内置映射 + 用户自定义路径（collector 与 client 两种模式共用）
    db = SessionLocal()
    try:
        mapper = _build_mapper(db)
    finally:
        db.close()

    # 端口可用性预检：标准端口可能被 Windows 排除范围占用而无法绑定。
    # 用户在系统设置里显式配置的端口**不自动回退**——绑定失败就明确报错，
    # 避免「以为在监听自己设的端口，实际平台偷偷换了一个」
    from app.services.settings_service import explicit_keys

    db_probe = SessionLocal()
    try:
        user_set = "collector.gnmi_port" in explicit_keys(db_probe)
    finally:
        db_probe.close()
    actual_port, fallback = resolve_gnmi_port(
        gnmi_port, auto_fallback=not user_set
    )
    if fallback:
        # 只发一条日志：系统日志服务会自动捕获 warning 级日志，
        # extra 里的结构化上下文会一并落到系统日志的 context 字段
        logger.warning(
            "gNMI 标准端口 %s 不可用（可能落在系统排除端口范围或被占用），"
            "自动改用端口 %s。设备采用 dial-out 推送时需把 target 端口改为 %s",
            gnmi_port, actual_port, actual_port,
            extra={
                "syslog_context": {
                    "preferred_port": gnmi_port,
                    "actual_port": actual_port,
                    "reason": "标准端口被系统排除或被其它程序占用，已自动回退",
                }
            },
        )

    collector_server = TelemetryCollectorServer(on_samples, resolve_device_by_peer, mapper)
    if not await collector_server.start(actual_port):
        collector_server = None
        logger.warning(
            "gNMI collector 启动失败，端口 %s 可能被占用",
            actual_port,
            extra={
                "syslog_context": {
                    "port": actual_port,
                    "reason": "端口绑定失败，可能被占用或落在系统排除端口范围内",
                }
            },
        )
        return False

    # ---------------------------------------------------------------- Cisco MDT
    # 传统 MDT 的常见端口是 57000（gNMI 是 57400）——两个端口都监听，
    # 设备配置哪个都能通。57400 已由上面的 collector 覆盖
    # （MDT 与 gNMI 注册在同一服务器上），这里再起一个 MDT 专用服务器。
    global mdt_server
    db_probe2 = SessionLocal()
    try:
        mdt_user_set = "collector.mdt_port" in explicit_keys(db_probe2)
    finally:
        db_probe2.close()
    mdt_port = int(_settings_get("collector.mdt_port", 57000) or 57000)
    mdt_port, mdt_fallback = resolve_gnmi_port(
        mdt_port, auto_fallback=not mdt_user_set
    )
    if mdt_port != collector_server.port:  # 与 gNMI 端口相同时不重复监听
        mdt_server = MdtCollectorServer(on_samples, resolve_device_by_peer)
        if await mdt_server.start(mdt_port):
            if mdt_fallback:
                logger.warning(
                    "Cisco MDT 端口 57000 不可用，改用 %s。"
                    "设备侧 receiver 端口需改为 %s", mdt_port, mdt_port,
                )
        else:
            mdt_server = None
            if mdt_user_set:
                logger.warning(
                    "Cisco MDT 端口 %s（用户配置）绑定失败——不自动回退。"
                    "请检查端口是否被占用或落在系统排除范围", mdt_port,
                )
    else:
        mdt_server = None

    # 启动 client 模式会话
    db = SessionLocal()
    try:
        configs = (
            db.query(TelemetryConfig)
            .filter(TelemetryConfig.enabled.is_(True), TelemetryConfig.mode == "client")
            .all()
        )
        for cfg in configs:
            try:
                paths = json.loads(cfg.subscribe_paths or "[]")
            except Exception:
                paths = []
            if not cfg.device_id or not cfg.target_addr or not paths:
                continue
            session = TelemetryClientSession(
                device_id=cfg.device_id,
                target=cfg.target_addr,
                port=cfg.target_port or 57400,
                paths=paths,
                sample_interval_ms=cfg.sample_interval_ms or 10000,
                on_samples=on_samples,
                username=cfg.username or "",
                password=cfg.password or "",
                mapper=mapper,
            )
            await session.start()
            client_sessions[cfg.device_id] = session
            logger.info("gNMI 客户端会话已启动: device=%s target=%s", cfg.device_id, cfg.target_addr)
    finally:
        db.close()
    return True


async def stop_telemetry_services() -> None:
    global mdt_server
    if mdt_server is not None:
        try:
            await mdt_server.stop()
        except Exception:
            pass
        mdt_server = None

    global collector_server
    for session in list(client_sessions.values()):
        await session.stop()
    client_sessions.clear()
    if collector_server:
        await collector_server.stop()
        collector_server = None


def telemetry_status() -> Dict[str, Any]:
    """遥测运行时状态。"""
    sessions = []
    if collector_server:
        for peer, st in collector_server.sessions.items():
            sessions.append({
                "peer": peer,
                "device_id": st.device_id,
                "mode": st.mode,
                "connected_at": st.connected_at.isoformat() if st.connected_at else None,
                "last_update": st.last_update.isoformat() if st.last_update else None,
                "subscriptions": st.subscriptions[:10],
                "received": st.received,
                "status": st.status,
            })
    for did, sess in client_sessions.items():
        sessions.append({
            "peer": f"{sess.target}:{sess.port}",
            "device_id": did,
            "mode": "client",
            "connected_at": sess.connected_at.isoformat() if sess.connected_at else None,
            "last_update": None,
            "subscriptions": sess.paths[:10],
            "received": sess.received,
            "status": sess.status,
        })
    return {
        "collector_running": collector_server is not None,
        "collector_port": collector_server.port if collector_server else None,
        # 只在「实际端口 ≠ 用户配置的端口」时才标回退——
        # 用户显式改过端口且按用户端口在监听，不算回退
        "collector_port_fallback": (
            collector_server.port != int(cfg_port)
            if collector_server and collector_server.port
            and (cfg_port := _settings_get("collector.gnmi_port", DEFAULT_GNMI_PORT))
            else False
        ),
        "sessions": sessions,
    }
