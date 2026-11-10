"""NetFlow 服务层：流记录入库、聚合、TopN 查询。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.collectors.netflow import FlowRecord
from app.db.session import SessionLocal
from app.models.models import Device, FlowAggregate, FlowRecord as FlowRecordModel

logger = logging.getLogger("netops.netflow.service")

# 聚合窗口粒度（分钟）
AGG_WINDOW_MINUTES = 5


def _window(ts: datetime) -> datetime:
    """把时间对齐到聚合窗口起点。"""
    minute = (ts.minute // AGG_WINDOW_MINUTES) * AGG_WINDOW_MINUTES
    return ts.replace(minute=minute, second=0, microsecond=0)


def store_flows(records: List[FlowRecord], source_ip: str = "") -> int:
    """把解析出的流记录落库并更新聚合表。"""
    if not records:
        return 0
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.ip == source_ip).first()
        device_id = device.id if device else None

        agg_map: Dict[Tuple[Any, ...], FlowAggregate] = {}

        for rec in records:
            ts = rec.start_time or datetime.now()
            row = FlowRecordModel(
                device_id=device_id,
                exporter_ip=source_ip,
                version=rec.version,
                template_id=rec.template_id,
                observation_domain=rec.observation_domain,
                src_ip=rec.src_ip,
                dst_ip=rec.dst_ip,
                src_port=rec.src_port,
                dst_port=rec.dst_port,
                protocol=rec.protocol,
                application=rec.application,
                bytes_total=rec.bytes_total,
                packets_total=rec.packets_total,
                start_time=rec.start_time,
                end_time=rec.end_time,
                duration_sec=rec.duration_sec,
                tcp_flags=rec.tcp_flags,
                dscp=rec.dscp,
                src_as=rec.src_as,
                dst_as=rec.dst_as,
                src_mac=rec.src_mac,
                dst_mac=rec.dst_mac,
                ingress_interface=rec.ingress_interface,
                egress_interface=rec.egress_interface,
                vlan_id=rec.vlan_id,
                flow_direction=rec.flow_direction,
                ts=ts,
            )
            db.add(row)

            key = (device_id, _window(ts), rec.src_ip, rec.dst_ip, rec.protocol)
            agg = agg_map.get(key)
            if agg is None:
                agg = db.query(FlowAggregate).filter(
                    FlowAggregate.device_id == device_id,
                    FlowAggregate.window_ts == key[1],
                    FlowAggregate.src_ip == rec.src_ip,
                    FlowAggregate.dst_ip == rec.dst_ip,
                    FlowAggregate.protocol == rec.protocol,
                ).first()
                if agg is None:
                    agg = FlowAggregate(
                        device_id=device_id,
                        window_ts=key[1],
                        src_ip=rec.src_ip,
                        dst_ip=rec.dst_ip,
                        protocol=rec.protocol,
                        bytes_total=0,
                        packets_total=0,
                        flow_count=0,
                    )
                    db.add(agg)
                agg_map[key] = agg
            agg.bytes_total += rec.bytes_total
            agg.packets_total += rec.packets_total
            agg.flow_count += 1

        db.commit()
        return len(records)
    except Exception as exc:
        db.rollback()
        logger.warning("流记录入库失败: %s", exc)
        return 0
    finally:
        db.close()


# ---------------------------------------------------------------- 查询
def flow_statistics(db: Session, hours: int = 24, device_id: Optional[int] = None,
                    store_id: Optional[int] = None) -> Dict[str, Any]:
    """流统计概览：总量、Top 应用、Top 源/目的、协议分布。"""
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(FlowRecordModel).filter(FlowRecordModel.ts >= since)
    if device_id:
        q = q.filter(FlowRecordModel.device_id == device_id)
    elif store_id:
        ids = [d.id for d in db.query(Device).filter(Device.store_id == store_id).all()]
        q = q.filter(FlowRecordModel.device_id.in_(ids or [-1]))

    total_bytes = 0
    total_packets = 0
    total_flows = 0
    unique_src = set()
    unique_dst = set()
    by_app: Dict[str, int] = {}
    by_proto: Dict[str, int] = {}

    for row in q.all():
        total_bytes += row.bytes_total
        total_packets += row.packets_total
        total_flows += 1
        if row.src_ip:
            unique_src.add(row.src_ip)
        if row.dst_ip:
            unique_dst.add(row.dst_ip)
        app = row.application or f"proto/{row.protocol}"
        by_app[app] = by_app.get(app, 0) + row.bytes_total
        by_proto[str(row.protocol)] = by_proto.get(str(row.protocol), 0) + row.bytes_total

    return {
        "total_flows": total_flows,
        "total_bytes": total_bytes,
        "total_packets": total_packets,
        "unique_sources": len(unique_src),
        "unique_destinations": len(unique_dst),
        "by_application": dict(sorted(by_app.items(), key=lambda kv: kv[1], reverse=True)[:15]),
        "by_protocol": dict(sorted(by_proto.items(), key=lambda kv: kv[1], reverse=True)),
    }


def top_talkers(db: Session, hours: int = 24, limit: int = 20, device_id: Optional[int] = None,
                direction: str = "src") -> List[Dict[str, Any]]:
    """Top 通信源 / 目的（按字节数）。"""
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(FlowRecordModel).filter(FlowRecordModel.ts >= since)
    if device_id:
        q = q.filter(FlowRecordModel.device_id == device_id)

    field = FlowRecordModel.src_ip if direction == "src" else FlowRecordModel.dst_ip
    rows = (
        q.with_entities(
            field.label("ip"),
            func.sum(FlowRecordModel.bytes_total).label("bytes_total"),
            func.sum(FlowRecordModel.packets_total).label("packets_total"),
            func.count(FlowRecordModel.id).label("flow_count"),
        )
        .group_by(field)
        .order_by(func.sum(FlowRecordModel.bytes_total).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "ip": r.ip,
            "bytes_total": r.bytes_total or 0,
            "packets_total": r.packets_total or 0,
            "flow_count": r.flow_count or 0,
            "mbps": round((r.bytes_total or 0) * 8 / 3600 / 1_000_000, 3),
        }
        for r in rows
        if r.ip
    ]


def top_conversations(db: Session, hours: int = 24, limit: int = 20,
                      device_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Top 会话对（源 → 目的）。"""
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(FlowRecordModel).filter(FlowRecordModel.ts >= since)
    if device_id:
        q = q.filter(FlowRecordModel.device_id == device_id)
    rows = (
        q.with_entities(
            FlowRecordModel.src_ip.label("src_ip"),
            FlowRecordModel.dst_ip.label("dst_ip"),
            FlowRecordModel.protocol.label("protocol"),
            func.sum(FlowRecordModel.bytes_total).label("bytes_total"),
            func.sum(FlowRecordModel.packets_total).label("packets_total"),
            func.count(FlowRecordModel.id).label("flow_count"),
        )
        .group_by(
            FlowRecordModel.src_ip,
            FlowRecordModel.dst_ip,
            FlowRecordModel.protocol,
        )
        .order_by(func.sum(FlowRecordModel.bytes_total).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "src_ip": r.src_ip,
            "dst_ip": r.dst_ip,
            "protocol": r.protocol,
            "bytes_total": r.bytes_total or 0,
            "packets_total": r.packets_total or 0,
            "flow_count": r.flow_count or 0,
            "mbps": round((r.bytes_total or 0) * 8 / 3600 / 1_000_000, 3),
        }
        for r in rows
    ]


