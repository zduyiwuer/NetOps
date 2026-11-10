"""配置备份服务：SSH 抓取配置，落盘并记录历史版本。"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.collectors.engine import Collector
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.models import ConfigBackup, Device


def _safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)


async def run_backup(
    device_id: int,
    trigger_type: str = "manual",
    operator: str = "system",
    db: Session = None,
) -> bool:
    """执行一次备份，返回是否成功。"""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if device is None:
            return False

        collector = Collector(device)
        try:
            content = await collector.backup_config()
        except Exception as exc:
            record = ConfigBackup(
                device_id=device.id,
                filename="",
                status="failed",
                message=str(exc),
                trigger_type=trigger_type,
                operator=operator,
            )
            db.add(record)
            db.commit()
            return False

        if not content or not content.strip():
            record = ConfigBackup(
                device_id=device.id,
                filename="",
                status="failed",
                message="未获取到配置内容（请检查 SSH 账号、权限或 enable 口令）",
                trigger_type=trigger_type,
                operator=operator,
            )
            db.add(record)
            db.commit()
            return False

        # 按厂商规范化配置内容（飞塔输出为标准 .conf）
        from app.collectors.vendors import resolve_vendor
        from app.services.config_formatter import (
            backup_filename,
            format_config,
            validate_fortinet_config,
        )

        vendor = resolve_vendor(device.vendor)
        content = format_config(
            vendor_key=vendor.key,
            raw=content,
            device_name=device.name,
            device_ip=device.ip,
            source_commands=list(vendor.backup_cmd),
        )

        # 飞塔配置做完整性校验，发现截断时记录告警
        warn = ""
        if vendor.key == "fortinet":
            check = validate_fortinet_config(content)
            if not check["valid"]:
                warn = "；配置完整性校验未通过：" + "、".join(check["issues"])

        now = datetime.now()
        filename = backup_filename(vendor.key, device.name, device.ip, now)
        device_dir = os.path.join(settings.backup_dir, str(device.id))
        os.makedirs(device_dir, exist_ok=True)
        file_path = os.path.join(device_dir, filename)
        with open(file_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

        raw = content.encode("utf-8")
        record = ConfigBackup(
            device_id=device.id,
            filename=filename,
            file_path=file_path,
            size_bytes=len(raw),
            md5=hashlib.md5(raw).hexdigest(),
            method="ssh",
            trigger_type=trigger_type,
            status="success",
            message=warn,
            operator=operator,
        )
        db.add(record)
        db.commit()
        return True
    finally:
        if own_session:
            db.close()


def diff_configs(old_text: str, new_text: str) -> str:
    """生成配置差异（unified diff 风格）。"""
    import difflib

    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="旧版本",
        tofile="新版本",
        lineterm="",
        n=3,
    )
    return "\n".join(diff)
