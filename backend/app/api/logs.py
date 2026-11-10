"""日志 API：设备日志查询、统计、手动触发采集。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_operator
from app.db.session import get_db
from app.models.models import Device, DeviceLog, Store, User
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