def flow_timeseries(db: Session, hours: int = 24, device_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """流量趋势（按 5 分钟窗口）。"""
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(FlowAggregate).filter(FlowAggregate.window_ts >= since)
    if device_id:
        q = q.filter(FlowAggregate.device_id == device_id)
    rows = (
        q.with_entities(
            FlowAggregate.window_ts.label("ts"),
            func.sum(FlowAggregate.bytes_total).label("bytes_total"),
            func.sum(FlowAggregate.packets_total).label("packets_total"),
            func.sum(FlowAggregate.flow_count).label("flow_count"),
        )
        .group_by(FlowAggregate.window_ts)
        .order_by(FlowAggregate.window_ts)
        .all()
    )
    return [
        {
            "ts": r.ts.isoformat(),
            "bytes_total": r.bytes_total or 0,
            "mb": round((r.bytes_total or 0) / 1024 / 1024, 3),
            "packets_total": r.packets_total or 0,
            "flow_count": r.flow_count or 0,
        }
        for r in rows
    ]


def list_flows(db: Session, hours: int = 1, limit: int = 100, device_id: Optional[int] = None,
               keyword: Optional[str] = None, application: Optional[str] = None) -> List[Dict[str, Any]]:
    """流记录明细。"""
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(FlowRecordModel).filter(FlowRecordModel.ts >= since)
    if device_id:
        q = q.filter(FlowRecordModel.device_id == device_id)
    if application:
        q = q.filter(FlowRecordModel.application == application)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            (FlowRecordModel.src_ip.like(like))
            | (FlowRecordModel.dst_ip.like(like))
            | (FlowRecordModel.application.like(like))
        )
    rows = q.order_by(FlowRecordModel.ts.desc()).limit(limit).all()
    devices = {d.id: d for d in db.query(Device).all()}
    return [
        {
            "id": r.id,
            "device_id": r.device_id,
            "device_name": devices[r.device_id].name if r.device_id in devices else "",
            "exporter_ip": r.exporter_ip,
            "version": r.version,
            "src_ip": r.src_ip,
            "dst_ip": r.dst_ip,
            "src_port": r.src_port,
            "dst_port": r.dst_port,
            "protocol": r.protocol,
            "application": r.application,
            "bytes_total": r.bytes_total,
            "kb": round(r.bytes_total / 1024, 2),
            "packets_total": r.packets_total,
            "duration_sec": r.duration_sec,
            "tcp_flags": r.tcp_flags,
            "dscp": r.dscp,
            "ts": r.ts.isoformat(),
        }
        for r in rows
    ]


def cleanup_old_flows(days: int = 7) -> int:
    """清理过期流记录。"""
    cutoff = datetime.now() - timedelta(days=days)
    db = SessionLocal()
    try:
        n = db.query(FlowRecordModel).filter(FlowRecordModel.ts < cutoff).delete(synchronize_session=False)
        db.query(FlowAggregate).filter(FlowAggregate.window_ts < cutoff).delete(synchronize_session=False)
        db.commit()
        return n
    except Exception as exc:
        db.rollback()
        logger.warning("清理流记录失败: %s", exc)
        return 0
    finally:
        db.close()
