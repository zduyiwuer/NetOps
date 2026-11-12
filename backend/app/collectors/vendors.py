"""多厂商适配：OID 候补表、SSH 命令模板、指标解析规则。

设计要点：
- 通用指标走标准 MIB（IF-MIB / HOST-RESOURCES-MIB / SNMPv2-MIB）。
- 厂商私有 OID 采用「候补列表」策略：按优先级依次尝试，取第一个有值的。
  这样即使某型号固件不支持某个私有 MIB，也能自动回退。
- CPU/内存无法通过 SNMP 获取时（部分华为/H3C 型号），自动改用 SSH 采集。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------- 通用 OID
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_OBJECTID = "1.3.6.1.2.1.1.2.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
OID_SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# IF-MIB
OID_IF_ENTRY = "1.3.6.1.2.1.2.2.1"
OID_IF_INDEX = f"{OID_IF_ENTRY}.1"
OID_IF_DESCR = f"{OID_IF_ENTRY}.2"
OID_IF_TYPE = f"{OID_IF_ENTRY}.3"
OID_IF_MTU = f"{OID_IF_ENTRY}.4"
OID_IF_SPEED = f"{OID_IF_ENTRY}.5"
OID_IF_PHYS_ADDRESS = f"{OID_IF_ENTRY}.6"
OID_IF_ADMIN_STATUS = f"{OID_IF_ENTRY}.7"
OID_IF_OPER_STATUS = f"{OID_IF_ENTRY}.8"
OID_IF_LAST_CHANGE = f"{OID_IF_ENTRY}.9"
OID_IF_IN_OCTETS = f"{OID_IF_ENTRY}.10"
OID_IF_IN_UCAST_PKTS = f"{OID_IF_ENTRY}.11"
OID_IF_IN_DISCARDS = f"{OID_IF_ENTRY}.13"
OID_IF_IN_ERRORS = f"{OID_IF_ENTRY}.14"
OID_IF_OUT_OCTETS = f"{OID_IF_ENTRY}.16"
OID_IF_OUT_UCAST_PKTS = f"{OID_IF_ENTRY}.17"
OID_IF_OUT_DISCARDS = f"{OID_IF_ENTRY}.19"
OID_IF_OUT_ERRORS = f"{OID_IF_ENTRY}.20"

# IF-MIB 扩展（ifName / ifHighSpeed / ifAlias / 64 位计数器）
OID_IF_X_ENTRY = "1.3.6.1.2.1.31.1.1.1"
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# HOST-RESOURCES-MIB
OID_HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"
OID_HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"
OID_HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"
OID_HR_STORAGE_TYPE = "1.3.6.1.2.1.25.2.3.1.2"
OID_HR_STORAGE_DESCR = "1.3.6.1.2.1.25.2.3.1.3"
OID_HR_MEMORY_SIZE = "1.3.6.1.2.1.25.2.2.0"  # 旧式单值内存


@dataclass
class VendorProfile:
    """厂商画像。"""

    key: str
    label: str
    netmiko_device_type: str
    # 设备类别（交换机 / 防火墙 / 无线控制器 / 通用），用于前端分组展示
    category: str = "通用"
    # 一句话说明该厂商的采集特点与特有指标
    desc: str = ""
    cpu_oids: List[str] = field(default_factory=list)
    mem_total_oids: List[str] = field(default_factory=list)
    mem_used_oids: List[str] = field(default_factory=list)
    # 直接返回「内存使用百分比」的 OID（值域 0-100）。
    # 有些设备（思科 ASA / WLC 实测）虽然能给出总内存，
    # 但 ciscoSysMonExtMemoryUsed 返回的值大于总量（设备侧的历史缺陷），
    # 只有 ciscoSysMonExtMemoryUtilization 是准的——这类设备用这里的 OID。
    mem_pct_oids: List[str] = field(default_factory=list)
    # 上面几个 OID 返回的是**字节**时置 True，平台会自动换算成 MB。
    # CISCO-SYSTEM-EXT-MIB 的 MemorySize / MemoryUsed 就是字节。
    mem_total_bytes: bool = False
    mem_used_bytes: bool = False
    temperature_oids: List[str] = field(default_factory=list)
    # 厂商特有指标：指标名 → OID 候补列表
    extra_oids: Dict[str, List[str]] = field(default_factory=dict)
    # 厂商特有指标的 SSH 采集命令：指标名 → 命令列表
    extra_ssh_cmds: Dict[str, List[str]] = field(default_factory=dict)
    cpu_ssh_cmd: List[str] = field(default_factory=list)
    mem_ssh_cmd: List[str] = field(default_factory=list)
    backup_cmd: List[str] = field(default_factory=list)
    log_cmd: List[str] = field(default_factory=list)
    version_cmd: List[str] = field(default_factory=list)
    interface_bulk: bool = True

    # ---- 无线控制器专属 ----
    # 无线明细（AP / 客户端 / 漫游）走 SNMP 采集
    # （AIRESPACE-WIRELESS-MIB 的 bsnAPTable / bsnMobileStationTable 等）
    wireless_snmp: bool = False
    # 旧方案：CLI 采集无线明细（已弃用，保留字段兼容历史配置）
    wireless_ap_cmds: List[str] = field(default_factory=list)
    wireless_client_cmds: List[str] = field(default_factory=list)

    # ---- 需要 WALK 才能取到的指标 ----
    # 有一类设备的 CPU / 内存指标是**带索引的表**（不是标量），
    # 例如 Catalyst 2960 的 cpmCPUTotal5minRev 在 .1.3.11 / .1.5.11，
    # ciscoMemoryPoolUsed 在 .48.1.1.1.5.1 —— 用固定 OID 做 GET 只会得到
    # "No Such Instance"。这些 OID 用 WALK 遍历后取第一个有效值。
    # 仅在常规 GET 取不到时才 WALK，避免无谓的网络开销。
    cpu_walk_oids: List[str] = field(default_factory=list)
    # 内存「已用」与「空闲」两张表，total = used + free（内存池语义）
    mem_used_walk_oids: List[str] = field(default_factory=list)
    mem_free_walk_oids: List[str] = field(default_factory=list)

    def all_sys_oids(self) -> List[str]:
        oids: List[str] = []
        for group in (
            self.cpu_oids,
            self.mem_total_oids,
            self.mem_used_oids,
            self.mem_pct_oids,
            self.temperature_oids,
        ):
            oids.extend(group)
        for group in self.extra_oids.values():
            oids.extend(group)
        return oids

    def all_extra_metric_names(self) -> List[str]:
        return list(self.extra_oids.keys())


VENDORS: Dict[str, VendorProfile] = {
    "routeros": VendorProfile(
        key="routeros",
        label="MikroTik RouterOS",
        netmiko_device_type="mikrotik_routeros",
        category="交换机",
        desc="RouterOS 交换机与路由器；CPU 取 CPU 负载，内存走系统资源命令",
        # mtxrInfo: .4=CpuLoad .7=CpuTemperature .9=TotalMemory .10=FreeMemory
        cpu_oids=["1.3.6.1.4.1.14988.1.1.3.4.4.0"],
        mem_total_oids=["1.3.6.1.4.1.14988.1.1.3.4.9.0"],
        mem_used_oids=["1.3.6.1.4.1.14988.1.1.3.4.10.0"],
        temperature_oids=["1.3.6.1.4.1.14988.1.1.3.4.7.0"],
        cpu_ssh_cmd=["/system resource print"],
        backup_cmd=["/export hide-sensitive"],
        log_cmd=["/log print"],
        version_cmd=["/system resource print", "/system routeros print"],
    ),
    "cisco": VendorProfile(
        key="cisco",
        label="Cisco IOS/NXOS",
        netmiko_device_type="cisco_ios",
        category="交换机",
        desc="Cisco IOS / NXOS 交换机与路由器；CPU 取 5 分钟平均负载，内存走进程内存池",
        # CISCO-PROCESS-MIB 5min + HOST-RESOURCES-MIB
        cpu_oids=[
            "1.3.6.1.4.1.9.9.109.1.1.1.1.8",
            "1.3.6.1.4.1.9.9.109.1.1.1.1.4",
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            "1.3.6.1.4.1.9.9.48.1.1.1.6",  # ciscoMemoryPoolFree + Used 需组合
            "1.3.6.1.4.1.9.2.1.47",
            OID_HR_MEMORY_SIZE,
        ],
        mem_used_oids=[
            "1.3.6.1.4.1.9.9.48.1.1.1.5",
            "1.3.6.1.4.1.9.2.1.46",
        ],
        # CISCO-PROCESS-MIB 的 CPU 指标是带索引的表
        # (Catalyst 2960 实测在 .1.3.11 / .1.5.11，索引由实体号决定)，
        # 固定 OID 做 GET 只会拿到 No Such Instance，必须 WALK
        cpu_walk_oids=[
            "1.3.6.1.4.1.9.9.109.1.1.1.1.5",  # cpmCPUTotal5minRev
            "1.3.6.1.4.1.9.9.109.1.1.1.1.3",  # cpmCPUTotal5secRev
        ],
        # CISCO-MEMORY-POOL-MIB: Processor 池的 used / free 都是带索引的表,
        # 总内存 = used + free (单位字节)
        mem_used_walk_oids=[
            "1.3.6.1.4.1.9.9.48.1.1.1.5",  # ciscoMemoryPoolUsed
        ],
        mem_free_walk_oids=[
            "1.3.6.1.4.1.9.9.48.1.1.1.6",  # ciscoMemoryPoolFree
        ],
        cpu_ssh_cmd=["show processes cpu | include CPU utilization"],
        mem_ssh_cmd=["show memory statistics"],
        backup_cmd=["show running-config"],
        log_cmd=["show logging | tail 200"],
        version_cmd=["show version"],
    ),
    "huawei": VendorProfile(
        key="huawei",
        label="华为 VRP",
        netmiko_device_type="huawei_vrp",
        category="交换机",
        desc="华为 VRP 系列交换机与路由器；CPU 取 5 分钟平均负载，内存走设备内存占用",
        cpu_oids=[
            "1.3.6.1.4.1.2011.2.239.1.1.1.1.4",
            "1.3.6.1.4.1.2011.2.239.1.1.1.1.6",
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            "1.3.6.1.4.1.2011.2.239.1.1.1.1.5",
            OID_HR_MEMORY_SIZE,
        ],
        mem_used_oids=[
            "1.3.6.1.4.1.2011.2.239.1.1.1.1.9",
        ],
        cpu_ssh_cmd=["display cpu-usage"],
        mem_ssh_cmd=["display memory"],
        backup_cmd=["display current-configuration"],
        log_cmd=["display logbuffer"],
        version_cmd=["display version"],
    ),
    "h3c": VendorProfile(
        key="h3c",
        label="新华三 H3C Comware",
        netmiko_device_type="hp_comware",
        category="交换机",
        desc="新华三 H3C Comware 系列；CPU 取 5 分钟平均负载，内存走系统内存使用率",
        cpu_oids=[
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.1.6",
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.1.8",
            OID_HR_MEMORY_SIZE,
        ],
        mem_used_oids=[
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.1.7",
        ],
        cpu_ssh_cmd=["display cpu-usage"],
        mem_ssh_cmd=["display memory"],
        backup_cmd=["display current-configuration"],
        log_cmd=["display logbuffer"],
        version_cmd=["display version"],
    ),
    "cisco_asa": VendorProfile(
        key="cisco_asa",
        label="Cisco ASA 防火墙",
        netmiko_device_type="cisco_asa",
        category="防火墙",
        desc="Cisco ASA 防火墙；额外采集连接数、CSC 表容量等安全设备专属指标",
        # CISCO-PROCESS-MIB + ASA 内存池
        cpu_oids=[
            # CISCO-SYSTEM-EXT-MIB 的 CPU——ASA 实测可用（取值 10）
            "1.3.6.1.4.1.9.9.43.1.1.4.0",  # ciscoSysMonExtCPUUtilization
            "1.3.6.1.4.1.9.9.109.1.1.1.1.8",
            "1.3.6.1.4.1.9.9.109.1.1.1.1.4",
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            # ciscoSysMonExtMemorySize（字节）——ASA 实测可用
            "1.3.6.1.4.1.9.9.43.1.1.1.0",
            "1.3.6.1.4.1.9.9.48.1.1.1.6",
            OID_HR_MEMORY_SIZE,
        ],
        mem_used_oids=[
            "1.3.6.1.4.1.9.9.43.1.1.2.0",  # ciscoSysMonExtMemoryUsed（ASA 上不准）
            "1.3.6.1.4.1.9.9.48.1.1.1.5",
        ],
        # ASA 的 ciscoSysMonExtMemoryUsed 返回的值**大于**总内存
        # （设备侧的历史缺陷），只有 MemoryUtilization 是准的
        mem_pct_oids=[
            "1.3.6.1.4.1.9.9.43.1.1.5.0",  # ciscoSysMonExtMemoryUtilization
        ],
        # CISCO-SYSTEM-EXT-MIB 的内存值单位是字节
        mem_total_bytes=True,
        mem_used_bytes=True,
        # ASA 特有：当前连接数、并发连接峰值、CSC 表项数
        # CISCO-ASA-MIB 的正确层级是 ciscoASAConnStats(1.2).ciscoASAConnStatsTable(1)
        extra_oids={
            "conn_count": ["1.3.6.1.4.1.9.9.166.1.2.1.1.0"],  # ciscoASAConnCount
            "conn_peak": ["1.3.6.1.4.1.9.9.166.1.2.1.2.0"],  # ciscoASAConnPeak
            "csc_table_size": ["1.3.6.1.4.1.9.9.166.1.3.2.1.1.0"],  # ciscoASACscTableSize
        },
        extra_ssh_cmds={
            "conn_count": ["show connection count"],
            "conn_peak": ["show connection detail | include Total"],
        },
        cpu_ssh_cmd=["show processes cpu"],
        mem_ssh_cmd=["show memory statistics"],
        # ASA 配置：running-config 含完整配置；backup 保留一份副本
        backup_cmd=["show running-config"],
        log_cmd=["show logging | tail 200"],
        version_cmd=["show version"],
    ),
    "cisco_wlc": VendorProfile(
        key="cisco_wlc",
        label="Cisco WLC 无线控制器",
        netmiko_device_type="cisco_wlc",
        category="无线控制器",
        desc="Cisco WLC 无线控制器；采集 AP 与客户端明细、AP 数与客户端数等无线侧指标",
        # CISCO-WLC-MIB / CISCO-SYSTEM-EXT-MIB / HOST-RESOURCES-MIB
        # WLC 的 CPU 在 CISCO-SYSTEM-EXT-MIB（ciscoSysMonExtCPUUtilization）
        # —— 实测 14179.1.6.3.x 这条路径在设备上并不存在
        cpu_oids=[
            "1.3.6.1.4.1.9.9.43.1.1.4.0",  # ciscoSysMonExtCPUUtilization
            "1.3.6.1.4.1.14179.1.1.1.11.0",  # ciscoWlcSysInfoCpuUtilization
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            "1.3.6.1.4.1.9.9.43.1.1.1.0",  # ciscoSysMonExtMemorySize（字节）
            "1.3.6.1.4.1.14179.1.1.1.10.0",  # ciscoWlcSysInfoMemTotal
            OID_HR_MEMORY_SIZE,
        ],
        # WLC 的 ciscoSysMonExtMemoryUsed 与 MemoryUtilization 都不可信
        # （实测 used≈size、utilization 返回 230），因此这里**不配** mem_used_oids
        # 与 mem_pct_oids——让内存走 SSH 的 show memory statistics（那张表是准的）。
        mem_used_oids=[],
        mem_pct_oids=[],
        # CISCO-SYSTEM-EXT-MIB 的内存值单位是字节
        mem_total_bytes=True,
        # WLC 特有：已关联 AP 数、已关联客户端数
        # 注意：IOS XE 的 WLC 没有这两个标量，实际数量靠 SSH 命令解析
        extra_oids={
            "ap_count": ["1.3.6.1.4.1.14179.1.1.1.13.0"],  # ciscoWlcApClientAssociated
            "client_count": ["1.3.6.1.4.1.14179.1.1.1.14.0"],  # ciscoWlcDot11ClientAssociated
        },
        extra_ssh_cmds={
            "ap_count": ["show ap summary"],
            # AireOS 是 show client summary，IOS XE 是 show wireless client summary
            "client_count": ["show wireless client summary", "show client summary"],
        },
        cpu_ssh_cmd=["show cpu usage"],
        # IOS XE 的 WLC 只有 show memory statistics 能拿到准的内存数据
        mem_ssh_cmd=["show memory statistics", "show memory usage"],
        # 无线明细走 SNMP（AIRESPACE-WIRELESS-MIB），含信道 / RSSI / SNR / 收发流量
        wireless_snmp=True,
        backup_cmd=["show running-config"],
        log_cmd=["show messages | tail 200"],
        version_cmd=["show version"],
    ),
    "fortinet": VendorProfile(
        key="fortinet",
        label="飞塔 Fortinet FortiGate",
        netmiko_device_type="fortinet",
        category="防火墙",
        desc="飞塔 FortiGate 防火墙；额外采集会话数、VPN 隧道数等安全设备专属指标",
        # FORTINET-MIB: fgSysCpuUtilization / fgSysMemUsage
        cpu_oids=[
            "1.3.6.1.4.1.12356.101.1.1.1.0",  # fgSysCpuUtilization
            f"{OID_HR_PROCESSOR_LOAD}.1",
        ],
        mem_total_oids=[
            "1.3.6.1.4.1.12356.101.1.1.2.0",  # fgSysMemTotal
            OID_HR_MEMORY_SIZE,
        ],
        mem_used_oids=[
            "1.3.6.1.4.1.12356.101.1.1.3.0",  # fgSysMemUsage
        ],
        # 飞塔特有：并发会话数、登录用户数、VPN 隧道数
        extra_oids={
            "session_count": ["1.3.6.1.4.1.12356.101.1.2.6.0"],  # fgSysSesCount
            "user_count": ["1.3.6.1.4.1.12356.101.1.2.7.0"],  # fgSysUserCount
            "vpn_tunnel_count": ["1.3.6.1.4.1.12356.101.1.2.10.0"],  # fgSysVpnTunnelCount
        },
        extra_ssh_cmds={
            "session_count": ["get system performance status"],
            "user_count": ["get system performance status"],
        },
        cpu_ssh_cmd=["get system performance status"],
        mem_ssh_cmd=["get system performance status"],
        # 飞塔备份：先保存一份诊断快照（便于回滚），再导出可读配置
        backup_cmd=[
            "diagnose firewall config-file save - flash",
            "show full-configuration",
        ],
        log_cmd=["execute log filter category event"],
        version_cmd=["get system status"],
    ),
    "generic": VendorProfile(
        key="generic",
        label="通用标准 MIB 设备",
        netmiko_device_type="autodetect",
        category="通用",
        desc="按标准 MIB 采集的通用设备，适用于其他未单独适配的厂商设备",
        cpu_oids=[f"{OID_HR_PROCESSOR_LOAD}.1"],
        mem_total_oids=[OID_HR_MEMORY_SIZE],
        mem_used_oids=[],
        cpu_ssh_cmd=["show processes cpu"],
        backup_cmd=["show running-config"],
        log_cmd=["show logging"],
        version_cmd=["show version"],
    ),
}

# 别名归一化：用户可能填各种写法
VENDOR_ALIASES = {
    "mikrotik": "routeros",
    "mikrotik_routeros": "routeros",
    "routeros": "routeros",
    "cisco": "cisco",
    "ios": "cisco",
    "ios_xe": "cisco",
    "nxos": "cisco",
    "nexus": "cisco",
    "asa": "cisco_asa",
    "cisco_asa": "cisco_asa",
    "firewall": "cisco_asa",
    "pix": "cisco_asa",
    "wlc": "cisco_wlc",
    "cisco_wlc": "cisco_wlc",
    "wireless": "cisco_wlc",
    "aireos": "cisco_wlc",
    "huawei": "huawei",
    "vrp": "huawei",
    "h3c": "h3c",
    "comware": "h3c",
    "hp_comware": "h3c",
    "hewlett-packard": "h3c",
    "fortinet": "fortinet",
    "fortigate": "fortinet",
    "fortios": "fortinet",
    "飞塔": "fortinet",
    "generic": "generic",
}


def resolve_vendor(name: Optional[str]) -> VendorProfile:
    """按名称取厂商画像，未知厂商回退到标准 MIB。"""
    if not name:
        return VENDORS["generic"]
    key = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    key = VENDOR_ALIASES.get(key, key)
    return VENDORS.get(key, VENDORS["generic"])


def vendor_options() -> List[Dict[str, str]]:
    """厂商选项列表。

    登录页与系统管理的「平台说明」都用这份数据，
    新增厂商只需注册画像，前端两处自动同步。
    """
    return [
        {
            "value": v.key,
            "label": v.label,
            "netmiko": v.netmiko_device_type,
            "category": v.category,
            "desc": v.desc,
        }
        for v in VENDORS.values()
    ]
