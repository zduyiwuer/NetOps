"""采集引擎：把 SNMP/SSH 数据转成平台内部指标模型。

接口速率通过相邻两次采样的 Octet 差分计算，因此引擎维护一个
进程内的速率状态缓存（按 device_id + ifIndex 维度）。
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.collectors.snmp import SnmpClient, SnmpError, target_from_device
from app.collectors.ssh import SshClient, SshError
from app.collectors.vendors import (
    OID_IF_ADMIN_STATUS,
    OID_IF_ALIAS,
    OID_IF_DESCR,
    OID_IF_ENTRY,
    OID_IF_HC_IN_OCTETS,
    OID_IF_HC_OUT_OCTETS,
    OID_IF_HIGH_SPEED,
    OID_IF_INDEX,
    OID_IF_IN_ERRORS,
    OID_IF_IN_OCTETS,
    OID_IF_NAME,
    OID_IF_OPER_STATUS,
    OID_IF_OUT_ERRORS,
    OID_IF_OUT_OCTETS,
    OID_IF_SPEED,
    OID_IF_TYPE,
    OID_IF_X_ENTRY,
    OID_SYS_UPTIME,
    VendorProfile,
    resolve_vendor,
)

@dataclass
class InterfaceSample:
    if_index: int
    name: str = ""
    descr: str = ""
    alias: str = ""
    if_type: int = 0
    speed_bps: int = 0
    admin_status: int = 0
    oper_status: int = 0
    in_bps: float = 0.0
    out_bps: float = 0.0
    in_octets: int = 0
    out_octets: int = 0
    in_errors: int = 0
    out_errors: int = 0
    utilization: float = 0.0


@dataclass
class SystemSample:
    cpu_usage: Optional[float] = None
    mem_usage: Optional[float] = None
    mem_total_mb: Optional[float] = None
    mem_used_mb: Optional[float] = None
    temperature: Optional[float] = None
    uptime_sec: Optional[int] = None
    response_ms: Optional[float] = None
    reachable: bool = True
    error: str = ""
    source: str = "snmp"  # snmp/ssh/mock
    # 厂商特有指标：连接数 / AP 数 / 会话数等
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class DeviceSample:
    device_id: int
    system: SystemSample = field(default_factory=SystemSample)
    interfaces: List[InterfaceSample] = field(default_factory=list)
    collected_at: datetime = field(default_factory=datetime.now)
    raw_syslog: List[Tuple[str, str]] = field(default_factory=list)


class RateTracker:
    """接口速率差分状态。key = (device_id, if_index)。"""

    def __init__(self, ttl_seconds: int = 900):
        self.ttl = ttl_seconds
        self._state: Dict[Tuple[int, int], Tuple[float, int, int]] = {}

    def compute(
        self,
        device_id: int,
        if_index: int,
        in_octets: int,
        out_octets: int,
        ts: float,
    ) -> Tuple[float, float]:
        key = (device_id, if_index)
        prev = self._state.get(key)
        self._state[key] = (ts, in_octets, out_octets)
        if not prev:
            return 0.0, 0.0
        prev_ts, prev_in, prev_out = prev
        dt = ts - prev_ts
        if dt <= 0:
            return 0.0, 0.0
        delta_in = max(0, in_octets - prev_in)
        delta_out = max(0, out_octets - prev_out)
        in_bps = delta_in * 8 / dt
        out_bps = delta_out * 8 / dt
        # 设备重启（计数器归零）时重新计数
        if in_octets < prev_in:
            in_bps = 0.0
        if out_octets < prev_out:
            out_bps = 0.0
        return in_bps, out_bps

    def gc(self, now_ts: Optional[float] = None) -> None:
        now_ts = now_ts or time.time()
        stale = [k for k, (ts, _, _) in self._state.items() if now_ts - ts > self.ttl]
        for k in stale:
            self._state.pop(k, None)


# 全局速率追踪器
rate_tracker = RateTracker()

# mock 模式计数器状态：key = (device_id, if_index) → [ts, in_octets, out_octets]
_mock_counter_state: Dict[Tuple[int, int], List[Any]] = {}


class Collector:
    """设备采集器。"""

    def __init__(self, device, mock_enabled: bool = False):
        self.device = device
        self.vendor: VendorProfile = resolve_vendor(device.vendor)
        self.mock = bool(device.mock_mode) or mock_enabled

    # ---------------------------------------------------------- 入口
    async def collect(self, collect_interface: bool = True, collect_system: bool = True) -> DeviceSample:
        if self.mock:
            return self._collect_mock(collect_interface, collect_system)
        try:
            return await self._collect_snmp(collect_interface, collect_system)
        except Exception as exc:
            sample = DeviceSample(device_id=self.device.id)
            sample.system.reachable = False
            sample.system.error = str(exc)
            # SNMP 失败时尝试 SSH 兜底（部分设备只开 SSH）
            if self.device.ssh_username and self.device.ssh_password:
                try:
                    ssh_sample = await self._collect_ssh_fallback()
                    sample.interfaces = ssh_sample.interfaces
                    sample.system = ssh_sample.system
                except Exception:
                    pass
            return sample

    # ---------------------------------------------------------- SNMP
    async def _collect_snmp(self, collect_interface: bool, collect_system: bool) -> DeviceSample:
        client = SnmpClient(target_from_device(self.device))
        sample = DeviceSample(device_id=self.device.id)

        # 1) 系统 OID（一次 GET）
        sys_oids = [OID_SYS_UPTIME] + self.vendor.all_sys_oids()
        if collect_system:
            result = await client.get(sys_oids)
            sample.system.response_ms = result.elapsed_ms
            values = result.values
            sample.system.uptime_sec = _as_int(values.get(OID_SYS_UPTIME))
            sample.system.cpu_usage = _pick(values, self.vendor.cpu_oids)
            sample.system.temperature = _pick(values, self.vendor.temperature_oids)
            mem_total = _pick(values, self.vendor.mem_total_oids)
            mem_used = _pick(values, self.vendor.mem_used_oids)
            if mem_total is not None and mem_used is not None:
                sample.system.mem_total_mb = mem_total
                sample.system.mem_used_mb = mem_used
                sample.system.mem_usage = round(mem_used / mem_total * 100, 2) if mem_total else None
            elif mem_total is not None:
                sample.system.mem_total_mb = mem_total
                # 华为/思科部分 MIB 返回的是「已用百分比」
                if 0 < mem_total <= 100 and self.vendor.key in ("huawei", "cisco"):
                    sample.system.mem_usage = mem_total
                else:
                    sample.system.mem_used_mb = mem_total
                    sample.system.mem_usage = None
            elif mem_used is not None:
                sample.system.mem_used_mb = mem_used

            # 厂商特有指标（连接数 / AP 数 / 会话数等）
            for name, oids in self.vendor.extra_oids.items():
                val = _pick(values, oids)
                if val is not None:
                    sample.system.extra[name] = val

        # 2) 接口表（GET 优先，超阈值改用 BULK）
        if collect_interface:
            sample.interfaces = await self._collect_interfaces(client)

        # 3) SNMP 拿不到 CPU/内存时，SSH 兜底
        if collect_system and (sample.system.cpu_usage is None or sample.system.mem_usage is None):
            if self.device.ssh_username and self.device.ssh_password:
                try:
                    await self._enrich_via_ssh(sample)
                except Exception:
                    pass

        # 4) 厂商特有指标：SNMP 缺失时用 SSH 兜底
        if collect_system and self.vendor.extra_ssh_cmds:
            missing = [
                name
                for name in self.vendor.extra_ssh_cmds
                if name not in sample.system.extra
            ]
            if missing and self.device.ssh_username and self.device.ssh_password:
                try:
                    ssh = self._ssh_client()
                    extra = await ssh.fetch_extra_metrics()
                    for name, value in extra.items():
                        sample.system.extra.setdefault(name, value)
                except Exception:
                    pass

        sample.system.reachable = True
        return sample

    async def _collect_interfaces(self, client: SnmpClient) -> List[InterfaceSample]:
        """采集接口表。

        性能关键：按「整表」遍历而非逐列遍历。
        IF-MIB 的 ifEntry 表（1.3.6.1.2.1.2.2.1）与 IF-MIB 扩展表（1.3.6.1.2.1.31.1.1.1）
        各做一次 BULK 遍历即可拿到全部列，比逐列遍历减少 90% 的网络往返。
        """
        raw: Dict[int, Dict[str, Any]] = {}

        # OID 列 → 字段名
        column_map = {
            OID_IF_ENTRY: {
                "1": "index", "2": "descr", "3": "type", "5": "speed",
                "7": "admin_status", "8": "oper_status",
                "10": "in_octets", "14": "in_errors",
                "16": "out_octets", "20": "out_errors",
            },
            OID_IF_X_ENTRY: {
                "1": "name", "6": "hc_in_octets", "10": "hc_out_octets",
                "15": "high_speed", "18": "alias",
            },
        }

        for table_root, columns in column_map.items():
            try:
                result = await client.bulk(table_root)
            except SnmpError:
                continue
            base = table_root + "."
            for oid, value in result.values.items():
                if not oid.startswith(base):
                    continue
                suffix = oid[len(base) :]
                parts = suffix.split(".")
                if len(parts) != 2:
                    continue
                col, idx = parts
                field = columns.get(col)
                if field is None:
                    continue
                try:
                    index = int(idx)
                except ValueError:
                    continue
                raw.setdefault(index, {})[field] = value

        ts = time.time()
        samples: List[InterfaceSample] = []
        for idx, fields in raw.items():
            in_octets = _as_int(fields.get("hc_in_octets")) or _as_int(fields.get("in_octets")) or 0
            out_octets = _as_int(fields.get("hc_out_octets")) or _as_int(fields.get("out_octets")) or 0
            speed = _as_int(fields.get("speed")) or 0
            high_speed = _as_int(fields.get("high_speed")) or 0
            if high_speed:
                speed = high_speed * 1_000_000
            if speed and speed > 4_294_967_296:  # ifSpeed 对 >4G 端口返回 4294967295
                speed = high_speed * 1_000_000 if high_speed else 0
            in_bps, out_bps = rate_tracker.compute(self.device.id, idx, in_octets, out_octets, ts)
            util = 0.0
            if speed > 0:
                util = round(min(100.0, max(in_bps, out_bps) / speed * 100), 2)
            samples.append(
                InterfaceSample(
                    if_index=idx,
                    name=str(fields.get("name") or ""),
                    descr=str(fields.get("descr") or ""),
                    alias=str(fields.get("alias") or ""),
                    if_type=_as_int(fields.get("type")) or 0,
                    speed_bps=speed,
                    admin_status=_as_int(fields.get("admin_status")) or 0,
                    oper_status=_as_int(fields.get("oper_status")) or 0,
                    in_bps=round(in_bps, 2),
                    out_bps=round(out_bps, 2),
                    in_octets=in_octets,
                    out_octets=out_octets,
                    in_errors=_as_int(fields.get("in_errors")) or 0,
                    out_errors=_as_int(fields.get("out_errors")) or 0,
                    utilization=util,
                )
            )
        samples.sort(key=lambda s: s.if_index)
        return samples

    # ---------------------------------------------------------- SSH
    async def _collect_ssh_fallback(self) -> DeviceSample:
        sample = DeviceSample(device_id=self.device.id)
        ssh = self._ssh_client()
        cpu = await ssh.fetch_cpu()
        mem_pct, mem_total = await ssh.fetch_memory()
        sample.system.cpu_usage = cpu
        sample.system.mem_usage = mem_pct
        sample.system.mem_total_mb = mem_total
        if mem_pct is not None and mem_total:
            sample.system.mem_used_mb = round(mem_total * mem_pct / 100, 2)
        sample.system.source = "ssh"
        sample.system.reachable = True
        return sample

    async def _enrich_via_ssh(self, sample: DeviceSample) -> None:
        ssh = self._ssh_client()
        if sample.system.cpu_usage is None:
            sample.system.cpu_usage = await ssh.fetch_cpu()
            if sample.system.cpu_usage is not None:
                sample.system.source = "ssh"
        if sample.system.mem_usage is None:
            pct, total = await ssh.fetch_memory()
            if pct is not None:
                sample.system.mem_usage = pct
                sample.system.mem_total_mb = total
                if total:
                    sample.system.mem_used_mb = round(total * pct / 100, 2)
                sample.system.source = "ssh"

    def _ssh_client(self) -> SshClient:
        return SshClient(
            host=self.device.ip,
            username=self.device.ssh_username,
            password=self.device.ssh_password,
            vendor=self.device.vendor,
            port=self.device.ssh_port,
            timeout=self.device.ssh_timeout,
            enable_password=self.device.ssh_enable_password,
        )

    async def fetch_logs(self) -> List[Tuple[str, str]]:
        if self.mock:
            return []
        ssh = self._ssh_client()
        return await ssh.fetch_logs()

    async def backup_config(self) -> str:
        if self.mock:
            return self._mock_config()
        ssh = self._ssh_client()
        return await ssh.backup_config()

    # ---------------------------------------------------------- Mock
    def _collect_mock(self, collect_interface: bool, collect_system: bool) -> DeviceSample:
        """离线演示模式：生成逼真的模拟数据。

        计数器按时间增量推进，保证两次采样之间速率稳定可解释，
        与真实设备的 Octet 差分行为一致。
        """
        sample = DeviceSample(device_id=self.device.id)
        sample.system.source = "mock"
        sample.system.reachable = True
        sample.system.response_ms = round(random.uniform(3, 40), 2)
        base = random.uniform(5, 60)
        sample.system.cpu_usage = round(min(99.0, base + random.uniform(-5, 5)), 2)
        mem_total = random.choice([512, 1024, 2048, 4096])
        sample.system.mem_total_mb = float(mem_total)
        sample.system.mem_used_mb = round(mem_total * random.uniform(0.25, 0.75), 2)
        sample.system.mem_usage = round(
            sample.system.mem_used_mb / mem_total * 100, 2
        )
        sample.system.temperature = round(random.uniform(32, 58), 1)
        sample.system.uptime_sec = random.randint(3600, 8640000)

        if collect_interface:
            ts = time.time()
            if_index = 1
            for port_name, speed in [
                ("GigabitEthernet0/0/1", 1_000_000_000),
                ("GigabitEthernet0/0/2", 1_000_000_000),
                ("GigabitEthernet0/0/3", 1_000_000_000),
                ("TenGigabitEthernet1/0/1", 10_000_000_000),
                ("Port-channel1", 2_000_000_000),
            ]:
                in_octets, out_octets = self._mock_octets(if_index, ts)
                in_bps, out_bps = rate_tracker.compute(self.device.id, if_index, in_octets, out_octets, ts)
                oper = 1 if random.random() > 0.1 else 2
                sample.interfaces.append(
                    InterfaceSample(
                        if_index=if_index,
                        name=port_name,
                        descr=port_name,
                        alias=f"链路-{if_index}",
                        if_type=6,
                        speed_bps=speed,
                        admin_status=1,
                        oper_status=oper,
                        in_bps=round(in_bps, 2),
                        out_bps=round(out_bps, 2),
                        in_octets=in_octets,
                        out_octets=out_octets,
                        in_errors=0,
                        out_errors=0,
                        utilization=round(min(100.0, max(in_bps, out_bps) / speed * 100), 2),
                    )
                )
                if_index += 1
        return sample

    def _mock_octets(self, if_index: int, ts: float) -> tuple[int, int]:
        """按时间增量推进 mock 计数器（进程内状态）。"""
        key = (self.device.id, if_index)
        state = _mock_counter_state.get(key)
        if state is None:
            in_octets = int(ts * 1_000_000)
            out_octets = int(ts * 800_000)
            _mock_counter_state[key] = [ts, in_octets, out_octets]
            return in_octets, out_octets
        last_ts, in_octets, out_octets = state
        dt = max(0.0, ts - last_ts)
        # 目标流量在 20Mbps ~ 900Mbps 之间波动
        in_rate = 30_000_000 + (if_index * 137_000_000) % 800_000_000
        out_rate = 25_000_000 + (if_index * 91_000_000) % 700_000_000
        in_octets += int(dt * in_rate / 8)
        out_octets += int(dt * out_rate / 8)
        state[0] = ts
        state[1] = in_octets
        state[2] = out_octets
        return in_octets, out_octets

    def _mock_config(self) -> str:
        return f"""! Mock running-configuration for {self.device.name} ({self.device.ip})
!
hostname {self.device.name}
!
version 12.4
!
interface GigabitEthernet0/0/1
 description uplink-to-core
 switchport mode trunk
!
interface GigabitEthernet0/0/2
 description access-port
 switchport mode access
!
end
"""


# ---------------------------------------------------------------- 工具
def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _pick(values: Dict[str, Any], oids: List[str]) -> Optional[float]:
    """从候补 OID 中取第一个有效数值。"""
    for oid in oids:
        if oid in values:
            num = _as_int(values[oid])
            if num is not None:
                return float(num)
    return None
