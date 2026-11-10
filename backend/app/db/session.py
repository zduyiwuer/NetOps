"""数据库会话与初始化。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_connect_args = {}
engine_kwargs = {"pool_pre_ping": True, "future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _connect_args = engine_kwargs["connect_args"]

engine = create_engine(settings.database_url, **engine_kwargs)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 引擎钩子
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        # 写锁等待：多进程/多线程并发写入时避免立即报 database is locked
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator:
    """FastAPI 依赖：普通（同步）路由使用。"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def async_db() -> AsyncIterator:
    """异步采集任务使用的会话；在工作线程中执行，避免阻塞事件循环。"""

    def _work():
        db = SessionLocal()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # SessionLocal 的同步调用放入线程池
    import queue
    import threading

    q: "queue.Queue" = queue.Queue()

    def runner():
        try:
            db = SessionLocal()
            q.put(("ok", db))
        except Exception as exc:  # pragma: no cover
            q.put(("err", exc))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    status, payload = q.get()
    if status == "err":
        raise payload
    db = payload
    try:
        yield db
        await asyncio.to_thread(db.commit)
    except Exception:
        await asyncio.to_thread(db.rollback)
        raise
    finally:
        await asyncio.to_thread(db.close)
