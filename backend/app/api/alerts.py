"""告警 API：规则管理、事件查询、确认与恢复、通知渠道。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import AlertEvent, AlertRule, Device, NotificationChannel, Store, User
from app.services.alert_detail import metric_name

router = APIRouter(prefix="/api/alerts", tags=["告警"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1)
    device_id: Optional[int] = None
    store_id: Optional[int] = None
    metric: str = "cpu"
    operator: str = ">"
    threshold: float = 0.0
    duration_sec: int = 0
    severity: str = "warning"
    enabled: bool = True
    notify_channels: str = ""
    silence_minutes: int = 10
    description: str = ""


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    device_id: Optional[int] = None
    store_id: Optional[int] = None
    metric: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    duration_sec: Optional[int] = None
    severity: Optional[str] = None
    enabled: Optional[bool] = None
    notify_channels: Optional[str] = None
    silence_minutes: Optional[int] = None
    description: Optional[str] = None


class ChannelIn(BaseModel):
    name: str = Field(min_length=1)
    type: str = "webhook"
    target: str = ""
    extra: str = "{}"
    enabled: bool = True
    min_severity: str = "warning"


@router.get("/rules")
def list_rules(
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(AlertRule)
    if device_id:
        q = q.filter(AlertRule.device_id == device_id)
    rules = q.order_by(AlertRule.id).all()
    devices = {d.id: d.name for d in db.query(Device).all()}
    stores = {s.id: s.name for s in db.query(Store).all()}
    return {
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "device_id": r.device_id,
                "device_name": devices.get(r.device_id, "全部设备"),
                "store_id": r.store_id,
                "store_name": stores.get(r.store_id, "全部门店"),
                "metric": r.metric,
                "operator": r.operator,
                "threshold": r.threshold,
                "duration_sec": r.duration_sec,
                "severity": r.severity,
                "enabled": r.enabled,
                "notify_channels": r.notify_channels,
                "silence_minutes": r.silence_minutes,
                "description": r.description,
            }
            for r in rules
        ]
    }


@router.post("/rules")
def create_rule(payload: RuleIn, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    rule = AlertRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    return {"id": rule.id}


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: RuleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        return {"error": "规则不存在"}
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(rule, key, value)
    db.commit()
    return {"id": rule.id}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    rule = db.get(AlertRule, rule_id)
    if rule:
        db.delete(rule)
        db.commit()
    return {"ok": True}


@router.get("/events")
def list_events(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    keyword: Optional[str] = None,
    hours: int = Query(0, ge=0, le=8760),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(AlertEvent)
    if status:
        q = q.filter(AlertEvent.status == status)
    if severity:
        q = q.filter(AlertEvent.severity == severity)
    if device_id:
        q = q.filter(AlertEvent.device_id == device_id)
    if store_id:
        q = q.filter(AlertEvent.store_id == store_id)
    if hours:
        q = q.filter(AlertEvent.fired_at >= datetime.now() - timedelta(hours=hours))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter((AlertEvent.title.like(like)) | (AlertEvent.message.like(like)))

    total = q.count()
    events = q.order_by(AlertEvent.fired_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": e.id,
                "rule_id": e.rule_id,
                "device_id": e.device_id,
                "device_name": devices[e.device_id].name if e.device_id in devices else "",
                "device_ip": devices[e.device_id].ip if e.device_id in devices else "",
                "store_id": e.store_id,
                "store_name": stores[e.store_id].name if e.store_id in stores else "",
                "title": e.title,
                "message": e.message,
                "detail": _parse_detail(e.detail),
                "metric": e.metric,
                "metric_name": metric_name(e.metric),
                "value": e.value,
                "threshold": e.threshold,
                "severity": e.severity,
                "status": e.status,
                "acknowledged": e.acknowledged,
                "ack_by": e.ack_by,
                "notified": e.notified,
                "fired_at": e.fired_at.isoformat(),
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            }
            for e in events
        ],
    }


def _parse_detail(raw: str) -> Dict[str, Any]:
    """解析告警结构化明细。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.post("/events/{event_id}/ack")
