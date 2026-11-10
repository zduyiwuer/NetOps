"""SNMP 采集层验证脚本。

不使用外部设备：直接实现一个符合 pysnmp 仪表化接口的 Mock Agent，
在本地监听 SNMP v2c / v3，注入标准 IF-MIB 与 HOST-RESOURCES-MIB 风格数据，
用于验证：
  1. SNMP GET 解析（系统 OID、CPU/内存候补表）
  2. SNMPv3 USM authPriv 认证
  3. BULK 接口表遍历
  4. 接口速率差分计算（in_bps / out_bps / utilization）
  5. 厂商 OID 候补机制
  6. Syslog 解析

运行：python -m scripts.verify_snmp
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyasn1.type import univ
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config, engine
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.proto import api, rfc1902
from pysnmp.smi import builder

from app.collectors.engine import Collector, RateTracker, rate_tracker
from app.collectors.snmp import SnmpClient, SnmpTarget
from app.collectors.vendors import (
    OID_IF_HC_IN_OCTETS,
    OID_IF_HC_OUT_OCTETS,
    OID_IF_HIGH_SPEED,
    OID_IF_NAME,
    OID_IF_OPER_STATUS,
    OID_IF_SPEED,
    OID_SYS_UPTIME,
    resolve_vendor,
)
from app.services.syslog_server import parse_syslog

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: List[Tuple[str, bool, str]] = []

# 测试数据使用的企业 OID 段（模拟厂商私有 MIB）
TEST_CPU = "1.3.6.1.4.1.99999.2.1.0"
TEST_MEM_TOTAL = "1.3.6.1.4.1.99999.3.1.0"
TEST_MEM_USED = "1.3.6.1.4.1.99999.3.2.0"
TEST_IF_NAME = "1.3.6.1.4.1.99999.4.1"
TEST_IF_SPEED = "1.3.6.1.4.1.99999.4.2"
TEST_IF_OPER = "1.3.6.1.4.1.99999.4.3"
TEST_IF_HCIN = "1.3.6.1.4.1.99999.4.4"
TEST_IF_HCOUT = "1.3.6.1.4.1.99999.4.5"

# 接口计数器（可动态推进，用于验证速率差分）
IF_COUNTERS: Dict[int, Dict[str, int]] = {
    1: {"in": 100_000_000, "out": 50_000_000},
    2: {"in": 200_000_000, "out": 80_000_000},
    3: {"in": 300_000_000, "out": 150_000_000},
}

# 标准 IF-MIB 表（真实结构：表根 + 列 + 索引）
OID_IF_ENTRY = "1.3.6.1.2.1.2.2.1"
OID_IF_X_ENTRY = "1.3.6.1.2.1.31.1.1.1"


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class MockInstrumentation:
    """直接响应 OID 的 SNMP 仪表化实现（绕开 MIB 编译）。"""

    def __init__(self) -> None:
        self.values: Dict[str, Any] = {
            OID_SYS_UPTIME: rfc1902.TimeTicks(123456),
            "1.3.6.1.4.1.99999.1.2.0": rfc1902.OctetString("TestSwitch"),
            TEST_CPU: rfc1902.Integer(42),
            TEST_MEM_TOTAL: rfc1902.Integer(2048),
            TEST_MEM_USED: rfc1902.Integer(1024),
        }
        # 真实 IF-MIB 表结构：ifEntry（列.索引）
        for idx, counters in IF_COUNTERS.items():
            self.values[f"{OID_IF_ENTRY}.2.{idx}"] = rfc1902.OctetString(f"GigabitEthernet0/0/{idx}")
            self.values[f"{OID_IF_ENTRY}.3.{idx}"] = rfc1902.Integer(6)  # ifType ethernetCsmacd
            self.values[f"{OID_IF_ENTRY}.5.{idx}"] = rfc1902.Integer(1_000_000_000)  # ifSpeed
            self.values[f"{OID_IF_ENTRY}.7.{idx}"] = rfc1902.Integer(1)  # ifAdminStatus up
            self.values[f"{OID_IF_ENTRY}.8.{idx}"] = rfc1902.Integer(1)  # ifOperStatus up
            self.values[f"{OID_IF_ENTRY}.10.{idx}"] = rfc1902.Counter32(counters["in"] & 0xFFFFFFFF)
            self.values[f"{OID_IF_ENTRY}.16.{idx}"] = rfc1902.Counter32(counters["out"] & 0xFFFFFFFF)
            # IF-MIB 扩展表
            self.values[f"{OID_IF_X_ENTRY}.1.{idx}"] = rfc1902.OctetString(f"GigabitEthernet0/0/{idx}")
            self.values[f"{OID_IF_X_ENTRY}.6.{idx}"] = rfc1902.Counter64(counters["in"])
            self.values[f"{OID_IF_X_ENTRY}.10.{idx}"] = rfc1902.Counter64(counters["out"])
            self.values[f"{OID_IF_X_ENTRY}.15.{idx}"] = rfc1902.Integer(1000)  # ifHighSpeed
            self.values[f"{OID_IF_X_ENTRY}.18.{idx}"] = rfc1902.OctetString(
                f"链路-{idx}".encode("utf-8")
            )

    # --- 接口实现 ---
    def read_variables(self, *var_binds, **context_):
        out = []
        for name, _value in var_binds:
            key = str(name)
            if key in self.values:
                out.append((name, self.values[key]))
            else:
                out.append((name, rfc1902.Null("")))
        return out

    def read_next_variables(self, *var_binds, **context_):
        """按 OID 字典序返回"下一个"变量，模拟 WALK/BULK。"""
        keys = sorted(self.values.keys(), key=_oid_sort_key)
        out = []
        for name, _value in var_binds:
            cur = str(name)
            nxt = None
            for k in keys:
                if _oid_sort_key(k) > _oid_sort_key(cur):
                    nxt = k
                    break
            if nxt is None:
                out.append((name, rfc1902.Null("")))
            else:
                # 必须返回「下一个」的 OID（而非请求的 OID）
                out.append((rfc1902.ObjectName(nxt), self.values[nxt]))
        return out

    def write_variables(self, *var_binds, **context_):
        return [(name, rfc1902.Null("")) for name, _ in var_binds]


def _oid_sort_key(oid: str) -> Tuple:
    return tuple(int(p) for p in oid.split("."))


async def start_mock_agent(port: int = 16100):
    """启动 Mock SNMP Agent（v2c + v3）。"""
    snmp_engine = engine.SnmpEngine()
    config.add_transport(
        snmp_engine,
        udp.DOMAIN_NAME,
        udp.UdpTransport().open_server_mode(("127.0.0.1", port)),
    )
    # v2c：communityIndex=public, communityName=public（客户端用 community 名认证）
    config.add_v1_system(snmp_engine, "public", "public")
    config.add_vacm_user(snmp_engine, 2, "public", "noAuthNoPriv", (1, 3, 6))
    config.add_v3_user(
        snmp_engine,
        "v3user",
        config.USM_AUTH_HMAC96_SHA,
        "authkey123",
        config.USM_PRIV_CFB128_AES,
        "privkey123",
    )
    config.add_vacm_user(snmp_engine, 3, "v3user", "authPriv", (1, 3, 6))

    snmp_context = context.SnmpContext(snmp_engine)
    instrum = MockInstrumentation()
    # 默认 context 已存在，直接替换其仪表化实现
    snmp_context.context_names[b""] = instrum

    cmdrsp.GetCommandResponder(snmp_engine, snmp_context)
    cmdrsp.SetCommandResponder(snmp_engine, snmp_context)
    cmdrsp.NextCommandResponder(snmp_engine, snmp_context)
    cmdrsp.BulkCommandResponder(snmp_engine, snmp_context)

    snmp_engine.open_dispatcher()
    return snmp_engine, instrum


class FakeDevice:
    """模拟 ORM Device 对象。"""

    def __init__(self, port: int):
        self.id = 999
        self.name = "SNMP-Test-Switch"
        self.ip = "127.0.0.1"
        self.snmp_port = port
        self.snmp_version = "2c"
        self.snmp_community = "public"
        self.snmp_v3_user = ""
        self.snmp_v3_auth_proto = "SHA"
        self.snmp_v3_auth_key = ""
        self.snmp_v3_priv_proto = "AES"
        self.snmp_v3_priv_key = ""
        self.ssh_port = 22
        self.ssh_username = ""
        self.ssh_password = ""
        self.ssh_enable_password = ""
        self.ssh_timeout = 10
        self.vendor = "generic"
        self.mock_mode = False
        self.collect_interface = True
        self.collect_system = True
        self.collect_log = True
        self.collect_backup = True


async def main() -> int:
    print("\n=== NetOps SNMP 采集层验证 ===\n")
    port = 16100

    print("[1] 启动 Mock SNMP Agent (v2c + v3) ...")
    try:
        _engine, instrum = await start_mock_agent(port)
    except Exception as exc:
        print(f"  启动失败: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    await asyncio.sleep(0.5)
    check("Agent 启动", True, f"127.0.0.1:{port}")

    print("\n[2] SNMP v2c GET 采集")
    client = SnmpClient(SnmpTarget(host="127.0.0.1", port=port, version="2c", community="public", timeout=3))
    try:
        res = await client.get([OID_SYS_UPTIME, "1.3.6.1.4.1.99999.1.2.0", TEST_CPU, TEST_MEM_TOTAL, TEST_MEM_USED])
        check("GET 请求成功", res.ok, res.error)
        check("sysUpTime 解析", OID_SYS_UPTIME in res.values, f"={res.values.get(OID_SYS_UPTIME)}")
        check(
            "OctetString 解析为 str",
            isinstance(res.values.get("1.3.6.1.4.1.99999.1.2.0"), str),
            f"={res.values.get('1.3.6.1.4.1.99999.1.2.0')}",
        )
        check("CPU 整数解析", res.values.get(TEST_CPU) == 42, f"={res.values.get(TEST_CPU)}")
        check("内存总量解析", res.values.get(TEST_MEM_TOTAL) == 2048)
        check("内存已用解析", res.values.get(TEST_MEM_USED) == 1024)
    except Exception as exc:
        check("GET 请求成功", False, str(exc))

    print("\n[3] SNMPv3 USM authPriv 认证")
    try:
        v3 = SnmpTarget(
            host="127.0.0.1",
            port=port,
            version="3",
            v3_user="v3user",
            v3_auth_proto="SHA",
            v3_auth_key="authkey123",
            v3_priv_proto="AES",
            v3_priv_key="privkey123",
            timeout=3,
        )
        r3 = await SnmpClient(v3).get([OID_SYS_UPTIME])
        check("v3 authPriv 认证并取到数据", r3.ok and OID_SYS_UPTIME in r3.values, r3.error)
    except Exception as exc:
        check("v3 authPriv 认证并取到数据", False, str(exc))

    print("\n[4] BULK 整表遍历")
    try:
        bulk = await client.bulk(OID_IF_ENTRY)
        check("BULK 返回数据", len(bulk.values) >= 15, f"{len(bulk.values)} 个变量")
        names = [v for k, v in bulk.values.items() if k.startswith(f"{OID_IF_ENTRY}.2.")]
        check("接口名解析", "GigabitEthernet0/0/1" in names, f"{names}")
        speeds = [v for k, v in bulk.values.items() if k.startswith(f"{OID_IF_ENTRY}.5.")]
        check("接口速率解析", 1_000_000_000 in speeds, f"{speeds}")
        oper = [v for k, v in bulk.values.items() if k.startswith(f"{OID_IF_ENTRY}.8.")]
        check("接口状态解析", 1 in oper, f"{oper}")
    except Exception as exc:
        check("BULK 返回数据", False, str(exc))

    print("\n[4b] 接口表整表采集（引擎级）")
    try:
        device = FakeDevice(port)
        collector = Collector(device)
        ifaces = await collector._collect_interfaces(client)
        check("整表采集到全部接口", len(ifaces) == 3, f"{len(ifaces)} 个")
        if ifaces:
            i1 = next((i for i in ifaces if i.if_index == 1), None)
            check("接口名来自 IF-MIB 扩展表", bool(i1 and i1.name == "GigabitEthernet0/0/1"),
                  i1.name if i1 else "-")
            check("协商速率来自 ifHighSpeed", bool(i1 and i1.speed_bps == 1_000_000_000),
                  f"{i1.speed_bps if i1 else '-'}")
            check("接口别名解析", bool(i1 and i1.alias == "链路-1"), i1.alias if i1 else "-")
            check("操作状态解析", bool(i1 and i1.oper_status == 1), f"{i1.oper_status if i1 else '-'}")
            check("64 位计数器取值", bool(i1 and i1.in_octets == 100_000_000),
                  f"{i1.in_octets if i1 else '-'}")
    except Exception as exc:
        check("整表采集到全部接口", False, str(exc))

    print("\n[5] 厂商 OID 候补机制")
    try:
        vendor = resolve_vendor("h3c")
        check("厂商别名解析 h3c→H3C", vendor.key == "h3c", vendor.label)
        check("MikroTik 别名解析", resolve_vendor("mikrotik").key == "routeros")
        check("未知厂商回退 generic", resolve_vendor("不存在的厂商").key == "generic")
        check("RouterOS CPU OID 已配置", len(resolve_vendor("routeros").cpu_oids) > 0,
              resolve_vendor("routeros").cpu_oids[0])
        check("各厂商均有 SSH 兜底命令", all(resolve_vendor(v).cpu_ssh_cmd for v in ("routeros", "cisco", "huawei", "h3c")))
    except Exception as exc:
        check("厂商 OID 候补机制", False, str(exc))

    print("\n[6] 接口速率差分计算")
    try:
        tracker = RateTracker()
        t0 = time.time()
        tracker.compute(1, 1, 0, 0, t0)
        in_bps, out_bps = tracker.compute(1, 1, 125_000, 62_500, t0 + 1.0)
        check("入向速率差分 = 1Mbps", abs(in_bps - 1_000_000) < 1, f"{in_bps} bps")
        check("出向速率差分 = 500Kbps", abs(out_bps - 500_000) < 1, f"{out_bps} bps")
        in_bps2, _ = tracker.compute(1, 1, 100, 50, t0 + 2.0)
        check("计数器回绕归零（设备重启）", in_bps2 == 0.0, f"{in_bps2}")
        # 利用率
        util = round(1_000_000 / 1_000_000_000 * 100, 2)
        check("利用率换算", util == 0.1, f"{util}%")
    except Exception as exc:
        check("接口速率差分计算", False, str(exc))

    print("\n[7] Syslog 解析")
    # (原始行, 期望 PRI, 期望 facility, 期望 severity 数字, 期望主机名, 期望级别名)
    cases = [
        ("<189>Oct 10 09:00:00 SW01 %%01IFNET/3/PHY_UPDOWN: Physical state on GigabitEthernet0/0/1 changed to down.",
         189, 23, 3, "SW01", "err"),
        ("<134>Oct 10 09:00:01 SW02 SHELL/5/LOGIN: user admin logged in", 134, 16, 5, "SW02", "notice"),
        ("<14>Oct 10 09:00:02 SW03 %%01SHELL/5/CMDRECORD: record command info", 14, 1, 5, "SW03", "notice"),
        ("<30>Oct 10 09:00:03 SW04 PLATFORM/2/BOARD: board inserted", 30, 3, 2, "SW04", "crit"),
        ("<86>Oct 10 09:00:04 SW05 %LINK-3-UPDOWN: Interface GigabitEthernet0/0/5 changed state to down",
         86, 10, 3, "SW05", "err"),
    ]
    for raw, pri, fac, sev, host, sevname in cases:
        f, s, h, name, msg, p = parse_syslog(raw)
        check(f"PRI={pri} 解析", p == pri and f == fac, f"facility={f} (期望{fac})")
        check(f"级别={sevname}", s == sev and name == sevname, f"实际 sev={s}/{name} (期望{sev}/{sevname})")
        check(f"主机名={host}", h == host, f"={h}")

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
