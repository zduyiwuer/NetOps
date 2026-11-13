"""交换机 CAM/FDB 表（接口学到的 MAC 地址）的 SNMP 采集。

数据来源（BRIDGE-MIB，几乎所有交换机都支持）：
  dot1dTpFdbTable     1.3.6.1.2.1.17.4.3.1.2   索引 = MAC，值 = bridge port
  dot1dBasePortIfIndex 1.3.6.1.2.1.17.1.4.1.2 索引 = bridge port，值 = ifIndex

链路：MAC → bridge port → 接口号。老 dot1dTpFdbTable 不带 VLAN
（Q-BRIDGE 的 dot1qTpFdbTable 才有，但很多设备的该表项为空——
实测 Catalyst 2960 也是旧表有数据），够用于「端口下挂了哪些设备」。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Tuple

from app.collectors.snmp import SnmpClient, SnmpTarget

logger = logging.getLogger("netops.cam")

FDB_PORT_OID = "1.3.6.1.2.1.17.4.3.1.2"    # dot1dTpFdbPort：索引=MAC
PORT_IFINDEX_OID = "1.3.6.1.2.1.17.1.4.1.2"  # dot1dBasePortIfIndex


def _mac_from_oid_index(parts: List[str]) -> str:
    try:
        return ":".join(f"{int(p):02x}" for p in parts)
    except (ValueError, TypeError):
        return ""


async def collect_cam(target: SnmpTarget) -> Dict[int, List[str]]:
    """采集 CAM 表，返回 {if_index: [mac, ...]}。"""
    client = SnmpClient(target)

    # bridge port -> ifIndex 映射
    res = await client.walk(PORT_IFINDEX_OID)
    if not res.ok or not res.values:
        return {}
    port_if: Dict[str, int] = {}
    for oid, v in res.values.items():
        rest = oid[len(PORT_IFINDEX_OID) + 1:]
        try:
            port_if[rest] = int(v)
        except (ValueError, TypeError):
            continue

    # MAC -> bridge port
    res2 = await client.walk(FDB_PORT_OID)
    if not res2.ok or not res2.values:
        return {}
    out: Dict[int, List[str]] = {}
    for oid, v in res2.values.items():
        rest = oid[len(FDB_PORT_OID) + 1:]
        mac = _mac_from_oid_index(rest.split("."))
        if not mac:
            continue
        ifx = port_if.get(str(v))
        if ifx is None:
            continue
        out.setdefault(ifx, []).append(mac)
    for macs in out.values():
        macs.sort()
    return out


def save_cam(device_id: int, cam: Dict[int, List[str]]) -> int:
    """落库（整体替换快照），返回 MAC 总数。"""
    from app.db.session import SessionLocal
    from app.models.models import InterfaceMac

    total = sum(len(v) for v in cam.values())
    db = SessionLocal()
    try:
        db.query(InterfaceMac).filter(
            InterfaceMac.device_id == device_id
        ).delete(synchronize_session=False)
        now = datetime.now()
        for ifx, macs in cam.items():
            for mac in macs[:200]:  # 单接口保护上限
                db.add(
                    InterfaceMac(
                        device_id=device_id, if_index=ifx, mac=mac, ts=now
                    )
                )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("保存 CAM 表失败: %s", exc)
    finally:
        db.close()
    return total
