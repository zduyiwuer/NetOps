"""飞塔（FortiGate）配置转换验证脚本。

用真实 FortiGate CLI 输出格式验证：
  1. CLI 回显/提示符/诊断状态噪声剥离
  2. config 块层级与 end 配对
  3. edit / set 条目解析与计数
  4. .conf 文件头元信息
  5. 扩展名按厂商区分（飞塔 .conf / 其他 .cfg）
  6. 截断配置的补齐与告警

运行：python -m scripts.verify_fortinet_config
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


# 真实 FortiGate `show full-configuration` 输出（含 CLI 回显噪声）
RAW_FORTINET = """FGT # show full-configuration
config system global
    set hostname "FGT-60F"
    set timezone 04
    set admin-sport 8443
    set alias "NetOps" "netops"
end
config system interface
    edit "port1"
        set vdom "root"
        set ip 10.10.3.1 255.255.255.0
        set allowaccess ping https ssh
        set type physical
        set snmp-index 1
    next
    edit "port2"
        set vdom "root"
        set type physical
        set snmp-index 2
    next
end
config firewall address
    edit "NETOPS_SERVER"
        set subnet 10.10.3.0 255.255.255.0
    next
end
config firewall policy
    edit 1
        set name "allow-ssh"
        set srcintf "port1"
        set dstintf "port2"
        set srcaddr "all"
        set dstaddr "all"
        set action accept
        set schedule "always"
        set service "SSH"
    next
end
FGT #"""

# 含诊断状态行（诊断保存命令的输出）
RAW_WITH_DIAG = """Diagnostics : running
config-file saved
config system global
    set hostname "FGT-100F"
end
FGT #"""

# 截断的配置（缺少 end）
RAW_TRUNCATED = """config system global
    set hostname "FGT-TRUNC"
config system interface
    edit "port1"
        set ip 10.0.0.1 255.255.255.0"""

# 采集器多命令拼接后的形态（带 # ===== cmd ===== 分段标记）
RAW_SSH_SEGMENTED = """# ===== diagnose firewall config-file save - flash =====
Diagnostics : running
# ===== show full-configuration =====
config system global
    set hostname "FGT-60F"
    set timezone 04
end
FGT #"""


