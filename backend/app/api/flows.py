"""NetFlow / IPFIX 与 gNMI Telemetry API。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.collectors.netflow import DEFAULT_LISTEN_PORTS, DEFAULT_TCP_PORTS
from app.collectors.telemetry import DEFAULT_MAPPINGS, TelemetryPathMapper
from app.core.security import get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import (
    Device,
    FlowExporter,
    FlowRecord,
    Store,
    TelemetryConfig,
    TelemetryPathMapping,
    TelemetrySample,
    User,
)
from app.services import netflow_service
from app.services.telemetry_service import (
    client_sessions,
    collector_server,
    store_telemetry_samples,
    telemetry_status,
)

netflow_router = APIRouter(prefix="/api/netflow", tags=["NetFlow 流量分析"])
telemetry_router = APIRouter(prefix="/api/telemetry", tags=["Telemetry 遥测"])


# ================================================================ NetFlow
class ExporterIn(BaseModel):
    device_id: Optional[int] = None
    name: str = ""
    version: str = "v9"
    listen_port: int = 2055
    source_ip: str = ""
    enabled: bool = True
    remark: str = ""


@netflow_router.get("/statistics")
def flow_statistics(
    hours: int = Query(24, ge=1, le=720),
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """流统计概览。"""
    return netflow_service.flow_statistics(db, hours=hours, device_id=device_id, store_id=store_id)


@netflow_router.get("/top-talkers")
def top_talkers(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(20, ge=1, le=200),
    direction: str = Query("src", description="src 源 / dst 目的"),
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Top 通信源 / 目的。"""
    return {
        "items": netflow_service.top_talkers(
            db, hours=hours, limit=limit, device_id=device_id, direction=direction
        ),
        "direction": direction,
    }


