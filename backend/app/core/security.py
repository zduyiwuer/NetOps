"""安全：密码哈希、JWT、角色依赖。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.roles import (
    ROLE_LEVEL,
    can_approve,
    effective_roles,
    normalize_roles,
)
from app.db.session import get_db
from app.models.models import User


def _encode(password: str) -> bytes:
    """bcrypt 仅接受前 72 字节。"""
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    """使用 bcrypt 原生 API（避免 passlib 1.7 与 bcrypt 4.1+ 的不兼容）。"""
    import bcrypt

    return bcrypt.hashpw(_encode(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    import bcrypt

    try:
        return bcrypt.checkpw(_encode(plain), hashed.encode("utf-8"))
    except Exception:
        return False

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def create_access_token(
    username: str,
    role: str,
    roles: Optional[List[str]] = None,
    expires_minutes: Optional[int] = None,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {
        "sub": username,
        "role": role,
        "roles": normalize_roles(roles) or normalize_roles([role]),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态无效或已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用")
    return user


def require_role(min_role: str):
    """角色门槛依赖：admin > operator > viewer。

    多角色用户取所有角色中的**最高层级**判定——
    同时具备 operator 与 approver 的用户，操作门槛按 operator 通过。
    """

    def _dep(user: User = Depends(get_current_user)) -> User:
        levels = [ROLE_LEVEL.get(r, 0) for r in effective_roles(user)]
        current = max(levels, default=0)
        if current < ROLE_LEVEL.get(min_role, 99):
            raise HTTPException(status_code=403, detail=f"需要 {min_role} 及以上权限")
        return user

    return _dep


require_admin = require_role("admin")
require_operator = require_role("operator")


def require_approver(user: User = Depends(get_current_user)) -> User:
    """审批权依赖：拥有 approver 或 admin 角色即可审批。

    这是与操作权**相互独立**的一条能力线：
    - 纯审批员可以审批，但不因此获得设备增删改权限；
    - 管理员天然含审批权（admin 角色隐含 approver）。
    """
    if not can_approve(user):
        raise HTTPException(status_code=403, detail="需要审批员权限才能审批配置变更")
    return user


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