def ack_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    event = db.get(AlertEvent, event_id)
    if event is None:
        return {"error": "告警不存在"}
    event.acknowledged = True
    event.status = "acked"
    event.ack_by = user.username
    event.ack_at = datetime.now()
    db.commit()
    return {"ok": True, "status": event.status}


@router.post("/events/{event_id}/resolve")
def resolve_event(
    event_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    event = db.get(AlertEvent, event_id)
    if event is None:
        return {"error": "告警不存在"}
    event.status = "resolved"
    event.resolved_at = datetime.now()
    if not event.acknowledged:
        event.acknowledged = True
        event.ack_by = user.username
        event.ack_at = datetime.now()
    db.commit()
    return {"ok": True, "status": event.status}


@router.post("/events/batch-ack")
def batch_ack(
    event_ids: List[int],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    count = 0
    for eid in event_ids:
        event = db.get(AlertEvent, eid)
        if event and event.status != "resolved":
            event.acknowledged = True
            event.status = "acked"
            event.ack_by = user.username
            event.ack_at = datetime.now()
            count += 1
    db.commit()
    return {"ok": True, "count": count}


@router.get("/statistics")
def alert_statistics(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """告警统计：按天、按级别、按指标。"""
    since = datetime.now() - timedelta(days=days)
    events = db.query(AlertEvent).filter(AlertEvent.fired_at >= since).all()
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}

    by_day: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    by_metric: Dict[str, int] = {}
    by_device: Dict[str, int] = {}
    by_store: Dict[str, int] = {}

    for e in events:
        day = e.fired_at.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + 1
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
        by_metric[e.metric] = by_metric.get(e.metric, 0) + 1
        if e.device_id in devices:
            by_device[devices[e.device_id].name] = by_device.get(devices[e.device_id].name, 0) + 1
        if e.store_id in stores:
            by_store[stores[e.store_id].name] = by_store.get(stores[e.store_id].name, 0) + 1

    # 补齐空缺日期
    day_list = []
    for i in range(days - 1, -1, -1):
        key = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_list.append({"date": key, "count": by_day.get(key, 0)})

    return {
        "total": len(events),
        "by_day": day_list,
        "by_severity": by_severity,
        "by_metric": by_metric,
        "by_device": dict(sorted(by_device.items(), key=lambda kv: kv[1], reverse=True)[:10]),
        "by_store": by_store,
    }


# ---------------------------------------------------------------- 通知渠道
@router.get("/channels")
def list_channels(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    channels = db.query(NotificationChannel).order_by(NotificationChannel.id).all()
    return {
        "items": [
            {
                "id": c.id,
                "name": c.name,
                "type": c.type,
                "target": c.target,
                "extra": c.extra,
                "enabled": c.enabled,
                "min_severity": c.min_severity,
            }
            for c in channels
        ]
    }


@router.post("/channels")
def create_channel(payload: ChannelIn, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    if db.query(NotificationChannel).filter(NotificationChannel.name == payload.name).first():
        return {"error": "渠道名称已存在"}
    ch = NotificationChannel(**payload.model_dump())
    db.add(ch)
    db.commit()
    return {"id": ch.id}


@router.put("/channels/{channel_id}")
def update_channel(
    channel_id: int,
    payload: ChannelIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    ch = db.get(NotificationChannel, channel_id)
    if ch is None:
        return {"error": "渠道不存在"}
    for key, value in payload.model_dump().items():
        setattr(ch, key, value)
    db.commit()
    return {"id": ch.id}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    ch = db.get(NotificationChannel, channel_id)
    if ch:
        db.delete(ch)
        db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    from app.services.notifier import notifier

    ch = db.get(NotificationChannel, channel_id)
    if ch is None:
        return {"ok": False, "message": "渠道不存在"}
    payload = {
        "id": 0,
        "title": "网络设备运维平台 通知渠道测试",
        "message": "这是一条来自网络设备运维平台的测试通知，若你看到它说明渠道配置正确。",
        "metric": "test",
        "value": 0,
        "threshold": 0,
        "severity": "info",
        "device_name": "测试设备",
        "device_ip": "0.0.0.0",
        "store_name": "-",
        "fired_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    ok = await notifier.send(ch, payload)
    return {"ok": ok, "message": "发送成功" if ok else "发送失败，请检查渠道配置"}
