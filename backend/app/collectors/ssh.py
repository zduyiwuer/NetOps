"""SSH 采集：Netmiko 封装（配置备份、日志、CPU/内存补充采集）。"""
from __future__ import annotations

import asyncio
import re
from typing import Dict, List, Optional, Tuple

from app.collectors.vendors import VendorProfile, resolve_vendor


class SshError(Exception):
    """SSH 操作失败。"""


class SshClient:
    """Netmiko 异步封装（Netmiko 本身是同步的，放线程池执行）。"""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        vendor: str = "generic",
        port: int = 22,
        timeout: int = 20,
        enable_password: str = "",
        secret: str = "",
    ):
        self.host = host
        self.username = username
        self.password = password
        self.vendor = resolve_vendor(vendor)
        self.port = port or 22
        self.timeout = timeout or 20
        self.enable_password = enable_password or secret

    # ------------------------------------------------------------ 底层
    def _connect(self, device_type: Optional[str] = None):
        from netmiko import ConnectHandler

        params: Dict = {
            "device_type": device_type or self.vendor.netmiko_device_type,
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "port": self.port,
            "timeout": self.timeout,
            "fast_cli": False,
        }
        if self.enable_password:
            params["secret"] = self.enable_password
        try:
            return ConnectHandler(**params)
        except Exception as exc:
            raise SshError(f"SSH 连接失败: {exc}") from exc

    async def _run(self, command: str, device_type: Optional[str] = None) -> str:
        def _exec() -> str:
            conn = None
            try:
                conn = self._connect(device_type)
                return conn.send_command(command, read_timeout=self.timeout + 20)
            finally:
                if conn is not None:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass

        return await asyncio.to_thread(_exec)

    async def run_commands(self, commands: List[str]) -> Dict[str, str]:
        """一次连接执行多条命令，显著降低开销。"""
        if not commands:
            return {}

        def _exec() -> Dict[str, str]:
            conn = None
            out: Dict[str, str] = {}
            try:
                conn = self._connect()
                for cmd in commands:
                    try:
                        out[cmd] = conn.send_command(cmd, read_timeout=self.timeout + 20)
                    except Exception as exc:
                        out[cmd] = f"[ERROR] {exc}"
                return out
            finally:
                if conn is not None:
                    try:
                        conn.disconnect()
                    except Exception:
                        pass

        return await asyncio.to_thread(_exec)

    # ------------------------------------------------------------ 业务
    async def backup_config(self) -> str:
        """抓取运行配置。

        多数厂商首条命令即可取到完整配置；
        飞塔需要多条命令组合（诊断保存命令不输出配置内容，需另取）。
        """
        results = await self.run_commands(self.vendor.backup_cmd or ["show running-config"])
        parts: List[str] = []
        for cmd, text in results.items():
            if not text or text.startswith("[ERROR]"):
                continue
            stripped = text.strip()
            if not stripped:
                continue
            # 飞塔诊断保存命令只有状态回显，不含配置内容
            if stripped.startswith("Diagnostics") or "config-file saved" in stripped.lower():
                continue
            parts.append(f"# ===== {cmd} =====\n{stripped}")
        return "\n\n".join(parts)

    async def fetch_logs(self, limit: int = 200) -> List[Tuple[str, str]]:
        """抓取设备日志，返回 [(级别, 文本)]。"""
        results = await self.run_commands(self.vendor.log_cmd or ["show logging"])
        lines: List[Tuple[str, str]] = []
        for _cmd, text in results.items():
            for raw in (text or "").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                level = parse_syslog_level(raw)
                lines.append((level, raw))
        return lines[-limit:]

    async def fetch_cpu(self) -> Optional[float]:
        """SSH 采集 CPU 使用率（%）。"""
        results = await self.run_commands(self.vendor.cpu_ssh_cmd)
        for _cmd, text in results.items():
            value = parse_cpu_usage(text, self.vendor.key)
            if value is not None:
                return value
        return None

    async def fetch_memory(self) -> Tuple[Optional[float], Optional[float]]:
        """SSH 采集内存，返回 (使用率%, 总内存MB)。"""
        results = await self.run_commands(self.vendor.mem_ssh_cmd)
        for _cmd, text in results.items():
            pct, total = parse_memory_usage(text, self.vendor.key)
            if pct is not None or total is not None:
                return pct, total
        return None, None

    async def fetch_version(self) -> str:
        results = await self.run_commands(self.vendor.version_cmd)
        for _cmd, text in results.items():
            if text and not text.startswith("[ERROR]"):
                return text.strip()
        return ""

    async def fetch_extra_metrics(self) -> Dict[str, float]:
        """采集厂商特有指标（连接数 / AP 数 / 会话数等）。"""
        out: Dict[str, float] = {}
        cmds = self.vendor.extra_ssh_cmds
        if not cmds:
            return out
        for metric, command_list in cmds.items():
            if not command_list:
                continue
            try:
                results = await self.run_commands(list(command_list))
            except Exception:
                continue
            for _cmd, text in results.items():
                if not text or text.startswith("[ERROR]"):
                    continue
                value = parse_extra_metric(text, metric)
                if value is not None:
                    out[metric] = value
                    break
        return out


