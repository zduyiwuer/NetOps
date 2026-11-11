"""FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.api import (
    alerts, auth, device_access, devices, flows, logs, monitor, ops, platform, scheduler_api,
)
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import Base, SessionLocal, engine
from app.models.models import (
    AlertRule,
    Device,
    InspectionTemplate,
    NotificationChannel,
    Store,
    User,
)
from app.services.inspection import ensure_default_templates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("netops")


def _resolve_frontend_dist() -> str:
    """定位前端静态资源目录。

    1. 优先使用环境变量 NETOPS_FRONTEND_DIR（容器部署场景）；
    2. 否则从当前文件位置向上查找 `frontend/dist`，
       兼容源码布局（netops/backend/app/main.py）与容器布局（/app/app/main.py）。
    """
    env_dir = os.environ.get("NETOPS_FRONTEND_DIR", "").strip()
    if env_dir:
        return env_dir

    here = os.path.dirname(os.path.abspath(__file__))
    # 从 app 目录逐级向上，最多找 4 层
    current = here
    for _ in range(4):
        candidate = os.path.join(current, "frontend", "dist")
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    # 兜底：按源码布局推导
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(project_root, "frontend", "dist")


FRONTEND_DIST = _resolve_frontend_dist()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    init_data()
    # 调度器
    from app.services.scheduler import start_scheduler, shutdown_scheduler

    start_scheduler()

    # 采集服务统一由 collector_control 管理（保持单一状态源）
    from app.services.collector_control import (
        start_gnmi,
        start_netflow,
        start_syslog,
        stop_gnmi,
        stop_netflow,
        stop_syslog,
    )

    if settings.syslog_enabled:
        await start_syslog()
    if settings.netflow_enabled:
        await start_netflow(settings.netflow_udp_ports or None)
    if settings.telemetry_enabled:
        await start_gnmi(settings.gnmi_listen_port)

    # 应用数据库中保存的采集开关（覆盖环境变量默认值）
    try:
        from app.services.collector_control import apply_switches
        from app.services.settings_service import explicit_keys

        db = SessionLocal()
        try:
            changed = [k for k in explicit_keys(db) if k.startswith("collector.")]
        finally:
            db.close()
        if changed:
            await apply_switches(changed)
    except Exception as exc:
        logger.warning("应用采集开关失败: %s", exc)

    # TACACS+ / RADIUS 认证服务（设备侧登录平台时使用）
    try:
        from app.services.aaa_service import start_aaa_services
        from app.services.settings_service import effective_settings

        aaa_cfg = effective_settings()
        if aaa_cfg.get("aaa.enabled"):
            await start_aaa_services(
                tacacs_port=int(aaa_cfg.get("aaa.tacacs_port") or 49),
                radius_port=int(aaa_cfg.get("aaa.radius_port") or 1812),
            )
    except Exception as exc:
        logger.warning("AAA 服务启动失败: %s", exc)

    logger.info("后端已就绪 · %s", settings.app_name)
    try:
        yield
    finally:
        await shutdown_scheduler()
        await stop_gnmi()
        await stop_netflow()
        await stop_syslog()
        try:
            from app.services.aaa_service import stop_aaa_services

            await stop_aaa_services()
        except Exception:
            pass
        logger.info("后端已关闭")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="多厂商交换机监控平台：接口速率 / CPU 内存 / 告警 / 日志 / 资产 / 备份 / 基线巡检",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(monitor.router)
app.include_router(alerts.router)
app.include_router(logs.router)
app.include_router(ops.backup_router)
app.include_router(ops.inspection_router)
app.include_router(flows.netflow_router)
app.include_router(flows.telemetry_router)
app.include_router(device_access.router)
app.include_router(scheduler_api.router)
app.include_router(platform.settings_router)
app.include_router(platform.topology_router)
app.include_router(platform.audit_router)
app.include_router(platform.ai_router)


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """写操作自动记录审计日志。"""
    response = await call_next(request)
    try:
        from app.services.audit_service import record, should_audit

        path = request.url.path
        if should_audit(request.method, path):
            username = ""
            auth_header = request.headers.get("authorization") or ""
            if auth_header.lower().startswith("bearer "):
                from app.core.security import decode_token

                try:
                    payload = decode_token(auth_header[7:])
                    username = payload.get("sub", "")
                except Exception:
                    username = ""
            ip = request.client.host if request.client else ""
            record(
                None,
                username,
                f"{request.method} {path.split('/')[2] if len(path.split('/')) > 2 else path}",
                path[:120],
                f"status={response.status_code}",
                ip,
            )
    except Exception:
        pass
    return response


# ---------------------------------------------------------------- 初始化数据
def _migrate_schema() -> None:
    """轻量 schema 迁移：为 SQLite 已有表补齐新增列。

    SQLAlchemy 的 create_all 不会给已存在的表加列，
    因此版本升级新增字段时需要在这里显式 ALTER。
    这里按 ORM 模型自动比对，缺什么补什么，避免每次升级都要手写迁移清单。
    """
    if not settings.database_url.startswith("sqlite"):
        return
    import sqlalchemy as sa

    db = SessionLocal()
    try:
        inspector = sa.inspect(engine)
        existing_tables = set(inspector.get_table_names())
        added = 0
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            cols = {c["name"] for c in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in cols:
                    continue
                ddl = _column_ddl(column)
                if not ddl:
                    continue
                with engine.begin() as conn:
                    conn.execute(sa.text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {ddl}"))
                logger.info("schema 迁移: 表 %s 新增列 %s", table_name, column.name)
                added += 1
        if added:
            logger.info("schema 迁移完成，共新增 %s 列", added)
    except Exception as exc:
        logger.warning("schema 迁移失败: %s", exc)
    finally:
        db.close()


def _column_ddl(column) -> str:
    """根据 ORM 列定义生成 ALTER TABLE 用的 DDL 片段。

    注意：SQLite 的 ALTER TABLE ADD COLUMN 不支持对已有行无默认值的
    NOT NULL 列，因此这里不生成 NOT NULL 约束，避免迁移失败。
    字符串默认值必须用单引号包裹并转义，否则 '{}' 这类值会产生非法 SQL。
    """
    try:
        col_type = column.type.compile(dialect=engine.dialect)
    except Exception:
        return ""
    ddl = col_type

    def _literal(value) -> str:
        """把 Python 默认值转成安全的 SQL 字面量。"""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).replace("'", "''")
        return f"'{text}'"

    # 优先用 server_default，其次用 ORM 的 Python 层 default
    default = getattr(column.server_default, "arg", None)
    if default is not None:
        literal = _literal(default)
        if literal != "NULL":
            ddl += f" DEFAULT {literal}"
    else:
        py_default = getattr(column, "default", None)
        if py_default is not None and getattr(py_default, "is_scalar", False):
            ddl += f" DEFAULT {_literal(py_default.arg)}"
        elif not column.nullable:
            ddl += " DEFAULT 0"
    return ddl


def init_data() -> None:
    """首次启动写入默认管理员、门店、告警规则、巡检模板。"""
    db = SessionLocal()
    try:
        # 回收上次异常中断遗留的僵死巡检任务（服务刚启动，不会有进行中的任务）
        from app.services.inspection import recover_stale_tasks

        stale = recover_stale_tasks(db, force=True)
        if stale:
            logger.warning("已回收 %s 个僵死的巡检任务", stale)

        if not db.query(User).filter(User.username == "admin").first():
            db.add(
                User(
                    username="admin",
                    display_name="系统管理员",
                    password_hash=hash_password("admin123"),
                    role="admin",
                    source="local",
                    email="admin@example.com",
                )
            )
            logger.info("已创建默认管理员账号 admin / admin123")

        if not db.query(Store).count():
            demo_stores = [
                ("SH001", "上海总部", "华东", "上海市浦东新区张江高科", "张伟", "021-88880000"),
                ("BJ002", "北京分公司", "华北", "北京市海淀区中关村大街", "李娜", "010-66660000"),
                ("GZ003", "广州门店", "华南", "广州市天河区珠江新城", "王强", "020-88880000"),
                ("CD004", "成都门店", "西南", "成都市高新区天府大道", "赵敏", "028-66660000"),
            ]
            for code, name, region, address, contact, phone in demo_stores:
                db.add(Store(code=code, name=name, region=region, address=address, contact=contact, phone=phone))
            db.flush()

        if not db.query(AlertRule).count():
            default_rules = [
                ("CPU 使用率过高", "cpu", ">", 80, "warning", "所有设备 CPU 使用率超过 80%"),
                ("内存使用率过高", "mem", ">", 85, "critical", "所有设备内存使用率超过 85%"),
                ("设备离线", "offline", ">", 0, "critical", "设备无法访问"),
                ("接口流量超阈值", "traffic", ">", 800, "warning", "单接口流量超过 800Mbps"),
                ("接口利用率过高", "util", ">", 90, "warning", "接口利用率超过 90%"),
                ("接口错包过多", "loss", ">", 10, "info", "接口错包数超过 10"),
                ("设备温度过高", "temperature", ">", 65, "warning", "设备温度超过 65℃"),
            ]
            for name, metric, op, threshold, severity, desc in default_rules:
                db.add(
                    AlertRule(
                        name=name,
                        metric=metric,
                        operator=op,
                        threshold=threshold,
                        severity=severity,
                        description=desc,
                        silence_minutes=10,
                    )
                )

        if not db.query(NotificationChannel).count():
            db.add(
                NotificationChannel(
                    name="默认 Webhook",
                    type="webhook",
                    target="",
                    enabled=False,
                    min_severity="warning",
                )
            )

        ensure_default_templates(db)
        db.commit()
    except Exception as exc:
        logger.exception("初始化数据失败: %s", exc)
        db.rollback()
    finally:
        db.close()


@app.get("/api/health")
def health(request: Request) -> Dict[str, Any]:
    from app.services.scheduler import get_scheduler
    from app.services.telemetry_service import telemetry_status

    sched = get_scheduler()
    nf = getattr(request.app.state, "netflow", None)
    tel = telemetry_status()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "scheduler_running": sched.running,
        "jobs": [j.id for j in sched.get_jobs()],
        "syslog_udp_port": settings.syslog_udp_port,
        "ldap_enabled": settings.ldap_enabled,
        "netflow_running": nf is not None,
        "netflow_ports": settings.netflow_udp_ports,
        "telemetry_running": tel["collector_running"],
        "gnmi_port": tel["collector_port"],
        "telemetry_sessions": len(tel["sessions"]),
        "time": datetime.now().isoformat(),
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": f"服务器内部错误: {exc}"})


# ---------------------------------------------------------------- 前端静态资源
if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "接口不存在"})
        index = os.path.join(FRONTEND_DIST, "index.html")
        if os.path.exists(index):
            return FileResponse(index)
        return JSONResponse(status_code=404, content={"detail": "前端未构建"})
else:
    @app.get("/")
    def root() -> Dict[str, Any]:
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "note": "前端未构建，请执行 cd frontend && npm install && npm run build",
        }
