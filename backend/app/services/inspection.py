"""基线巡检引擎：模板化检查项，采集 → 判定 → 结果落库 → 生成报告。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.collectors.engine import Collector
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.models import Device, InspectionTask, InspectionTemplate, Store

logger = logging.getLogger("netops.inspection")


# ---------------------------------------------------------------- 默认模板
DEFAULT_TEMPLATES: List[Dict[str, Any]] = [
    {
        "name": "通用交换机基线巡检",
        "vendor": "",
        "description": "适用于所有厂商的通用基线检查项",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "CPU 使用率应低于 80%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 85, "unit": "%",
             "level": "critical", "desc": "内存使用率应低于 85%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "temp", "title": "设备温度", "metric": "temperature", "operator": "<=", "threshold": 65, "unit": "℃",
             "level": "warning", "desc": "设备温度应低于 65℃"},
            {"key": "iface_down", "title": "接口异常数量", "metric": "interface_down", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "不应存在 down 状态的接口"},
            {"key": "iface_error", "title": "接口错包总数", "metric": "interface_error", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "接口不应有错包"},
            {"key": "util", "title": "接口最大利用率", "metric": "utilization", "operator": "<=", "threshold": 85, "unit": "%",
             "level": "warning", "desc": "接口利用率应低于 85%"},
        ],
    },
    {
        "name": "H3C / 华为 基线巡检",
        "vendor": "h3c",
        "description": "H3C Comware 与华为 VRP 通用基线",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 70, "unit": "%",
             "level": "critical", "desc": "CPU 使用率应低于 70%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "iface_down", "title": "接口异常数量", "metric": "interface_down", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "不应存在 down 状态的接口"},
            {"key": "util", "title": "接口最大利用率", "metric": "utilization", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "warning", "desc": "接口利用率应低于 80%"},
            {"key": "uptime", "title": "运行时长", "metric": "uptime", "operator": ">=", "threshold": 86400,
             "unit": "秒", "level": "info", "desc": "设备运行时间应大于 1 天（确认无频繁重启）"},
        ],
    },
    {
        "name": "Cisco 基线巡检",
        "vendor": "cisco",
        "description": "Cisco IOS 通用基线",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 75, "unit": "%",
             "level": "critical", "desc": "CPU 使用率应低于 75%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "iface_error", "title": "接口错包总数", "metric": "interface_error", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "接口不应有错包"},
            {"key": "util", "title": "接口最大利用率", "metric": "utilization", "operator": "<=", "threshold": 85, "unit": "%",
             "level": "warning", "desc": "接口利用率应低于 85%"},
        ],
    },
    {
        "name": "MikroTik RouterOS 基线巡检",
        "vendor": "routeros",
        "description": "RouterOS 基线",
        "items": [
            {"key": "cpu", "title": "CPU 负载", "metric": "cpu", "operator": "<=", "threshold": 60, "unit": "%",
             "level": "critical", "desc": "CPU 负载应低于 60%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "iface_down", "title": "接口异常数量", "metric": "interface_down", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "不应存在 down 状态的接口"},
        ],
    },
    {
        "name": "Cisco ASA 防火墙基线巡检",
        "vendor": "cisco_asa",
        "description": "Cisco ASA 防火墙专用基线：CPU/内存、连接数、接口与流量",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 70, "unit": "%",
             "level": "critical", "desc": "ASA CPU 使用率应低于 70%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "ASA 内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "conn_count", "title": "当前连接数", "metric": "conn_count", "operator": "<=", "threshold": 5000,
             "unit": "条", "level": "warning", "desc": "并发连接数应低于 5000（按实际容量调整）"},
            {"key": "csc_table_size", "title": "连接状态表容量", "metric": "csc_table_size", "operator": "<=",
             "threshold": 100000, "unit": "条", "level": "warning", "desc": "CSC 表不应超出配置上限"},
            {"key": "iface_down", "title": "接口异常数量", "metric": "interface_down", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "不应存在 down 状态的接口"},
            {"key": "util", "title": "接口最大利用率", "metric": "utilization", "operator": "<=", "threshold": 80,
             "unit": "%", "level": "warning", "desc": "接口利用率应低于 80%"},
            {"key": "uptime", "title": "运行时长", "metric": "uptime", "operator": ">=", "threshold": 86400,
             "unit": "秒", "level": "info", "desc": "设备运行时间应大于 1 天（确认无频繁重启）"},
        ],
    },
    {
        "name": "Cisco WLC 无线控制器基线巡检",
        "vendor": "cisco_wlc",
        "description": "Cisco WLC 专用基线：CPU/内存、AP 与客户端数量",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 75, "unit": "%",
             "level": "critical", "desc": "WLC CPU 使用率应低于 75%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "WLC 内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "ap_count", "title": "已关联 AP 数", "metric": "ap_count", "operator": ">=", "threshold": 1,
             "unit": "个", "level": "info", "desc": "至少应有 1 个 AP 正常关联"},
            {"key": "client_count", "title": "已关联客户端数", "metric": "client_count", "operator": "<=",
             "threshold": 1000, "unit": "个", "level": "warning", "desc": "关联客户端数应低于容量上限"},
            {"key": "iface_error", "title": "接口错包总数", "metric": "interface_error", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "接口不应有错包"},
            {"key": "uptime", "title": "运行时长", "metric": "uptime", "operator": ">=", "threshold": 86400,
             "unit": "秒", "level": "info", "desc": "设备运行时间应大于 1 天（确认无频繁重启）"},
        ],
    },
    {
        "name": "飞塔 FortiGate 防火墙基线巡检",
        "vendor": "fortinet",
        "description": "飞塔 FortiGate 专用基线：CPU/内存、会话数、VPN 隧道与接口",
        "items": [
            {"key": "cpu", "title": "CPU 使用率", "metric": "cpu", "operator": "<=", "threshold": 70, "unit": "%",
             "level": "critical", "desc": "FortiGate CPU 使用率应低于 70%"},
            {"key": "mem", "title": "内存使用率", "metric": "mem", "operator": "<=", "threshold": 80, "unit": "%",
             "level": "critical", "desc": "FortiGate 内存使用率应低于 80%"},
            {"key": "reachability", "title": "设备可达性", "metric": "reachability", "operator": "==", "threshold": 1,
             "unit": "", "level": "critical", "desc": "设备应正常可达"},
            {"key": "session_count", "title": "并发会话数", "metric": "session_count", "operator": "<=",
             "threshold": 50000, "unit": "条", "level": "warning", "desc": "并发会话数应低于 50000（按实际容量调整）"},
            {"key": "vpn_tunnel_count", "title": "VPN 隧道数", "metric": "vpn_tunnel_count", "operator": "<=",
             "threshold": 1000, "unit": "条", "level": "info", "desc": "VPN 隧道数应低于 1000"},
            {"key": "iface_down", "title": "接口异常数量", "metric": "interface_down", "operator": "<=", "threshold": 0,
             "unit": "个", "level": "warning", "desc": "不应存在 down 状态的接口"},
            {"key": "util", "title": "接口最大利用率", "metric": "utilization", "operator": "<=", "threshold": 80,
             "unit": "%", "level": "warning", "desc": "接口利用率应低于 80%"},
            {"key": "uptime", "title": "运行时长", "metric": "uptime", "operator": ">=", "threshold": 86400,
             "unit": "秒", "level": "info", "desc": "设备运行时间应大于 1 天（确认无频繁重启）"},
        ],
    },
]


def ensure_default_templates(db: Session) -> int:
    """初始化默认巡检模板。"""
    count = 0
    for tpl in DEFAULT_TEMPLATES:
        exists = db.query(InspectionTemplate).filter(InspectionTemplate.name == tpl["name"]).first()
        if exists:
            continue
        db.add(
            InspectionTemplate(
                name=tpl["name"],
                vendor=tpl["vendor"],
                description=tpl["description"],
                items=json.dumps(tpl["items"], ensure_ascii=False),
            )
        )
        count += 1
    if count:
        db.commit()
    return count


def _judge(item: Dict[str, Any], value: Optional[float]) -> Dict[str, Any]:
    """判定单个检查项。"""
    result = {
        "key": item.get("key"),
        "title": item.get("title"),
        "desc": item.get("desc", ""),
        "unit": item.get("unit", ""),
        "level": item.get("level", "warning"),
        "operator": item.get("operator", "<="),
        "threshold": item.get("threshold"),
        "value": value,
        "status": "pass",
        "message": "",
    }
    if value is None:
        result["status"] = "skip"
        result["message"] = "未采集到该指标"
        return result

    op = result["operator"]
    threshold = float(result["threshold"])
    ok = {
        "<=": value <= threshold,
        "<": value < threshold,
        ">=": value >= threshold,
        ">": value > threshold,
        "==": abs(value - threshold) < 1e-9,
        "!=": abs(value - threshold) >= 1e-9,
    }.get(op, True)

    if ok:
        result["status"] = "pass"
        result["message"] = f"符合基线（{value:g}{result['unit']} {op} {threshold:g}）"
    else:
        result["status"] = "fail"
        result["message"] = f"不符合基线（{value:g}{result['unit']} {op} {threshold:g}）"
    return result


async def inspect_device(
    device: Device,
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """巡检单台设备。"""
    collector = Collector(device)
    sample = await collector.collect(collect_interface=True, collect_system=True)

    metrics: Dict[str, Optional[float]] = {
        "cpu": sample.system.cpu_usage,
        "mem": sample.system.mem_usage,
        "temperature": sample.system.temperature,
        "uptime": float(sample.system.uptime_sec) if sample.system.uptime_sec else None,
        "reachability": 1.0 if sample.system.reachable else 0.0,
        "response_time": sample.system.response_ms,
    }
    # 厂商特有指标（连接数 / AP 数 / 会话数等）
    for key, value in (sample.system.extra or {}).items():
        metrics[key] = float(value)

    if sample.interfaces:
        metrics["interface_down"] = float(sum(1 for i in sample.interfaces if i.oper_status == 2))
        metrics["interface_error"] = float(sum(i.in_errors + i.out_errors for i in sample.interfaces))
        metrics["utilization"] = float(max((i.utilization for i in sample.interfaces), default=0.0))
        metrics["traffic_mbps"] = float(max((max(i.in_bps, i.out_bps) for i in sample.interfaces), default=0.0) / 1_000_000)
    else:
        metrics["interface_down"] = 0.0
        metrics["interface_error"] = 0.0
        metrics["utilization"] = 0.0

    checks = []
    for item in items:
        metric = item.get("metric")
        value = metrics.get(metric)
        checks.append(_judge(item, value))

    return {
        "device_id": device.id,
        "device_name": device.name,
        "device_ip": device.ip,
        "vendor": device.vendor,
        "model": device.model,
        "store_id": device.store_id,
        "reachable": sample.system.reachable,
        "error": sample.system.error,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["status"] == "pass"),
            "failed": sum(1 for c in checks if c["status"] == "fail"),
            "skipped": sum(1 for c in checks if c["status"] == "skip"),
        },
        "metrics": metrics,
        "interfaces": [
            {
                "if_index": i.if_index,
                "name": i.name or i.descr,
                "speed_mbps": round(i.speed_bps / 1_000_000, 2),
                "in_bps": i.in_bps,
                "out_bps": i.out_bps,
                "utilization": i.utilization,
                "oper_status": i.oper_status,
                "admin_status": i.admin_status,
                "in_errors": i.in_errors,
                "out_errors": i.out_errors,
            }
            for i in sample.interfaces
        ],
    }


def recover_stale_tasks(db: Session, force: bool = False) -> int:
    """回收僵死的巡检任务。

    服务重启后不会有正在执行的巡检（进程已终止），因此启动时把残留的
    running / pending 任务全部标记为 failed，避免任务列表出现永久「执行中」。
    force=True 时无视时间阈值（服务启动时使用）。
    """
    q = db.query(InspectionTask).filter(InspectionTask.status.in_(["running", "pending"]))
    if not force:
        q = q.filter(
            InspectionTask.started_at.isnot(None),
            InspectionTask.started_at < datetime.now() - timedelta(hours=2),
        )
    stale = q.all()
    for t in stale:
        t.status = "failed"
        t.finished_at = datetime.now()
        t.result = "[]"
        if not t.remark:
            t.remark = "服务重启导致任务中断，请重新执行"
    if stale:
        db.commit()
    return len(stale)


async def run_inspection_task(task_id: int) -> Dict[str, Any]:
    """执行一次巡检任务（同步等待完成，供 API 调用）。"""
    db = SessionLocal()
    try:
        task = db.get(InspectionTask, task_id)
        if task is None:
            return {"ok": False, "message": "任务不存在"}

        task.status = "running"
        task.started_at = datetime.now()
        db.commit()
        template = db.get(InspectionTemplate, task.template_id) if task.template_id else None
        items: List[Dict[str, Any]] = []
        if template:
            try:
                items = json.loads(template.items or "[]")
            except Exception:
                items = []

        # 按厂商建立模板索引：厂商 → 该厂商的专用模板
        vendor_templates: Dict[str, List[Dict[str, Any]]] = {}
        generic_items: List[Dict[str, Any]] = items
        for tpl in db.query(InspectionTemplate).filter(InspectionTemplate.enabled.is_(True)).all():
            try:
                tpl_items = json.loads(tpl.items or "[]")
            except Exception:
                continue
            if not tpl_items:
                continue
            vendor = (tpl.vendor or "").strip().lower()
            if vendor:
                vendor_templates.setdefault(vendor, tpl_items)
            elif not generic_items:
                generic_items = tpl_items

        # 选设备
        q = db.query(Device).filter(Device.enabled.is_(True))
        if task.scope_type == "store" and task.scope_value:
            store = db.get(Store, int(task.scope_value)) if str(task.scope_value).isdigit() else None
            if store:
                q = q.filter(Device.store_id == store.id)
            else:
                q = q.filter(Device.store_id == int(task.scope_value))
        elif task.scope_type == "device" and task.scope_value:
            q = q.filter(Device.id == int(task.scope_value))
        devices = q.all()

        results: List[Dict[str, Any]] = []

        async def inspect_one(device) -> Dict[str, Any]:
            """巡检单台设备，异常也返回结构化结果。

            检查项按设备厂商选取：优先使用该厂商的专用模板，
            没有专用模板时回退到通用模板。
            """
            # 厂商别名归一化（asa/pix → cisco_asa 等）
            vendor_key = (device.vendor or "").strip().lower()
            vendor_key = {
                "asa": "cisco_asa",
                "pix": "cisco_asa",
                "wlc": "cisco_wlc",
                "aireos": "cisco_wlc",
                "fortigate": "fortinet",
                "fortios": "fortinet",
            }.get(vendor_key, vendor_key)

            device_items = vendor_templates.get(vendor_key) or generic_items
            try:
                return await inspect_device(device, device_items)
            except Exception as exc:
                return {
                    "device_id": device.id,
                    "device_name": device.name,
                    "device_ip": device.ip,
                    "vendor": device.vendor,
                    "reachable": False,
                    "error": str(exc),
                    "checks": [],
                    "summary": {"total": 0, "passed": 0, "failed": 1, "skipped": 0},
                }

        # 并发巡检：串行会在多设备场景下叠加 SNMP 超时，导致整体耗时不可接受
        semaphore = asyncio.Semaphore(settings.max_concurrent_poll)

        async def guarded(device) -> Dict[str, Any]:
            async with semaphore:
                return await inspect_one(device)

        results = list(await asyncio.gather(*(guarded(d) for d in devices)))

        passed = sum(r["summary"]["passed"] for r in results)
        failed = sum(r["summary"]["failed"] for r in results)
        skipped = sum(r["summary"]["skipped"] for r in results)

        task.status = "success"
        task.total = len(results)
        task.passed = passed
        task.failed = failed
        task.warn = skipped
        task.result = json.dumps(results, ensure_ascii=False)
        task.finished_at = datetime.now()
        db.commit()

        # 生成报告
        try:
            from app.services.report import generate_inspection_report

            report_path, report_file = await generate_inspection_report(task_id)
            task.report_path = report_path
            task.report_file = report_file
            db.commit()
        except Exception as exc:
            logger.warning("巡检报告生成失败: %s", exc)
            task.remark = f"报告生成失败：{exc}"
            db.commit()

        return {
            "ok": True,
            "task_id": task_id,
            "devices": len(results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "results": results,
        }
    finally:
        db.close()
