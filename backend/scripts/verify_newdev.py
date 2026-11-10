"""新增设备类型（飞塔 / Cisco ASA / Cisco WLC）验证脚本。

覆盖：
  1. 厂商画像与别名归一化
  2. 厂商特有指标的 SNMP OID 与 SSH 解析
  3. 基线巡检模板覆盖（三类设备各有专用模板）
  4. 巡检按厂商自动匹配模板
  5. 配置备份命令正确性

运行：python -m scripts.verify_newdev
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("\n=== 新增设备类型验证（飞塔 / ASA / WLC）===\n")

    from app.collectors.ssh import parse_extra_metric
    from app.collectors.vendors import VENDORS, resolve_vendor

    print("[1] 厂商画像与别名")
    for key, label in (
        ("cisco_asa", "Cisco ASA 防火墙"),
        ("cisco_wlc", "Cisco WLC 无线控制器"),
        ("fortinet", "飞塔 Fortinet FortiGate"),
    ):
        check(f"{label} 已注册", key in VENDORS, VENDORS[key].label if key in VENDORS else "-")
    check("别名 asa → cisco_asa", resolve_vendor("asa").key == "cisco_asa")
    check("别名 pix → cisco_asa", resolve_vendor("pix").key == "cisco_asa")
    check("别名 wlc → cisco_wlc", resolve_vendor("wlc").key == "cisco_wlc")
    check("别名 fortios → fortinet", resolve_vendor("fortios").key == "fortinet")
    check("别名 飞塔 → fortinet", resolve_vendor("飞塔").key == "fortinet")
    check("厂商总数为 8", len(VENDORS) == 8, f"{len(VENDORS)} 个")

    print("\n[2] 厂商特有指标定义")
    asa = VENDORS["cisco_asa"]
    wlc = VENDORS["cisco_wlc"]
    fgt = VENDORS["fortinet"]
    check("ASA 连接数 OID", "conn_count" in asa.extra_oids, str(asa.extra_oids.get("conn_count")))
    check("ASA CSC 表项 OID", "csc_table_size" in asa.extra_oids)
    check("WLC AP 数 OID", "ap_count" in wlc.extra_oids, str(wlc.extra_oids.get("ap_count")))
    check("WLC 客户端数 OID", "client_count" in wlc.extra_oids)
    check("飞塔会话数 OID", "session_count" in fgt.extra_oids, str(fgt.extra_oids.get("session_count")))
    check("飞塔 VPN 隧道数 OID", "vpn_tunnel_count" in fgt.extra_oids)
    check("额外指标进入 sys OID 集合", "1.3.6.1.4.1.12356.101.1.2.6.0" in fgt.all_sys_oids())
    check("额外指标有 SSH 兜底命令", bool(asa.extra_ssh_cmds and wlc.extra_ssh_cmds and fgt.extra_ssh_cmds))

    print("\n[3] 厂商输出解析")
    asa_text = "Current number of connections: 1234"
    check("ASA 连接数解析", parse_extra_metric(asa_text, "conn_count") == 1234.0,
          str(parse_extra_metric(asa_text, "conn_count")))
    check("ASA 'X connections' 解析", parse_extra_metric("567 connections", "conn_count") == 567.0)
    wlc_text = "Number of APs: 12"
    check("WLC AP 数解析", parse_extra_metric(wlc_text, "ap_count") == 12.0,
          str(parse_extra_metric(wlc_text, "ap_count")))
    wlc_client = "Number of clients: 340"
    check("WLC 客户端数解析", parse_extra_metric(wlc_client, "client_count") == 340.0)
    fgt_text = "CPU states: 1% user 2% system 97% idle\nMemory: 4096MB total\nCurrent sessions: 25000"
    check("飞塔会话数解析", parse_extra_metric(fgt_text, "session_count") == 25000.0,
          str(parse_extra_metric(fgt_text, "session_count")))
    check("不匹配时返回 None", parse_extra_metric("random text", "conn_count") is None)

    print("\n[4] 配置备份命令")
    check("ASA 备份命令", asa.backup_cmd == ["show running-config"], str(asa.backup_cmd))
    check("WLC 备份命令", wlc.backup_cmd == ["show running-config"], str(wlc.backup_cmd))
    check(
        "飞塔备份命令含诊断快照+配置导出",
        len(fgt.backup_cmd) == 2
        and "config-file save" in fgt.backup_cmd[0]
        and "full-configuration" in fgt.backup_cmd[1],
        str(fgt.backup_cmd),
    )

    print("\n[5] 基线巡检模板覆盖")
    from app.services.inspection import DEFAULT_TEMPLATES, ensure_default_templates

    by_vendor = {t["vendor"]: t for t in DEFAULT_TEMPLATES}
    for vendor, name in (
        ("cisco_asa", "Cisco ASA 防火墙基线巡检"),
        ("cisco_wlc", "Cisco WLC 无线控制器基线巡检"),
        ("fortinet", "飞塔 FortiGate 防火墙基线巡检"),
    ):
        tpl = by_vendor.get(vendor)
        check(f"{name} 模板存在", tpl is not None, name)
        if tpl:
            metrics = {i["metric"] for i in tpl["items"]}
            check(f"  {vendor} 模板含 CPU/内存/可达性",
                  {"cpu", "mem", "reachability"}.issubset(metrics), str(sorted(metrics)))
            check(f"  {vendor} 模板含厂商特有指标",
                  bool(metrics & {"conn_count", "ap_count", "session_count"}),
                  str(sorted(metrics & {"conn_count", "ap_count", "session_count"})))

    print("\n[6] 巡检按厂商自动匹配模板")
    from app.db.session import Base, SessionLocal, engine

    import app.models.models  # noqa: F401  触发模型注册

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        created = ensure_default_templates(db)
        check("默认模板已初始化", created >= 0, f"新增 {created} 个")
        from app.models.models import InspectionTemplate

        rows = db.query(InspectionTemplate).all()
        vendors = {r.vendor for r in rows}
        check("数据库含三类新厂商模板",
              {"cisco_asa", "cisco_wlc", "fortinet"}.issubset(vendors), str(sorted(vendors)))
        total = len(rows)
        check("模板总数为 7", total == 7, f"{total} 个")
    finally:
        db.close()

    print("\n[7] 巡检指标覆盖（额外指标可被判定）")
    from app.services.inspection import _judge

    item = {"key": "conn_count", "title": "当前连接数", "metric": "conn_count",
            "operator": "<=", "threshold": 5000, "unit": "条", "level": "warning"}
    r1 = _judge(item, 1200.0)
    r2 = _judge(item, 9000.0)
    r3 = _judge(item, None)
    check("额外指标在阈值内判定通过", r1["status"] == "pass", r1["message"])
    check("额外指标超阈值判定失败", r2["status"] == "fail", r2["message"])
    check("额外指标缺失判定跳过", r3["status"] == "skip", r3["message"])

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
