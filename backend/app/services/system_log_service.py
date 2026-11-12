"""系统日志服务：记录平台自身的运行问题。

设计要点
--------
1. **自动捕获**：通过 ``logging.Handler`` 挂到根 logger 上，
   业务代码里现成的 ``logger.warning(...) / logger.error(...)``
   会自动落库——不必在每个失败分支手工埋点，避免漏记。
2. **只收有问题的**：仅 WARNING 及以上级别入库，
   INFO 级别的流水不记，防止系统日志被噪音淹没。
3. **去重限流**：同一来源的相同错误在冷却窗口内只记一条，
   避免「每 30 秒失败一次」刷出几千条一样的记录。
4. **写库失败不影响主流程**：日志记录本身失败时静默降级，
   绝不能因为「记日志失败」把主业务搞挂。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("netops.systemlog")

# 记录级别：只记 warning 以上
_MIN_LEVEL = logging.WARNING

# 同一 key 的重复记录冷却时间（秒）
_DEDUP_WINDOW = 300

# 分类常量
CAT_COLLECTOR = "collector"
CAT_PORT = "port"
CAT_SCHEDULER = "scheduler"
CAT_STORAGE = "storage"
CAT_DATABASE = "database"
CAT_NETWORK = "network"
CAT_SECURITY = "security"
CAT_SYSTEM = "system"

# 级别名（与前端展示一致）
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"
LEVEL_CRITICAL = "critical"

_PY_LEVEL_TO_NAME = {
    logging.WARNING: LEVEL_WARNING,
    logging.ERROR: LEVEL_ERROR,
    logging.CRITICAL: LEVEL_CRITICAL,
}

_lock = threading.Lock()
# (source, message) -> 上次记录时间
_dedup_cache: Dict[tuple, float] = {}

# 写入器就绪标记（避免在应用启动前写库）
_enabled = False


def _trim(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def write_log(
    level: str,
    category: str,
    source: str,
    message: str,
    detail: str = "",
    device_id: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """写入一条系统日志。返回是否真正落库。"""
    if not _enabled:
        return False
    try:
        from app.db.session import SessionLocal
        from app.models.models import SystemLog

        ctx = json.dumps(context or {}, ensure_ascii=False)
        row = SystemLog(
            ts=datetime.now(),
            level=level,
            category=category,
            source=source,
            message=_trim(message),
            detail=_trim(detail),
            device_id=device_id,
            context=ctx,
        )
        db = SessionLocal()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()
        return True
    except Exception:
        # 记日志失败不能影响主流程
        return False


def log_once(
    level: str,
    category: str,
    source: str,
    message: str,
    detail: str = "",
    device_id: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
    window: int = _DEDUP_WINDOW,
) -> bool:
    """带去重的写入：同一 (source, message) 在冷却窗口内只记一条。"""
    key = (source, message)
    now = time.time()
    with _lock:
        last = _dedup_cache.get(key)
        if last is not None and now - last < window:
            return False
        _dedup_cache[key] = now
        # 顺手清理过期项，避免缓存无限增长
        if len(_dedup_cache) > 512:
            stale = [k for k, v in _dedup_cache.items() if now - v > window]
            for k in stale:
                _dedup_cache.pop(k, None)
    return write_log(level, category, source, message, detail, device_id, context)


class SystemLogHandler(logging.Handler):
    """把应用日志里的告警与错误自动写入系统日志表。

    需要附加结构化上下文时，在日志调用处用
    ``extra={"syslog_context": {...}}`` 传入，
    这里会一并存进系统日志的 context 字段。
    """

    # 只捕获平台自己的 logger——第三方库（paramiko / grpc / sqlalchemy 等）
    # 的告警属于依赖内部行为，不是平台运行问题，混进来只会污染系统日志
    _LOGGER_PREFIX = "netops."

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < _MIN_LEVEL:
                return
            # 避免系统日志服务自身的日志再被捕获，形成递归
            if not record.name.startswith(self._LOGGER_PREFIX):
                return
            level = _PY_LEVEL_TO_NAME.get(record.levelno, LEVEL_WARNING)
            message = record.getMessage()
            detail = ""
            if record.exc_info:
                detail = self.format_exception(record.exc_info)
            # 多行消息（traceback 之类）只在摘要里留第一行，
            # 完整内容放到详情，避免一条问题被拆成多条日志
            if "\n" in message:
                head, _, tail = message.partition("\n")
                message = head.strip()
                if tail.strip():
                    detail = (tail.strip() + "\n" + detail).strip()
            source = record.name.replace("netops.", "")
            category = _infer_category(source, message)
            context = getattr(record, "syslog_context", None)
            context = context if isinstance(context, dict) else None
            # context 里带 device_id 时同步到独立字段，
            # 这样系统日志才能按门店（经设备）筛选
            device_id = None
            if context and context.get("device_id"):
                try:
                    device_id = int(context["device_id"])
                except (TypeError, ValueError):
                    device_id = None
            log_once(
                level,
                category,
                source,
                message,
                detail,
                device_id=device_id,
                context=context,
                window=_DEDUP_WINDOW,
            )
        except Exception:
            pass  # 绝不因日志处理失败而抛错

    @staticmethod
    def format_exception(exc_info) -> str:
        import traceback

        try:
            return "".join(traceback.format_exception(*exc_info))
        except Exception:
            return ""


def _infer_category(source: str, message: str) -> str:
    """从来源模块与消息内容推断分类。"""
    s = (source or "").lower()
    m = (message or "").lower()
    if "collector" in s or "采集" in m:
        return CAT_COLLECTOR
    # 注意用词边界匹配，避免 "transport" 这类词被误判成 "port"
    if "port" in s.split(".") or "端口" in m or " bind" in m or "bind:" in m or "占用" in m:
        return CAT_PORT
    if "scheduler" in s or "调度" in m:
        return CAT_SCHEDULER
    if "backup" in s or "storage" in s or "备份" in m or "存储" in m:
        return CAT_STORAGE
    if "db" in s or "database" in s or "sqlalchemy" in s or "数据库" in m:
        return CAT_DATABASE
    if "netflow" in s or "snmp" in s or "ssh" in s or "telemetry" in s or "gnmi" in s:
        return CAT_COLLECTOR
    if "auth" in s or "security" in s or "login" in s or "认证" in m:
        return CAT_SECURITY
    return CAT_SYSTEM


_handler: Optional[SystemLogHandler] = None


def setup(root: Optional[logging.Logger] = None) -> None:
    """安装日志处理器（应用启动时调用一次）。"""
    global _handler, _enabled
    _enabled = True
    if _handler is not None:
        return
    root = root or logging.getLogger()
    handler = SystemLogHandler()
    handler.setLevel(_MIN_LEVEL)
    root.addHandler(handler)
    _handler = handler


def teardown() -> None:
    """卸载日志处理器（测试用）。"""
    global _handler, _enabled
    if _handler is not None:
        logging.getLogger().removeHandler(_handler)
        _handler = None
    _enabled = False


def purge(days: int = 30) -> int:
    """清理过期日志，返回删除条数。"""
    try:
        from app.db.session import SessionLocal
        from app.models.models import SystemLog

        since = datetime.now() - timedelta(days=days)
        db = SessionLocal()
        try:
            rows = db.query(SystemLog).filter(SystemLog.ts < since).all()
            for r in rows:
                db.delete(r)
            db.commit()
            return len(rows)
        finally:
            db.close()
    except Exception:
        return 0


def stats(db) -> Dict[str, Any]:
    """按级别与分类统计，供页面展示。"""
    from sqlalchemy import func

    from app.models.models import SystemLog

    by_level = dict(
        db.query(SystemLog.level, func.count(SystemLog.id)).group_by(SystemLog.level).all()
    )
    by_category = dict(
        db.query(SystemLog.category, func.count(SystemLog.id)).group_by(SystemLog.category).all()
    )
    total = db.query(func.count(SystemLog.id)).scalar() or 0
    return {
        "total": total,
        "by_level": by_level,
        "by_category": by_category,
    }
