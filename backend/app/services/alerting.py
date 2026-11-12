"""告警引擎：规则匹配、事件生成、恢复、通知分发。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.models import AlertEvent, AlertRule, Device, DeviceMetric, InterfaceMetric, Store
from app.services.alert_detail import build_detail
from app.services.notifier import notifier

logger = logging.getLogger("netops.alerting")


def _matches(value: float, operator: str, threshold: float) -> bool:
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator in ("==", "="):
        return abs(value - threshold) < 1e-9
    if operator == "!=":
        return abs(value - threshold) >= 1e-9
    return False


def _device_scope_query(rule: AlertRule):
    q = Device.enabled.is_(True)
    if rule.device_id:
        q = q & (Device.id == rule.device_id)
    elif rule.store_id:
        q = q & (Device.store_id == rule.store_id)
    return q


async def evaluate_rules(
    db: Session,
    device: Device,
    metric_row: Optional[DeviceMetric] = None,
    interfaces: Optional[List[InterfaceMetric]] = None,
) -> List[AlertEvent]:
    """针对单台设备评估全部适用规则，返回新产生的告警事件。"""
    rules = (
        db.query(AlertRule)
        .filter(AlertRule.enabled.is_(True))
        .filter(or_(AlertRule.device_id == device.id, AlertRule.device_id.is_(None)))
        .all()
    )
    if not rules:
        return []

    store = db.get(Store, device.store_id) if device.store_id else None
    fired: List[AlertEvent] = []

    for rule in rules:
        if rule.store_id and device.store_id != rule.store_id:
            continue
        value = _extract_value(rule.metric, device, metric_row, interfaces)
        if value is None:
            continue
        if not _matches(value, rule.operator, rule.threshold):
            # 条件不再满足 → 自动恢复相关活动告警
            await _auto_resolve(db, rule, device)
            continue
        # 已存在活动告警则不重复产生
        existing = (
            db.query(AlertEvent)
            .filter(
                AlertEvent.rule_id == rule.id,
                AlertEvent.device_id == device.id,
                AlertEvent.status.in_(["firing", "acked"]),
            )
            .order_by(AlertEvent.fired_at.desc())
            .first()
        )
        if existing:
            existing.value = value
            continue
        # 静默期
        if rule.silence_minutes > 0:
            recent = (
                db.query(AlertEvent)
                .filter(
                    AlertEvent.rule_id == rule.id,
                    AlertEvent.device_id == device.id,
                    AlertEvent.fired_at >= datetime.now() - timedelta(minutes=rule.silence_minutes),
                )
                .count()
            )
            if recent:
                continue

        # 生成详细告警描述（含具体接口、各项数值等明细）
        message, detail = build_detail(
            metric=rule.metric,
            value=value,
            threshold=rule.threshold,
            operator=rule.operator,
            device=device,
            rule_name=rule.name,
            interfaces=interfaces,
        )

        event = AlertEvent(
            rule_id=rule.id,
            device_id=device.id,
            store_id=device.store_id,
            title=f"[{_severity_cn(rule.severity)}] {rule.name} - {device.name}",
            message=message,
            detail=json.dumps(detail, ensure_ascii=False),
            metric=rule.metric,
            value=value,
            threshold=rule.threshold,
            severity=rule.severity,
            status="firing",
            fired_at=datetime.now(),
        )
        db.add(event)
        db.flush()
        fired.append(event)

    if fired:
        db.commit()
        await _dispatch(db, fired)
    return fired


def _extra_metric(metric_row: DeviceMetric, name: str) -> Optional[float]:
    """从 extra_metrics（JSON 字符串）里取厂商特有指标。"""
    raw = getattr(metric_row, "extra_metrics", None)
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    v = data.get(name)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _extract_value(
    metric: str,
    device: Device,
    metric_row: Optional[DeviceMetric],
    interfaces: Optional[List[InterfaceMetric]],
) -> Optional[float]:
    if metric == "offline":
        if metric_row is None:
            return None
        return 0.0 if not metric_row.reachability else None
    if metric_row is None:
        return None
    if metric == "cpu":
        return metric_row.cpu_usage
    if metric == "mem":
        return metric_row.mem_usage
    if metric == "temperature":
        return metric_row.temperature
    if metric == "response_time":
        return metric_row.response_ms
    # 防火墙 / 无线等厂商特有指标：存在 extra_metrics（JSON 字符串）里
    if metric in (
        "session_count",   # 防火墙当前会话数（飞塔 fgSysSesCount 等）
        "conn_count",      # ASA 当前连接数
        "ap_count",        # 无线 AP 数
        "client_count",    # 无线客户端数
        "vpn_tunnel_count",  # VPN 隧道数
        "user_count",      # 在线用户数
    ):
        return _extra_metric(metric_row, metric)
    if metric in ("traffic", "util", "loss", "interface_down"):
        if not interfaces:
            return None
        if metric == "interface_down":
            downs = [i for i in interfaces if i.oper_status == 2]
            return float(len(downs)) if downs else None
        if metric == "traffic":
            vals = [max(i.in_bps, i.out_bps) for i in interfaces]
            return max(vals) / 1_000_000 if vals else None  # Mbps
        if metric == "util":
            vals = [i.utilization for i in interfaces]
            return max(vals) if vals else None
        if metric == "loss":
            total = sum(i.in_errors + i.out_errors for i in interfaces)
            return float(total)
    return None


async def _auto_resolve(db: Session, rule: AlertRule, device: Device) -> None:
    events = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.rule_id == rule.id,
            AlertEvent.device_id == device.id,
            AlertEvent.status.in_(["firing", "acked"]),
        )
        .all()
    )
    for ev in events:
        ev.status = "resolved"
        ev.resolved_at = datetime.now()
    if events:
        db.commit()


async def _dispatch(db: Session, events: List[AlertEvent]) -> None:
    """把新告警分发到各通知渠道。"""
    from app.models.models import NotificationChannel

    devices = {d.id: d for d in db.query(Device).filter(Device.id.in_([e.device_id for e in events if e.device_id])).all()}
    stores = {s.id: s for s in db.query(Store).all()}
    channels = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True)).all()

    for ev in events:
        device = devices.get(ev.device_id)
        store = stores.get(ev.store_id) if ev.store_id else None
        payload = {
            "id": ev.id,
            "title": ev.title,
            "message": ev.message,
            "metric": ev.metric,
            "value": ev.value,
            "operator": ">",
            "threshold": ev.threshold,
            "severity": ev.severity,
            "device_name": device.name if device else "",
            "device_ip": device.ip if device else "",
            "store_name": store.name if store else "",
            "fired_at": ev.fired_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rule = db.get(AlertRule, ev.rule_id) if ev.rule_id else None
        target_channels = channels
        if rule and rule.notify_channels:
            wanted = {c.strip() for c in rule.notify_channels.split(",") if c.strip()}
            target_channels = [c for c in channels if c.name in wanted]
        for ch in target_channels:
            ok = await notifier.send(ch, payload)
            if ok:
                ev.notified = True
    db.commit()


def _severity_cn(severity: str) -> str:
    return {
        "critical": "严重",
        "warning": "警告",
        "info": "提示",
        "crit": "严重",
    }.get((severity or "").lower(), severity)


async def check_offline_devices(db: Session) -> List[AlertEvent]:
    """离线检测：设备状态 offline 超过阈值触发告警。"""
    from app.models.models import AlertRule

    offline_rules = (
        db.query(AlertRule)
        .filter(AlertRule.enabled.is_(True), AlertRule.metric == "offline")
        .all()
    )
    if not offline_rules:
        return []
    fired: List[AlertEvent] = []
    for rule in offline_rules:
        devices = db.query(Device).filter(_device_scope_query(rule), Device.status == "offline").all()
        for device in devices:
            existing = (
                db.query(AlertEvent)
                .filter(
                    AlertEvent.rule_id == rule.id,
                    AlertEvent.device_id == device.id,
                    AlertEvent.status.in_(["firing", "acked"]),
                )
                .first()
            )
            if existing:
                continue
            ev = AlertEvent(
                rule_id=rule.id,
                device_id=device.id,
                store_id=device.store_id,
                title=f"[严重] {rule.name} - {device.name}",
                message=(
                    f"设备 {device.name}({device.ip}) 无法访问，SNMP/SSH 均无响应，判定为离线。\n"
                    f"最后在线时间：{device.last_seen or '未知'}。"
                    f"\n触发规则「{rule.name}」。\n"
                    "排查建议：确认设备电源与链路状态、检查管理网可达性、"
                    "确认 SNMP Community 或 SSH 凭据是否变更、查看设备侧日志与告警。"
                ),
                detail=json.dumps(
                    {
                        "metric": "offline",
                        "metric_name": "设备可达性",
                        "device": {"id": device.id, "name": device.name, "ip": device.ip},
                        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
                        "items": [],
                        "summary": "设备不可达",
                    },
                    ensure_ascii=False,
                ),
                metric="offline",
                value=1.0,
                threshold=0.0,
                severity=rule.severity,
                status="firing",
                fired_at=datetime.now(),
            )
            db.add(ev)
            fired.append(ev)
    if fired:
        db.commit()
        await _dispatch(db, fired)
    return fired


async def recover_offline_alerts(db: Session) -> None:
    """设备恢复在线后，自动恢复离线告警。"""
    events = (
        db.query(AlertEvent)
        .join(AlertRule, AlertRule.id == AlertEvent.rule_id)
        .filter(AlertRule.metric == "offline", AlertEvent.status.in_(["firing", "acked"]))
        .all()
    )
    for ev in events:
        device = db.get(Device, ev.device_id) if ev.device_id else None
        if device and device.status == "online":
            ev.status = "resolved"
            ev.resolved_at = datetime.now()
    db.commit()
