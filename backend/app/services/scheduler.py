"""调度器：周期采集、日志拉取、配置备份、巡检任务、数据清理。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.collectors.engine import Collector, DeviceSample, rate_tracker
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.models import (
    AlertRule,
    Device,
    DeviceLog,
    DeviceMetric,
    InterfaceMetric,
    InspectionTask,
)
from app.services.alerting import check_offline_devices, evaluate_rules, recover_offline_alerts

logger = logging.getLogger("netops.scheduler")

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# 单设备采集互斥锁，避免重复并发
_device_locks: Dict[int, asyncio.Lock] = {}
_overall_semaphore: Optional[asyncio.Semaphore] = None


def _lock_for(device_id: int) -> asyncio.Lock:
    if device_id not in _device_locks:
        _device_locks[device_id] = asyncio.Lock()
    return _device_locks[device_id]


def get_scheduler() -> AsyncIOScheduler:
    return scheduler


# ---------------------------------------------------------------- 采集
async def collect_device(device_id: int) -> Optional[DeviceSample]:
    """采集单台设备并写入指标表。"""
    global _overall_semaphore
    if _overall_semaphore is None:
        _overall_semaphore = asyncio.Semaphore(settings.max_concurrent_poll)

    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if device is None or not device.enabled:
            return None
        # 关闭 SNMP 轮询的设备改由 Telemetry 推送供数，不参与轮询
        if not device.use_snmp:
            logger.debug("设备 %s 已关闭 SNMP 轮询，跳过", device.name)
            return None

        async with _lock_for(device_id):
            async with _overall_semaphore:
                collector = Collector(device, mock_enabled=False)
                sample = await collector.collect(
                    collect_interface=device.collect_interface,
                    collect_system=device.collect_system,
                )

            now = datetime.now()
            metric = DeviceMetric(
                device_id=device.id,
                ts=now,
                cpu_usage=sample.system.cpu_usage,
                mem_usage=sample.system.mem_usage,
                mem_total_mb=sample.system.mem_total_mb,
                mem_used_mb=sample.system.mem_used_mb,
                temperature=sample.system.temperature,
                uptime_sec=sample.system.uptime_sec,
                response_ms=sample.system.response_ms,
                reachability=sample.system.reachable,
                extra_metrics=json.dumps(sample.system.extra or {}, ensure_ascii=False),
            )
            db.add(metric)

            device.last_seen = now if sample.system.reachable else device.last_seen
            device.status = "online" if sample.system.reachable else "offline"

            iface_rows: List[InterfaceMetric] = []
            for itf in sample.interfaces:
                row = InterfaceMetric(
                    device_id=device.id,
                    if_index=itf.if_index,
                    if_name=itf.name,
                    if_descr=itf.descr,
                    if_alias=itf.alias,
                    if_type=itf.if_type,
                    speed_bps=itf.speed_bps,
                    admin_status=itf.admin_status,
                    oper_status=itf.oper_status,
                    in_bps=itf.in_bps,
                    out_bps=itf.out_bps,
                    in_octets=itf.in_octets,
                    out_octets=itf.out_octets,
                    in_errors=itf.in_errors,
                    out_errors=itf.out_errors,
                    utilization=itf.utilization,
                    ts=now,
                )
                db.add(row)
                iface_rows.append(row)

            db.commit()

            # 告警评估
            try:
                await evaluate_rules(db, device, metric, iface_rows)
            except Exception as exc:
                logger.warning("告警评估失败 device=%s: %s", device.name, exc)
            return sample
    except Exception as exc:
        logger.exception("采集设备 %s 失败: %s", device_id, exc)
        return None
    finally:
        db.close()


async def collect_all_devices() -> int:
    """一轮全量采集。"""
    # 运行时开关：SNMP 轮询可被在线关闭
    try:
        from app.services.settings_service import effective_settings

        if not effective_settings().get("collector.snmp_enabled", True):
            logger.debug("SNMP 轮询已被运行时关闭")
            return 0
    except Exception:
        pass

    db = SessionLocal()
    try:
        device_ids = [d.id for d in db.query(Device).filter(Device.enabled.is_(True)).all()]
    finally:
        db.close()

    results = await asyncio.gather(*(collect_device(did) for did in device_ids), return_exceptions=True)
    ok = sum(1 for r in results if isinstance(r, DeviceSample) and r.system.reachable)
    logger.info("采集完成：%s/%s 成功", ok, len(device_ids))
    rate_tracker.gc()
    return ok


# ---------------------------------------------------------------- 日志采集
async def collect_device_logs(device_id: int) -> int:
    """通过 SSH 主动拉取设备日志。"""
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if not device or not device.enabled or not device.collect_log:
            return 0
        if not device.ssh_username or not device.ssh_password:
            return 0
        collector = Collector(device)
        rows = await collector.fetch_logs(limit=300)
        now = datetime.now()
        count = 0
        for level, text in rows:
            existing = (
                db.query(DeviceLog)
                .filter(DeviceLog.device_id == device.id, DeviceLog.message == text)
                .order_by(DeviceLog.id.desc())
                .first()
            )
            if existing:
                continue
            db.add(
                DeviceLog(
                    device_id=device.id,
                    source_ip=device.ip,
                    hostname=device.hostname or device.name,
                    severity=level,
                    message=text,
                    raw=text,
                    protocol="ssh",
                    ts=now,
                )
            )
            count += 1
        db.commit()
        return count
    except Exception as exc:
        logger.warning("日志采集失败 device=%s: %s", device_id, exc)
        return 0
    finally:
        db.close()


async def collect_all_logs() -> int:
    db = SessionLocal()
    try:
        ids = [
            d.id
            for d in db.query(Device).filter(
                Device.enabled.is_(True),
                Device.collect_log.is_(True),
                Device.ssh_username != "",
            ).all()
        ]
    finally:
        db.close()
    total = 0
    for did in ids:
        total += await collect_device_logs(did)
    return total


# ---------------------------------------------------------------- 备份
async def backup_all_devices(trigger: str = "scheduled") -> int:
    from app.services.backup import run_backup

    db = SessionLocal()
    try:
        ids = [
            d.id
            for d in db.query(Device).filter(
                Device.enabled.is_(True),
                Device.collect_backup.is_(True),
            ).all()
        ]
    finally:
        db.close()
    count = 0
    for did in ids:
        try:
            ok = await run_backup(did, trigger_type=trigger)
            count += 1 if ok else 0
        except Exception as exc:
            logger.warning("自动备份失败 device=%s: %s", did, exc)
    return count


# ---------------------------------------------------------------- 巡检
async def run_scheduled_inspection() -> Optional[int]:
    """按计划执行巡检（默认每天 02:00）。"""
    from app.services.inspection import run_inspection_task

    db = SessionLocal()
    try:
        task = (
            db.query(InspectionTask)
            .filter(InspectionTask.status == "pending", InspectionTask.scope_type == "scheduled")
            .first()
        )
        if not task:
            return None
        task_id = task.id
    finally:
        db.close()
    await run_inspection_task(task_id)
    return task_id


# ---------------------------------------------------------------- 清理
async def cleanup_old_data() -> int:
    """清理过期指标与日志。"""
    db = SessionLocal()
    try:
        metric_cut = datetime.now() - timedelta(days=settings.metric_retention_days)
        log_cut = datetime.now() - timedelta(days=settings.log_retention_days)
        m = db.query(DeviceMetric).filter(DeviceMetric.ts < metric_cut).delete(synchronize_session=False)
        i = db.query(InterfaceMetric).filter(InterfaceMetric.ts < metric_cut).delete(synchronize_session=False)
        l = db.query(DeviceLog).filter(DeviceLog.ts < log_cut).delete(synchronize_session=False)
        db.commit()
        logger.info("数据清理：指标 %s，接口 %s，日志 %s", m, i, l)
        return m + i + l
    except Exception as exc:
        logger.warning("数据清理失败: %s", exc)
        return 0
    finally:
        db.close()


async def _job_daily_backup() -> None:
    """APScheduler 包装：每日自动备份。"""
    try:
        count = await backup_all_devices("scheduled")
        logger.info("每日自动备份完成：%s 台设备", count)
    except Exception as exc:
        logger.warning("每日自动备份失败: %s", exc)


# ---------------------------------------------------------------- 生命周期
async def _job_check_offline() -> None:
    """APScheduler 包装：自带会话执行离线检测。"""
    db = SessionLocal()
    try:
        await check_offline_devices(db)
    except Exception as exc:
        logger.warning("离线检测失败: %s", exc)
    finally:
        db.close()


async def _job_recover_offline() -> None:
    """APScheduler 包装：自带会话执行告警恢复。"""
    db = SessionLocal()
    try:
        await recover_offline_alerts(db)
    except Exception as exc:
        logger.warning("告警恢复失败: %s", exc)
    finally:
        db.close()


def record_job_run(job_id: str, status: str = "success", duration_ms: int = 0, error: str = "") -> None:
    """记录任务最近一次执行结果。"""
    from app.models.models import ScheduledJob

    db = SessionLocal()
    try:
        row = db.query(ScheduledJob).filter(ScheduledJob.job_id == job_id).first()
        if row is None:
            return
        row.run_count = (row.run_count or 0) + 1
        row.last_run_at = datetime.now()
        row.last_status = status
        row.last_duration_ms = duration_ms
        row.last_error = (error or "")[:2000]
        db.commit()
    except Exception as exc:
        logger.debug("记录任务执行状态失败: %s", exc)
        db.rollback()
    finally:
        db.close()


def _wrap_job(spec: Dict[str, Any]):
    """包装任务函数，自动记录执行状态与耗时。"""
    func = spec["func"]
    job_id = spec["job_id"]

    async def _runner():
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                await result
            record_job_run(job_id, "success", int((time.perf_counter() - started) * 1000))
        except Exception as exc:
            record_job_run(job_id, "failed", int((time.perf_counter() - started) * 1000), str(exc))
            logger.exception("定时任务 %s 执行失败: %s", job_id, exc)

    _runner.__name__ = f"job_{job_id}"
    return _runner


# ---------------------------------------------------------------- 任务注册表
# 每个后台任务在此登记：默认调度、分类、说明与执行函数。
# 调度策略（cron / 间隔 / 启停）可在 Web 界面调整，配置持久化在 scheduled_jobs 表。
JOB_REGISTRY: List[Dict[str, Any]] = [
    {
        "job_id": "collect_all",
        "name": "设备指标采集",
        "description": "采集全部启用设备的接口速率、CPU/内存等指标",
        "category": "采集",
        "func": collect_all_devices,
        "default_interval": 30,
        "default_cron": "",
        "misfire_grace_time": 120,
    },
    {
        "job_id": "check_offline",
        "name": "设备离线检测",
        "description": "检测设备可达性并触发离线告警",
        "category": "告警",
        "func": _job_check_offline,
        "default_interval": 60,
        "default_cron": "",
        "misfire_grace_time": 60,
    },
    {
        "job_id": "recover_offline",
        "name": "告警自动恢复",
        "description": "设备恢复在线后关闭离线告警",
        "category": "告警",
        "func": _job_recover_offline,
        "default_interval": 120,
        "default_cron": "",
        "misfire_grace_time": 60,
    },
    {
        "job_id": "collect_logs",
        "name": "设备日志采集",
        "description": "通过 SSH 定期拉取交换机日志",
        "category": "采集",
        "func": collect_all_logs,
        "default_interval": 300,
        "default_cron": "",
        "misfire_grace_time": 120,
    },
    {
        "job_id": "daily_backup",
        "name": "每日配置备份",
        "description": "自动备份全部设备配置",
        "category": "备份",
        "func": _job_daily_backup,
        "default_interval": 86400,
        "default_cron": "0 2 * * *",
        "misfire_grace_time": 3600,
    },
    {
        "job_id": "hourly_inspection_dispatch",
        "name": "计划巡检调度",
        "description": "检查并执行待处理的计划巡检任务",
        "category": "巡检",
        "func": run_scheduled_inspection,
        "default_interval": 3600,
        "default_cron": "",
        "misfire_grace_time": 600,
    },
    {
        "job_id": "cleanup",
        "name": "历史数据清理",
        "description": "按保留策略清理过期指标与日志",
        "category": "维护",
        "func": cleanup_old_data,
        "default_interval": 21600,
        "default_cron": "",
        "misfire_grace_time": 3600,
    },
]


def _load_job_configs() -> Dict[str, Any]:
    """读取数据库中的任务配置（不存在则用默认值）。"""
    from app.models.models import ScheduledJob

    db = SessionLocal()
    try:
        rows = db.query(ScheduledJob).all()
        return {r.job_id: r for r in rows}
    except Exception as exc:
        logger.warning("读取定时任务配置失败: %s", exc)
        return {}
    finally:
        db.close()


def ensure_job_configs() -> int:
    """初始化任务配置记录（已存在则保留用户修改）。"""
    from app.models.models import ScheduledJob

    db = SessionLocal()
    try:
        count = 0
        for spec in JOB_REGISTRY:
            exists = db.query(ScheduledJob).filter(ScheduledJob.job_id == spec["job_id"]).first()
            if exists:
                continue
            db.add(
                ScheduledJob(
                    job_id=spec["job_id"],
                    name=spec["name"],
                    description=spec["description"],
                    category=spec["category"],
                    cron=spec["default_cron"],
                    interval_seconds=spec["default_interval"],
                    enabled=True,
                )
            )
            count += 1
        db.commit()
        return count
    except Exception as exc:
        logger.warning("初始化定时任务配置失败: %s", exc)
        db.rollback()
        return 0
    finally:
        db.close()


def build_trigger(spec: Dict[str, Any], config: Any):
    """按配置构造触发器：优先 cron，其次间隔秒数。"""
    cron = (getattr(config, "cron", "") or spec.get("default_cron") or "").strip()
    if cron:
        try:
            from apscheduler.triggers.cron import CronTrigger

            return CronTrigger.from_crontab(cron, timezone="Asia/Shanghai")
        except Exception as exc:
            logger.warning("任务 %s 的 cron 表达式「%s」无效，回退为间隔模式: %s",
                           spec["job_id"], cron, exc)
    interval = getattr(config, "interval_seconds", 0) or spec.get("default_interval") or 60
    return IntervalTrigger(seconds=max(5, int(interval)))


def apply_job_config(job_id: str) -> bool:
    """把单个任务的最新配置应用到调度器（运行时即时生效）。"""
    spec = next((s for s in JOB_REGISTRY if s["job_id"] == job_id), None)
    if spec is None:
        return False

    configs = _load_job_configs()
    config = configs.get(job_id)
    enabled = getattr(config, "enabled", True) if config is not None else True

    try:
        if not enabled:
            if scheduler.get_job(job_id):
                scheduler.remove_job(job_id)
            logger.info("定时任务 %s 已停用", job_id)
            return True

        trigger = build_trigger(spec, config)
        scheduler.add_job(
            _wrap_job(spec),
            trigger,
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=spec.get("misfire_grace_time", 120),
        )
        logger.info("定时任务 %s 已更新：%s", job_id, trigger)
        return True
    except Exception as exc:
        logger.warning("应用定时任务配置失败 %s: %s", job_id, exc)
        return False


def start_scheduler() -> AsyncIOScheduler:
    if scheduler.running:
        return scheduler
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用（NETOPS_SCHEDULER_ENABLED=false）")
        return scheduler

    ensure_job_configs()
    for spec in JOB_REGISTRY:
        apply_job_config(spec["job_id"])

    scheduler.start()
    logger.info("调度器已启动，共 %s 个任务", len(scheduler.get_jobs()))
    return scheduler


async def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
