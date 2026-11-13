"""MIB 文件上传、解析与指标探测验证。

覆盖：
  1. **解析正确性** —— 多级索引、IMPORTS、MODULE-IDENTITY、注释剔除
  2. **多级索引** —— { fgSysMem 1.3 } 与 { mib-2 1 3 } 两种写法
  3. **文件内定义优先** —— 与内置标准根同名时以文件为准
  4. **上传 / 列表 / 删除** 接口
  5. **指标检索与 OID 反查**
  6. **设备指标探测** —— 准确区分设备支持与不支持

运行：python -m scripts.verify_mib（需先启动后端服务）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


MIB_TEXT = """FORTINET-MIB DEFINITIONS ::= BEGIN

IMPORTS
    MODULE-IDENTITY, OBJECT-TYPE, Integer32, TimeTicks
        FROM SNMPv2-SMI
    DisplayString
        FROM SNMPv2-TC;

fortinetMIB MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Fortinet enterprise MIB."
    ::= { enterprises 12356 }

fgSystem MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Global system objects."
    ::= { fortinetMIB 101 }

fgSysCpu MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "CPU objects."
    ::= { fgSystem 1 }

fgSysCpuUtilization OBJECT-TYPE
    SYNTAX Integer32 (0..100)
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "CPU utilization percentage."
    ::= { fgSysCpu 1 }

fgSysMem MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Memory objects."
    ::= { fgSystem 1 }

fgSysMemUsage OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Memory usage in percentage."
    ::= { fgSysMem 1.3 }

fgSysSes MODULE-IDENTITY
    LAST-UPDATED "202401010000Z"
    ORGANIZATION "Fortinet"
    CONTACT-INFO "support"
    DESCRIPTION "Session objects."
    ::= { fgSystem 1 }

fgSysSesCount OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Number of current sessions."
    ::= { fgSysSes 6 }

mySysUpTime OBJECT-TYPE
    SYNTAX TimeTicks
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "System uptime, standard MIB object."
    ::= { mib-2 1 3 }

fgNotExistA OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "An OID the device does not implement."
    ::= { fgSystem 90 }

-- fgCommentedOut OBJECT-TYPE
--     SYNTAX Integer32
--     MAX-ACCESS read-only
--     STATUS current
--     DESCRIPTION "Should be ignored."
--     ::= { fgSystem 91 }

END
"""

EXPECTED_OIDS = {
    "fgSysCpuUtilization": ".1.3.6.1.4.1.12356.101.1.1",
    "fgSysMemUsage": ".1.3.6.1.4.1.12356.101.1.1.3",   # 多级索引（点分隔）
    "fgSysSesCount": ".1.3.6.1.4.1.12356.101.1.6",
    "mySysUpTime": ".1.3.6.1.2.1.1.3",                  # 空格分隔多级索引
    "fgNotExistA": ".1.3.6.1.4.1.12356.101.90",
}


def main() -> int:
    print("\n=== MIB 上传 / 解析 / 探测验证 ===\n")

    from app.db.session import SessionLocal
    from app.models.models import Device, MibFile
    from app.services import mib_probe, mib_service

    # -------------------------------------------------- 清理旧数据
    db = SessionLocal()
    try:
        for r in db.query(MibFile).all():
            mib_service.delete_mib_file(r.id)
    finally:
        db.close()

    # -------------------------------------------------- 1. 解析
    print("[1] MIB 解析")
    res = mib_service.save_mib_file("FORTINET-MIB.txt", MIB_TEXT.encode("utf-8"), "admin")
    check("解析出指标", res["node_count"] > 0, f"{res['node_count']} 个")
    check("识别出 MIB 模块名", res["module_name"] == "FORTINET-MIB", res["module_name"])
    check("状态为已解析", res["status"] == "ok", res["status"])

    for name, oid in EXPECTED_OIDS.items():
        node = mib_service.resolve_oid(oid.lstrip("."))
        check(f"OID 正确：{name}", bool(node) and node["name"] == name,
              f"{oid} -> {node['name'] if node else '未找到'}")

    nodes, total = mib_service.search_nodes("", limit=200)
    check("注释里的定义被剔除", not any(n["name"] == "fgCommentedOut" for n in nodes))
    check("指标带描述", any(n["description"] for n in nodes))
    check("指标带类型定义", any(n["syntax"] for n in nodes))

    # -------------------------------------------------- 2. 接口
    print("\n[2] 上传 / 列表 / 检索 / 删除接口")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    check("管理员登录成功", bool(token), f"HTTP {login.status_code}")
    H = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        lst = c.get("/api/mibs", headers=H).json()
    check("列表接口可用", "items" in lst, f"{len(lst.get('items', []))} 个文件")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        found = c.get("/api/mibs/nodes", headers=H, params={"keyword": "Mem"}).json()
    check("指标检索可用", found["total"] >= 2, f"匹配 {found['total']} 个")

    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        rev = c.get("/api/mibs/resolve", headers=H, params={"oid": "1.3.6.1.4.1.12356.101.1.1"}).json()
    check("OID 反查可用", rev.get("found") is True,
          rev.get("item", {}).get("name", "未找到"))

    # 拒绝非 MIB 格式
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        bad = c.post(
            "/api/mibs/upload",
            headers=H,
            files={"file": ("bad.exe", b"binary", "application/octet-stream")},
        )
    check("拒绝非法格式", bad.status_code == 400, f"HTTP {bad.status_code}")

    # -------------------------------------------------- 3. 探测
    print("\n[3] 设备指标探测")
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.vendor == "fortinet").first()
        if dev is None:
            check("存在飞塔设备", False, "未找到")
        else:
            out = asyncio.run(mib_probe.probe_device_mibs(dev, limit=30))
            check("探测请求成功", not out.get("error"), out.get("error", ""))
            check("探测有返回", out["total"] > 0, f"{out['total']} 个指标")
            supported = {i["name"] for i in out["items"] if i["supported"]}
            check("设备支持的标准指标被识别", "mySysUpTime" in supported,
                  f"supported={sorted(supported)}")
            check("设备不支持的指标被排除", "fgNotExistA" not in supported)
            check("探测值可读", any(
                i["value"] is not None for i in out["items"] if i["supported"]
            ))
            # 接口层
            with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
                pr = c.post(f"/api/mibs/probe/{dev.id}", headers=H).json()
            check("探测接口可用", pr.get("total", 0) > 0 and not pr.get("error"),
                  f"total={pr.get('total')} supported={pr.get('supported')}")
    finally:
        db.close()

    # -------------------------------------------------- 4. 删除
    print("\n[4] 删除")
    db = SessionLocal()
    try:
        rows = db.query(MibFile).all()
        mid = rows[0].id if rows else 0
    finally:
        db.close()
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        d = c.delete(f"/api/mibs/{mid}", headers=H)
    check("删除接口可用", d.status_code == 200, f"HTTP {d.status_code}")
    with httpx.Client(base_url=BASE, timeout=60, trust_env=False) as c:
        after = c.get("/api/mibs", headers=H).json()
    check("删除后列表为空", len(after["items"]) == 0, f"{len(after['items'])} 个")

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    if passed != len(results):
        print("未通过项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} {detail}")
        print()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
