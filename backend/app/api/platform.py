"""运行时设置、采集服务开关、拓扑、审计日志、AI 助手 API。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import (
    AiConversation,
    AiMessage,
    AuditLog,
    Device,
    Store,
    TopologyNeighbor,
    User,
)
from app.services import ai_service, audit_service, settings_service
from app.services.collector_control import apply_switches, status as collector_status

settings_router = APIRouter(prefix="/api/settings", tags=["平台设置"])
topology_router = APIRouter(prefix="/api/topology", tags=["网络拓扑"])
audit_router = APIRouter(prefix="/api/audit", tags=["审计日志"])
ai_router = APIRouter(prefix="/api/ai", tags=["AI 助手"])


# ================================================================ 平台设置
@settings_router.get("")
def get_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """读取全部运行时设置（敏感项脱敏）。"""
    return {
        "items": settings_service.get_all(db),
        "collector": collector_status(),
    }


@settings_router.put("")
async def update_settings(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """更新运行时设置，并按需动态启停采集服务。"""
    changed = settings_service.update_many(db, payload)
    applied = await apply_switches(changed)
    audit_service.record_action(
        db, user.username, "settings.update", "平台设置",
        {"changed": changed}, "",
    )
    return {
        "ok": True,
        "changed": changed,
        "applied": applied,
        "collector": collector_status(),
    }


@settings_router.get("/collector-status")
def get_collector_status(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """采集服务运行状态。"""
    return collector_status()


class CollectorSwitch(BaseModel):
    target: str = Field(description="snmp / telemetry / netflow / syslog")
    enabled: bool = True


# 采集通道的中文名（用于错误提示）
_TARGET_LABEL = {
    "snmp": "SNMP 轮询",
    "telemetry": "gNMI 遥测",
    "netflow": "NetFlow 采集",
    "syslog": "Syslog 接收",
}


@settings_router.post("/collector/toggle")
async def toggle_collector(
    payload: CollectorSwitch,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """启用 / 关闭指定采集服务。"""
    key_map = {
        "snmp": "collector.snmp_enabled",
        "telemetry": "collector.telemetry_enabled",
        "netflow": "collector.netflow_enabled",
        "syslog": "collector.syslog_enabled",
    }
    key = key_map.get(payload.target)
    if not key:
        raise HTTPException(status_code=400, detail="未知的采集通道")
    settings_service.set_value(db, key, payload.enabled)
    applied = await apply_switches([key])
    audit_service.record_action(
        db, user.username, "collector.toggle", payload.target,
        {"enabled": payload.enabled}, "",
    )
    # applied 里对应项为 False 表示**实际**启停失败——设置值改了但服务没起来。
    # 必须如实告诉前端，否则界面会显示「已启用」而实际没开。
    actually_applied = applied.get(key, True)
    message = ""
    if not actually_applied:
        message = (
            f"{_TARGET_LABEL.get(payload.target, payload.target)} 启动失败："
            "监听端口不可用（可能被系统排除端口范围占用或被其它程序占用），"
            "请查看日志，或在平台设置中手动指定其它端口"
        )
    return {
        "ok": True,
        "target": payload.target,
        "enabled": payload.enabled,
        "applied": applied,
        "actually_applied": actually_applied,
        "message": message,
        "collector": collector_status(),
    }


# ================================================================ 拓扑
@topology_router.get("/neighbors")
def list_neighbors(
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    protocol: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """邻居关系列表。"""
    q = db.query(TopologyNeighbor)
    if device_id:
        q = q.filter(TopologyNeighbor.local_device_id == device_id)
    if protocol:
        q = q.filter(TopologyNeighbor.protocol == protocol)
    rows = q.order_by(TopologyNeighbor.local_device_name).all()

    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s.name for s in db.query(Store).all()}
    if store_id:
        allowed = {d.id for d in devices.values() if d.store_id == store_id}
        rows = [r for r in rows if r.local_device_id in allowed]

    return {
        "items": [
            {
                "id": r.id,
                "local_device_id": r.local_device_id,
                "local_device_name": r.local_device_name,
                "local_device_ip": r.local_device_ip,
                "local_interface": r.local_interface,
                "remote_device": r.remote_device,
                "remote_ip": r.remote_ip,
                "remote_interface": r.remote_interface,
                "platform": r.platform,
                "protocol": r.protocol,
                "store_name": stores.get(
                    devices[r.local_device_id].store_id, "-"
                ) if r.local_device_id in devices else "-",
                "collected_at": r.collected_at.isoformat(),
            }
            for r in rows
        ]
    }


@topology_router.get("/graph")
def topology_graph(
    store_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """拓扑图数据：节点 + 连线。"""
    devices = db.query(Device).all()
    stores = {s.id: s.name for s in db.query(Store).all()}
    if store_id:
        devices = [d for d in devices if d.store_id == store_id]
    dev_ids = {d.id for d in devices}

    neighbors = db.query(TopologyNeighbor).all()
    edges = [
        {
            "source": n.local_device_name,
            "target": n.remote_device,
            "source_ip": n.local_device_ip,
            "target_ip": n.remote_ip,
            "local_interface": n.local_interface,
            "remote_interface": n.remote_interface,
            "protocol": n.protocol,
        }
        for n in neighbors
        if n.local_device_id in dev_ids
    ]

    # 节点：所有设备 + 邻居中出现但不在设备表中的远端设备
    nodes = [
        {
            "id": d.name,
            "ip": d.ip,
            "ip_label": d.name,
            "category": "device",
            "model": d.model or "",
            "vendor": d.vendor,
            "status": d.status,
            "store_name": stores.get(d.store_id, "未分配"),
            "value": 1,
        }
        for d in devices
    ]
    known = {n["id"] for n in nodes}
    for e in edges:
        if e["target"] not in known:
            nodes.append(
                {
                    "id": e["target"],
                    "ip": e["target_ip"],
                    "ip_label": e["target"],
                    "category": "neighbor",
                    "model": "",
                    "vendor": "",
                    "status": "unknown",
                    "store_name": "-",
                    "value": 1,
                }
            )
            known.add(e["target"])

    return {"nodes": nodes, "edges": edges, "total": len(nodes)}


@topology_router.post("/collect")
async def collect_topology(
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """采集 LLDP/CDP 邻居信息。"""
    from app.collectors.topology import collect_neighbors

    q = db.query(Device).filter(Device.enabled.is_(True))
    if device_id:
        q = q.filter(Device.id == device_id)
    devices = q.all()
    if not devices:
        return {"ok": True, "added": 0, "devices": 0, "message": "没有可采集的设备"}

    # 并发采集：串行会在多设备场景下叠加 SNMP 超时
    semaphore = asyncio.Semaphore(8)

    async def _one(dev):
        async with semaphore:
            try:
                return await collect_neighbors(dev)
            except Exception:
                return []

    results = await asyncio.gather(*(_one(d) for d in devices), return_exceptions=True)

    total = 0
    for dev, neighbors in zip(devices, results):
        if isinstance(neighbors, Exception) or not neighbors:
            continue
        for nb in neighbors:
            existing = (
                db.query(TopologyNeighbor)
                .filter(
                    TopologyNeighbor.local_device_id == nb.local_device_id,
                    TopologyNeighbor.local_interface == nb.local_interface,
                    TopologyNeighbor.remote_device == nb.remote_device,
                    TopologyNeighbor.remote_interface == nb.remote_interface,
                )
                .first()
            )
            if existing:
                existing.remote_ip = nb.remote_ip
                existing.platform = nb.platform
                existing.protocol = nb.protocol
                existing.collected_at = nb.collected_at
            else:
                db.add(
                    TopologyNeighbor(
                        local_device_id=nb.local_device_id,
                        local_device_name=nb.local_device_name,
                        local_device_ip=dev.ip,
                        local_interface=nb.local_interface,
                        remote_device=nb.remote_device,
                        remote_ip=nb.remote_ip,
                        remote_interface=nb.remote_interface,
                        platform=nb.platform,
                        protocol=nb.protocol,
                        collected_at=nb.collected_at,
                    )
                )
                total += 1
    db.commit()
    audit_service.record_action(db, user.username, "topology.collect", f"device={device_id}", {"new": total}, "")
    return {"ok": True, "added": total, "devices": len(devices)}


@topology_router.delete("/neighbors")
def clear_topology(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    n = db.query(TopologyNeighbor).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": n}


# ================================================================ 审计日志
@audit_router.get("")
def list_audit(
    username: Optional[str] = None,
    action: Optional[str] = None,
    keyword: Optional[str] = None,
    days: int = Query(7, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """操作审计日志。"""
    return audit_service.list_audit_logs(
        db, username=username, action=action, keyword=keyword,
        days=days, page=page, page_size=page_size,
    )


@audit_router.get("/actions")
def audit_actions(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """已记录的操作类型列表。"""
    return {"items": audit_service.list_actions(db)}


@audit_router.get("/summary")
def audit_summary(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """审计统计：按操作类型与用户聚合。"""
    from datetime import timedelta

    since = datetime.now() - timedelta(days=days)
    rows = db.query(AuditLog).filter(AuditLog.created_at >= since).all()
    by_action: Dict[str, int] = {}
    by_user: Dict[str, int] = {}
    for r in rows:
        by_action[r.action] = by_action.get(r.action, 0) + 1
        by_user[r.username] = by_user.get(r.username, 0) + 1
    return {
        "total": len(rows),
        "by_action": dict(sorted(by_action.items(), key=lambda kv: kv[1], reverse=True)),
        "by_user": dict(sorted(by_user.items(), key=lambda kv: kv[1], reverse=True)[:20]),
    }


# ================================================================ AI 助手
@ai_router.get("/providers")
def ai_providers(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """可用的 AI 服务商。"""
    return {"items": ai_service.provider_options()}


@ai_router.get("/config")
def ai_config(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """AI 配置（API Key 脱敏）。"""
    cfg = ai_service.load_config()
    return {
        "enabled": cfg.enabled,
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.resolved_model(),
        "api_key": "********" if cfg.api_key else "",
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "timeout": cfg.timeout,
        "context_enabled": cfg.context_enabled,
        "system_prompt": cfg.system_prompt,
    }


@ai_router.post("/config")
async def update_ai_config(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """更新 AI 配置。"""
    changed = settings_service.update_many(db, {f"ai.{k}": v for k, v in payload.items()})
    audit_service.record_action(db, user.username, "ai.config", "AI 配置", {"changed": changed}, "")
    return {"ok": True, "changed": changed}


@ai_router.post("/test")
async def ai_test(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """测试 AI 服务连通性。"""
    cfg = ai_service.load_config()
    return await ai_service.test_connection(cfg)


@ai_router.get("/conversations")
def ai_conversations(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """我的对话列表。"""
    rows = (
        db.query(AiConversation)
        .filter(AiConversation.username == user.username)
        .order_by(AiConversation.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": c.id,
                "title": c.title,
                "provider": c.provider,
                "model": c.model,
                "message_count": c.message_count,
                "device_id": c.device_id,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in rows
        ]
    }


@ai_router.get("/conversations/{conversation_id}")
def ai_conversation_detail(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """对话详情（含消息）。"""
    conv = db.get(AiConversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    msgs = db.query(AiMessage).filter(AiMessage.conversation_id == conv.id).order_by(AiMessage.id).all()
    return {
        "id": conv.id,
        "title": conv.title,
        "provider": conv.provider,
        "model": conv.model,
        "device_id": conv.device_id,
        "created_at": conv.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "elapsed_ms": m.elapsed_ms,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


@ai_router.delete("/conversations/{conversation_id}")
def ai_delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    conv = db.get(AiConversation, conversation_id)
    if conv and conv.username == user.username:
        db.delete(conv)
        db.commit()
    return {"ok": True}


class AIChatRequest(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: Optional[int] = None
    device_id: Optional[int] = None
    context_enabled: bool = True
    hours: int = 6


@ai_router.post("/chat")
async def ai_chat(
    payload: AIChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """流式对话（Server-Sent Events）。"""
    cfg = ai_service.load_config()
    if not cfg.enabled:
        async def _err():
            yield f"data: {json.dumps({'error': 'AI 助手未启用，请在系统管理中配置并启用'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(_err(), media_type="text/event-stream")

    # 上下文
    context = ""
    if payload.context_enabled and cfg.context_enabled:
        context = ai_service.build_network_context(
            db,
            device_id=payload.device_id,
            hours=payload.hours,
        )

    # 会话
    conv = None
    history: List[Dict[str, str]] = []
    if payload.conversation_id:
        conv = db.get(AiConversation, payload.conversation_id)
        if conv is not None:
            msgs = (
                db.query(AiMessage)
                .filter(AiMessage.conversation_id == conv.id)
                .order_by(AiMessage.id.desc())
                .limit(10)
                .all()
            )
            for m in reversed(msgs):
                if m.role in ("user", "assistant"):
                    history.append({"role": m.role, "content": m.content})
    if conv is None:
        conv = AiConversation(
            title=payload.question[:60],
            username=user.username,
            provider=cfg.provider,
            model=cfg.resolved_model(),
            device_id=payload.device_id,
            context_enabled=payload.context_enabled,
        )
        db.add(conv)
        db.commit()

    user_msg = AiMessage(conversation_id=conv.id, role="user", content=payload.question)
    db.add(user_msg)
    conv.message_count = (conv.message_count or 0) + 1
    db.commit()

    async def _gen():
        started = time.perf_counter()
        buf: List[str] = []
        async for delta in ai_service.stream_chat(cfg, payload.question, context, history):
            buf.append(delta)
            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        text = "".join(buf)
        elapsed = int((time.perf_counter() - started) * 1000)
        try:
            db.add(
                AiMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content=text,
                    elapsed_ms=elapsed,
                )
            )
            conv.message_count = (conv.message_count or 0) + 1
            conv.updated_at = datetime.now()
            db.commit()
        except Exception:
            pass
        yield f"data: {json.dumps({'done': True, 'conversation_id': conv.id, 'elapsed_ms': elapsed}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@ai_router.post("/context")
def ai_preview_context(
    device_id: Optional[int] = None,
    hours: int = 6,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """预览将要发送给模型的上下文（便于调试）。"""
    text = ai_service.build_network_context(db, device_id=device_id, hours=hours)
    return {"content": text, "length": len(text)}
