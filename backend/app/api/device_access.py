"""设备接入 API：本地账号、配置变更审批、TACACS/RADIUS 配置与会话日志。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.crypto import MASK
from app.core.security import client_ip, get_current_user, require_admin, require_approver, require_operator
from app.db.session import get_db
from app.models.models import AaaSession, ConfigChangeRequest, Device, User
from app.services import aaa_service, device_access_service
from app.services.audit_service import record_action

router = APIRouter(prefix="/api/device-access", tags=["设备接入与审批"])


# ---------------------------------------------------------------- 设备账号
class DeviceAccountIn(BaseModel):
    device_id: int
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)
    enable_password: str = ""
    privilege: int = 15
    auth_type: str = "local"
    remark: str = ""
    enabled: bool = True


@router.get("/accounts")
def list_accounts(
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """设备本地账号列表。"""
    return {"items": device_access_service.list_device_accounts(db, device_id)}


@router.post("/accounts")
def create_account(
    payload: DeviceAccountIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """新增设备本地账号。"""
    try:
        acc = device_access_service.create_device_account(
            db,
            device_id=payload.device_id,
            username=payload.username,
            password=payload.password,
            enable_password=payload.enable_password,
            privilege=payload.privilege,
            auth_type=payload.auth_type,
            remark=payload.remark,
            operator=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": acc.id, "username": acc.username}


@router.put("/accounts/{account_id}")
def update_account(
    account_id: int,
    payload: DeviceAccountIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """更新设备账号。"""
    from app.models.models import DeviceAccount

    acc = db.get(DeviceAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    acc.username = payload.username
    if payload.password and payload.password != MASK:
        acc.password = payload.password
    if payload.enable_password and payload.enable_password != MASK:
        acc.enable_password = payload.enable_password
    acc.privilege = payload.privilege
    acc.auth_type = payload.auth_type
    acc.remark = payload.remark
    acc.enabled = payload.enabled
    device_access_service.log_audit(
        db,
        user.username,
        "device_account.update",
        f"account={account_id}",
        f"username={payload.username}",
        client_ip(request),
    )
    db.commit()
    return {"id": acc.id}


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """删除设备账号。"""
    from app.models.models import DeviceAccount

    acc = db.get(DeviceAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    username = acc.username
    db.delete(acc)
    device_access_service.log_audit(
        db, user.username, "device_account.delete", f"account={account_id}",
        f"username={username}", client_ip(request),
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 配置变更
class ChangeRequestIn(BaseModel):
    device_id: int
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    config_content: str = ""
    change_type: str = "config"
    target_backup_id: Optional[int] = None


@router.get("/changes")
def list_changes(
    status: Optional[str] = None,
    device_id: Optional[int] = None,
    applicant: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """配置变更申请列表。"""
    return {"items": device_access_service.list_change_requests(db, status, device_id, applicant)}


@router.post("/changes")
def create_change(
    payload: ChangeRequestIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """提交配置变更申请。"""
    try:
        req = device_access_service.create_change_request(
            db,
            device_id=payload.device_id,
            title=payload.title,
            description=payload.description,
            config_content=payload.config_content,
            change_type=payload.change_type,
            target_backup_id=payload.target_backup_id,
            applicant=user.username,
            ip=client_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": req.id, "ticket_no": req.ticket_no, "status": req.status}


class ReviewIn(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/changes/{change_id}/review")
def review_change(
    change_id: int,
    payload: ReviewIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_approver),
) -> Dict[str, Any]:
    """审批配置变更（需审批员或管理员权限）。"""
    try:
        req = device_access_service.review_change_request(
            db,
            request_id=change_id,
            approve=payload.approve,
            reviewer=user.username,
            comment=payload.comment,
            ip=client_ip(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": req.id, "status": req.status}


@router.post("/changes/{change_id}/apply")
async def apply_change(
    change_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """下发已批准的配置变更。"""
    result = await device_access_service.apply_change_request(
        db, change_id, operator=user.username, ip=client_ip(request)
    )
    return result


@router.get("/changes/{change_id}")
def get_change(
    change_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """配置变更详情。"""
    items = device_access_service.list_change_requests(db)
    for item in items:
        if item["id"] == change_id:
            return item
    raise HTTPException(status_code=404, detail="申请不存在")


# ---------------------------------------------------------------- AAA
@router.get("/aaa/status")
def aaa_status(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """TACACS+/RADIUS 服务状态。"""
    from app.services.settings_service import effective_settings

    cfg = effective_settings()
    status = aaa_service.aaa_status()
    return {
        **status,
        "enabled": bool(cfg.get("aaa.enabled")),
        "secret_masked": "********" if cfg.get("aaa.tacacs_secret") else "",
        "tacacs_port": int(cfg.get("aaa.tacacs_port") or 49),
        "radius_port": int(cfg.get("aaa.radius_port") or 1812),
    }


class AaaConfigIn(BaseModel):
    enabled: bool = False
    tacacs_secret: str = ""
    tacacs_port: int = 49
    radius_port: int = 1812


@router.put("/aaa/config")
async def update_aaa_config(
    payload: AaaConfigIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """配置并启停 TACACS+/RADIUS 服务。"""
    from app.services.collector_control import apply_switches
    from app.services.settings_service import update_many

    data = {
        "aaa.enabled": payload.enabled,
        "aaa.tacacs_port": payload.tacacs_port,
        "aaa.radius_port": payload.radius_port,
    }
    if payload.tacacs_secret and payload.tacacs_secret != MASK:
        data["aaa.tacacs_secret"] = payload.tacacs_secret
    changed = update_many(db, data)

    # 重启 AAA 服务使配置生效
    from app.services.aaa_service import start_aaa_services, stop_aaa_services

    await stop_aaa_services()
    if payload.enabled:
        await start_aaa_services(
            tacacs_port=payload.tacacs_port,
            radius_port=payload.radius_port,
        )
    record_action(db, user.username, "aaa.config", "AAA 服务", {"changed": changed}, "")
    return {"ok": True, "changed": changed, "status": aaa_service.aaa_status()}


@router.get("/aaa/sessions")
def aaa_sessions(
    limit: int = Query(100, ge=1, le=500),
    result_filter: Optional[str] = Query(None, alias="result"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """TACACS+/RADIUS 认证会话日志。"""
    q = db.query(AaaSession)
    if result_filter:
        q = q.filter(AaaSession.result == result_filter)
    rows = q.order_by(AaaSession.id.desc()).limit(limit).all()
    return {
        "items": [
            {
                "id": r.id,
                "protocol": r.protocol,
                "username": r.username,
                "nas_ip": r.nas_ip,
                "nas_port": r.nas_port,
                "service": r.service,
                "result": r.result,
                "reason": r.reason,
                "privilege": r.privilege,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/aaa/test")
async def aaa_test(
    username: str = Query(...),
    password: str = Query(...),
    nas_ip: str = Query(""),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    """测试认证（不真正连接设备）。"""
    engine = aaa_service.get_engine()
    result = await engine.authenticate(username, password, nas_ip)
    return {
        "ok": result.ok,
        "reason": result.reason,
        "privilege": result.privilege,
        "source": result.source,
    }