# ---------------------------------------------------------------- 文本解析
_SYSLOG_LEVELS = {
    "EMERG": "emerg",
    "ALERT": "alert",
    "CRIT": "crit",
    "CRITICAL": "crit",
    "ERR": "err",
    "ERROR": "err",
    "WARNING": "warning",
    "WARN": "warning",
    "NOTICE": "notice",
    "INFO": "info",
    "INFORMATION": "info",
    "DEBUG": "debug",
}


def parse_extra_metric(text: str, metric: str) -> Optional[float]:
    """从厂商特有命令输出中解析数值。

    针对三类新增设备：
    - Cisco ASA：`show connection count` → "123 connections"
    - Cisco WLC：`show ap summary` / `show client summary` → 数量
    - Fortinet：`get system performance status` → "Current sessions: 123"
    """
    if not text:
        return None
    patterns: Dict[str, List[str]] = {
        "conn_count": [
            r"Current\s+number\s+of\s+connections?\s*[:=]\s*(\d+)",
            r"Current\s+connections?\s*[:=]\s*(\d+)",
            r"(\d+)\s+connections?\b",
            r"Total\s+\S+\s+Active\s+Connection.*?(\d+)",
        ],
        "conn_peak": [
            r"[Pp]eak\s*[:=]\s*(\d+)",
            r"(\d+)\s+peak",
        ],
        "csc_table_size": [
            r"CSC\s+table\s+size\s*[:=]\s*(\d+)",
            r"(\d+)\s+entries\s+in\s+the\s+connection",
        ],
        "ap_count": [
            r"Number\s+of\s+APs\s*[:=]\s*(\d+)",
            r"Total\s+APs\s*[:=]\s*(\d+)",
            r"^\s*(\d+)\s+APs?\s+registered",
            r"AP\s+Count\s*[:=]\s*(\d+)",
        ],
        "client_count": [
            r"Number\s+of\s+clients\s*[:=]\s*(\d+)",
            r"Total\s+Clients\s*[:=]\s*(\d+)",
            r"Client\s+Count\s*[:=]\s*(\d+)",
            r"^\s*(\d+)\s+clients?\s+associated",
        ],
        "session_count": [
            r"Current\s+sessions?\s*[:=]\s*(\d+)",
            r"Sessions?\s*[:=]\s*(\d+)",
            r"(\d+)\s+sessions?\s+in\s+use",
        ],
        "user_count": [
            r"Users?\s*[:=]\s*(\d+)",
            r"Number\s+of\s+users\s*[:=]\s*(\d+)",
            r"Logged\s+in\s+users?\s*[:=]\s*(\d+)",
        ],
        "vpn_tunnel_count": [
            r"VPN\s+tunnels?\s*[:=]\s*(\d+)",
            r"(\d+)\s+VPN\s+tunnels?",
        ],
    }
    for pattern in patterns.get(metric, []):
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            try:
                return float(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


def parse_syslog_level(line: str) -> str:
    """从一行日志中解析级别。"""
    upper = line.upper()
    # RFC3164 优先级 <PRI> 已由外层解析，这里匹配关键字
    for key in ("EMERG", "ALERT", "CRIT", "ERROR", "WARNING", "NOTICE", "INFO", "DEBUG"):
        if re.search(rf"\b{key}\b", upper):
            return _SYSLOG_LEVELS[key]
    if re.search(r"\bWARN\b", upper):
        return "warning"
    if re.search(r"\bERR\b", upper):
        return "err"
    return "info"


def parse_cpu_usage(text: str, vendor: str) -> Optional[float]:
    """从命令输出中解析 CPU 使用率。"""
    if not text:
        return None
    patterns = [
        r"CPU utilization for five seconds:\s*(\d+)%",
        r"CPU utilization:\s*(\d+)%",
        r"CPU[^\n:]*:\s*(\d+)%",
        r"CPU usage[^\n:]*:\s*(\d+)%",
        r"CPU[^\n]*?(\d{1,3})%\s*(?:used|/ 1 sec)",
        r"cpu-load\s*[:=]\s*(\d+)",
        r"load\s*[:=]\s*(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def parse_memory_usage(text: str, vendor: str) -> Tuple[Optional[float], Optional[float]]:
    """从命令输出中解析内存使用率与总内存(MB)。"""
    if not text:
        return None, None
    pct: Optional[float] = None
    total_mb: Optional[float] = None

    # 百分比：Used(%): 42  /  Memory Usage : 42%
    m = re.search(r"(?:used|usage)\s*\(%\)\s*[:=]?\s*(\d+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"memory[^\n:]*[:=]\s*(\d+)\s*%", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d+)\s*%\s*(?:of\s*)?memory", text, re.IGNORECASE)
    if m:
        pct = float(m.group(1))

    # 总内存
    m = re.search(r"total\s*memory\s*[:=]\s*(\d+)\s*(K|KB|M|MB|G|GB)?", text, re.IGNORECASE)
    if not m:
        m = re.search(r"memory[^:\n]*:\s*(\d+)\s*(K|KB|M|MB|G|GB)?\s*(?:total|\(total)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d+)\s*(K|KB|M|MB|G|GB)\s*(?:total\s*)?memory", text, re.IGNORECASE)
    if m:
        try:
            value = float(m.group(1))
            unit = (m.group(2) or "KB").upper()
            factor = {"K": 1 / 1024, "KB": 1 / 1024, "M": 1, "MB": 1, "G": 1024, "GB": 1024}.get(unit, 1 / 1024)
            total_mb = value * factor
        except ValueError:
            pass
    return pct, total_mb
