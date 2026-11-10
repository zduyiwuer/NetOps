"""定时任务配置 API：在 Web 界面调整调度策略、启停与立即执行。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import client_ip, get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import ScheduledJob, User
from app.services.audit_service import record_action
from app.services.scheduler import (
    JOB_REGISTRY,
    apply_job_config,
    ensure_job_configs,
    record_job_run,
    scheduler,
)

router = APIRouter(prefix="/api/scheduler", tags=["定时任务"])


class JobConfigIn(BaseModel):
    cron: str = ""
    interval_seconds: int = 0
    enabled: bool = True


def _next_run_time(job_id: str) -> Optional[str]:
    try:
        job = scheduler.get_job(job_id)
        if job and getattr(job, "next_run_time", None):
            return job.next_run_time.isoformat()
    except Exception:
        pass
    return None


def _cron_preview(cron: str, interval_seconds: int) -> str:
    """生成人类可读的调度说明。"""
    cron = (cron or "").strip()
    if cron:
        try:
            from apscheduler.triggers.cron import CronTrigger

            trigger = CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
            return f"cron {cron}（{trigger}）"
        except Exception:
            return f"cron {cron}（表达式无效，将回退为间隔模式）"
    if interval_seconds:
        if interval_seconds % 3600 == 0:
            return f"每 {interval_seconds // 3600} 小时"
        if interval_seconds % 60 == 0:
            return f"每 {interval_seconds // 60} 分钟"
        return f"每 {interval_seconds} 秒"
    return "使用默认间隔"


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """定时任务列表（含当前调度策略与运行状态）。"""
    ensure_job_configs()
    configs = {c.job_id: c for c in db.query(ScheduledJob).all()}

    items: List[Dict[str, Any]] = []
    for spec in JOB_REGISTRY:
        cfg = configs.get(spec["job_id"])
        cron = (cfg.cron if cfg else spec["default_cron"]) or ""
        interval = (cfg.interval_seconds if cfg else spec["default_interval"]) or 0
        enabled = cfg.enabled if cfg else True
        try:
            live = scheduler.get_job(spec["job_id"])
        except Exception:
            live = None
        items.append({
            "job_id": spec["job_id"],
            "name": spec["name"],
            "description": spec["description"],
            "category": spec["category"],
            "cron": cron,
            "interval_seconds": interval,
            "enabled": enabled,
            "schedule_text": _cron_preview(cron, interval),
            "running": live is not None,
            "next_run_at": _next_run_time(spec["job_id"]),
            "run_count": cfg.run_count if cfg else 0,
            "last_run_at": cfg.last_run_at.isoformat() if cfg and cfg.last_run_at else None,
            "last_status": cfg.last_status if cfg else "",
            "last_duration_ms": cfg.last_duration_ms if cfg else 0,
            "last_error": cfg.last_error if cfg else "",
            "default_cron": spec["default_cron"],
            "default_interval": spec["default_interval"],
        })
    return {"items": items, "scheduler_running": scheduler.running}


@router.put("/jobs/{job_id}")
def update_job(
    job_id: str,
    payload: JobConfigIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """修改任务调度策略并即时生效。"""
    spec = next((s for s in JOB_REGISTRY if s["job_id"] == job_id), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 校验 cron 表达式
    if payload.cron.strip():
        try:
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(payload.cron.strip(), timezone="Asia/Shanghai")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"cron 表达式无效：{exc}")

    row = db.query(ScheduledJob).filter(ScheduledJob.job_id == job_id).first()
    if row is None:
        row = ScheduledJob(job_id=job_id, name=spec["name"], description=spec["description"],
                           category=spec["category"])
        db.add(row)
    row.cron = payload.cron.strip()
    row.interval_seconds = max(0, int(payload.interval_seconds or 0))
    row.enabled = payload.enabled
    row.updated_by = user.username
    db.commit()

    ok = apply_job_config(job_id)
    record_action(
        db, user.username, "scheduler.update", f"job={job_id}",
        {"cron": row.cron, "interval_seconds": row.interval_seconds, "enabled": row.enabled},
        client_ip(request),
    )
    return {"ok": ok, "job_id": job_id, "next_run_at": _next_run_time(job_id)}


@router.post("/jobs/{job_id}/toggle")
def toggle_job(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """启用 / 停用任务。"""
    spec = next((s for s in JOB_REGISTRY if s["job_id"] == job_id), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = db.query(ScheduledJob).filter(ScheduledJob.job_id == job_id).first()
    if row is None:
        row = ScheduledJob(job_id=job_id, name=spec["name"], description=spec["description"],
                           category=spec["category"], cron=spec["default_cron"],
                           interval_seconds=spec["default_interval"])
        db.add(row)
        db.commit()
    row.enabled = not row.enabled
    row.updated_by = user.username
    db.commit()
    ok = apply_job_config(job_id)
    record_action(
        db, user.username, "scheduler.toggle", f"job={job_id}",
        {"enabled": row.enabled}, client_ip(request),
    )
    return {"ok": ok, "job_id": job_id, "enabled": row.enabled}


@router.post("/jobs/{job_id}/run")
async def run_job_now(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_operator),
) -> Dict[str, Any]:
    """立即执行一次任务。"""
    spec = next((s for s in JOB_REGISTRY if s["job_id"] == job_id), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    from app.services.scheduler import _wrap_job

    started = datetime.now()
    try:
        await _wrap_job(spec)()
        row = db.query(ScheduledJob).filter(ScheduledJob.job_id == job_id).first()
        status = row.last_status if row else "success"
        err = row.last_error if row else ""
    except Exception as exc:
        status, err = "failed", str(exc)

    record_action(
        db, user.username, "scheduler.run", f"job={job_id}",
        {"status": status, "error": err[:500]}, client_ip(request),
    )
    return {
        "ok": status == "success",
        "job_id": job_id,
        "status": status,
        "error": err,
        "started_at": started.isoformat(),
    }


@router.post("/jobs/{job_id}/reset")
def reset_job(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """恢复任务的默认调度策略。"""
    spec = next((s for s in JOB_REGISTRY if s["job_id"] == job_id), None)
    if spec is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = db.query(ScheduledJob).filter(ScheduledJob.job_id == job_id).first()
    if row is None:
        row = ScheduledJob(job_id=job_id, name=spec["name"], description=spec["description"],
                           category=spec["category"])
        db.add(row)
    row.cron = spec["default_cron"]
    row.interval_seconds = spec["default_interval"]
    row.enabled = True
    row.updated_by = user.username
    db.commit()
    ok = apply_job_config(job_id)
    record_action(db, user.username, "scheduler.reset", f"job={job_id}", {}, client_ip(request))
    return {"ok": ok, "job_id": job_id}
