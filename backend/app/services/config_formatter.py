"""设备配置格式转换器。

把各厂商的 CLI 原始输出整理为规范的、可直接回灌设备的配置文件。

目前实现：
- **FortiGate（飞塔）**：`show full-configuration` 输出整理为标准 .conf
  - 剥离 CLI 回显噪声（命令回显、提示符、诊断状态行）
  - 规范化缩进（FortiOS 用 4 空格层级）
  - 补全元信息注释头（设备、IP、采集时间、来源命令）
  - 校验 config 块配对完整性（config ... end）
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# FortiOS 配置树形结构
_FORTINET_CONFIG_RE = re.compile(r"^config\s+([\w\-/\.]+)(.*)$")
_FORTINET_EDIT_RE = re.compile(r'^\s*edit\s+"?(.*?)"?\s*$')
_FORTINET_SET_RE = re.compile(r"^\s*set\s+")
_FORTINET_NEXT_RE = re.compile(r"^\s*next\s*$")
_FORTINET_END_RE = re.compile(r"^\s*end\s*$")
_FORTINET_MOVE_RE = re.compile(r"^\s*move\s+")
_FORTINET_DELETE_RE = re.compile(r"^\s*delete\s+")
_FORTINET_SPLIT_RE = re.compile(r"^\s*(?:config|edit|set|next|end|move|delete|rename)\b", re.IGNORECASE)


def _strip_cli_noise(lines: List[str]) -> List[str]:
    """剥离 CLI 回显与提示符噪声。

    FortiGate SSH 会话常见噪声：
    - 命令回显：`FGT # show full-configuration`
    - 提示符：`FGT #` / `FGT(config) #`
    - 诊断状态行：`Diagnostics : ...` / `config-file saved`
    - 采集器分段标记：`# ===== show full-configuration =====`
    - 分隔线：`---------`
    - 空行
    """
    out: List[str] = []
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        # 提示符 + 命令回显
        if re.match(r"^[A-Za-z0-9_\-\.]+[\(\)][^\s]*\s*#\s*\S", stripped) or re.match(
            r"^[A-Za-z0-9_\-\.]+\s*#\s*\S", stripped
        ):
            continue
        # 纯提示符行
        if re.match(r"^[A-Za-z0-9_\-\.]+(\([\w\-]+\))?\s*#\s*$", stripped):
            continue
        # 采集器分段标记（# ===== 命令 =====），来源命令已记录在文件头
        if re.match(r"^#\s*=+\s*.*?\s*=+\s*$", stripped):
            continue
        # 诊断状态行
        if stripped.startswith("Diagnostics") or "config-file saved" in stripped.lower():
            continue
        # 分隔线
        if set(stripped) == {"-"} and len(stripped) >= 3:
            continue
        out.append(line)
    return out


def format_fortinet_config(
    raw: str,
    device_name: str = "",
    device_ip: str = "",
    source_commands: Optional[List[str]] = None,
    collected_at: Optional[datetime] = None,
) -> str:
    """把飞塔原始输出转换为规范 .conf 文件内容。

    产出形如：

        # ============================================================
        # FortiGate 配置备份
        # 设备名称 : FGT-60F
        # 管理地址 : 10.10.3.1
        # 采集时间 : 2026-11-10 15:20:30
        # 来源命令 : show full-configuration
        # ============================================================
        config system global
            set hostname "FGT-60F"
        end
        ...
    """
    if not raw or not raw.strip():
        return ""

    lines = _strip_cli_noise(raw.splitlines())

    # 校验 config 块配对
    depth = 0
    config_blocks = 0
    for line in lines:
        if _FORTINET_CONFIG_RE.match(line.strip()):
            config_blocks += 1
            depth += 1
        elif _FORTINET_END_RE.match(line):
            depth = max(0, depth - 1)

    stamp = (collected_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    cmds = " / ".join(source_commands or ["show full-configuration"])

    header = [
        "# " + "=" * 60,
        "# FortiGate 配置备份（FortiOS configuration）",
        f"# 设备名称 : {device_name or '-'}",
        f"# 管理地址 : {device_ip or '-'}",
        f"# 采集时间 : {stamp}",
        f"# 来源命令 : {cmds}",
        f"# 配置块数 : {config_blocks}",
        "# " + "=" * 60,
        "",
    ]

    body: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 顶层 config 行不缩进，其余按 FortiOS 4 空格层级处理
        if _FORTINET_CONFIG_RE.match(stripped):
            body.append(stripped)
        else:
            # 保留原始相对缩进但归一化为 4 空格制表符
            indent = len(line) - len(line.lstrip())
            level = indent // 4 if indent else 0
            body.append("    " * level + stripped)

    # 截断的配置文件不自动补 end —— 补齐后会产生「看似完整但实际缺配置」的
    # 危险文件。保持原样，由 validate_fortinet_config 检出并在备份记录中标注。
    if depth > 0:
        header.insert(
            7,
            f"# [警告] 检测到 {depth} 个配置块未闭合，配置可能被截断，请核查采集完整性",
        )

    footer = ["", "# " + "=" * 60, "# 备份结束"]
    return "\n".join(header + body + footer) + "\n"


# ---------------------------------------------------------------- 通用
def format_config(
    vendor_key: str,
    raw: str,
    device_name: str = "",
    device_ip: str = "",
    source_commands: Optional[List[str]] = None,
    collected_at: Optional[datetime] = None,
) -> str:
    """按厂商格式化配置内容。"""
    if not raw or not raw.strip():
        return ""
    if vendor_key == "fortinet":
        return format_fortinet_config(
            raw,
            device_name=device_name,
            device_ip=device_ip,
            source_commands=source_commands,
            collected_at=collected_at,
        )
    # 其他厂商：仅剥离明显的噪声并补注释头
    lines = _strip_cli_noise(raw.splitlines())
    if not lines:
        return ""
    stamp = (collected_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    cmds = " / ".join(source_commands or [])
    header = [
        "# " + "=" * 60,
        f"# {device_name or '设备'} 配置备份",
        f"# 管理地址 : {device_ip or '-'}",
        f"# 采集时间 : {stamp}",
        f"# 来源命令 : {cmds}",
        "# " + "=" * 60,
        "",
    ]
    return "\n".join(header + lines) + "\n"


def config_extension(vendor_key: str) -> str:
    """按厂商返回配置文件扩展名。"""
    return ".conf" if vendor_key == "fortinet" else ".cfg"


def safe_filename(text: str) -> str:
    """把设备名等文本转换为文件名安全的片段。"""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text or "")


def backup_filename(
    vendor_key: str,
    device_name: str,
    device_ip: str,
    stamp: Optional[datetime] = None,
) -> str:
    """生成备份文件名：``设备名_IP_时间戳.扩展名``。

    扩展名按厂商区分——飞塔为 ``.conf``，其余厂商为 ``.cfg``。
    """
    moment = stamp or datetime.now()
    ext = config_extension(vendor_key)
    return f"{safe_filename(device_name)}_{device_ip}_{moment.strftime('%Y%m%d_%H%M%S')}{ext}"


def validate_fortinet_config(content: str) -> Dict[str, Any]:
    """校验转换后的飞塔配置是否完整可用。"""
    result: Dict[str, Any] = {
        "valid": True,
        "config_blocks": 0,
        "edit_entries": 0,
        "set_commands": 0,
        "unclosed_blocks": 0,
        "issues": [],
    }
    depth = 0
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            continue
        if _FORTINET_CONFIG_RE.match(line):
            result["config_blocks"] += 1
            depth += 1
        elif _FORTINET_END_RE.match(line):
            depth -= 1
            if depth < 0:
                result["issues"].append("存在多余的 end")
                result["valid"] = False
                depth = 0
        elif _FORTINET_EDIT_RE.match(line):
            result["edit_entries"] += 1
        elif _FORTINET_SET_RE.match(line):
            result["set_commands"] += 1
    if depth != 0:
        result["unclosed_blocks"] = depth
        result["issues"].append(f"有 {depth} 个配置块未闭合")
        result["valid"] = False
    if result["config_blocks"] == 0:
        result["issues"].append("未解析到任何 config 块")
        result["valid"] = False
    return result
