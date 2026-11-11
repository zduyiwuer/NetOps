"""设备在线探测服务。

提供两种粒度的探测：
  - **单设备探测**：探测指定设备，立刻返回结果
  - **批量探测**：并发探测一批设备，逐个回报进度

与采集（collect）的区别：
  - 采集会拉取接口流量、CPU、内存等全部指标，开销大；
  - 探测只做一次轻量 SNMP GET（sysUpTime），快速判断设备是否可达；
  - 探测不写入指标表，只更新设备的 ``status`` 与 ``last_seen``。

批量探测用信号量限制并发，避免一次性对上百台设备发起请求把网络打满。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.collectors.snmp import SnmpClient, target_from_device
from app.collectors.vendors import OID_SYS_UPTIME
from app.core.config import settings
from app.models.models import Device

# 批量探测的默认并发上限
DEFAULT_CONCURRENCY = 10


class ProbeResult:
    """单台设备的探测结果。"""

    def __init__(
        self,
        device_id: int,
        name: str,
        ip: str,
        online: bool,
        response_ms: Optional[float] = None,
        error: str = "",
        elapsed_ms: float = 0.0,
    ):
        self.device_id = device_id
        self.name = name
        self.ip = ip
        self.online = online
        self.response_ms = response_ms
        self.error = error
        self.elapsed_ms = elapsed_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "ip": self.ip,
            "online": self.online,
            "status": "online" if self.online else "offline",
            "response_ms": round(self.response_ms, 1) if self.response_ms is not None else None,
            "error": self.error,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


async def probe_device(device: Device) -> ProbeResult:
    """探测单台设备是否在线。

    优先用 SNMP GET sysUpTime 判断；SNMP 不可用但配置了 SSH 凭据时，
    回退到 SSH 探测（部分设备只开放 SSH）。
    """
    started = time.perf_counter()

    # 演示模式直接视为在线，避免误报
    if getattr(device, "mock_mode", False):
        return ProbeResult(
            device.id, device.name, device.ip, True,
            response_ms=0.0, elapsed_ms=0.0,
        )

    # ---- 轻量 SNMP 探测
    try:
        client = SnmpClient(target_from_device(device))
        result = await client.get([OID_SYS_UPTIME])
        elapsed = time.perf_counter() - started
        if result.ok:
            return ProbeResult(
                device.id, device.name, device.ip, True,
                response_ms=result.elapsed_ms, elapsed_ms=elapsed * 1000,
            )
        error = result.error or "SNMP 无响应"
    except Exception as exc:  # noqa: BLE001
        error = str(exc) or exc.__class__.__name__

    # ---- SSH 兜底
    if device.ssh_username and device.ssh_password:
        try:
            from app.collectors.ssh import SshClient

            client = SshClient(
                host=device.ip,
                username=device.ssh_username,
                password=device.ssh_password or "",
                vendor=device.vendor,
                port=device.ssh_port or 22,
                timeout=device.ssh_timeout or 20,
                enable_password=device.ssh_enable_password or "",
            )
            await client.run_commands(["echo netops-probe"])
            elapsed = time.perf_counter() - started
            return ProbeResult(
                device.id, device.name, device.ip, True,
                response_ms=round(elapsed * 1000, 1), elapsed_ms=elapsed * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"{error}；SSH 也不通: {exc}"

    elapsed = time.perf_counter() - started
    return ProbeResult(
        device.id, device.name, device.ip, False,
        error=error, elapsed_ms=elapsed * 1000,
    )


def _apply_status(db: Session, result: ProbeResult) -> None:
    """把探测结果写回设备状态。"""
    device = db.get(Device, result.device_id)
    if device is None:
        return
    device.status = "online" if result.online else "offline"
    if result.online:
        device.last_seen = datetime.now()


async def probe_one(db: Session, device_id: int) -> Dict[str, Any]:
    """探测单台设备并更新其状态。"""
    device = db.get(Device, device_id)
    if device is None:
        raise ValueError("设备不存在")
    result = await probe_device(device)
    _apply_status(db, result)
    db.commit()
    return result.to_dict()


async def probe_many(
    db: Session,
    device_ids: Optional[List[int]] = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    update_status: bool = True,
    on_progress=None,
) -> Dict[str, Any]:
    """批量探测设备。

    :param device_ids: 指定设备 ID 列表；
        ``None`` 表示探测全部已启用设备，
        空列表 ``[]`` 表示不探测任何设备（与「全部」区分开）
    :param concurrency: 并发上限
    :param update_status: 是否把结果写回设备状态
    :param on_progress: 每完成一台调用的回调（用于实时进度）
    """
    if device_ids is None:
        devices = db.query(Device).filter(Device.enabled.is_(True)).all()
    elif device_ids:
        devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    else:
        devices = []

    total = len(devices)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 32)))
    results: List[ProbeResult] = []
    started = time.perf_counter()

    async def worker(device: Device) -> None:
        async with semaphore:
            try:
                res = await probe_device(device)
            except Exception as exc:  # noqa: BLE001
                res = ProbeResult(
                    device.id, device.name, device.ip, False,
                    error=str(exc) or exc.__class__.__name__,
                )
            results.append(res)
            if on_progress:
                try:
                    on_progress(len(results), total, res)
                except Exception:
                    pass

    await asyncio.gather(*(worker(d) for d in devices))

    # 结果按设备 ID 排序，保证前端展示顺序稳定
    results.sort(key=lambda r: r.device_id)

    if update_status:
        for res in results:
            _apply_status(db, res)
        db.commit()

    online = [r for r in results if r.online]
    offline = [r for r in results if not r.online]
    elapsed = time.perf_counter() - started
    return {
        "total": total,
        "online": len(online),
        "offline": len(offline),
        "elapsed_sec": round(elapsed, 2),
        "items": [r.to_dict() for r in results],
    }
