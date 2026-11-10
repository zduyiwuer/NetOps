"""审计日志服务：记录用户操作与系统关键事件。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.models import AuditLog

logger = logging.getLogger("netops.audit")

# 不记录审计的路径（高频读接口 / 静态资源）
SKIP_PATHS = (
    "/api/health",
    "/api/auth/login",
    "/api/auth/me",
    "/api/auth/config",
    "/api/monitor/overview",
    "/docs",
    "/openapi.json",
    "/assets",
    "/favicon",
)

# 写操作才记录审计
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def should_audit(method: str, path: str) -> bool:
    if method.upper() not in WRITE_METHODS:
        return False
    for skip in SKIP_PATHS:
        if path.startswith(skip):
            return False
    return True


def record(
    db: Optional[Session],
    username: str,
    action: str,
    target: str = "",
    detail: str = "",
    ip: str = "",
) -> None:
    """写入一条审计记录。"""
    own_session = db is None
    session = db or SessionLocal()
    try:
        session.add(
            AuditLog(
                username=username or "anonymous",
                action=action,
                target=(target or "")[:120],
                detail=(detail or "")[:4000],
                ip=(ip or "")[:64],
            )
        )
        if own_session:
            session.commit()
    except Exception as exc:
        logger.debug("审计日志写入失败: %s", exc)
        if own_session:
            session.rollback()
    finally:
        if own_session:
            session.close()


def record_action(
    db: Optional[Session],
    username: str,
    action: str,
    target: str = "",
    detail: Any = "",
    ip: str = "",
) -> None:
    """写审计，detail 支持 dict（自动转 JSON）。"""
    if isinstance(detail, dict):
        import json

        detail_text = json.dumps(detail, ensure_ascii=False)[:4000]
    elif isinstance(detail, (list, tuple)):
        import json

        detail_text = json.dumps(list(detail), ensure_ascii=False)[:4000]
    else:
        detail_text = str(detail or "")
    record(db, username, action, target, detail_text, ip)


def list_audit_logs(
    db: Session,
    username: Optional[str] = None,
    action: Optional[str] = None,
    keyword: Optional[str] = None,
    days: int = 7,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """分页查询审计日志。"""
    since = datetime.now() - __import__("datetime").timedelta(days=days)
    q = db.query(AuditLog).filter(AuditLog.created_at >= since)
    if username:
        q = q.filter(AuditLog.username == username)
    if action:
        q = q.filter(AuditLog.action == action)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            (AuditLog.action.like(like))
            | (AuditLog.target.like(like))
            | (AuditLog.detail.like(like))
            | (AuditLog.username.like(like))
        )
    total = q.count()
    rows = (
        q.order_by(AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "username": r.username,
                "action": r.action,
                "target": r.target,
                "detail": r.detail,
                "ip": r.ip,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


def list_actions(db: Session) -> List[str]:
    """已出现过的操作类型（用于筛选下拉）。"""
    rows = db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    return [r[0] for r in rows if r[0]]
