"""设备 / 门店 / 资产 API。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.collectors.vendors import VENDORS, vendor_options
from app.core.crypto import MASK, mask_secret
from app.core.security import require_admin, require_operator
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.models import (
    AlertEvent,
    ConfigBackup,
    Device,
    DeviceLog,
    DeviceMetric,
    Store,
    User,
    WirelessAP,
    WirelessClient,
    WirelessRoaming,
)
from app.services import importer
from app.services.scheduler import collect_device

router = APIRouter(prefix="/api", tags=["设备与资产"])

# 敏感字段：更新时若留空或为掩码则不覆盖原值
SECRET_FIELDS = (
    "snmp_community",
    "snmp_v3_auth_key",
    "snmp_v3_priv_key",
    "ssh_password",
    "ssh_enable_password",
)


# ---------------------------------------------------------------- 模型
class DeviceIn(BaseModel):
    name: str = Field(min_length=1)
    ip: str = Field(min_length=1)
    vendor: str = "generic"
    device_type: str = "switch"
    model: str = ""
    serial_number: str = ""
    mac: str = ""
    location: str = ""
    store_id: Optional[int] = None
    snmp_version: str = "2c"
    snmp_community: str = "public"
    snmp_port: int = 161
    snmp_v3_user: str = ""
    snmp_v3_auth_proto: str = "SHA"
    snmp_v3_auth_key: str = ""
    snmp_v3_priv_proto: str = "AES"
    snmp_v3_priv_key: str = ""
    ssh_port: int = 22
    ssh_username: str = ""
    ssh_password: str = ""
    ssh_enable_password: str = ""
    poll_interval: int = 60
    collect_interface: bool = True
    collect_system: bool = True
    collect_log: bool = True
    collect_backup: bool = True
    mock_mode: bool = False
    use_snmp: bool = True
    use_telemetry: bool = False
    use_netflow: bool = False
    asset_tag: str = ""
    firmware_version: str = ""
    purchase_date: str = ""
    warranty_end: str = ""
    vendor_contact: str = ""
    contract_no: str = ""
    price: float = 0.0
    remark: str = ""
    enabled: bool = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    vendor: Optional[str] = None
    device_type: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    mac: Optional[str] = None
    location: Optional[str] = None
    store_id: Optional[int] = None
    snmp_version: Optional[str] = None
    snmp_community: Optional[str] = None
    snmp_port: Optional[int] = None
    snmp_v3_user: Optional[str] = None
    snmp_v3_auth_proto: Optional[str] = None
    snmp_v3_auth_key: Optional[str] = None
    snmp_v3_priv_proto: Optional[str] = None
    snmp_v3_priv_key: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_username: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_enable_password: Optional[str] = None
    poll_interval: Optional[int] = None
    collect_interface: Optional[bool] = None
    collect_system: Optional[bool] = None
    collect_log: Optional[bool] = None
    collect_backup: Optional[bool] = None
    mock_mode: Optional[bool] = None
    use_snmp: Optional[bool] = None
    use_telemetry: Optional[bool] = None
    use_netflow: Optional[bool] = None
    asset_tag: Optional[str] = None
    firmware_version: Optional[str] = None
    purchase_date: Optional[str] = None
    warranty_end: Optional[str] = None
    vendor_contact: Optional[str] = None
    contract_no: Optional[str] = None
    price: Optional[float] = None
    remark: Optional[str] = None
    enabled: Optional[bool] = None


class StoreIn(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    region: str = ""
    address: str = ""
    contact: str = ""
    phone: str = ""
    manager: str = ""
    remark: str = ""


def _device_payload(d: Device, store: Optional[Store] = None) -> Dict[str, Any]:
    """设备信息对外输出。

    密码类字段一律脱敏（掩码），前端回显为 ********；
    更新时若仍为掩码或空串，则保留原值不变。
    """
    return {
        "id": d.id,
        "name": d.name,
        "hostname": d.hostname,
        "ip": d.ip,
        "vendor": d.vendor,
        "vendor_label": VENDORS.get(d.vendor, VENDORS["generic"]).label if d.vendor in VENDORS else d.vendor,
        "model": d.model,
        "serial_number": d.serial_number,
        "mac": d.mac,
        "device_type": d.device_type,
        "location": d.location,
        "store_id": d.store_id,
        "store_name": store.name if store else "",
        "snmp_version": d.snmp_version,
        "snmp_community": mask_secret(d.snmp_community),
        "snmp_port": d.snmp_port,
        "snmp_v3_user": d.snmp_v3_user,
        "snmp_v3_auth_proto": d.snmp_v3_auth_proto,
        "snmp_v3_auth_key": mask_secret(d.snmp_v3_auth_key),
        "snmp_v3_priv_proto": d.snmp_v3_priv_proto,
        "snmp_v3_priv_key": mask_secret(d.snmp_v3_priv_key),
        "ssh_port": d.ssh_port,
        "ssh_username": d.ssh_username,
        "ssh_password": mask_secret(d.ssh_password),
        "ssh_enable_password": mask_secret(d.ssh_enable_password),
        "poll_interval": d.poll_interval,
        "collect_interface": d.collect_interface,
        "collect_system": d.collect_system,
        "collect_log": d.collect_log,
        "collect_backup": d.collect_backup,
        "mock_mode": d.mock_mode,
        "use_snmp": d.use_snmp,
        "use_telemetry": d.use_telemetry,
        "use_netflow": d.use_netflow,
        "asset_tag": d.asset_tag,
        "firmware_version": d.firmware_version,
        "purchase_date": d.purchase_date,
        "warranty_end": d.warranty_end,
        "vendor_contact": d.vendor_contact,
        "contract_no": d.contract_no,
        "price": d.price,
        "remark": d.remark,
        "status": d.status,
        "enabled": d.enabled,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


# ---------------------------------------------------------------- 设备
@router.get("/devices")
def list_devices(
    store_id: Optional[int] = None,
    vendor: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    enabled: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    q = db.query(Device)
    if store_id:
        q = q.filter(Device.store_id == store_id)
    if vendor:
        q = q.filter(Device.vendor == vendor)
    if status:
        q = q.filter(Device.status == status)
    if enabled is not None:
        q = q.filter(Device.enabled == enabled)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            (Device.name.like(like))
            | (Device.ip.like(like))
            | (Device.model.like(like))
            | (Device.serial_number.like(like))
            | (Device.location.like(like))
        )
    total = q.count()
    devices = q.order_by(Device.id).offset((page - 1) * page_size).limit(page_size).all()
    stores = {s.id: s for s in db.query(Store).all()}
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_device_payload(d, stores.get(d.store_id)) for d in devices],
    }


@router.get("/devices/{device_id}")
def get_device(device_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    store = db.get(Store, device.store_id) if device.store_id else None
    data = _device_payload(device, store)
    latest = (
        db.query(DeviceMetric)
        .filter(DeviceMetric.device_id == device_id)
        .order_by(DeviceMetric.id.desc())
        .first()
    )
    data["latest_metric"] = _metric_payload(latest) if latest else None
    return data


def _metric_payload(m: DeviceMetric) -> Dict[str, Any]:
    return {
        "id": m.id,
        "ts": m.ts.isoformat(),
        "cpu_usage": m.cpu_usage,
        "mem_usage": m.mem_usage,
        "mem_total_mb": m.mem_total_mb,
        "mem_used_mb": m.mem_used_mb,
        "temperature": m.temperature,
        "uptime_sec": m.uptime_sec,
        "response_ms": m.response_ms,
        "reachability": m.reachability,
    }


@router.post("/devices")
def create_device(payload: DeviceIn, db: Session =Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    if db.query(Device).filter(Device.ip == payload.ip).first():
        raise HTTPException(status_code=400, detail="该 IP 已存在设备记录")
    data = payload.model_dump()
    device = Device(**data)
    db.add(device)
    db.commit()
    store = db.get(Store, device.store_id) if device.store_id else None
    return _device_payload(device, store)


@router.put("/devices/{device_id}")
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    data = payload.model_dump(exclude_none=True)
    # 密码类字段：留空或为掩码时保持原值不变
    for field in SECRET_FIELDS:
        if field in data and (not data[field] or data[field] == MASK):
            data.pop(field)
    for key, value in data.items():
        setattr(device, key, value)
    db.commit()
    store = db.get(Store, device.store_id) if device.store_id else None
    return _device_payload(device, store)


@router.delete("/devices/{device_id}")
def delete_device(device_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    db.delete(device)
    db.commit()
    return {"ok": True}


@router.post("/devices/{device_id}/collect")
async def trigger_collect(device_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    sample = await collect_device(device_id)
    if sample is None:
        raise HTTPException(status_code=500, detail="采集失败")
    return {
        "ok": sample.system.reachable,
        "error": sample.system.error,
        "cpu_usage": sample.system.cpu_usage,
        "mem_usage": sample.system.mem_usage,
        "response_ms": sample.system.response_ms,
        "interfaces": len(sample.interfaces),
    }


class ProbeBatchIn(BaseModel):
    """批量探测参数。"""

    device_ids: Optional[List[int]] = Field(
        default=None,
        description="指定设备 ID 列表；不传则探测全部已启用设备",
    )
    concurrency: int = Field(default=10, ge=1, le=32, description="并发上限")
    update_status: bool = Field(default=True, description="是否写回设备状态")


@router.post("/devices/probe/batch")
async def probe_devices_batch(
    payload: ProbeBatchIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """批量探测设备在线状态。

    并发受限（默认 10），避免一次性对大量设备发起请求打满网络；
    逐台返回结果并更新设备状态。
    """
    from app.services import probe_service

    return await probe_service.probe_many(
        db,
        device_ids=payload.device_ids,
        concurrency=payload.concurrency,
        update_status=payload.update_status,
    )


@router.post("/devices/{device_id}/probe")
async def probe_device(
    device_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """探测单台设备是否在线。

    比 ``/test`` 更轻量：只做一次 SNMP GET（sysUpTime）判断可达性，
    不采集接口流量与 CPU/内存，结果写回设备的 status 与 last_seen。
    """
    from app.services import probe_service

    try:
        return await probe_service.probe_one(db, device_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/devices/{device_id}/test")
async def test_device(device_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    """连通性测试（SNMP + 可选 SSH）。"""
    from app.collectors.engine import Collector

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    collector = Collector(device)
    sample = await collector.collect(collect_interface=True, collect_system=True)
    return {
        "snmp_ok": sample.system.reachable,
        "error": sample.system.error,
        "response_ms": sample.system.response_ms,
        "cpu_usage": sample.system.cpu_usage,
        "mem_usage": sample.system.mem_usage,
        "uptime_sec": sample.system.uptime_sec,
        "interfaces": [
            {
                "if_index": i.if_index,
                "name": i.name or i.descr,
                "speed_mbps": round(i.speed_bps / 1_000_000, 2),
                "oper_status": i.oper_status,
                "in_bps": i.in_bps,
                "out_bps": i.out_bps,
            }
            for i in sample.interfaces
        ],
    }


# ---------------------------------------------------------------- 门店
@router.get("/stores")
def list_stores(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    stores = db.query(Store).order_by(Store.id).all()
    counts = {s.id: s for s in stores}
    device_counts: Dict[int, int] = {}
    online_counts: Dict[int, int] = {}
    for d in db.query(Device).all():
        device_counts[d.store_id] = device_counts.get(d.store_id, 0) + 1
        if d.status == "online":
            online_counts[d.store_id] = online_counts.get(d.store_id, 0) + 1
    items = []
    for s in stores:
        items.append({
            "id": s.id,
            "code": s.code,
            "name": s.name,
            "region": s.region,
            "address": s.address,
            "contact": s.contact,
            "phone": s.phone,
            "manager": s.manager,
            "remark": s.remark,
            "device_count": device_counts.get(s.id, 0),
            "online_count": online_counts.get(s.id, 0),
            "offline_count": device_counts.get(s.id, 0) - online_counts.get(s.id, 0),
        })
    return {"items": items}


@router.post("/stores")
def create_store(payload: StoreIn, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    if db.query(Store).filter(Store.code == payload.code).first():
        raise HTTPException(status_code=400, detail="门店编码已存在")
    store = Store(**payload.model_dump())
    db.add(store)
    db.commit()
    return {"id": store.id, **payload.model_dump()}


@router.put("/stores/{store_id}")
def update_store(store_id: int, payload: StoreIn, db: Session = Depends(get_db), _: User = Depends(require_operator)) -> Dict[str, Any]:
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="门店不存在")
    for key, value in payload.model_dump().items():
        setattr(store, key, value)
    db.commit()
    return {"id": store.id, **payload.model_dump()}


@router.delete("/stores/{store_id}")
def delete_store(store_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> Dict[str, Any]:
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="门店不存在")
    db.delete(store)
    db.commit()
    return {"ok": True}


@router.get("/stores/{store_id}/devices")
def store_devices(store_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail="门店不存在")
    devices = db.query(Device).filter(Device.store_id == store_id).order_by(Device.id).all()
    return {"items": [_device_payload(d, store) for d in devices]}


# ---------------------------------------------------------------- 资产
@router.get("/assets/summary")
def asset_summary(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    devices = db.query(Device).all()
    by_vendor: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    by_store: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    total_value = 0.0
    warranty_soon = 0
    today = datetime.now()
    for d in devices:
        by_vendor[d.vendor] = by_vendor.get(d.vendor, 0) + 1
        by_type[d.device_type] = by_type.get(d.device_type, 0) + 1
        by_store[d.store_id] = by_store.get(d.store_id, 0) + 1
        by_status[d.status] = by_status.get(d.status, 0) + 1
        total_value += d.price or 0
        if d.warranty_end:
            try:
                end = datetime.strptime(d.warranty_end, "%Y-%m-%d")
                if (end - today).days <= 180:
                    warranty_soon += 1
            except ValueError:
                pass
    stores = {s.id: s.name for s in db.query(Store).all()}
    return {
        "total": len(devices),
        "total_value": round(total_value, 2),
        "warranty_soon": warranty_soon,
        "by_vendor": by_vendor,
        "by_type": by_type,
        "by_status": by_status,
        "by_store": {stores.get(k, "未分配"): v for k, v in by_store.items()},
    }


# ---------------------------------------------------------------- 导入导出
@router.get("/import/template.csv")
def import_template_csv(_: User = Depends(get_current_user)) -> Response:
    content = importer.build_template_csv()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="device_import_template.csv"'},
    )


@router.get("/import/template.xlsx")
def import_template_xlsx(_: User = Depends(get_current_user)) -> Response:
    content = importer.build_template_xlsx()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="device_import_template.xlsx"'},
    )


@router.post("/import/devices")
async def import_devices_api(
    file: UploadFile = File(...),
    update_existing: bool = Query(False, description="已存在相同 IP 时是否覆盖"),
    db: Session = Depends(get_db),
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    content = await file.read()
    try:
        rows = importer.parse_upload(file.filename or "", content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not rows:
        raise HTTPException(status_code=400, detail="文件为空或未识别到有效数据行")
    result = importer.import_devices(db, rows, update_existing=update_existing)
    return result


@router.get("/export/devices.csv")
def export_devices_csv(
    store_id: Optional[int] = None,
    vendor: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    rows = importer.export_devices(db, store_id=store_id, vendor=vendor)
    content = importer.export_csv(rows)
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="devices_export.csv"'},
    )


@router.get("/export/devices.xlsx")
def export_devices_xlsx(
    store_id: Optional[int] = None,
    vendor: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = importer.export_devices(db, store_id=store_id, vendor=vendor)
    wb = Workbook()
    ws = wb.active
    ws.title = "设备清单"
    headers = list(rows[0].keys()) if rows else [c["key"] for c in importer.COLUMNS]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="374151")
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 16
    ws.freeze_panes = "A2"
    import io

    buffer = io.BytesIO()
    wb.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="devices_export.xlsx"'},
    )


@router.get("/vendors")
def list_vendors(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    return {"items": vendor_options()}


# ---------------------------------------------------------------- 无线数据
@router.get("/devices/{device_id}/wireless/aps")
def list_wireless_aps(
    device_id: int,
    keyword: str = Query("", description="按名称 / 型号 / IP 搜索"),
    state: str = Query("", description="按状态过滤"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """无线控制器下挂的 AP 列表（SNMP 采集，含信道与序列号）。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    q = db.query(WirelessAP).filter(WirelessAP.device_id == device_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter(
            (WirelessAP.name.like(like))
            | (WirelessAP.model.like(like))
            | (WirelessAP.ip.like(like))
        )
    if state:
        q = q.filter(WirelessAP.state == state)
    rows = q.order_by(WirelessAP.name).all()
    return {
        "items": [
            {
                "name": r.name,
                "model": r.model,
                "slots": r.slots,
                "radio_mac": r.radio_mac,
                "location": r.location,
                "serial_number": r.serial_number,
                "ip": r.ip,
                "status": r.status,
                "state": r.state,
                "channels": r.channels,
                "ts": r.ts.isoformat() if r.ts else "",
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/devices/{device_id}/wireless/clients")
def list_wireless_clients(
    device_id: int,
    keyword: str = Query("", description="按 MAC / AP 名 / SSID 搜索"),
    ap_name: str = Query("", description="按 AP 过滤"),
    ssid: str = Query("", description="按 SSID 过滤"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """无线客户端列表（SNMP 采集，含信道 / RSSI / SNR / 收发流量）。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    q = db.query(WirelessClient).filter(WirelessClient.device_id == device_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter(
            (WirelessClient.mac.like(like))
            | (WirelessClient.ap_name.like(like))
            | (WirelessClient.ssid.like(like))
        )
    if ap_name:
        q = q.filter(WirelessClient.ap_name == ap_name)
    if ssid:
        q = q.filter(WirelessClient.ssid == ssid)
    rows = q.order_by(WirelessClient.ap_name, WirelessClient.mac).all()
    # SSID 列表（前端筛选用）
    ssids = sorted({r.ssid for r in rows if r.ssid})
    return {
        "items": [
            {
                "mac": r.mac,
                "ip": r.ip,
                "username": r.username,
                "bssid": r.bssid,
                "ap_name": r.ap_name,
                "ssid": r.ssid,
                "slot": r.slot,
                "channel": r.channel,
                "signal_strength": r.signal_strength,
                "rssi": r.rssi,
                "snr": r.snr,
                "bytes_in": r.bytes_in,
                "bytes_out": r.bytes_out,
                "packets_in": r.packets_in,
                "packets_out": r.packets_out,
                "state": r.state,
                "ts": r.ts.isoformat() if r.ts else "",
            }
            for r in rows
        ],
        "total": len(rows),
        "ssids": ssids,
    }


@router.get("/devices/{device_id}/wireless/roaming")
def list_wireless_roaming(
    device_id: int,
    keyword: str = Query("", description="按 MAC / AP 名搜索"),
    hours: int = Query(24, ge=1, le=720, description="近 N 小时"),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """无线客户端漫游记录（平台对比采集快照检测 AP 变化生成）。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")
    since = datetime.now() - timedelta(hours=hours)
    q = db.query(WirelessRoaming).filter(
        WirelessRoaming.device_id == device_id,
        WirelessRoaming.ts >= since,
    )
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter(
            (WirelessRoaming.mac.like(like))
            | (WirelessRoaming.from_ap.like(like))
            | (WirelessRoaming.to_ap.like(like))
        )
    rows = q.order_by(WirelessRoaming.ts.desc()).limit(limit).all()
    return {
        "items": [
            {
                "mac": r.mac,
                "from_ap": r.from_ap,
                "to_ap": r.to_ap,
                "from_bssid": r.from_bssid,
                "to_bssid": r.to_bssid,
                "from_channel": r.from_channel,
                "to_channel": r.to_channel,
                "rssi": r.rssi,
                "snr": r.snr,
                "ts": r.ts.isoformat() if r.ts else "",
            }
            for r in rows
        ],
        "total": len(rows),
    }
