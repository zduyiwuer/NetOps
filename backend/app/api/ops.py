"""备份 / 巡检 / 报告 API。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import (
    ConfigBackup,
    Device,
    InspectionTask,
    InspectionTemplate,
    Store,
    User,
)
from app.services.backup import diff_configs, run_backup
from app.services.inspection import ensure_default_templates, run_inspection_task

backup_router = APIRouter(prefix="/api/backups", tags=["配置备份"])
inspection_router = APIRouter(prefix="/api/inspection", tags=["基线巡检"])


# ================================================================ 备份
@backup_router.get("")
def list_backups(
    device_id: Optional[int] = None,
    store_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(ConfigBackup)
    if device_id:
        q = q.filter(ConfigBackup.device_id == device_id)
    if store_id:
        allowed = [d.id for d in db.query(Device).filter(Device.store_id == store_id).all()]
        q = q.filter(ConfigBackup.device_id.in_(allowed or [-1]))
    if status:
        q = q.filter(ConfigBackup.status == status)
    total = q.count()
    rows = q.order_by(ConfigBackup.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    devices = {d.id: d for d in db.query(Device).all()}
    stores = {s.id: s for s in db.query(Store).all()}
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "device_id": r.device_id,
                "device_name": devices[r.device_id].name if r.device_id in devices else "",
                "device_ip": devices[r.device_id].ip if r.device_id in devices else "",
                "store_id": devices[r.device_id].store_id if r.device_id in devices else None,
                "store_name": (
                    stores.get(devices[r.device_id].store_id, "").name
                    if r.device_id in devices and devices[r.device_id].store_id in stores
                    else ""
                ),
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "size_kb": round(r.size_bytes / 1024, 2),
                "md5": r.md5,
                "method": r.method,
                "trigger_type": r.trigger_type,
                "status": r.status,
                "message": r.message,
                "operator": r.operator,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@backup_router.get("/stores")
def backup_store_summary(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """按门店汇总备份情况（设备数、已备份数、可下载数）。"""
    stores = db.query(Store).order_by(Store.id).all()
    devices = db.query(Device).all()
    backups = db.query(ConfigBackup).filter(ConfigBackup.status == "success").all()

    backed_devices = {b.device_id for b in backups}
    latest: Dict[int, ConfigBackup] = {}
    for b in sorted(backups, key=lambda x: x.id):
        latest[b.device_id] = b

    items = []
    for s in stores:
        dev_ids = [d.id for d in devices if d.store_id == s.id]
        backed = sum(1 for did in dev_ids if did in backed_devices)
        downloadable = sum(
            1
            for did in dev_ids
            if did in latest
            and latest[did].file_path
            and os.path.exists(latest[did].file_path)
        )
        items.append({
            "store_id": s.id,
            "store_code": s.code,
            "store_name": s.name,
            "region": s.region,
            "device_count": len(dev_ids),
            "backed_count": backed,
            "downloadable_count": downloadable,
        })
    return {"items": items}


@backup_router.post("/{device_id}")
async def create_backup(
    device_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    ok = await run_backup(device_id, trigger_type="manual", operator=user.username, db=db)
    record = (
        db.query(ConfigBackup).filter(ConfigBackup.device_id == device_id).order_by(ConfigBackup.id.desc()).first()
    )
    return {
        "ok": ok,
        "message": "备份成功" if ok else (record.message if record else "备份失败"),
        "backup": {
            "id": record.id if record else None,
            "filename": record.filename if record else "",
            "size_kb": round(record.size_bytes / 1024, 2) if record else 0,
            "created_at": record.created_at.isoformat() if record else None,
        } if record else None,
    }


@backup_router.post("/batch")
async def batch_backup(
    device_ids: List[int],
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    results = []
    for did in device_ids:
        ok = await run_backup(did, trigger_type="manual", operator=user.username, db=db)
        results.append({"device_id": did, "ok": ok})
    return {"total": len(results), "success": sum(1 for r in results if r["ok"]), "items": results}


@backup_router.get("/download-store/{store_id}")
def download_store_backups(
    store_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """按门店打包下载该门店所有设备的最新配置备份（ZIP）。"""
    import io
    import zipfile

    from app.models.models import Device, Store

    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="门店不存在")

    devices = db.query(Device).filter(Device.store_id == store_id).all()
    if not devices:
        raise HTTPException(status_code=404, detail="该门店下没有设备")

    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for device in devices:
            latest = (
                db.query(ConfigBackup)
                .filter(
                    ConfigBackup.device_id == device.id,
                    ConfigBackup.status == "success",
                )
                .order_by(ConfigBackup.id.desc())
                .first()
            )
            if latest is None or not latest.file_path or not os.path.exists(latest.file_path):
                continue
            try:
                with open(latest.file_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            safe_name = f"{device.name}_{device.ip}.cfg".replace("/", "_")
            zf.writestr(f"{store.name}/{safe_name}", content)
            written += 1
        # 附带清单
        manifest = [
            {
                "设备名称": d.name,
                "管理IP": d.ip,
                "厂商": d.vendor,
                "型号": d.model,
                "位置": d.location,
            }
            for d in devices
        ]
        zf.writestr(f"{store.name}/设备清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    if written == 0:
        raise HTTPException(status_code=404, detail="该门店设备暂无可下载的备份文件")

    buffer.seek(0)
    filename = f"{store.name}_配置备份_{datetime.now():%Y%m%d_%H%M%S}.zip"
    from urllib.parse import quote

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@backup_router.get("/view/{backup_id}")
def view_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """查看备份内容（文本返回，前端高亮展示）。"""
    record = db.get(ConfigBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if not record.file_path or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="备份文件已丢失")
    try:
        with open(record.file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取备份文件失败: {exc}")

    lines = content.splitlines()
    return {
        "id": record.id,
        "device_id": record.device_id,
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "md5": record.md5,
        "created_at": record.created_at.isoformat(),
        "operator": record.operator,
        "trigger_type": record.trigger_type,
        "line_count": len(lines),
        "content": content[:400_000],
        "truncated": len(content) > 400_000,
    }


@backup_router.get("/preview/{backup_id}")
def preview_backup(
    backup_id: int,
    lines: int = Query(200, ge=1, le=5000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """预览备份文件的前 N 行。"""
    record = db.get(ConfigBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if not record.file_path or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="备份文件已丢失")
    try:
        with open(record.file_path, "r", encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(lines)]
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"读取备份文件失败: {exc}")
    return {"filename": record.filename, "lines": [h.rstrip("\n") for h in head]}


@backup_router.get("/download/{backup_id}")
def download_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    record = db.get(ConfigBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if not record.file_path or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="备份文件已丢失")
    return FileResponse(
        record.file_path,
        media_type="text/plain; charset=utf-8",
        filename=record.filename or "config.cfg",
    )


@backup_router.get("/backup/{backup_id}")
def download_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    record = db.get(ConfigBackup, backup_id)
    if record is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if not record.file_path or not os.path.exists(record.file_path):
        raise HTTPException(status_code=404, detail="备份文件已丢失")
    return FileResponse(
        record.file_path,
        media_type="text/plain; charset=utf-8",
        filename=record.filename or "config.cfg",
    )


@backup_router.get("/diff/{backup_id}")
def compare_backup(
    backup_id: int,
    against_id: Optional[int] = Query(None, description="对比的另一条备份，缺省则与最新一条对比"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    old = db.get(ConfigBackup, backup_id)
    if old is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    if against_id:
        new = db.get(ConfigBackup, against_id)
    else:
        new = (
            db.query(ConfigBackup)
            .filter(ConfigBackup.device_id == old.device_id, ConfigBackup.id != old.id, ConfigBackup.status == "success")
            .order_by(ConfigBackup.id.desc())
            .first()
        )
    if new is None:
        return {"old": old.filename, "new": None, "diff": "", "message": "没有可对比的版本"}
    try:
        with open(old.file_path, "r", encoding="utf-8", errors="replace") as fh:
            old_text = fh.read()
        with open(new.file_path, "r", encoding="utf-8", errors="replace") as fh:
            new_text = fh.read()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"读取备份文件失败: {exc}")
    return {
        "old": old.filename,
        "new": new.filename,
        "diff": diff_configs(old_text, new_text),
        "same": old_text == new_text,
    }


@backup_router.delete("/{backup_id}")
def delete_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    record = db.get(ConfigBackup, backup_id)
    if record:
        if record.file_path and os.path.exists(record.file_path):
            try:
                os.remove(record.file_path)
            except OSError:
                pass
        db.delete(record)
        db.commit()
    return {"ok": True}


# ================================================================ 巡检
class TemplateIn(BaseModel):
    name: str = Field(min_length=1)
    vendor: str = ""
    description: str = ""
    items: List[Dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True


class TaskIn(BaseModel):
    name: str = Field(min_length=1)
    template_id: Optional[int] = None
    scope_type: str = "all"  # all/store/device/scheduled
    scope_value: str = ""


@inspection_router.get("/templates")
def list_templates(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    ensure_default_templates(db)
    templates = db.query(InspectionTemplate).order_by(InspectionTemplate.id).all()
    return {
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "vendor": t.vendor,
                "description": t.description,
                "items": json.loads(t.items or "[]"),
                "enabled": t.enabled,
                "created_at": t.created_at.isoformat(),
            }
            for t in templates
        ]
    }


@inspection_router.post("/templates")
def create_template(
    payload: TemplateIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    tpl = InspectionTemplate(
        name=payload.name,
        vendor=payload.vendor,
        description=payload.description,
        items=json.dumps(payload.items, ensure_ascii=False),
        enabled=payload.enabled,
    )
    db.add(tpl)
    db.commit()
    return {"id": tpl.id}


@inspection_router.put("/templates/{template_id}")
def update_template(
    template_id: int,
    payload: TemplateIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    tpl = db.get(InspectionTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    tpl.name = payload.name
    tpl.vendor = payload.vendor
    tpl.description = payload.description
    tpl.items = json.dumps(payload.items, ensure_ascii=False)
    tpl.enabled = payload.enabled
    db.commit()
    return {"id": tpl.id}


@inspection_router.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> Dict[str, Any]:
    tpl = db.get(InspectionTemplate, template_id)
    if tpl:
        db.delete(tpl)
        db.commit()
    return {"ok": True}


@inspection_router.get("/tasks")
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(InspectionTask)
    total = q.count()
    tasks = q.order_by(InspectionTask.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    templates = {t.id: t.name for t in db.query(InspectionTemplate).all()}
    return {
        "total": total,
        "items": [
            {
                "id": t.id,
                "name": t.name,
                "template_id": t.template_id,
                "template_name": templates.get(t.template_id, "-"),
                "scope_type": t.scope_type,
                "scope_value": t.scope_value,
                "status": t.status,
                "total": t.total,
                "passed": t.passed,
                "failed": t.failed,
                "warn": t.warn,
                "operator": t.operator,
                "remark": t.remark,
                "report_file": t.report_file,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "created_at": t.created_at.isoformat(),
            }
            for t in tasks
        ],
    }


@inspection_router.post("/tasks")
async def create_task(
    payload: TaskIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    task = InspectionTask(
        name=payload.name,
        template_id=payload.template_id,
        scope_type=payload.scope_type,
        scope_value=payload.scope_value,
        status="pending",
        operator=user.username,
    )
    db.add(task)
    db.commit()
    task_id = task.id

    if payload.scope_type == "scheduled":
        return {"id": task_id, "status": "pending", "message": "已创建计划巡检任务，将由调度器按小时检查执行"}

    result = await run_inspection_task(task_id)
    return {"id": task_id, **result}


@inspection_router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    task = db.get(InspectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="巡检任务不存在")
    try:
        results = json.loads(task.result or "[]")
    except Exception:
        results = []
    stores = {s.id: s.name for s in db.query(Store).all()}
    for r in results:
        r["store_name"] = stores.get(r.get("store_id"), "-")
    template = db.get(InspectionTemplate, task.template_id) if task.template_id else None
    return {
        "id": task.id,
        "name": task.name,
        "template_name": template.name if template else "-",
        "status": task.status,
        "scope_type": task.scope_type,
        "scope_value": task.scope_value,
        "total": task.total,
        "passed": task.passed,
        "failed": task.failed,
        "warn": task.warn,
        "operator": task.operator,
        "report_file": task.report_file,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "results": results,
    }


@inspection_router.get("/tasks/{task_id}/report")
def get_report(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    task = db.get(InspectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="巡检任务不存在")
    if not task.report_path or not os.path.exists(task.report_path):
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return FileResponse(task.report_path, media_type="text/html; charset=utf-8", filename=f"inspection_{task_id}.html")


@inspection_router.get("/tasks/{task_id}/report.xlsx")
def get_report_excel(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    task = db.get(InspectionTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="巡检任务不存在")
    if not task.report_file:
        raise HTTPException(status_code=404, detail="Excel 报告尚未生成")
    path = os.path.join(settings.report_dir, task.report_file)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="报告文件已丢失")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=task.report_file,
    )


@inspection_router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    task = db.get(InspectionTask, task_id)
    if task:
        db.delete(task)
        db.commit()
    return {"ok": True}
