"""告警详情生成器。

把「指标 + 阈值」翻译成运维人员能直接定位问题的描述：
- 接口类告警指出**具体是哪个接口**、各接口的数值与排序
- 设备类告警给出当前值、阈值、差距与可能的关注点
- 同时产出结构化明细（JSON），供前端展开表格查看
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 指标中文名
METRIC_NAMES = {
    "cpu": "CPU 使用率",
    "mem": "内存使用率",
    "temperature": "设备温度",
    "uptime": "运行时长",
    "response_time": "SNMP 响应时延",
    "traffic": "接口流量",
    "util": "接口利用率",
    "utilization": "接口利用率",
    "interface_down": "Down 接口数",
    "interface_error": "接口错包数",
    "loss": "接口错包数",
    "offline": "设备可达性",
    "conn_count": "并发连接数",
    "conn_peak": "连接峰值",
    "csc_table_size": "连接状态表容量",
    "ap_count": "已关联 AP 数",
    "client_count": "已关联客户端数",
    "session_count": "并发会话数",
    "user_count": "登录用户数",
    "vpn_tunnel_count": "VPN 隧道数",
}

# 单位
METRIC_UNITS = {
    "cpu": "%",
    "mem": "%",
    "temperature": "℃",
    "uptime": "秒",
    "response_time": "ms",
    "traffic": "Mbps",
    "util": "%",
    "utilization": "%",
    "interface_down": "个",
    "interface_error": "个",
    "loss": "个",
    "conn_count": "条",
    "conn_peak": "条",
    "csc_table_size": "条",
    "ap_count": "个",
    "client_count": "个",
    "session_count": "条",
    "user_count": "个",
    "vpn_tunnel_count": "条",
}


def metric_name(metric: str) -> str:
    return METRIC_NAMES.get(metric, metric)


def metric_unit(metric: str) -> str:
    return METRIC_UNITS.get(metric, "")


def _fmt(value: Optional[float], unit: str = "") -> str:
    if value is None:
        return "—"
    if unit == "Mbps":
        return f"{value:.2f} Mbps"
    if unit == "%":
        return f"{value:.2f}%"
    if unit in ("秒",):
        return f"{int(value)} 秒"
    if unit == "ms":
        return f"{value:.2f} ms"
    if float(value).is_integer():
        return f"{int(value)}{unit}"
    return f"{value:.2f}{unit}"


def _iface_label(iface) -> str:
    """接口显示名：优先别名，其次描述，最后接口名。"""
    name = getattr(iface, "if_name", "") or ""
    descr = getattr(iface, "if_descr", "") or ""
    alias = getattr(iface, "if_alias", "") or ""
    for candidate in (alias, descr, name):
        if candidate:
            return candidate
    return f"接口 {getattr(iface, 'if_index', '?')}"


def build_detail(
    metric: str,
    value: Optional[float],
    threshold: float,
    operator: str,
    device,
    rule_name: str = "指标阈值规则",
    interfaces: Optional[List[Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """生成告警详细描述与结构化明细。

    返回 (描述文本, 明细字典)
    """
    unit = metric_unit(metric)
    name = metric_name(metric)
    dev_name = device.name
    dev_ip = device.ip
    op_text = {
        ">": "高于", ">=": "达到或超过", "<": "低于", "<=": "低于或等于",
        "==": "等于", "!=": "不等于",
    }.get(operator, operator)

    detail: Dict[str, Any] = {
        "metric": metric,
        "metric_name": name,
        "value": value,
        "threshold": threshold,
        "operator": operator,
        "unit": unit,
        "device": {"id": device.id, "name": device.name, "ip": device.ip},
        "items": [],
    }

    # ---------------- 接口类指标 ----------------
    if metric in ("interface_error", "loss", "util", "utilization", "traffic", "interface_down"):
        items: List[Dict[str, Any]] = []
        if interfaces:
            for itf in interfaces:
                if metric in ("interface_error", "loss"):
                    err = (itf.in_errors or 0) + (itf.out_errors or 0)
                    if err > 0:
                        items.append({
                            "interface": _iface_label(itf),
                            "if_name": itf.if_name or itf.if_descr or "",
                            "value": err,
                            "in_errors": itf.in_errors or 0,
                            "out_errors": itf.out_errors or 0,
                            "speed_mbps": round((itf.speed_bps or 0) / 1_000_000, 2),
                            "status": "UP" if itf.oper_status == 1 else "DOWN",
                        })
                elif metric in ("util", "utilization"):
                    # 记录全部接口，按利用率排序后取 Top，避免"告警触发但无明细"
                    items.append({
                        "interface": _iface_label(itf),
                        "if_name": itf.if_name or itf.if_descr or "",
                        "value": round(itf.utilization or 0, 2),
                        "in_mbps": round((itf.in_bps or 0) / 1_000_000, 3),
                        "out_mbps": round((itf.out_bps or 0) / 1_000_000, 3),
                        "speed_mbps": round((itf.speed_bps or 0) / 1_000_000, 2),
                        "status": "UP" if itf.oper_status == 1 else "DOWN",
                    })
                elif metric == "traffic":
                    peak = max(itf.in_bps or 0, itf.out_bps or 0) / 1_000_000
                    items.append({
                        "interface": _iface_label(itf),
                        "if_name": itf.if_name or itf.if_descr or "",
                        "value": round(peak, 2),
                        "in_mbps": round((itf.in_bps or 0) / 1_000_000, 3),
                        "out_mbps": round((itf.out_bps or 0) / 1_000_000, 3),
                        "speed_mbps": round((itf.speed_bps or 0) / 1_000_000, 2),
                        "status": "UP" if itf.oper_status == 1 else "DOWN",
                    })
                elif metric == "interface_down":
                    if itf.oper_status != 1:
                        items.append({
                            "interface": _iface_label(itf),
                            "if_name": itf.if_name or itf.if_descr or "",
                            "value": 1,
                            "status": "DOWN",
                            "speed_mbps": round((itf.speed_bps or 0) / 1_000_000, 2),
                        })

        # 排序：错包/利用率/流量降序，Down 接口按接口名
        if metric != "interface_down":
            items.sort(key=lambda x: x.get("value") or 0, reverse=True)
        detail["items"] = items[:20]

        if not items:
            text = (
                f"设备 {dev_name}({dev_ip}) 的{name}触发告警："
                f"当前值 {_fmt(value, unit)}，{op_text}阈值 {threshold:g}{unit}。"
                "（本次采样未捕获到具体接口明细）"
            )
            return text, detail

        if metric in ("interface_error", "loss"):
            top = items[0]
            text = (
                f"设备 {dev_name}({dev_ip}) 存在接口错包："
                f"共 {len(items)} 个接口有错包，合计 {int(value or 0)} 个。\n"
                f"错包最多的接口是 {top['interface']}（{top['value']} 个"
                f"，入向 {top.get('in_errors', 0)} / 出向 {top.get('out_errors', 0)}，"
                f"速率 {top.get('speed_mbps', 0)}Mbps，状态 {top.get('status')}）。\n"
                f"触发规则「{rule_name}」条件 {op_text} {threshold:g}{unit}。"
            )
            if len(items) > 1:
                others = "、".join(
                    f"{i['interface']}({i['value']})" for i in items[1:4]
                )
                text += f"\n其他异常接口：{others}"
            detail["summary"] = f"{len(items)} 个接口存在错包"
        elif metric in ("util", "utilization"):
            top = items[0]
            text = (
                f"设备 {dev_name}({dev_ip}) 接口利用率超阈值："
                f"最高为 {top['interface']}"
                f"（{top['value']:.2f}%，速率 {top.get('speed_mbps', 0)}Mbps，"
                f"入向 {top.get('in_mbps', 0)}Mbps / 出向 {top.get('out_mbps', 0)}Mbps，"
                f"状态 {top.get('status')}）。\n"
                f"触发规则「{rule_name}」条件 {op_text} {threshold:g}{unit}。"
            )
            over = [i for i in items if (i.get("value") or 0) > threshold]
            if over:
                text += (
                    f"\n超过阈值的接口（{len(over)} 个）："
                    + "、".join(f"{i['interface']}({i['value']:.2f}%)" for i in over[:5])
                )
                detail["summary"] = f"{len(over)} 个接口利用率超阈值"
            else:
                text += f"\n当前采样中最高的接口为 {top['interface']}（{top['value']:.2f}%）"
                detail["summary"] = f"最高接口利用率 {top['value']:.2f}%"
        elif metric == "traffic":
            top = items[0]
            text = (
                f"设备 {dev_name}({dev_ip}) 接口流量超阈值："
                f"峰值接口为 {top['interface']}，流量 {top['value']:.2f}Mbps"
                f"（入向 {top.get('in_mbps', 0)}Mbps / 出向 {top.get('out_mbps', 0)}Mbps，"
                f"带宽 {top.get('speed_mbps', 0)}Mbps，状态 {top.get('status')}）。\n"
                f"触发规则「{rule_name}」条件 {op_text} {threshold:g}{unit}。"
            )
            over = [i for i in items if (i.get("value") or 0) > threshold]
            if over:
                text += (
                    f"\n超阈值接口（{len(over)} 个）："
                    + "、".join(f"{i['interface']}({i['value']:.2f}Mbps)" for i in over[:5])
                )
                detail["summary"] = f"{len(over)} 个接口流量超阈值"
            else:
                detail["summary"] = f"峰值接口流量 {top['value']:.2f}Mbps"
        else:  # interface_down
            names = "、".join(i["interface"] for i in items[:6])
            text = (
                f"设备 {dev_name}({dev_ip}) 有 {len(items)} 个接口处于 DOWN 状态：{names}。\n"
                f"触发规则「{rule_name}」条件 {op_text} {threshold:g}{unit}。"
            )
            detail["summary"] = f"{len(items)} 个接口 DOWN"
        return text, detail

    # ---------------- 设备类指标 ----------------
    gap = ""
    if value is not None:
        try:
            diff = float(value) - float(threshold)
            if abs(diff) > 1e-9:
                gap = f"，超出阈值 {abs(diff):.2f}{unit}"
        except (TypeError, ValueError):
            pass

    text = (
        f"设备 {dev_name}({dev_ip}) 的{name}触发告警："
        f"当前值 {_fmt(value, unit)}，{op_text}阈值 {threshold:g}{unit}{gap}。\n"
        f"触发规则「{rule_name}」。"
    )
    detail["summary"] = f"{name} {_fmt(value, unit)}"
    return text, detail