@netflow_router.get("/conversations")
def conversations(
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(20, ge=1, le=200),
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Top 会话对。"""
    return {
        "items": netflow_service.top_conversations(db, hours=hours, limit=limit, device_id=device_id)
    }


@netflow_router.get("/timeseries")
def flow_timeseries(
    hours: int = Query(24, ge=1, le=720),
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """流量趋势。"""
    return {"items": netflow_service.flow_timeseries(db, hours=hours, device_id=device_id)}


@netflow_router.get("/flows")
def list_flows(
    hours: int = Query(1, ge=1, le=168),
    limit: int = Query(100, ge=1, le=1000),
    device_id: Optional[int] = None,
    keyword: Optional[str] = None,
    application: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """流记录明细。"""
    items = netflow_service.list_flows(
        db, hours=hours, limit=limit, device_id=device_id, keyword=keyword, application=application
    )
    return {"items": items}


@netflow_router.get("/exporters")
def list_exporters(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """导出器配置列表。"""
    rows = db.query(FlowExporter).order_by(FlowExporter.id).all()
    devices = {d.id: d for d in db.query(Device).all()}
    return {
        "items": [
            {
                "id": e.id,
                "device_id": e.device_id,
                "device_name": devices[e.device_id].name if e.device_id in devices else "",
                "name": e.name,
                "version": e.version,
                "listen_port": e.listen_port,
                "source_ip": e.source_ip,
                "enabled": e.enabled,
                "remark": e.remark,
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ],
        "listen_ports": DEFAULT_LISTEN_PORTS,
    }


@netflow_router.post("/exporters")
def create_exporter(
    payload: ExporterIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    row = FlowExporter(**payload.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id}


@netflow_router.put("/exporters/{exporter_id}")
def update_exporter(
    exporter_id: int,
    payload: ExporterIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    row = db.get(FlowExporter, exporter_id)
    if row is None:
        raise HTTPException(status_code=404, detail="导出器配置不存在")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return {"id": row.id}


@netflow_router.delete("/exporters/{exporter_id}")
def delete_exporter(
    exporter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    row = db.get(FlowExporter, exporter_id)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@netflow_router.get("/collector-status")
def collector_status(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """采集器运行状态。"""
    from app.collectors.netflow import NetflowCollector

    # 全局单例在 app.state 中
    return {"ok": True}


@netflow_router.delete("/cleanup")
def cleanup_flows(
    days: int = Query(7, ge=1),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    n = netflow_service.cleanup_old_flows(days)
    return {"ok": True, "deleted": n}


# ================================================================ Telemetry
class TelemetryConfigIn(BaseModel):
    device_id: Optional[int] = None
    name: str = ""
    mode: str = "collector"
    target_addr: str = ""
    target_port: int = 57400
    subscribe_paths: List[str] = Field(default_factory=list)
    sample_interval_ms: int = 10000
    username: str = ""
    password: str = ""
    enabled: bool = True


@telemetry_router.get("/status")
def status(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """遥测服务运行状态与活跃会话。"""
    return telemetry_status()


def _custom_mappings(db: Session) -> List[TelemetryPathMapping]:
    """已启用的自定义路径映射。"""
    return (
        db.query(TelemetryPathMapping)
        .filter(TelemetryPathMapping.enabled.is_(True))
        .order_by(TelemetryPathMapping.id)
        .all()
    )


def _mapping_rows(db: Session) -> List[Dict[str, Any]]:
    """内置映射 + 自定义映射，统一成一份列表。"""
    rows: List[Dict[str, Any]] = [
        {
            "path": m.path,
            "metric": m.metric,
            "per_interface": m.metric.startswith("if_"),
            "source": "builtin",
            "description": "",
            "id": None,
        }
        for m in DEFAULT_MAPPINGS
    ]
    for m in _custom_mappings(db):
        rows.append(
            {
                "path": m.path,
                "metric": m.metric,
                "per_interface": m.per_interface,
                "source": "custom",
                "description": m.description,
                "id": m.id,
            }
        )
    return rows


@telemetry_router.get("/paths")
def list_paths(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """可订阅的遥测路径（内置映射表 + 用户自定义映射）。"""
    rows = _mapping_rows(db)
    return {
        "items": rows,
        "total": len(rows),
        "builtin_total": len(DEFAULT_MAPPINGS),
        "custom_total": len(rows) - len(DEFAULT_MAPPINGS),
    }


class PathMappingIn(BaseModel):
    """自定义遥测路径映射。"""

    path: str = Field(min_length=1, max_length=512)
    metric: str = Field(min_length=1, max_length=48)
    description: str = Field(default="", max_length=255)
    per_interface: bool = False
    scale: float = 1.0
    enabled: bool = True


@telemetry_router.post("/paths")
def create_path_mapping(
    payload: PathMappingIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """新增自定义遥测路径映射。

    厂商私有模型路径不统一，用户可以把自己知道的路径加进来订阅，
    无需等平台适配。
    """
    path = payload.path.strip()
    if not path:
        raise HTTPException(status_code=400, detail="路径不能为空")
    exists = (
        db.query(TelemetryPathMapping)
        .filter(TelemetryPathMapping.path == path)
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="该路径已存在")
    dup_builtin = next((m for m in DEFAULT_MAPPINGS if m.path == path), None)
    if dup_builtin:
        raise HTTPException(status_code=400, detail="该路径已在内置映射表中")
    row = TelemetryPathMapping(
        path=path,
        metric=payload.metric.strip(),
        description=payload.description.strip(),
        per_interface=payload.per_interface,
        scale=payload.scale,
        enabled=payload.enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "path": row.path,
        "metric": row.metric,
        "description": row.description,
        "per_interface": row.per_interface,
        "scale": row.scale,
        "enabled": row.enabled,
        "source": "custom",
    }


@telemetry_router.delete("/paths/{mapping_id}")
def delete_path_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    """删除自定义路径映射。"""
    row = db.get(TelemetryPathMapping, mapping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="映射不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@telemetry_router.get("/configs")
def list_configs(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    rows = db.query(TelemetryConfig).order_by(TelemetryConfig.id).all()
    devices = {d.id: d for d in db.query(Device).all()}
    return {
        "items": [
            {
                "id": c.id,
                "device_id": c.device_id,
                "device_name": devices[c.device_id].name if c.device_id in devices else "",
                "name": c.name,
                "mode": c.mode,
                "target_addr": c.target_addr,
                "target_port": c.target_port,
                "subscribe_paths": json.loads(c.subscribe_paths or "[]"),
                "sample_interval_ms": c.sample_interval_ms,
                "username": c.username,
                "password": "********" if c.password else "",
                "enabled": c.enabled,
                "status": c.status,
                "status_message": c.status_message,
                "received_count": c.received_count,
                "last_update_at": c.last_update_at.isoformat() if c.last_update_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in rows
        ]
    }


@telemetry_router.post("/configs")
async def create_config(
    payload: TelemetryConfigIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    cfg = TelemetryConfig(
        device_id=payload.device_id,
        name=payload.name,
        mode=payload.mode,
        target_addr=payload.target_addr,
        target_port=payload.target_port,
        subscribe_paths=json.dumps(payload.subscribe_paths, ensure_ascii=False),
        sample_interval_ms=payload.sample_interval_ms,
        username=payload.username,
        password=payload.password,
        enabled=payload.enabled,
    )
    db.add(cfg)
    db.commit()
    return {"id": cfg.id}


@telemetry_router.put("/configs/{config_id}")
async def update_config(
    config_id: int,
    payload: TelemetryConfigIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    cfg = db.get(TelemetryConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="遥测配置不存在")
    data = payload.model_dump()
    if data.get("password") in ("", "********"):
        data.pop("password", None)
    for key, value in data.items():
        if key == "subscribe_paths":
            value = json.dumps(value, ensure_ascii=False)
        setattr(cfg, key, value)
    db.commit()
    return {"id": cfg.id}


@telemetry_router.delete("/configs/{config_id}")
async def delete_config(
    config_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    cfg = db.get(TelemetryConfig, config_id)
    if cfg:
        if cfg.device_id in client_sessions:
            await client_sessions[cfg.device_id].stop()
            client_sessions.pop(cfg.device_id, None)
        db.delete(cfg)
        db.commit()
    return {"ok": True}


@telemetry_router.get("/samples")
def list_samples(
    device_id: Optional[int] = None,
    metric: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """遥测样本明细（原始路径）。"""
    q = db.query(TelemetrySample)
    if device_id:
        q = q.filter(TelemetrySample.device_id == device_id)
    if metric:
        q = q.filter(TelemetrySample.metric == metric)
    rows = q.order_by(TelemetrySample.id.desc()).limit(limit).all()
    return {
        "items": [
            {
                "id": s.id,
                "device_id": s.device_id,
                "ts": s.ts.isoformat(),
                "metric": s.metric,
                "value_num": s.value_num,
                "value_text": s.value_text,
                "interface": s.interface,
                "source_path": s.source_path,
                "source": s.source,
            }
            for s in rows
        ]
    }


@telemetry_router.post("/simulate")
def simulate(
    device_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """注入一条模拟遥测样本（用于演示与联调）。"""
    from app.collectors.telemetry import TelemetrySample

    now = datetime.now()
    samples = [
        TelemetrySample(
            device_id=device_id,
            metric="cpu_usage",
            value=42.5,
            timestamp=now,
            source_path="system/cpu/state/utilization",
        ),
        TelemetrySample(
            device_id=device_id,
            metric="if_in_octets",
            value=1_000_000_000,
            interface="TenGigabitEthernet1/0/1",
            timestamp=now,
            source_path="interfaces/interface[name=TenGigabitEthernet1/0/1]/state/counters/in-octets",
            per_interface=True,
        ),
        TelemetrySample(
            device_id=device_id,
            metric="if_out_octets",
            value=800_000_000,
            interface="TenGigabitEthernet1/0/1",
            timestamp=now,
            source_path="interfaces/interface[name=TenGigabitEthernet1/0/1]/state/counters/out-octets",
            per_interface=True,
        ),
    ]
    n = store_telemetry_samples(samples, device_id, source="simulate")
    return {"ok": True, "stored": n}
