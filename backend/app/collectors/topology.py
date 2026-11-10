"""拓扑采集：LLDP / CDP 邻居信息。

两种来源：
- **SNMP**：LLDP-MIB（lldpRemTable）与 CISCO-CDP-MIB（cdpCacheAddressTable）
- **SSH**：各厂商的 LLDP 邻居显示命令
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.collectors.snmp import SnmpClient, SnmpError, target_from_device

logger = logging.getLogger("netops.topology")

# LLDP-MIB
OID_LLDP_REM_TABLE = "1.0.8802.1.1.2.1.4.1.1"
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.1"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.2"
OID_LLDP_REM_PORT_DESC = "1.0.8802.1.1.2.1.4.1.1.3"
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.5"
OID_LLDP_REM_SYS_DESC = "1.0.8802.1.1.2.1.4.1.1.6"
OID_LLDP_REM_SYS_CAP = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_MGMT_ADDR = "1.0.8802.1.1.2.1.4.1.1.8"

# CISCO-CDP-MIB
OID_CDP_CACHE_ADDRESS = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"
OID_CDP_CACHE_ADDRESS_INDEX = "1.3.6.1.4.1.9.9.23.1.2.1.1.1"
OID_CDP_CACHE_IF_INDEX = "1.3.6.1.4.1.9.9.23.1.2.1.1.2"
OID_CDP_CACHE_DEVICE_ID = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"
OID_CDP_CACHE_PORT = "1.3.6.1.4.1.9.9.23.1.2.1.1.7"
OID_CDP_CACHE_PLATFORM = "1.3.6.1.4.1.9.9.23.1.2.1.1.8"

# 各厂商 SSH 邻居命令
LLDP_SSH_COMMANDS = {
    "huawei": ["display lldp neighbor brief"],
    "h3c": ["display lldp neighbor-information list"],
    "cisco": ["show lldp neighbors detail"],
    "cisco_asa": ["show lldp neighbor"],
    "cisco_wlc": ["show lldp neighbors"],
    "fortinet": ["get system lldp neighbor-summary"],
    "routeros": ["/ip/neighbor/print"],
    "generic": ["show lldp neighbors"],
}


@dataclass
class Neighbor:
    """一条邻居关系。"""

    local_device_id: int
    local_device_name: str = ""
    local_interface: str = ""
    remote_device: str = ""
    remote_interface: str = ""
    remote_ip: str = ""
    platform: str = ""
    protocol: str = "lldp"
    collected_at: Optional[datetime] = None


def _index_of(oid: str, root: str) -> Optional[str]:
    if not oid.startswith(root + "."):
        return None
    suffix = oid[len(root) + 1 :]
    return suffix if "." not in suffix else None


async def collect_lldp_snmp(device, timeout: float = 6.0) -> List[Neighbor]:
    """通过 SNMP 读取 LLDP / CDP 邻居（带超时保护）。"""
    neighbors: List[Neighbor] = []
    client = SnmpClient(target_from_device(device))
    now = datetime.now()

    try:
        result = await asyncio.wait_for(client.bulk(OID_LLDP_REM_TABLE), timeout=timeout)
    except asyncio.TimeoutError:
        logger.debug("LLDP SNMP 采集超时 %s", device.name)
        result = None
    except SnmpError as exc:
        logger.debug("LLDP 采集失败 %s: %s", device.name, exc)
        result = None

    if result:
        rows: Dict[str, Dict[str, Any]] = {}
        for oid, value in result.values.items():
            if not oid.startswith(OID_LLDP_REM_TABLE + "."):
                continue
            suffix = oid[len(OID_LLDP_REM_TABLE) + 1 :]
            parts = suffix.split(".")
            if len(parts) != 2:
                continue
            idx, col = parts
            rows.setdefault(idx, {})[col] = value
        for idx, cols in rows.items():
            chassis = cols.get("1", "")
            port_id = cols.get("2", "")
            port_desc = cols.get("3", "")
            sys_name = cols.get("5", "")
            sys_desc = cols.get("6", "")
            mgmt = cols.get("8", "")
            remote_name = sys_name or chassis
            if not remote_name:
                continue
            neighbors.append(
                Neighbor(
                    local_device_id=device.id,
                    local_device_name=device.name,
                    local_interface=port_desc or port_id,
                    remote_device=str(remote_name),
                    remote_interface=str(port_id),
                    remote_ip=str(mgmt),
                    platform=str(sys_desc)[:120],
                    protocol="lldp",
                    collected_at=now,
                )
            )

    # CDP
    try:
        result = await asyncio.wait_for(client.bulk(OID_CDP_CACHE_ADDRESS), timeout=timeout)
    except asyncio.TimeoutError:
        result = None
    except SnmpError as exc:
        logger.debug("CDP 采集失败 %s: %s", device.name, exc)
        result = None

    if result:
        rows = {}
        for oid, value in result.values.items():
            if not oid.startswith(OID_CDP_CACHE_ADDRESS + "."):
                continue
            suffix = oid[len(OID_CDP_CACHE_ADDRESS) + 1 :]
            parts = suffix.split(".")
            if len(parts) != 2:
                continue
            idx, col = parts
            rows.setdefault(idx, {})[col] = value
        for idx, cols in rows.items():
            remote_name = cols.get("6", "")
            if not remote_name:
                continue
            neighbors.append(
                Neighbor(
                    local_device_id=device.id,
                    local_device_name=device.name,
                    local_interface=str(cols.get("2", "")),
                    remote_device=str(remote_name),
                    remote_interface=str(cols.get("7", "")),
                    remote_ip=str(cols.get("4", "")),
                    platform=str(cols.get("8", ""))[:120],
                    protocol="cdp",
                    collected_at=now,
                )
            )

    return neighbors


_HUAWEI_LLDP_RE = re.compile(
    r"^\s*(?P<local>\S+)\s+(?P<ttl>\d+)\s+(?P<remote>\S+)\s+(?P<port>\S+)\s*$"
)
_CISCO_LLDP_RE = re.compile(
    r"Device ID:\s*(?P<remote>\S+).*?Interface:\s*(?P<local>[^,]+)", re.DOTALL
)


def parse_lldp_text(text: str, device) -> List[Neighbor]:
    """解析 SSH 输出的 LLDP 邻居表。"""
    neighbors: List[Neighbor] = []
    if not text:
        return neighbors
    now = datetime.now()
    lines = text.splitlines()

    for line in lines:
        line = line.strip()
        if not line or line.startswith(("Total", "Capability", "Port", "----")):
            continue
        # 华为 / H3C：Gi0/1  120  SW01  GE0/1
        m = _HUAWEI_LLDP_RE.match(line)
        if m and m.group("remote"):
            neighbors.append(
                Neighbor(
                    local_device_id=device.id,
                    local_device_name=device.name,
                    local_interface=m.group("local"),
                    remote_device=m.group("remote"),
                    remote_interface=m.group("port"),
                    protocol="lldp",
                    collected_at=now,
                )
            )
            continue
        # Cisco：多行块
        m = _CISCO_LLDP_RE.search(line)
        if m and m.group("remote"):
            neighbors.append(
                Neighbor(
                    local_device_id=device.id,
                    local_device_name=device.name,
                    local_interface=m.group("local").strip(),
                    remote_device=m.group("remote").strip(),
                    protocol="lldp",
                    collected_at=now,
                )
            )
    return neighbors


async def collect_lldp_ssh(device, timeout: float = 8.0) -> List[Neighbor]:
    """通过 SSH 采集 LLDP 邻居（带超时保护，避免拖慢整体采集）。"""
    if not device.ssh_username or not device.ssh_password:
        return []
    from app.collectors.ssh import SshClient

    ssh = SshClient(
        host=device.ip,
        username=device.ssh_username,
        password=device.ssh_password,
        vendor=device.vendor,
        port=device.ssh_port,
        timeout=min(device.ssh_timeout or 20, int(timeout)),
        enable_password=device.ssh_enable_password,
    )
    commands = LLDP_SSH_COMMANDS.get(device.vendor) or LLDP_SSH_COMMANDS["generic"]
    neighbors: List[Neighbor] = []
    try:
        for cmd in commands:
            text = await asyncio.wait_for(ssh.run_commands([cmd]), timeout=timeout)
            for _c, out in text.items():
                neighbors.extend(parse_lldp_text(out, device))
            break  # 只执行第一条命令
    except asyncio.TimeoutError:
        logger.debug("SSH LLDP 采集超时 %s", device.name)
    except Exception as exc:
        logger.debug("SSH LLDP 采集失败 %s: %s", device.name, exc)
    return neighbors


async def collect_neighbors(device) -> List[Neighbor]:
    """综合 SNMP + SSH 采集邻居（去重）。"""
    neighbors = await collect_lldp_snmp(device)
    if not neighbors:
        neighbors = await collect_lldp_ssh(device)
    # 去重：同 本地接口 + 远端设备 只保留一条
    seen = set()
    unique: List[Neighbor] = []
    for n in neighbors:
        key = (n.local_interface, n.remote_device, n.remote_interface)
        if key in seen:
            continue
        seen.add(key)
        unique.append(n)
    return unique
