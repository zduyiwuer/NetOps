"""采集服务运行时控制器。

支持在系统管理页在线启用/关闭各采集通道，无需重启平台：
- gNMI Collector（Telemetry）
- NetFlow / IPFIX 采集器
- Syslog 接收服务
- SNMP 轮询调度器
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("netops.collector")

# 运行时持有的服务实例
_gnmi_server = None
_netflow_collector = None
_syslog_udp = None
_syslog_tcp = None


async def start_gnmi(port: int = 57400) -> bool:
    """启动 gNMI Collector。"""
    global _gnmi_server
    if _gnmi_server is not None:
        return True
    try:
        from app.services.telemetry_service import start_telemetry_services

        ok = await start_telemetry_services(gnmi_port=port)
        if not ok:
            logger.warning("gNMI Collector 启动失败")
            _gnmi_server = None
            return False
        _gnmi_server = True
        logger.info("gNMI Collector 已启用，端口 %s", port)
        return True
    except Exception as exc:
        logger.warning("gNMI Collector 启用失败: %s", exc)
        _gnmi_server = None
        return False


async def stop_gnmi() -> bool:
    """停止 gNMI Collector。"""
    global _gnmi_server
    if _gnmi_server is None:
        return True
    try:
        from app.services.telemetry_service import stop_telemetry_services

        await stop_telemetry_services()
        _gnmi_server = None
        logger.info("gNMI Collector 已关闭")
        return True
    except Exception as exc:
        logger.warning("gNMI Collector 关闭失败: %s", exc)
        return False


async def start_netflow(ports: Optional[list] = None) -> bool:
    """启动 NetFlow 采集器。"""
    global _netflow_collector
    if _netflow_collector is not None:
        return True
    try:
        from app.collectors.netflow import NetflowCollector
        from app.services.netflow_service import store_flows

        collector = NetflowCollector(handler=lambda records, ip: store_flows(records, ip))
        await collector.start(udp_ports=ports or None)
        _netflow_collector = collector
        logger.info("NetFlow 采集器已启用，端口 %s", ports)
        return True
    except Exception as exc:
        logger.warning("NetFlow 采集器启用失败: %s", exc)
        return False


async def stop_netflow() -> bool:
    """停止 NetFlow 采集器。"""
    global _netflow_collector
    if _netflow_collector is None:
        return True
    try:
        await _netflow_collector.stop()
        _netflow_collector = None
        logger.info("NetFlow 采集器已关闭")
        return True
    except Exception as exc:
        logger.warning("NetFlow 采集器关闭失败: %s", exc)
        return False


async def start_syslog() -> bool:
    """启动 Syslog 接收服务。"""
    global _syslog_udp, _syslog_tcp
    if _syslog_udp is not None:
        return True
    try:
        from app.services.syslog_server import start_syslog_server

        udp, tcp = await start_syslog_server()
        _syslog_udp, _syslog_tcp = udp, tcp
        logger.info("Syslog 接收服务已启用")
        return True
    except Exception as exc:
        logger.warning("Syslog 接收服务启用失败: %s", exc)
        return False


async def stop_syslog() -> bool:
    """停止 Syslog 接收服务。"""
    global _syslog_udp, _syslog_tcp
    try:
        if _syslog_tcp:
            await _syslog_tcp.stop()
        if _syslog_udp:
            _syslog_udp.close()
    except Exception as exc:
        logger.warning("Syslog 接收服务关闭失败: %s", exc)
    _syslog_udp, _syslog_tcp = None, None
    logger.info("Syslog 接收服务已关闭")
    return True


async def set_scheduler_enabled(enabled: bool) -> bool:
    """启用/停用 SNMP 轮询调度器。"""
    from app.services.scheduler import get_scheduler

    sched = get_scheduler()
    try:
        if enabled:
            if not sched.running:
                from app.services.scheduler import start_scheduler

                start_scheduler()
        else:
            if sched.running:
                sched.pause()
                # 关闭时移除采集任务，保留其他任务
                job = sched.get_job("collect_all")
                if job:
                    job.remove()
                logger.info("SNMP 轮询调度已暂停")
        return True
    except Exception as exc:
        logger.warning("调度器开关失败: %s", exc)
        return False


async def apply_switches(changes: Dict[str, Any]) -> Dict[str, Any]:
    """根据设置变更动态启停各采集服务。"""
    from app.services.settings_service import effective_settings

    cfg = effective_settings()
    result: Dict[str, Any] = {}

    # SNMP 轮询
    if "collector.snmp_enabled" in changes:
        want = bool(cfg.get("collector.snmp_enabled"))
        ok = await set_scheduler_enabled(want)
        result["collector.snmp_enabled"] = ok

    # Telemetry / gNMI
    if "collector.telemetry_enabled" in changes:
        want = bool(cfg.get("collector.telemetry_enabled"))
        if want:
            result["collector.telemetry_enabled"] = await start_gnmi(
                int(cfg.get("collector.gnmi_port") or 57400)
            )
        else:
            result["collector.telemetry_enabled"] = await stop_gnmi()

    # 端口变更：若 gNMI 正在运行，需要用新端口重启才生效
    elif "collector.gnmi_port" in changes:
        if bool(cfg.get("collector.telemetry_enabled")):
            await stop_gnmi()
            result["collector.gnmi_port"] = await start_gnmi(
                int(cfg.get("collector.gnmi_port") or 57400)
            )

    # NetFlow
    if "collector.netflow_enabled" in changes:
        want = bool(cfg.get("collector.netflow_enabled"))
        if want:
            ports = [
                int(p)
                for p in str(cfg.get("collector.netflow_ports") or "2055,4739,9995").split(",")
                if p.strip().isdigit()
            ]
            result["collector.netflow_enabled"] = await start_netflow(ports or None)
        else:
            result["collector.netflow_enabled"] = await stop_netflow()

    # Syslog
    if "collector.syslog_enabled" in changes:
        want = bool(cfg.get("collector.syslog_enabled"))
        if want:
            result["collector.syslog_enabled"] = await start_syslog()
        else:
            result["collector.syslog_enabled"] = await stop_syslog()

    return result


def status() -> Dict[str, Any]:
    """采集服务当前运行状态。"""
    from app.services.scheduler import get_scheduler
    from app.services.telemetry_service import telemetry_status

    sched = get_scheduler()
    tel = telemetry_status()
    snmp_running = sched.running and sched.get_job("collect_all") is not None
    tel_detail = f"端口 {tel['collector_port']} · 会话 {len(tel['sessions'])}"
    if tel.get("collector_port_fallback"):
        tel_detail += "（标准端口不可用，已自动回退；设备推送需用此端口）"
    return {
        "snmp": {
            "running": snmp_running,
            "desc": "平台主动 SNMP 轮询采集接口计数器与 CPU/内存",
            "detail": f"调度任务 {len(sched.get_jobs())} 个",
        },
        "telemetry": {
            "running": tel["collector_running"],
            "desc": "gNMI 流式遥测服务（设备订阅 / 平台订阅）",
            "detail": tel_detail,
        },
        "netflow": {
            "running": _netflow_collector is not None,
            "desc": "NetFlow / IPFIX 流记录接收",
            "detail": (
                f"UDP {','.join(str(p) for p in _netflow_collector._udp_ports)}"
                if _netflow_collector and getattr(_netflow_collector, "_udp_ports", None)
                else ("已监听" if _netflow_collector else "未启动")
            ),
        },
        "syslog": {
            "running": _syslog_udp is not None,
            "desc": "Syslog 日志接收服务",
            "detail": f"UDP {settings_port()}",
        },
    }


def settings_port() -> int:
    from app.core.config import settings

    return settings.syslog_udp_port
