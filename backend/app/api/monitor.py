"""监控 API：总览、实时接口速率、CPU/内存曲线、流量 TopN。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.models import (
    AlertEvent,
    Device,
    DeviceLog,
    DeviceMetric,
    InterfaceMetric,
    Store,
    User,
)

router = APIRouter(prefix="/api/monitor", tags=["监控"])


def _ip_metrics(m: Optional[DeviceMetric]) -> Dict[str, Any]:
    if not m:
        return {}
    return {
        "cpu_usage": m.cpu_usage,
        "mem_usage": m.mem_usage,
        "mem_total_mb": m.mem_total_mb,
        "temperature": m.temperature,
        "uptime_sec": m.uptime_sec,
        "response_ms": m.response_ms,
        "reachability": m.reachability,
        "ts": m.ts.isoformat(),
    }


@router.get("/overview")
def overview(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """首页总览：设备状态、CPU/内存 Top、资源分布、告警统计。"""
    q = db.query(Device)
    if store_id:
        q = q.filter(Device.store_id == store_id)
    devices = q.all()
    stores = {s.id: s for s in db.query(Store).all()}

    online = sum(1 for d in devices if d.status == "online")
    offline = sum(1 for d in devices if d.status == "offline")
    unknown = len(devices) - online - offline

    # 各设备最新指标
    latest_rows: Dict[int, DeviceMetric] = {}
    for device in devices:
        row = (
            db.query(DeviceMetric)
            .filter(DeviceMetric.device_id == device.id)
            .order_by(DeviceMetric.id.desc())
            .first()
        )
        if row:
            latest_rows[device.id] = row

    cpu_top = sorted(
        [
            {"device_id": d.id, "name": d.name, "ip": d.ip, "value": latest_rows[d.id].cpu_usage,
             "store_name": stores[d.store_id].name if d.store_id in stores else ""}
            for d in devices
            if d.id in latest_rows and latest_rows[d.id].cpu_usage is not None
        ],
        key=lambda x: x["value"],
        reverse=True,
    )[:10]
    mem_top = sorted(
        [
            {"device_id": d.id, "name": d.name, "ip": d.ip, "value": latest_rows[d.id].mem_usage,
             "store_name": stores[d.store_id].name if d.store_id in stores else ""}
            for d in devices
            if d.id in latest_rows and latest_rows[d.id].mem_usage is not None
        ],
        key=lambda x: x["value"],
        reverse=True,
    )[:10]

    # 24h 告警统计
    since = datetime.now() - timedelta(hours=24)
    alerts_24h = (
        db.query(AlertEvent)
        .filter(AlertEvent.fired_at >= since)
        .count()
    )
    critical_open = (
        db.query(AlertEvent)
        .filter(AlertEvent.status.in_(["firing", "acked"]), AlertEvent.severity.in_(["critical", "crit"]))
        .count()
    )
    firing_open = (
        db.query(AlertEvent).filter(AlertEvent.status.in_(["firing", "acked"])).count()
    )

    by_vendor: Dict[str, int] = {}
    for d in devices:
        by_vendor[d.vendor] = by_vendor.get(d.vendor, 0) + 1
    by_store: Dict[str, int] = {}
    for d in devices:
        key = stores[d.store_id].name if d.store_id in stores else "未分配"
        by_store[key] = by_store.get(key, 0) + 1

    # 最近告警
    recent_alerts = (
        db.query(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(10).all()
    )
    device_names = {d.id: d for d in db.query(Device).all()}
    alerts_payload = [
        {
            "id": a.id,
            "title": a.title,
            "severity": a.severity,
            "status": a.status,
            "device_name": device_names[a.device_id].name if a.device_id in device_names else "",
            "value": a.value,
            "metric": a.metric,
            "fired_at": a.fired_at.isoformat(),
        }
        for a in recent_alerts
    ]

    # 接口速率总量（最近一次采样）
    latest_if_ts = (
        db.query(func.max(InterfaceMetric.ts)).scalar()
    )
    total_in = 0.0
    total_out = 0.0
    if latest_if_ts:
        for row in db.query(InterfaceMetric).filter(InterfaceMetric.ts == latest_if_ts).all():
            total_in += row.in_bps
            total_out += row.out_bps

    # 日志量（24h）
    logs_24h = db.query(DeviceLog).filter(DeviceLog.ts >= since).count()
    error_logs = (
        db.query(DeviceLog)
        .filter(DeviceLog.ts >= since, DeviceLog.severity.in_(["err", "crit", "alert", "emerg"]))
        .count()
    )

    return {
        "device_total": len(devices),
        "online": online,
        "offline": offline,
        "unknown": unknown,
        "availability": round(online / len(devices) * 100, 2) if devices else 0,
        "alerts_24h": alerts_24h,
        "alerts_open": firing_open,
        "alerts_critical": critical_open,
        "logs_24h": logs_24h,
        "error_logs_24h": error_logs,
        "total_in_mbps": round(total_in / 1_000_000, 2),
        "total_out_mbps": round(total_out / 1_000_000, 2),
        "cpu_top": cpu_top,
        "mem_top": mem_top,
        "by_vendor": by_vendor,
        "by_store": by_store,
        "recent_alerts": alerts_payload,
    }


@router.get("/interfaces/realtime")
def interfaces_realtime(
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    keyword: Optional[str] = None,
    only_active: bool = Query(False, description="仅显示 up 状态接口"),
    sort_by: str = Query("utilization", description="utilization/in_bps/out_bps/name"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """实时接口速率：取每台设备最新一次采样。"""
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}

    device_ids = list(devices.keys())
    if device_id:
        device_ids = [device_id]
    elif store_id:
        device_ids = [did for did, d in devices.items() if d.store_id == store_id]
    if not device_ids:
        return {"items": [], "ts": None}

    # 每台设备取最新采样时间
    rows: List[InterfaceMetric] = []
    latest_ts: Optional[datetime] = None
    for did in device_ids:
        ts = db.query(func.max(InterfaceMetric.ts)).filter(InterfaceMetric.device_id == did).scalar()
        if ts is None:
            continue
        latest_ts = max(latest_ts, ts) if latest_ts else ts
        rows.extend(db.query(InterfaceMetric).filter(InterfaceMetric.device_id == did, InterfaceMetric.ts == ts).all())

    items = []
    for r in rows:
        device = devices.get(r.device_id)
        if device is None:
            continue
        name = r.if_name or r.if_descr
        if keyword and keyword.lower() not in (name or "").lower() and keyword not in (r.device_id and str(device.ip)):
            continue
        if only_active and r.oper_status != 1:
            continue
        items.append({
            "device_id": r.device_id,
            "device_name": device.name if device else "",
            "device_ip": device.ip if device else "",
            "store_name": stores[device.store_id].name if device and device.store_id in stores else "",
            "if_index": r.if_index,
            "if_name": name,
            "if_descr": r.if_descr,
            "if_alias": r.if_alias,
            "speed_mbps": round(r.speed_bps / 1_000_000, 2),
            "admin_status": r.admin_status,
            "oper_status": r.oper_status,
            "in_bps": r.in_bps,
            "out_bps": r.out_bps,
            "in_mbps": round(r.in_bps / 1_000_000, 3),
            "out_mbps": round(r.out_bps / 1_000_000, 3),
            "in_octets": r.in_octets,
            "out_octets": r.out_octets,
            "in_errors": r.in_errors,
            "out_errors": r.out_errors,
            "utilization": r.utilization,
            "ts": r.ts.isoformat(),
        })

    key_map = {
        "utilization": lambda x: x["utilization"],
        "in_bps": lambda x: x["in_bps"],
        "out_bps": lambda x: x["out_bps"],
        "name": lambda x: x["if_name"] or "",
    }
    sorter = key_map.get(sort_by, key_map["utilization"])
    items.sort(key=sorter, reverse=sort_by != "name")

    return {"items": items, "ts": latest_ts.isoformat() if latest_ts else None}


@router.get("/devices/{device_id}/interfaces")
def device_interfaces(
    device_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """设备当前接口列表（最新采样）。"""
    device = db.get(Device, device_id)
    if device is None:
        return {"items": []}
    ts = db.query(func.max(InterfaceMetric.ts)).filter(InterfaceMetric.device_id == device_id).scalar()
    if ts is None:
        return {"items": [], "ts": None}
    rows = db.query(InterfaceMetric).filter(InterfaceMetric.device_id == device_id, InterfaceMetric.ts == ts).all()
    items = [
        {
            "if_index": r.if_index,
            "if_name": r.if_name or r.if_descr,
            "if_descr": r.if_descr,
            "if_alias": r.if_alias,
            "if_type": r.if_type,
            "speed_mbps": round(r.speed_bps / 1_000_000, 2),
            "admin_status": r.admin_status,
            "oper_status": r.oper_status,
            "in_bps": r.in_bps,
            "out_bps": r.out_bps,
            "in_mbps": round(r.in_bps / 1_000_000, 3),
            "out_mbps": round(r.out_bps / 1_000_000, 3),
            "in_errors": r.in_errors,
            "out_errors": r.out_errors,
            "utilization": r.utilization,
        }
        for r in rows
    ]
    items.sort(key=lambda x: x["if_index"])
    return {"items": items, "ts": ts.isoformat()}


@router.get("/devices/{device_id}/metrics")
def device_metrics(
    device_id: int,
    hours: int = Query(24, ge=1, le=720),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """设备 CPU/内存历史曲线。"""
    since = datetime.now() - timedelta(hours=hours)
    rows = (
        db.query(DeviceMetric)
        .filter(DeviceMetric.device_id == device_id, DeviceMetric.ts >= since)
        .order_by(DeviceMetric.ts)
        .all()
    )
    return {
        "items": [_ip_metrics(m) for m in rows],
        "device_id": device_id,
        "hours": hours,
    }


@router.get("/devices/{device_id}/interfaces/history")
def interface_history(
    device_id: int,
    if_index: int,
    hours: int = Query(6, ge=1, le=168),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """单个接口速率历史曲线。"""
    since = datetime.now() - timedelta(hours=hours)
    rows = (
        db.query(InterfaceMetric)
        .filter(
            InterfaceMetric.device_id == device_id,
            InterfaceMetric.if_index == if_index,
            InterfaceMetric.ts >= since,
        )
        .order_by(InterfaceMetric.ts)
        .all()
    )
    return {
        "items": [
            {
                "ts": r.ts.isoformat(),
                "in_bps": r.in_bps,
                "out_bps": r.out_bps,
                "utilization": r.utilization,
            }
            for r in rows
        ],
        "device_id": device_id,
        "if_index": if_index,
    }


# ---------------------------------------------------------------- TopN
@router.get("/topn/traffic")
def traffic_topn(
    limit: int = Query(20, ge=1, le=200),
    dimension: str = Query("interface", description="interface/device/store"),
    hours: int = Query(1, ge=1, le=168),
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """流量 TopN 排行。"""
    since = datetime.now() - timedelta(hours=hours)
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}

    q = db.query(InterfaceMetric).filter(InterfaceMetric.ts >= since)
    if store_id:
        allowed = [did for did, d in devices.items() if d.store_id == store_id]
        q = q.filter(InterfaceMetric.device_id.in_(allowed or [-1]))

    buckets: Dict[Any, Dict[str, Any]] = {}
    for r in q.all():
        device = devices.get(r.device_id)
        if device is None:
            continue
        total = max(r.in_bps, r.out_bps)
        if dimension == "device":
            key = (r.device_id, device.name, device.ip)
        elif dimension == "store":
            key = (device.store_id or 0, stores[device.store_id].name if device.store_id in stores else "未分配", "")
        else:
            key = (
                r.device_id,
                device.name,
                f"{r.if_name or r.if_descr}",
            )
        item = buckets.setdefault(
            key,
            {
                "device_id": r.device_id,
                "device_name": device.name,
                "device_ip": device.ip,
                "target": key[2],
                "store_name": stores[device.store_id].name if device and device.store_id in stores else "",
                "in_bps": 0.0,
                "out_bps": 0.0,
                "peak": 0.0,
                "samples": 0,
            },
        )
        item["in_bps"] = max(item["in_bps"], r.in_bps)
        item["out_bps"] = max(item["out_bps"], r.out_bps)
        item["peak"] = max(item["peak"], total)
        item["samples"] += 1

    items = sorted(buckets.values(), key=lambda x: x["peak"], reverse=True)[:limit]
    for it in items:
        it["in_mbps"] = round(it["in_bps"] / 1_000_000, 3)
        it["out_mbps"] = round(it["out_bps"] / 1_000_000, 3)
        it["peak_mbps"] = round(it["peak"] / 1_000_000, 3)
    return {"items": items, "dimension": dimension, "hours": hours}


@router.get("/topn/errors")
def error_topn(
    limit: int = Query(20, ge=1, le=200),
    hours: int = Query(24, ge=1, le=720),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """错包 TopN。"""
    since = datetime.now() - timedelta(hours=hours)
    devices = {d.id: d for d in db.query(Device).all()}
    buckets: Dict[Any, Dict[str, Any]] = {}
    for r in db.query(InterfaceMetric).filter(InterfaceMetric.ts >= since).all():
        errs = r.in_errors + r.out_errors
        if errs <= 0:
            continue
        device = devices.get(r.device_id)
        key = (r.device_id, device.name if device else "", f"{r.if_name or r.if_descr}")
        item = buckets.setdefault(
            key,
            {"device_id": r.device_id, "device_name": device.name if device else "",
             "device_ip": device.ip if device else "", "target": key[2],
             "in_errors": 0, "out_errors": 0},
        )
        item["in_errors"] = max(item["in_errors"], r.in_errors)
        item["out_errors"] = max(item["out_errors"], r.out_errors)
    items = sorted(buckets.values(), key=lambda x: x["in_errors"] + x["out_errors"], reverse=True)[:limit]
    for it in items:
        it["total_errors"] = it["in_errors"] + it["out_errors"]
    return {"items": items, "hours": hours}


@router.get("/traffic/timeseries")
def traffic_timeseries(
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    hours: int = Query(6, ge=1, le=168),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """整体流量时序（所有设备汇总）。"""
    since = datetime.now() - timedelta(hours=hours)
    devices = {d.id: d for d in db.query(Device).all()}
    q = db.query(InterfaceMetric).filter(InterfaceMetric.ts >= since)
    if device_id:
        q = q.filter(InterfaceMetric.device_id == device_id)
    elif store_id:
        allowed = [did for did, d in devices.items() if d.store_id == store_id]
        q = q.filter(InterfaceMetric.device_id.in_(allowed or [-1]))

    buckets: Dict[str, Dict[str, float]] = {}
    for r in q.all():
        key = r.ts.strftime("%Y-%m-%d %H:%M")
        item = buckets.setdefault(key, {"in": 0.0, "out": 0.0})
        item["in"] += r.in_bps
        item["out"] += r.out_bps

    items = [
        {
            "ts": k,
            "in_mbps": round(v["in"] / 1_000_000, 3),
            "out_mbps": round(v["out"] / 1_000_000, 3),
        }
        for k, v in sorted(buckets.items())
    ]
    return {"items": items, "hours": hours}
