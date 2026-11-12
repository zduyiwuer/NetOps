"""日志 API：设备日志查询、统计、手动触发采集。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_operator
from app.db.session import get_db
from app.models.models import Device, DeviceLog, Store, SystemLog, User
from app.services.scheduler import collect_device_logs

router = APIRouter(prefix="/api/logs", tags=["日志"])


@router.get("")
def list_logs(
    device_id: Optional[int] = None,
    severity: Optional[str] = None,
    protocol: Optional[str] = None,
    keyword: Optional[str] = None,
    store_id: Optional[int] = None,
    hours: int = Query(24, ge=1, le=8760),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(DeviceLog)
    if device_id:
        q = q.filter(DeviceLog.device_id == device_id)
    if severity:
        q = q.filter(DeviceLog.severity == severity)
    if protocol:
        q = q.filter(DeviceLog.protocol == protocol)
    if store_id:
        allowed = [d.id for d in db.query(Device).filter(Device.store_id == store_id).all()]
        q = q.filter(DeviceLog.device_id.in_(allowed or [-1]))
    if hours:
        q = q.filter(DeviceLog.ts >= datetime.now() - timedelta(hours=hours))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((DeviceLog.message.like(like)) | (DeviceLog.raw.like(like)) | (DeviceLog.hostname.like(like)))

    total = q.count()
    logs = q.order_by(DeviceLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": l.id,
                "device_id": l.device_id,
                "device_name": devices[l.device_id].name if l.device_id in devices else "",
                "device_ip": devices[l.device_id].ip if l.device_id in devices else "",
                "store_name": stores[devices[l.device_id].store_id].name
                if l.device_id in devices and devices[l.device_id].store_id in stores
                else "",
                "source_ip": l.source_ip,
                "hostname": l.hostname,
                "facility": l.facility,
                "severity": l.severity,
                "priority": l.priority,
                "message": l.message,
                "raw": l.raw,
                "protocol": l.protocol,
                "ts": l.ts.isoformat(),
                "received_at": l.received_at.isoformat(),
            }
            for l in logs
        ],
    }


@router.get("/statistics")
def log_statistics(
    hours: int = Query(24, ge=1, le=8760),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    since = datetime.now() - timedelta(hours=hours)
    logs = db.query(DeviceLog).filter(DeviceLog.ts >= since).all()
    devices = {d.id: d for d in db.query(Device).all()}
    by_severity: Dict[str, int] = {}
    by_device: Dict[str, int] = {}
    by_hour: Dict[str, int] = {}
    by_facility: Dict[str, int] = {}
    for l in logs:
        by_severity[l.severity] = by_severity.get(l.severity, 0) + 1
        by_facility[l.facility] = by_facility.get(l.facility, 0) + 1
        key = l.ts.strftime("%Y-%m-%d %H:00")
        by_hour[key] = by_hour.get(key, 0) + 1
        if l.device_id in devices:
            by_device[devices[l.device_id].name] = by_device.get(devices[l.device_id].name, 0) + 1
    return {
        "total": len(logs),
        "by_severity": by_severity,
        "by_facility": by_facility,
        "by_device": dict(sorted(by_device.items(), key=lambda kv: kv[1], reverse=True)[:10]),
        "by_hour": [{"ts": k, "count": v} for k, v in sorted(by_hour.items())],
    }


@router.post("/collect/{device_id}")
async def trigger_log_collect(
    device_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        return {"ok": False, "message": "设备不存在"}
    count = await collect_device_logs(device_id)
    return {"ok": True, "message": f"采集完成，新增 {count} 条日志"}


@router.delete("/cleanup")
def cleanup_logs(
    days: int = Query(90, ge=1),
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=days)
    count = db.query(DeviceLog).filter(DeviceLog.ts < cutoff).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": count}


# ================================================================ 系统日志
@router.get("/system")
def list_system_logs(
    level: Optional[str] = None,
    category: Optional[str] = None,
    keyword: Optional[str] = None,
    store_id: Optional[int] = None,
    hours: int = Query(168, ge=1, le=8760),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """查询平台自身的运行日志（端口占用、采集器启动失败等系统级问题）。"""
    q = db.query(SystemLog)
    since = datetime.now() - timedelta(hours=hours)
    q = q.filter(SystemLog.ts >= since)
    if level:
        q = q.filter(SystemLog.level == level)
    if category:
        q = q.filter(SystemLog.category == category)
    if store_id:
        # 系统日志大多与设备无关；有关联的按关联设备所属门店过滤
        allowed = [d.id for d in db.query(Device).filter(Device.store_id == store_id).all()]
        q = q.filter(SystemLog.device_id.in_(allowed or [-1]))
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter(
            (SystemLog.message.like(like))
            | (SystemLog.detail.like(like))
            | (SystemLog.source.like(like))
        )
    total = q.count()
    rows = (
        q.order_by(SystemLog.ts.desc(), SystemLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    devices = {d.id: d.name for d in db.query(Device).all()}
    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "level": r.level,
                "category": r.category,
                "source": r.source,
                "message": r.message,
                "detail": r.detail,
                "device_id": r.device_id,
                "device_name": devices.get(r.device_id, "") if r.device_id else "",
                "context": _safe_json(r.context),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/system/statistics")
def system_log_statistics(
    hours: int = Query(168, ge=1, le=8760),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """系统日志按级别 / 分类统计。"""
    since = datetime.now() - timedelta(hours=hours)
    by_level = dict(
        db.query(SystemLog.level, func.count(SystemLog.id))
        .filter(SystemLog.ts >= since)
        .group_by(SystemLog.level)
        .all()
    )
    by_category = dict(
        db.query(SystemLog.category, func.count(SystemLog.id))
        .filter(SystemLog.ts >= since)
        .group_by(SystemLog.category)
        .all()
    )
    total = (
        db.query(func.count(SystemLog.id)).filter(SystemLog.ts >= since).scalar() or 0
    )
    return {
        "total": total,
        "by_level": by_level,
        "by_category": by_category,
        "hours": hours,
    }


@router.delete("/system/cleanup")
def cleanup_system_logs(
    days: int = Query(30, ge=1),
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """清理过期系统日志。"""
    cutoff = datetime.now() - timedelta(days=days)
    count = (
        db.query(SystemLog).filter(SystemLog.ts < cutoff).delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "deleted": count}


def _safe_json(raw: str) -> Dict[str, Any]:
    """解析 context JSON，失败时返回空字典。"""
    import json

    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
