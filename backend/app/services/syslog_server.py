"""Syslog 接收服务：UDP/TCP 监听，解析后入库并可触发告警。"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.models import AlertEvent, Device, DeviceLog

logger = logging.getLogger("netops.syslog")

FACILITY_NAMES = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon", 4: "auth", 5: "syslog",
    6: "lpr", 7: "news", 8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    12: "ntp", 13: "audit", 14: "alert", 15: "clock", 16: "local0",
    17: "local1", 18: "local2", 19: "local3", 20: "local4", 21: "local5",
    22: "local6", 23: "local7",
}

SEVERITY_NAMES = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err", 4: "warning",
    5: "notice", 6: "info", 7: "debug",
}

# RFC3164: <PRI>TIMESTAMP HOSTNAME TAG: MSG
# TAG 允许包含 %（华为/H3C 的 %%01MODULE/LEVEL/TYPE: 格式）
_RFC3164 = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<tag>[^\s:]+):\s*(?P<msg>.*)$",
    re.DOTALL,
)
# RFC3164 无 TAG 形式：<PRI>TIMESTAMP HOSTNAME MSG
_RFC3164_NOTAG = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<ts>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?P<msg>.*)$",
    re.DOTALL,
)
# RFC5424: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID MSG
_RFC5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>1\s+(?P<ts>\S+)\s+(?P<host>\S+)\s+(?P<app>\S+)\s+"
    r"(?P<procid>\S+)\s+(?P<msgid>\S+)\s+(?P<msg>.*)$",
    re.DOTALL,
)
# 无 PRI 的裸消息
_BARE = re.compile(r"^(?P<host>\S+)\s+(?P<msg>.*)$", re.DOTALL)

# 华为/H3C 风格 TAG：%%01MODULE/LEVEL/TYPE:  → LEVEL 即严重级别数字
_HUAWEI_TAG_LEVEL = re.compile(r"/(\d)(?:/|$)")
# Cisco 风格 TAG：%LINK-3-UPDOWN:  → 破折号后的数字即级别
_CISCO_TAG_LEVEL = re.compile(r"^%[A-Za-z0-9_\-]*-(\d)-")

_LEVEL_KEYWORDS = [
    (r"\bEMERG(ENCY)?\b", 0), (r"\bALERT\b", 1), (r"\bCRIT(ICAL)?\b", 2),
    (r"\bERR(OR)?\b", 3), (r"\bWARN(ING)?\b", 4), (r"\bNOTICE\b", 5),
    (r"\bINFO\b", 6), (r"\bDEBUG\b", 7),
]


def parse_syslog(raw: str) -> Tuple[int, int, str, str, str, str]:
    """解析 syslog 行。

    返回 (facility_code, severity_code, hostname, severity_name, message, priority)
    """
    raw = (raw or "").strip()
    pri = 13 * 8 + 6  # 默认 local0.info
    hostname = ""
    message = raw
    tag_level: Optional[int] = None

    m = _RFC5424.match(raw)
    if m:
        pri = int(m.group("pri"))
        hostname = m.group("host") or ""
        message = m.group("msg") or ""
    else:
        m = _RFC3164.match(raw)
        if m:
            pri = int(m.group("pri"))
            hostname = m.group("host") or ""
            message = m.group("msg") or ""
            # 华为/H3C 的 %%01MODULE/LEVEL/TYPE: 形式，TAG 中的数字即级别
            tm = _HUAWEI_TAG_LEVEL.search(m.group("tag") or "")
            if tm:
                tag_level = int(tm.group(1))
            else:
                # Cisco 的 %LINK-3-UPDOWN: 形式
                cm = _CISCO_TAG_LEVEL.match(m.group("tag") or "")
                if cm:
                    tag_level = int(cm.group(1))
        else:
            m = _RFC3164_NOTAG.match(raw)
            if m:
                pri = int(m.group("pri"))
                hostname = m.group("host") or ""
                message = m.group("msg") or ""
            else:
                m = _BARE.match(raw)
                if m and " " in raw:
                    hostname = m.group("host")
                    message = m.group("msg")

    facility = pri // 8
    severity = pri % 8

    # 华为/H3C TAG 级别优先于 PRI 推算
    if tag_level is not None and 0 <= tag_level <= 7:
        severity = tag_level
    else:
        # 关键字覆盖（仅当 PRI 未提供有效级别信息时）
        for pattern, sev in _LEVEL_KEYWORDS:
            if re.search(pattern, message, re.IGNORECASE):
                severity = sev
                break

    return (
        facility,
        severity,
        hostname,
        SEVERITY_NAMES.get(severity, "info"),
        message.strip(),
        pri,
    )


def _find_device(db: Session, source_ip: str, hostname: str) -> Optional[Device]:
    device = db.query(Device).filter(Device.ip == source_ip).first()
    if device:
        return device
    if hostname:
        device = db.query(Device).filter(Device.hostname == hostname).first()
        if device:
            return device
        device = db.query(Device).filter(Device.name == hostname).first()
        if device:
            return device
    return None


def store_log(
    db: Session,
    raw: str,
    source_ip: str = "",
    protocol: str = "syslog",
) -> Optional[DeviceLog]:
    """解析并保存一条日志。"""
    facility, severity, hostname, severity_name, message, pri = parse_syslog(raw)
    device = _find_device(db, source_ip, hostname)
    row = DeviceLog(
        device_id=device.id if device else None,
        source_ip=source_ip,
        hostname=hostname or (device.hostname if device else ""),
        facility=FACILITY_NAMES.get(facility, str(facility)),
        severity=severity_name,
        priority=pri,
        facility_code=facility,
        severity_code=severity,
        message=message,
        raw=raw,
        protocol=protocol,
        ts=datetime.now(),
    )
    db.add(row)
    db.commit()
    return row


# ---------------------------------------------------------------- 服务器
class SyslogUdpProtocol(asyncio.DatagramProtocol):
    """UDP syslog 接收。"""

    def __init__(self) -> None:
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return
        source_ip = addr[0] if addr else ""
        asyncio.create_task(self._handle(text, source_ip))

    async def _handle(self, text: str, source_ip: str) -> None:
        await asyncio.to_thread(self._store, text, source_ip)

    def _store(self, text: str, source_ip: str) -> None:
        db = SessionLocal()
        try:
            store_log(db, text, source_ip)
        except Exception as exc:
            logger.warning("syslog 入库失败: %s", exc)
        finally:
            db.close()


class SyslogTcpServer:
    """TCP syslog 接收（按行解析，支持 octet-counting 帧）。"""

    def __init__(self) -> None:
        self.server: Optional[asyncio.AbstractServer] = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        source_ip = ""
        try:
            peer = writer.get_extra_info("peername")
            source_ip = peer[0] if peer else ""
            buffer = b""
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        # RFC6587 octet-counting 帧：<len>msg
                        if re.match(r"^\d+\s", text):
                            m = re.match(r"^(\d+)\s(.*)$", text, re.DOTALL)
                            if m:
                                text = m.group(2)
                        await asyncio.to_thread(self._store, text, source_ip)
        except Exception as exc:
            logger.warning("syslog tcp 处理异常: %s", exc)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _store(self, text: str, source_ip: str) -> None:
        db = SessionLocal()
        try:
            store_log(db, text, source_ip)
        except Exception as exc:
            logger.warning("syslog 入库失败: %s", exc)
        finally:
            db.close()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self.handle, "0.0.0.0", settings.syslog_tcp_port)
        logger.info("Syslog TCP 监听 0.0.0.0:%s", settings.syslog_tcp_port)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def start_syslog_server() -> Tuple[Optional[asyncio.DatagramTransport], SyslogTcpServer]:
    """启动 UDP + TCP syslog 接收。"""
    if not settings.syslog_enabled:
        logger.info("Syslog 接收已禁用")
        return None, SyslogTcpServer()

    loop = asyncio.get_running_loop()
    udp_transport, _ = await loop.create_datagram_endpoint(
        SyslogUdpProtocol, local_addr=(settings.syslog_host, settings.syslog_udp_port)
    )
    logger.info("Syslog UDP 监听 %s:%s", settings.syslog_host, settings.syslog_udp_port)
    tcp = SyslogTcpServer()
    try:
        await tcp.start()
    except Exception as exc:
        logger.warning("Syslog TCP 启动失败: %s", exc)
    return udp_transport, tcp