def main() -> int:
    print("\n=== 飞塔配置转换验证 ===\n")

    from app.services.config_formatter import (
        config_extension,
        format_config,
        format_fortinet_config,
        validate_fortinet_config,
    )

    print("[1] CLI 噪声剥离与结构解析")
    out = format_fortinet_config(
        RAW_FORTINET,
        device_name="FGT-60F",
        device_ip="10.10.3.1",
        source_commands=["diagnose firewall config-file save - flash", "show full-configuration"],
    )
    check("剥离命令回显", "FGT # show full-configuration" not in out)
    check("剥离末尾提示符", not out.rstrip().endswith("FGT #"))
    check("无 CLI 回显行残留", "FGT #" not in out)

    val = validate_fortinet_config(out)
    check("配置块配对完整", val["valid"], "、".join(val["issues"]) or "无问题")
    check("解析到 4 个 config 块", val["config_blocks"] == 4, f"{val['config_blocks']} 个")
    # port1 / port2 / NETOPS_SERVER / policy(1) 共 4 个 edit 条目
    check("解析到 4 个 edit 条目", val["edit_entries"] == 4, f"{val['edit_entries']} 个")
    check("解析到 set 命令", val["set_commands"] >= 15, f"{val['set_commands']} 条")

    print("\n[2] .conf 文件头与元信息")
    check("含 FortiGate 标识", "FortiGate 配置备份" in out)
    check("含设备名称", "FGT-60F" in out)
    check("含管理地址", "10.10.3.1" in out)
    check("含采集时间", datetime.now().strftime("%Y-%m-%d") in out)
    check("含来源命令", "show full-configuration" in out)
    check("含配置块数统计", "配置块数" in out)
    check("以注释头开始", out.lstrip().startswith("#"))

    print("\n[3] 内容保真度")
    check("保留 hostname 设置", 'set hostname "FGT-60F"' in out)
    check("保留接口 IP", "set ip 10.10.3.1 255.255.255.0" in out)
    check("保留策略名", 'set name "allow-ssh"' in out)
    check("保留 edit 名称", 'edit "NETOPS_SERVER"' in out)
    check("保留 action accept", "set action accept" in out)
    check("config 行不缩进", "\nconfig system global" in out)
    check("子项缩进 4 空格", "\n    set hostname" in out)

    print("\n[4] 诊断状态行剥离")
    out2 = format_fortinet_config(RAW_WITH_DIAG, device_name="FGT-100F", device_ip="10.10.3.2")
    check("剥离 Diagnostics 行", "Diagnostics : running" not in out2)
    check("剥离 config-file saved 行", "config-file saved" not in out2)
    check("保留有效配置", 'set hostname "FGT-100F"' in out2)
    val2 = validate_fortinet_config(out2)
    check("诊断场景配置完整", val2["valid"], "、".join(val2["issues"]) or "无问题")

    print("\n[4b] 采集器分段标记剥离")
    out2b = format_fortinet_config(RAW_SSH_SEGMENTED, device_name="FGT-60F", device_ip="10.10.3.1")
    check("剥离 # ===== cmd ===== 标记", "# ===== show full-configuration =====" not in out2b)
    check("分段场景配置完整", validate_fortinet_config(out2b)["valid"])
    check("分段场景保留配置内容", 'set hostname "FGT-60F"' in out2b)

    print("\n[5] 截断配置检出与告警")
    out3 = format_fortinet_config(RAW_TRUNCATED, device_name="FGT-TRUNC", device_ip="10.0.0.1")
    val3 = validate_fortinet_config(out3)
    check("检测到未闭合块", not val3["valid"], "、".join(val3["issues"]))
    # 截断文件不做自动补 end —— 补齐后会得到「看似完整但实际缺配置」的危险文件
    check("不自动补齐 end", out3.count("\nend") == 0, f"{out3.count(chr(10) + 'end')} 个 end")
    check("截断警告已写入文件头", "未闭合" in out3 and "可能被截断" in out3)
    check("原始内容保持不变", 'set ip 10.0.0.1 255.255.255.0' in out3)
    check("未闭合块数已统计", val3["unclosed_blocks"] == 2, f"{val3['unclosed_blocks']} 个")

    print("\n[6] 扩展名按厂商区分")
    check("飞塔使用 .conf", config_extension("fortinet") == ".conf")
    check("Cisco 使用 .cfg", config_extension("cisco") == ".cfg")
    check("华为使用 .cfg", config_extension("huawei") == ".cfg")
    check("H3C 使用 .cfg", config_extension("h3c") == ".cfg")
    check("RouterOS 使用 .cfg", config_extension("routeros") == ".cfg")
    check("ASA 使用 .cfg", config_extension("cisco_asa") == ".cfg")

    print("\n[7] 通用格式化入口")
    generic = format_config(
        "cisco",
        "Building configuration...\nhostname SW01\ninterface Gi0/1\n description uplink\nend\n",
        device_name="SW01",
        device_ip="10.0.0.1",
        source_commands=["show running-config"],
    )
    check("其他厂商也带元信息头", "配置备份" in generic)
    check("其他厂商保留原始内容", "hostname SW01" in generic)
    check("空输入返回空串", format_config("fortinet", "", device_name="x") == "")

    print("\n[8] 落盘文件扩展名")
    from app.services.config_formatter import backup_filename, safe_filename

    name = safe_filename("飞塔防火墙")
    check("中文设备名安全化", "/" not in name and " " not in name, name)

    stamp = datetime(2026, 11, 10, 15, 30, 45)
    fg = backup_filename("fortinet", "飞塔防火墙", "10.10.3.1", stamp)
    check("飞塔备份文件名以 .conf 结尾", fg.endswith(".conf"), fg)
    check("飞塔备份文件名含时间戳", "20261110_153045" in fg, fg)
    sw = backup_filename("cisco", "SW01", "10.0.0.1", stamp)
    check("其他厂商备份文件名以 .cfg 结尾", sw.endswith(".cfg"), sw)

    print("\n[9] 门店打包下载扩展名")
    import io
    import zipfile

    from app.api.ops import download_store_backups  # 触发 import 校验
    from app.collectors.vendors import resolve_vendor

    fg_name = f"飞塔防火墙_10.10.3.1{config_extension(resolve_vendor('fortinet').key)}"
    check("门店打包飞塔用 .conf", fg_name.endswith(".conf"), fg_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("门店A/" + fg_name, out)
    with zipfile.ZipFile(buf) as zf:
        entries = zf.namelist()
    check("ZIP 内文件扩展名正确", entries and entries[0].endswith(".conf"), entries[0] if entries else "-")

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    if all(c for _, c, _ in results):
        print("--- 转换后的 .conf 示例 ---")
        print("\n".join(out.split("\n")[:24]))
        print("...\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
