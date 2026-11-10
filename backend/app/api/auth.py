"""认证相关 API：登录、域控登录、用户管理、当前用户。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    client_ip,
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.db.session import get_db
from app.models.models import LoginLog, User
from app.services import ldap_service

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    source: str = Field(default="auto", description="auto/local/ldap")


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4)
    display_name: str = ""
    email: str = ""
    phone: str = ""
    role: str = "viewer"
    department: str = ""


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


def _log_login(db: Session, username: str, success: bool, source: str, ip: str, ua: str, message: str = "") -> None:
    db.add(
        LoginLog(
            username=username,
            success=success,
            source=source,
            ip=ip,
            user_agent=ua[:255],
            message=message[:255],
        )
    )
    db.commit()


@router.get("/config")
def auth_config() -> Dict[str, Any]:
    """前端登录页需要的公开配置。"""
    return {
        "ldap_enabled": settings.ldap_enabled,
        "ldap_domain": settings.ldap_domain,
        "app_name": settings.app_name,
        "version": settings.app_version,
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    user = db.query(User).filter(User.username == payload.username).first()
    source = payload.source
    use_ldap = False

    if source == "ldap" or (source == "auto" and user is None and settings.ldap_enabled):
        use_ldap = True

    if use_ldap:
        result = ldap_service.authenticate(payload.username, payload.password)
        if not result.get("ok"):
            _log_login(db, payload.username, False, "ldap", ip, ua, result.get("message", ""))
            raise HTTPException(status_code=401, detail=result.get("message") or "域账号认证失败")
        user = ldap_service.sync_user(db, result)
    else:
        if user is None:
            _log_login(db, payload.username, False, "local", ip, ua, "用户不存在")
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user.is_active:
            _log_login(db, payload.username, False, user.source, ip, ua, "账号已停用")
            raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
        if not verify_password(payload.password, user.password_hash):
            _log_login(db, payload.username, False, user.source, ip, ua, "密码错误")
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if user.source == "ldap":
            # 域用户：验证域口令，失败则回落本地口令
            result = ldap_service.authenticate(payload.username, payload.password)
            if not result.get("ok") and not verify_password(payload.password, user.password_hash):
                _log_login(db, payload.username, False, "ldap", ip, ua, "密码错误")
                raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_access_token(user.username, user.role)
    user.last_login_at = datetime.now()
    db.commit()
    _log_login(db, user.username, True, user.source, ip, ua, "登录成功")

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "user": _user_payload(user),
    }


def _user_payload(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "source": user.source,
        "department": user.department,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return _user_payload(user)


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    users = db.query(User).order_by(User.id).all()
    return {"items": [_user_payload(u) for u in users]}


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if payload.role not in ("admin", "operator", "viewer"):
        raise HTTPException(status_code=400, detail="角色非法")
    user = User(
        username=payload.username,
        display_name=payload.display_name or payload.username,
        email=payload.email,
        phone=payload.phone,
        role=payload.role,
        department=payload.department,
        password_hash=hash_password(payload.password),
        source="local",
    )
    db.add(user)
    db.commit()
    return _user_payload(user)


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.department is not None:
        user.department = payload.department
    if payload.role is not None:
        if payload.role not in ("admin", "operator", "viewer"):
            raise HTTPException(status_code=400, detail="角色非法")
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    db.commit()
    return _user_payload(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/login-logs")
def login_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    logs = db.query(LoginLog).order_by(LoginLog.id.desc()).limit(min(limit, 500)).all()
    return {
        "items": [
            {
                "id": l.id,
                "username": l.username,
                "success": l.success,
                "source": l.source,
                "ip": l.ip,
                "user_agent": l.user_agent,
                "message": l.message,
                "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
    }


# ---------------------------------------------------------------- 域控
@router.get("/ldap/test")
def ldap_test(_: User = Depends(require_admin)) -> Dict[str, Any]:
    return ldap_service.test_connection()


@router.get("/ldap/users")
def ldap_users(_: User = Depends(require_admin)) -> Dict[str, Any]:
    return ldap_service.list_domain_users()


@router.post("/ldap/sync")
def ldap_sync(db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    return ldap_service.sync_all_users(db)
