"""设备接入服务：本地账号管理 + 配置变更审批下发。

配置变更流程：
    提交申请(pending) → 审批(approved/rejected) → 下发(applied/failed)
每次流转均写入操作日志（AuditLog），并保留变更前后配置快照。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.models import (
    AuditLog,
    ConfigChangeRequest,
    Device,
    DeviceAccount,
    User,
)

logger = logging.getLogger("netops.device_access")

# 状态机
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"

STATUS_TEXT = {
    STATUS_PENDING: "待审批",
    STATUS_APPROVED: "已批准待下发",
    STATUS_REJECTED: "已驳回",
    STATUS_APPLIED: "已下发",
    STATUS_FAILED: "下发失败",
    STATUS_ROLLED_BACK: "已回滚",
}

# 允许的流转
ALLOWED_TRANSITIONS = {
    STATUS_PENDING: {STATUS_APPROVED, STATUS_REJECTED},
    STATUS_APPROVED: {STATUS_APPLIED, STATUS_FAILED, STATUS_REJECTED},
    STATUS_APPLIED: {STATUS_ROLLED_BACK},
}


def make_ticket_no() -> str:
    """生成工单号：CFG + 日期 + 6 位序列。"""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        today = datetime.now().strftime("%Y%m%d")
        prefix = f"CFG{today}"
        count = (
            db.query(ConfigChangeRequest)
            .filter(ConfigChangeRequest.ticket_no.like(f"{prefix}%"))
            .count()
        )
        return f"{prefix}{count + 1:04d}"
    finally:
        db.close()


def log_audit(
    db: Session,
    username: str,
    action: str,
    target: str,
    detail: str = "",
    ip: str = "",
) -> None:
    """写操作日志。"""
    db.add(
        AuditLog(
            username=username or "system",
            action=action,
            target=(target or "")[:120],
            detail=(detail or "")[:4000],
            ip=(ip or "")[:64],
        )
    )


# ---------------------------------------------------------------- 设备账号
def create_device_account(
    db: Session,
    device_id: int,
    username: str,
    password: str,
    enable_password: str = "",
    privilege: int = 15,
    auth_type: str = "local",
    remark: str = "",
    operator: str = "",
) -> DeviceAccount:
    """创建设备本地账号。"""
    device = db.get(Device, device_id)
    if device is None:
        raise ValueError("设备不存在")
    if not username or not password:
        raise ValueError("用户名与密码不能为空")
    exists = (
        db.query(DeviceAccount)
        .filter(DeviceAccount.device_id == device_id, DeviceAccount.username == username)
        .first()
    )
    if exists:
        raise ValueError(f"该设备下已存在账号 {username}")
    acc = DeviceAccount(
        device_id=device_id,
        username=username,
        password=password,
        enable_password=enable_password,
        privilege=privilege,
        auth_type=auth_type,
        remark=remark,
        created_by=operator,
    )
    db.add(acc)
    log_audit(
        db,
        operator,
        "device_account.create",
        f"device={device_id}",
        json.dumps({"username": username, "auth_type": auth_type}, ensure_ascii=False),
    )
    db.commit()
    return acc


def list_device_accounts(db: Session, device_id: Optional[int] = None) -> List[Dict[str, Any]]:
    q = db.query(DeviceAccount)
    if device_id:
        q = q.filter(DeviceAccount.device_id == device_id)
    rows = q.order_by(DeviceAccount.id).all()
    devices = {d.id: d for d in db.query(Device).all()}
    return [
        {
            "id": a.id,
            "device_id": a.device_id,
            "device_name": devices[a.device_id].name if a.device_id in devices else "",
            "device_ip": devices[a.device_id].ip if a.device_id in devices else "",
            "username": a.username,
            "password": "********" if a.password else "",
            "enable_password": "********" if a.enable_password else "",
            "privilege": a.privilege,
            "auth_type": a.auth_type,
            "remark": a.remark,
            "enabled": a.enabled,
            "created_by": a.created_by,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]


# ---------------------------------------------------------------- 配置变更
def create_change_request(
    db: Session,
    device_id: int,
    title: str,
    description: str,
    config_content: str,
    change_type: str = "config",
    target_backup_id: Optional[int] = None,
    applicant: str = "",
    ip: str = "",
) -> ConfigChangeRequest:
    """提交配置变更申请。"""
    device = db.get(Device, device_id)
    if device is None:
        raise ValueError("设备不存在")
    if change_type == "config" and not config_content.strip():
        raise ValueError("配置内容不能为空")

    req = ConfigChangeRequest(
        ticket_no=make_ticket_no(),
        title=title or "配置变更",
        device_id=device_id,
        device_name=device.name,
        device_ip=device.ip,
        change_type=change_type,
        description=description or "",
        config_content=config_content or "",
        target_backup_id=target_backup_id,
        status=STATUS_PENDING,
        applicant=applicant,
    )
    db.add(req)
    db.flush()
    log_audit(
        db,
        applicant,
        "config_change.submit",
        f"ticket={req.ticket_no}",
        json.dumps(
            {"device": device.name, "type": change_type, "title": title}, ensure_ascii=False
        ),
        ip,
    )
    db.commit()
    return req


def review_change_request(
    db: Session,
    request_id: int,
    approve: bool,
    reviewer: str,
    comment: str = "",
    ip: str = "",
) -> ConfigChangeRequest:
    """审批配置变更申请。

    职责分离：**申请人不能审批自己的申请**。
    当同一个人同时具备 operator 与 approver 角色时，这条规则尤其重要——
    否则「自己提交、自己审批」会让审批形同虚设。
    """
    req = db.get(ConfigChangeRequest, request_id)
    if req is None:
        raise ValueError("申请不存在")
    if req.status != STATUS_PENDING:
        raise ValueError(f"当前状态为「{STATUS_TEXT.get(req.status, req.status)}」，不可审批")
    if not approve and not comment.strip():
        raise ValueError("驳回时必须填写意见")
    if req.applicant and req.applicant == reviewer:
        raise ValueError("申请人不能审批自己的变更申请，请转交其他审批人")

    req.status = STATUS_APPROVED if approve else STATUS_REJECTED
    req.reviewer = reviewer
    req.review_comment = comment or ""
    req.reviewed_at = datetime.now()
    log_audit(
        db,
        reviewer,
        "config_change.review",
        f"ticket={req.ticket_no}",
        json.dumps(
            {"approved": approve, "comment": comment}, ensure_ascii=False
        ),
        ip,
    )
    db.commit()
    return req


async def apply_change_request(
    db: Session,
    request_id: int,
    operator: str,
    ip: str = "",
) -> Dict[str, Any]:
    """下发已批准的配置变更（通过 SSH 推送到设备）。"""
    from app.collectors.ssh import SshClient, SshError

    req = db.get(ConfigChangeRequest, request_id)
    if req is None:
        return {"ok": False, "message": "申请不存在"}
    if req.status not in (STATUS_APPROVED,):
        return {
            "ok": False,
            "message": f"当前状态为「{STATUS_TEXT.get(req.status, req.status)}」，不可下发",
        }

    device = db.get(Device, req.device_id)
    if device is None:
        return {"ok": False, "message": "设备不存在"}

    # 1) 下发前自动备份当前配置（用于回滚）
    backup_id = None
    try:
        from app.models.models import ConfigBackup
        from app.services.backup import run_backup

        await run_backup(req.device_id, trigger_type="manual", operator=operator, db=db)
        latest_backup = (
            db.query(ConfigBackup)
            .filter(ConfigBackup.device_id == req.device_id, ConfigBackup.status == "success")
            .order_by(ConfigBackup.id.desc())
            .first()
        )
        if latest_backup:
            backup_id = latest_backup.id
            req.backup_id = backup_id
    except Exception as exc:
        logger.debug("下发前自动备份失败: %s", exc)

    # 2) 推送配置
    content = req.config_content or ""
    if req.change_type == "rollback" and req.target_backup_id:
        from app.models.models import ConfigBackup

        target = db.get(ConfigBackup, req.target_backup_id)
        if target and target.file_path:
            try:
                with open(target.file_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError as exc:
                req.status = STATUS_FAILED
                req.applied_result = f"读取回滚目标失败: {exc}"
                req.applied_at = datetime.now()
                db.commit()
                return {"ok": False, "message": req.applied_result}

    if not content.strip():
        req.status = STATUS_FAILED
        req.applied_result = "无待下发内容"
        req.applied_at = datetime.now()
        db.commit()
        return {"ok": False, "message": req.applied_result}

    ok, message = await push_config_to_device(device, content)

    req.status = STATUS_APPLIED if ok else STATUS_FAILED
    req.applied_result = message
    req.applied_at = datetime.now()
    log_audit(
        db,
        operator,
        "config_change.apply",
        f"ticket={req.ticket_no}",
        json.dumps({"ok": ok, "result": message[:500]}, ensure_ascii=False),
        ip,
    )
    db.commit()
    return {"ok": ok, "message": message, "backup_id": backup_id}


async def push_config_to_device(device, content: str) -> Tuple[bool, str]:
    """把配置内容推送到设备（按厂商选择配置模式）。"""
    from app.collectors.ssh import SshClient, SshError
    from app.collectors.vendors import resolve_vendor

    vendor = resolve_vendor(device.vendor)
    account = None
    if device.ssh_username:
        account = device.ssh_username

    if not account:
        return False, "设备未配置 SSH 账号，无法下发配置"

    ssh = SshClient(
        host=device.ip,
        username=account,
        password=device.ssh_password or "",
        vendor=device.vendor,
        port=device.ssh_port,
        timeout=device.ssh_timeout,
        enable_password=device.ssh_enable_password or "",
    )

    # 各厂商进入配置模式的命令
    enter_cmd = {
        "routeros": "/system script add name=netops source=policy",
        "cisco": "configure terminal",
        "cisco_asa": "configure terminal",
        "cisco_wlc": "config",
        "huawei": "system-view",
        "h3c": "system-view",
        "fortinet": "config",
        "generic": "configure terminal",
    }.get(vendor.key, "configure terminal")

    lines = [ln for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("!")]
    if not lines:
        return False, "配置内容为空"

    try:
        results = await ssh.run_commands([enter_cmd] + lines + ["end", "write memory"])
    except SshError as exc:
        return False, f"下发失败: {exc}"
    except Exception as exc:
        return False, f"下发异常: {exc}"

    # 检查输出中是否有错误
    errors = []
    for cmd, text in results.items():
        if not text:
            continue
        for line in text.splitlines():
            low = line.lower()
            if any(k in low for k in ("% invalid", "% error", "error:", "invalid input", "syntax error")):
                errors.append(line.strip())
    if errors:
        return False, "设备返回错误: " + "; ".join(errors[:3])
    return True, f"配置已下发（{len(lines)} 行）"


def list_change_requests(
    db: Session,
    status: Optional[str] = None,
    device_id: Optional[int] = None,
    applicant: Optional[str] = None,
) -> List[Dict[str, Any]]:
    q = db.query(ConfigChangeRequest)
    if status:
        q = q.filter(ConfigChangeRequest.status == status)
    if device_id:
        q = q.filter(ConfigChangeRequest.device_id == device_id)
    if applicant:
        q = q.filter(ConfigChangeRequest.applicant == applicant)
    rows = q.order_by(ConfigChangeRequest.id.desc()).all()
    return [
        {
            "id": r.id,
            "ticket_no": r.ticket_no,
            "title": r.title,
            "device_id": r.device_id,
            "device_name": r.device_name,
            "device_ip": r.device_ip,
            "change_type": r.change_type,
            "description": r.description,
            "config_content": r.config_content,
            "target_backup_id": r.target_backup_id,
            "backup_id": r.backup_id,
            "status": r.status,
            "status_text": STATUS_TEXT.get(r.status, r.status),
            "applicant": r.applicant,
            "reviewer": r.reviewer,
            "review_comment": r.review_comment,
            "applied_result": r.applied_result,
            "created_at": r.created_at.isoformat(),
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None,
        }
        for r in rows
    ]
