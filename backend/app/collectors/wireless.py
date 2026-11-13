"""无线数据（AP / 客户端 / 漫游）的 SNMP 采集。

数据来源（AIRESPACE-WIRELESS-MIB，Catalyst 9800 / AireOS 通用）：

  bsnAPTable              1.3.6.1.4.1.14179.2.2.1.1
    列  1  bsnAPName 前的索引即 AP Radio MAC（值是 MAC 字符串）
    列  3  bsnAPName
    列  4  bsnAPLocation
    列  6  bsnAPOperationStatus（1=正常）
    列 16  bsnAPModel
    列 17  bsnAPSerialNumber
    列 19  bsnApIpAddress

  bsnAPIfPhyChannelNumber 1.3.6.1.4.1.14179.2.2.2.1.4
    索引 = <AP Radio MAC 十进制>.<slot>（slot 0 = 2.4G / 1 = 5G）

  bsnMobileStationTable   1.3.6.1.4.1.14179.2.1.4.1
    列  1  MacAddress
    列  2  IpAddress
    列  3  UserName
    列  4  APMacAddr（关联 AP 的 Radio MAC，即 BSSID）
    列  5  APIfSlotId
    列  7  Ssid
    列  8  SignalStrength
    列 23  状态文本（Run / ...）

  bsnMobileStationStatsTable 1.3.6.1.4.1.14179.2.1.6.1
    列  1  RSSI（dBm）
    列  2  BytesReceived / 列 3  BytesSent
    列  5  PacketsReceived / 列 6  PacketsSent
    列 26  SNR（dB）

为什么走 SNMP 而不是 CLI：CLI 只有汇总信息，SNMP 能拿到信道 /
序列号 / RSSI / SNR / 收发流量，而且不需要 SSH 凭据。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.collectors.snmp import SnmpClient, SnmpTarget

logger = logging.getLogger("netops.wireless")

# ---------------------------------------------------------------- OID 常量
AP_TABLE = "1.3.6.1.4.1.14179.2.2.1.1"
AP_COL_MAC = "1"        # 索引即 Radio MAC，列 1 的值也是 MAC 字符串
AP_COL_NAME = "3"
AP_COL_LOCATION = "4"
AP_COL_STATUS = "6"
AP_COL_MODEL = "16"
AP_COL_SERIAL = "17"
AP_COL_IP = "19"

CHANNEL_OID = "1.3.6.1.4.1.14179.2.2.2.1.4"  # 索引 = <MAC十进制>.<slot>

CLIENT_TABLE = "1.3.6.1.4.1.14179.2.1.4.1"
C_COL_MAC = "1"
C_COL_IP = "2"
C_COL_USERNAME = "3"
C_COL_BSSID = "4"
C_COL_SLOT = "5"
C_COL_SSID = "7"
C_COL_SIGNAL = "8"
C_COL_STATE = "23"

CLIENT_STATS_TABLE = "1.3.6.1.4.1.14179.2.1.6.1"
S_COL_RSSI = "1"
S_COL_BYTES_IN = "2"
S_COL_BYTES_OUT = "3"
S_COL_PKTS_IN = "5"
S_COL_PKTS_OUT = "6"
S_COL_SNR = "26"


def _norm_mac(v: Any) -> str:
    """规范化 MAC 值 → 统一 xx:xx:xx:xx:xx:xx 小写。

    pysnmp 对 MacAddress 类型的解码不统一：有的返回十六进制字符串
    （"1cd1e0754920"），有的返回原始字节（b"\\x1c\\xd1\\xe0..."，
    显示为乱码）——统一成冒号格式，与 _mac_from_oid_index 一致
    （否则 bssid 关联 AP 的 mac_to_name / channels 匹配不上）。
    """
    s = str(v)
    # 6 字节的二进制 MAC：每个字符的 latin-1 编码即原始字节
    try:
        b = s.encode("latin-1")
        if len(b) == 6 and all(c < 128 or c > 159 for c in b):
            # 确实是二进制（含非可打印字符）才转
            if any(c < 32 or c > 126 for c in b):
                s = b.hex()
    except (UnicodeEncodeError, ValueError):
        pass
    # 十六进制字符串：去掉分隔符后按 2 位一组加冒号
    import re as _re

    cleaned = _re.sub(r"[^0-9a-fA-F]", "", s).lower()
    if len(cleaned) == 12:
        return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))
    return cleaned


def _mac_from_oid_index(parts: List[str]) -> str:
    """把 OID 里的十进制 MAC 索引转成 xx:xx:xx:xx:xx:xx。

    OID 索引形如 "2.167.40.154.230.115"（每字节一个十进制数）。
    统一冒号格式——与遥测合并器一致，否则同一客户端在
    SNMP / 遥测两条通道各建一行（key 对不上）。
    """
    try:
        raw = "".join(f"{int(p):02x}" for p in parts)
        return ":".join(raw[i : i + 2] for i in range(0, len(raw), 2))
    except (ValueError, TypeError):
        return ""


def _split_oid(oid: str, root: str) -> Tuple[str, List[str]]:
    """拆出 <列号> 与 <索引段>。"""
    rest = oid[len(root) + 1:]
    parts = rest.split(".")
    return parts[0], parts[1:]


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(str(v)))
    except (ValueError, TypeError):
        return default


def _to_int_signed(v: Any, default: int = 0) -> int:
    """RSSI / SNR 可能是负数或无符号编码（如 2147483596 表示 -52）。"""
    try:
        n = int(float(str(v)))
        # 32 位补码：超过 2^31-50 的值是负数的补码表示
        if n > 2147483000:
            n -= 2**32
        return n
    except (ValueError, TypeError):
        return default


async def collect_wireless(
    target: SnmpTarget,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """SNMP 采集无线 AP 与客户端明细，返回 (aps, clients)。"""
    client = SnmpClient(target)

    # ---- AP 表：一次 walk 全表（15 AP × 28 列 ≈ 420 项，量很小）
    ap_res = await client.walk(AP_TABLE)
    if not ap_res.ok or not ap_res.values:
        return [], []
    ap_rows: Dict[str, Dict[str, str]] = {}  # radio_mac -> {列: 值}
    for oid, v in ap_res.values.items():
        col, idx = _split_oid(oid, AP_TABLE)
        mac = _mac_from_oid_index(idx)
        if len(mac) != 17:  # xx:xx:xx:xx:xx:xx
            continue
        ap_rows.setdefault(mac, {})[col] = str(v)

    # ---- 信道表（15 AP × 2 slot = 30 项）
    # 注意 CHANNEL_OID 是叶子节点（…2.2.2.1.4），rest 直接是 <MAC 6 段>.<slot>
    ch_res = await client.walk(CHANNEL_OID)
    channels: Dict[Tuple[str, int], int] = {}  # (radio_mac, slot) -> 信道
    if ch_res.ok and ch_res.values:
        for oid, v in ch_res.values.items():
            rest = oid[len(CHANNEL_OID) + 1:]
            parts = rest.split(".")
            if len(parts) != 7:
                continue
            mac = _mac_from_oid_index(parts[:6])
            slot = _to_int(parts[6], -1)
            if mac and slot >= 0:
                channels[(mac, slot)] = _to_int(v)

    # ---- 组装 AP 列表
    aps: List[Dict[str, Any]] = []
    # radio_mac -> 名称，客户端表用它找 AP
    mac_to_name: Dict[str, str] = {}
    for mac, cols in ap_rows.items():
        name = cols.get(AP_COL_NAME, "")
        if not name:
            continue
        mac_to_name[mac] = name
        ch_parts = []
        for slot in sorted({s for (m, s) in channels if m == mac}):
            ch = channels.get((mac, slot))
            if ch:
                ch_parts.append(f"slot{slot}: {ch}")
        status = _to_int(cols.get(AP_COL_STATUS, "0"))
        aps.append(
            {
                "name": name,
                "model": cols.get(AP_COL_MODEL, ""),
                "slots": _to_int(cols.get("2", "0")),
                "radio_mac": mac,
                "location": cols.get(AP_COL_LOCATION, ""),
                "serial_number": cols.get(AP_COL_SERIAL, ""),
                "ip": cols.get(AP_COL_IP, ""),
                "status": status,
                "state": {1: "正常", 2: "下载中", 3: "未关联"}.get(status, "未知"),
                "channels": " / ".join(ch_parts),
            }
        )
    aps.sort(key=lambda a: a["name"])

    # ---- 客户端主表（只 walk 需要的列，避免取全表）
    c_res = await client.walk(CLIENT_TABLE)
    if not c_res.ok or not c_res.values:
        return aps, []
    c_rows: Dict[str, Dict[str, str]] = {}  # mac -> {列: 值}
    for oid, v in c_res.values.items():
        col, idx = _split_oid(oid, CLIENT_TABLE)
        mac = _mac_from_oid_index(idx)
        if len(mac) != 17:  # xx:xx:xx:xx:xx:xx
            continue
        c_rows.setdefault(mac, {})[col] = str(v)

    # ---- 客户端统计表（125 客户端 × 7 列 ≈ 875 项）
    stats: Dict[str, Dict[str, str]] = {}
    s_res = await client.walk(CLIENT_STATS_TABLE)
    if s_res.ok and s_res.values:
        for oid, v in s_res.values.items():
            col, idx = _split_oid(oid, CLIENT_STATS_TABLE)
            mac = _mac_from_oid_index(idx)
            if len(mac) != 17:  # xx:xx:xx:xx:xx:xx
                continue
            stats.setdefault(mac, {})[col] = str(v)

    # ---- 组装客户端列表
    clients: List[Dict[str, Any]] = []
    for mac, cols in c_rows.items():
        bssid = _norm_mac(cols.get(C_COL_BSSID, ""))
        slot = _to_int(cols.get(C_COL_SLOT, "0"))
        channel = channels.get((bssid, slot), 0)
        st = stats.get(mac, {})
        clients.append(
            {
                "mac": mac,
                "ip": cols.get(C_COL_IP, ""),
                "username": cols.get(C_COL_USERNAME, ""),
                "bssid": bssid,
                "ap_name": mac_to_name.get(bssid, ""),
                "ssid": cols.get(C_COL_SSID, ""),
                "slot": slot,
                "channel": channel,
                "signal_strength": _to_int_signed(cols.get(C_COL_SIGNAL, "0")),
                "rssi": _to_int_signed(st.get(S_COL_RSSI, "0")),
                "snr": _to_int_signed(st.get(S_COL_SNR, "0")),
                "bytes_in": _to_int(st.get(S_COL_BYTES_IN, "0")),
                "bytes_out": _to_int(st.get(S_COL_BYTES_OUT, "0")),
                "packets_in": _to_int(st.get(S_COL_PKTS_IN, "0")),
                "packets_out": _to_int(st.get(S_COL_PKTS_OUT, "0")),
                "state": cols.get(C_COL_STATE, ""),
            }
        )
    clients.sort(key=lambda c: c["ap_name"] or c["mac"])
    return aps, clients


def _plain_mac(s: str) -> str:
    """去掉 MAC 里的分隔符（. 或 :），统一成 12 位 hex，便于新旧格式对比。"""
    import re as _re

    return _re.sub(r"[^0-9a-fA-F]", "", str(s or "")).lower()


def detect_roaming(
    device_id: int,
    new_clients: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """对比数据库里的上一批客户端，检测 AP 变化（漫游）。

    返回漫游事件列表并落库。注意新旧记录的 MAC / BSSID 格式可能不同
    （带点 "02a7.289a.e673" 与不带点 "02a7289ae673"），统一规范化后对比。
    """
    from app.db.session import SessionLocal
    from app.models.models import WirelessClient, WirelessRoaming

    events: List[Dict[str, Any]] = []
    db = SessionLocal()
    try:
        old_rows = (
            db.query(WirelessClient)
            .filter(WirelessClient.device_id == device_id)
            .all()
        )
        old = {_plain_mac(r.mac): r for r in old_rows if _plain_mac(r.mac)}
        new_by_mac = {_plain_mac(c["mac"]): c for c in new_clients}

        now = datetime.now()
        for mac, nc in new_by_mac.items():
            oc = old.get(mac)
            if oc is None:
                continue
            old_bssid = _plain_mac(oc.bssid)
            new_bssid = _plain_mac(nc.get("bssid", ""))
            # BSSID 变化即漫游（AP 变了；同名 AP 不同 radio 也算跨频段漫游）
            if old_bssid and new_bssid and new_bssid != old_bssid:
                events.append(
                    {
                        "device_id": device_id,
                        "mac": nc["mac"],
                        "from_ap": oc.ap_name,
                        "to_ap": nc["ap_name"],
                        "from_bssid": oc.bssid,
                        "to_bssid": nc["bssid"],
                        "from_channel": oc.channel or 0,
                        "to_channel": nc["channel"] or 0,
                        "rssi": nc["rssi"],
                        "snr": nc["snr"],
                        "ts": now,
                    }
                )
        if events:
            db.add_all([WirelessRoaming(**e) for e in events])
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("漫游检测失败: %s", exc)
    finally:
        db.close()
    return events


def save_wireless(
    device_id: int,
    aps: List[Dict[str, Any]],
    clients: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """落库（SNMP 快照合并），并检测漫游。

    采集数据展示优先级：遥测 > SNMP > SSH——SNMP 轮询（分钟级）
    **不能覆盖**遥测推送（5-30 秒级）已写入的行：
    - 按 key（AP name / 客户端 mac）upsert
    - 已有 telemetry 来源的行跳过（保留遥测的字段与新鲜度），
      只给 SNMP 独有的字段（收发流量等）补充更新
    - SNMP 独有的行正常替换（删除本轮没出现的 SNMP 行）
    """
    from app.db.session import SessionLocal
    from app.models.models import WirelessAP, WirelessClient

    # 先做漫游检测（要用旧数据），再合并
    events = detect_roaming(device_id, clients)

    db = SessionLocal()
    try:
        now = datetime.now()

        # ---- AP 表 ----
        old_aps = {
            a.name: a
            for a in db.query(WirelessAP)
            .filter(WirelessAP.device_id == device_id)
            .all()
        }
        seen = set()
        for a in aps:
            key = a.get("name") or ""
            if not key:
                continue
            seen.add(key)
            old = old_aps.get(key)
            if old is not None and old.source == "telemetry":
                # 遥测优先：只补充 SNMP 独有字段，不覆盖遥测数据
                continue
            if old is not None:
                for k, v in a.items():
                    if hasattr(old, k) and v is not None:
                        setattr(old, k, v)
                old.ts = now
                old.source = "snmp"
            else:
                db.add(WirelessAP(device_id=device_id, ts=now, source="snmp", **a))
        # 删除本轮消失的 SNMP 来源 AP（遥测来源的不动）
        gone = [
            k
            for k, a in old_aps.items()
            if k not in seen and a.source == "snmp"
        ]
        for k in gone:
            db.delete(old_aps[k])

        # ---- 客户端表 ----
        old_clients = {
            c.mac: c
            for c in db.query(WirelessClient)
            .filter(WirelessClient.device_id == device_id)
            .all()
        }
        seen_c = set()
        for c in clients:
            key = c.get("mac") or ""
            if not key:
                continue
            seen_c.add(key)
            old = old_clients.get(key)
            if old is not None and old.source == "telemetry":
                # 遥测优先：只补充收发流量等 SNMP 独有字段
                for f in ("bytes_in", "bytes_out", "packets_in", "packets_out"):
                    if c.get(f) is not None:
                        setattr(old, f, c[f])
                continue
            if old is not None:
                for k, v in c.items():
                    if hasattr(old, k) and v is not None:
                        setattr(old, k, v)
                old.ts = now
                old.source = "snmp"
            else:
                db.add(
                    WirelessClient(device_id=device_id, ts=now, source="snmp", **c)
                )
        gone_c = [
            k
            for k, c in old_clients.items()
            if k not in seen_c and c.source == "snmp"
        ]
        for k in gone_c:
            db.delete(old_clients[k])

        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("保存无线数据失败: %s", exc)
    finally:
        db.close()
    return events


def target_from_device(device) -> SnmpTarget:
    """从设备模型构造 SNMP 目标。"""
    return SnmpTarget(
        host=device.ip,
        port=device.snmp_port or 161,
        community=device.snmp_community or "public",
        version=(device.snmp_version or "2c"),
        timeout=8.0,
        retries=0,
    )
