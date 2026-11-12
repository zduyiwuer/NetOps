"""采集引擎：把 SNMP/SSH 数据转成平台内部指标模型。

接口速率通过相邻两次采样的 Octet 差分计算，因此引擎维护一个
进程内的速率状态缓存（按 device_id + ifIndex 维度）。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.collectors.snmp import SnmpClient, SnmpError, target_from_device
from app.collectors.ssh import SshClient, SshError
from app.db.session import SessionLocal
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

logger = logging.getLogger("netops.collector.engine")


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
    # 无线控制器专属：AP 与客户端明细
    wireless_aps: List[Dict[str, Any]] = field(default_factory=list)
    wireless_clients: List[Dict[str, Any]] = field(default_factory=list)


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
                except Exception as exc:
                    self._log_ssh_failure("整体采集兜底", exc)
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
            # 配置里的 community 带冗余后缀时，SNMP 客户端会自动清洗并重试。
            # 这里把清洗后的值写回设备配置——否则每次采集都要先超时一次，
            # 白白多花几秒。
            if result.effective_community and result.effective_community != self.device.snmp_community:
                self._fix_snmp_community(result.effective_community)
            sample.system.uptime_sec = _as_int(values.get(OID_SYS_UPTIME))
            # SNMP 的 sysUpTime 是 Timeticks（单位 1/100 秒），
            # 直接当秒用会让运行时长偏大 100 倍
            if sample.system.uptime_sec:
                sample.system.uptime_sec //= 100
            sample.system.cpu_usage = _pick(values, self.vendor.cpu_oids)
            sample.system.temperature = _pick(values, self.vendor.temperature_oids)
            mem_total = _pick(values, self.vendor.mem_total_oids)
            mem_used = _pick(values, self.vendor.mem_used_oids)
            # 部分 MIB 返回的是字节，统一换算成 MB
            if mem_total is not None and self.vendor.mem_total_bytes:
                mem_total = round(mem_total / 1024 / 1024, 2)
            if mem_used is not None and self.vendor.mem_used_bytes:
                mem_used = round(mem_used / 1024 / 1024, 2)
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

            # 3.1) 设备直接给出内存百分比时优先用它——
            #     有些设备（思科 ASA / WLC 实测）ciscoSysMonExtMemoryUsed
            #     返回的值比总内存还大（设备侧历史缺陷），
            #     只有 MemoryUtilization 是准的
            if self.vendor.mem_pct_oids:
                pct = _pick(values, self.vendor.mem_pct_oids)
                if pct is not None and 0 <= pct <= 100:
                    sample.system.mem_usage = round(pct, 2)
                    if sample.system.mem_total_mb:
                        sample.system.mem_used_mb = round(
                            sample.system.mem_total_mb * pct / 100, 2
                        )

            # 3) 带索引的表型指标：GET 取不到时改用 WALK
            #    例：Catalyst 2960 的 cpmCPUTotal5minRev 在 .1.5.11、
            #        ciscoMemoryPoolUsed 在 .48.1.1.1.5.1 —— 都是表而非标量
            if sample.system.cpu_usage is None and self.vendor.cpu_walk_oids:
                walked = await _walk_first(client, self.vendor.cpu_walk_oids)
                if walked is not None:
                    sample.system.cpu_usage = walked

            if (
                self.vendor.mem_used_walk_oids
                and sample.system.mem_usage is None
                and sample.system.mem_total_mb is None
            ):
                used_b = await _walk_first(client, self.vendor.mem_used_walk_oids)
                free_b = (
                    await _walk_first(client, self.vendor.mem_free_walk_oids)
                    if self.vendor.mem_free_walk_oids
                    else None
                )
                if used_b is not None:
                    # 内存池语义：total = used + free（单位字节 → MB）
                    total_bytes = used_b + (free_b or 0)
                    if total_bytes > 0:
                        sample.system.mem_total_mb = round(total_bytes / 1024 / 1024, 2)
                        sample.system.mem_used_mb = round(used_b / 1024 / 1024, 2)
                        sample.system.mem_usage = round(used_b / total_bytes * 100, 2)

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
                except Exception as exc:
                    self._log_ssh_failure("CPU/内存补采", exc)

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
                except Exception as exc:
                    self._log_ssh_failure("厂商特有指标补采", exc)

        # 5) 无线数据：AP 列表与客户端列表（仅无线控制器）
        if collect_system and (
            self.vendor.wireless_ap_cmds or self.vendor.wireless_client_cmds
        ):
            if self.device.ssh_username and self.device.ssh_password:
                try:
                    await self._collect_wireless(sample)
                except Exception as exc:
                    self._log_ssh_failure("无线数据采集", exc)

        sample.system.reachable = True
        return sample

    async def _collect_wireless(self, sample: DeviceSample) -> None:
        """采集并落库无线 AP 与客户端明细。"""
        ssh = self._ssh_client()
        aps = await ssh.fetch_wireless_aps()
        clients = await ssh.fetch_wireless_clients()
        if not aps and not clients:
            return
        sample.wireless_aps = aps
        sample.wireless_clients = clients
        self._save_wireless(aps, clients)

    def _save_wireless(
        self,
        aps: List[Dict[str, Any]],
        clients: List[Dict[str, Any]],
    ) -> None:
        """把无线明细写入数据库（整体替换，保留最新快照）。"""
        try:
            from app.db.session import SessionLocal
            from app.models.models import WirelessAP, WirelessClient

            now = datetime.now()
            db = SessionLocal()
            try:
                db.query(WirelessAP).filter(
                    WirelessAP.device_id == self.device.id
                ).delete(synchronize_session=False)
                db.query(WirelessClient).filter(
                    WirelessClient.device_id == self.device.id
                ).delete(synchronize_session=False)
                for a in aps:
                    db.add(WirelessAP(device_id=self.device.id, ts=now, **a))
                for c in clients:
                    db.add(WirelessClient(device_id=self.device.id, ts=now, **c))
                db.commit()
            finally:
                db.close()
            logger.info(
                "设备 %s 的无线数据已更新：%d 个 AP / %d 个客户端",
                self.device.name, len(aps), len(clients),
            )
        except Exception as exc:
            logger.debug("保存无线数据失败: %s", exc)

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
        if sample.system.mem_usage is None or sample.system.mem_total_mb is None:
            pct, total = await ssh.fetch_memory()
            # 百分比与总内存任一可用都要写入——
            # 只拿到总内存时也要记下来，否则「总内存」这一栏永远是空的
            if pct is not None:
                sample.system.mem_usage = pct
                sample.system.source = "ssh"
            if total is not None:
                sample.system.mem_total_mb = total
                if pct is not None:
                    sample.system.mem_used_mb = round(total * pct / 100, 2)
                else:
                    sample.system.mem_used_mb = None
                sample.system.source = "ssh"

    def _log_ssh_failure(self, scene: str, exc: Exception) -> None:
        """SSH 兜底失败时写入系统日志。

        这些分支原本是静默 pass，导致「某些指标采不到」时完全无从查证。
        这里记录失败场景与原因——老设备常见的失败是
        "no acceptable kex algorithm"（paramiko 5 已移除 group1-sha1 等老算法）。
        """
        msg = str(exc)
        hint = ""
        low = msg.lower()
        if "kex algorithm" in low:
            hint = (
                "该设备只支持较老的密钥交换算法（如 diffie-hellman-group1-sha1），"
                "而当前 paramiko 5.x 已将其移除，因此无法建立 SSH 连接。"
                "SNMP 采集不受影响；如需 CLI 采集，可考虑升级设备固件。"
            )
        elif "host key" in low:
            hint = (
                "该设备使用的主机密钥算法较旧（如 ssh-rsa / ssh-dss），"
                "当前 paramiko 5.x 已不再接受，因此无法建立 SSH 连接。"
                "SNMP 采集不受影响；如需 CLI 采集，可考虑升级设备固件，"
                "或在设备侧改用现代算法。"
            )
        elif not hint:
            hint = "SNMP 采集不受影响，仅依赖 CLI 的指标会缺失。"
        try:
            from app.services import system_log_service

            system_log_service.write_log(
                system_log_service.LEVEL_WARNING,
                system_log_service.CAT_COLLECTOR,
                "collector",
                f"设备 {self.device.name} 的 SSH {scene}失败：{msg}",
                hint,
                device_id=self.device.id,
                context={
                    "device_id": self.device.id,
                    "device_name": self.device.name,
                    "scene": scene,
                    "host": self.device.ip,
                },
            )
        except Exception:
            pass

    def _fix_snmp_community(self, effective: str) -> None:
        """把自动清洗后的 community 写回设备配置。

        配置里的 community 带 " ro"/" rw" 这类冗余后缀时，
        设备会当作错误的 community 直接丢弃（表现为超时而非认证失败）。
        既然已经确认清洗后的值可用，就顺手把配置改对——
        否则每次采集都要先超时一次，白白多花几秒。

        用独立的 UPDATE 语句而不是改 ORM 属性：
        self.device 属于调用方的 session，
        直接改属性要等调用方 commit 才落库，
        而采集链路上的调用方未必会提交。
        """
        try:
            from sqlalchemy import update

            from app.models.models import Device

            old = self.device.snmp_community
            db = SessionLocal()
            try:
                db.execute(
                    update(Device)
                    .where(Device.id == self.device.id)
                    .values(snmp_community=effective)
                )
                db.commit()
            finally:
                db.close()
            # 同步内存中的对象，避免同一 session 内后续仍用旧值
            self.device.snmp_community = effective
            logger.info(
                "已把设备 %s 的 SNMP community 从 %r 修正为 %r",
                self.device.name, old, effective,
            )
        except Exception as exc:
            logger.debug("修正 SNMP community 失败: %s", exc)

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

    async def fetch_logs(self, limit: int = 200) -> List[Tuple[str, str]]:
        if self.mock:
            return []
        ssh = self._ssh_client()
        return await ssh.fetch_logs(limit=limit)

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


async def _walk_first(client: Any, oids: List[str]) -> Optional[float]:
    """WALK 遍历候选根 OID，取第一个有效数值。

    有一类设备的 CPU / 内存指标是**带索引的表**而非标量，
    例如 Catalyst 2960 的 cpmCPUTotal5minRev 实际在 .1.5.11、
    ciscoMemoryPoolUsed 在 .48.1.1.1.5.1——
    用固定 OID 做 GET 只会得到 "No Such Instance"。
    WALK 遍历后按 OID 排序取第一个（即索引最小的那个，
    通常对应主实例，如 Processor 池）。
    """
    for root in oids:
        try:
            result = await client.walk(root)
        except Exception as exc:
            logger.debug("SNMP WALK %s 失败: %s", root, exc)
            continue
        if not result.ok or not result.values:
            continue
        for oid in sorted(result.values.keys()):
            num = _as_int(result.values[oid])
            if num is not None:
                return float(num)
    return None
