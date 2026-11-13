"""验证端口配置持久化与防火墙会话 OID 现状。

运行：python -m scripts.verify_port_and_sessions（需先启动后端服务）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = "http://127.0.0.1:8900"
PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 端口配置 / 防火墙会话 OID 验证 ===\n")

    # ------------------------------------------------ 端口配置
    print("[1] gNMI / MDT 端口配置（用户显式设置不自动回退）")
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        login = c.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123", "source": "local"},
        )
    token = login.json().get("access_token", "")
    H = {"Authorization": f"Bearer {token}"}
    check("管理员登录", bool(token))

    import time

    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        resp = c.get("/api/settings", headers=H).json()
        cur = resp.get("items", resp)  # 设置在 items 里
        original = cur.get("collector.gnmi_port")

        # 设置端口 15800
        cur["collector.gnmi_port"] = 15800
        r = c.put("/api/settings", headers=H, json=cur)
        time.sleep(4)
        st = c.get("/api/telemetry/status", headers=H).json()
        check("设置端口立即生效", st.get("collector_port") == 15800,
              f"collector_port={st.get('collector_port')}")
        check("用户显式端口不算回退", st.get("collector_port_fallback") is False,
              f"fallback={st.get('collector_port_fallback')}")

        # 恢复原端口
        cur["collector.gnmi_port"] = original or 57400
        c.put("/api/settings", headers=H, json=cur)
        time.sleep(4)

        # mdt_port 设置项存在
        resp2 = c.get("/api/settings", headers=H).json()
        cur2 = resp2.get("items", resp2)
        check("Cisco MDT 端口设置项存在", "collector.mdt_port" in cur2,
              f"mdt_port={cur2.get('collector.mdt_port')}")

    # ------------------------------------------------ 防火墙会话 OID
    print("\n[2] 防火墙会话数 SNMP OID 现状")
    from app.collectors.snmp import SnmpClient, SnmpTarget
    from app.collectors.vendors import VENDORS
    from app.db.session import SessionLocal
    from app.models.models import Device

    fg = VENDORS["fortinet"]
    asa = VENDORS["cisco_asa"]

    # 平台侧 OID 配置正确性（代码层面）
    check("飞塔 session_count OID 已配置",
          "1.3.6.1.4.1.12356.101.1.2.6.0" in fg.extra_oids.get("session_count", []),
          "fgSysSesCount")
    check("ASA conn_count OID 已配置",
          "1.3.6.1.4.1.9.9.166.1.2.1.1.0" in asa.extra_oids.get("conn_count", []),
          "ciscoASAConnCount")

    # 设备实测
    db = SessionLocal()
    try:
        devices = {d.name: d for d in db.query(Device).all()}

        async def probe(dev, oids):
            t = SnmpTarget(
                host=dev.ip, port=dev.snmp_port or 161,
                community=dev.snmp_community or "public",
                version=(dev.snmp_version or "2c"), timeout=5.0, retries=0,
            )
            c = SnmpClient(t)
            r = await c.get(list(oids))
            return r.values

        for name in ("szhoftg", "szdc"):
            d = devices.get(name)
            if not d:
                continue
            vals = asyncio.run(
                probe(d, fg.extra_oids["session_count"] + ["1.3.6.1.2.1.1.1.0"])
            )
            ses = vals.get("1.3.6.1.4.1.12356.101.1.2.6.0")
            sysdescr = vals.get("1.3.6.1.2.1.1.1.0")
            ok = ses is not None and "No Such" not in str(ses)
            check(
                f"{name} fgSysSesCount 可取",
                ok,
                f"值={ses}；sysDescr={sysdescr!r}（空=设备 SNMP 视图受限）",
            )

        d = devices.get("szho-asa")
        if d:
            vals = asyncio.run(
                probe(d, asa.extra_oids["conn_count"] + ["1.3.6.1.4.1.9.9.43.1.1.4.0"])
            )
            conn = vals.get("1.3.6.1.4.1.9.9.166.1.2.1.1.0")
            cpu = vals.get("1.3.6.1.4.1.9.9.43.1.1.4.0")
            ok = conn is not None and "No Such" not in str(conn)
            check(
                "szho-asa ciscoASAConnCount 可取",
                ok,
                f"值={conn}；对照 CPU={cpu}（CPU 可取说明 SNMP 本身通）",
            )
    finally:
        db.close()

    passed = sum(1 for _, c, _ in results if c)
    print(f"\n=== 结果：{passed}/{len(results)} 通过 ===\n")
    if passed != len(results):
        print("未通过项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name} {detail}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
