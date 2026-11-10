"""设备导入导出：Excel / CSV 模板、批量导入、导出。"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.collectors.vendors import VENDORS, resolve_vendor
from app.models.models import Device, Store

COLUMNS = [
    {"key": "name", "title": "设备名称", "width": 18, "required": True, "example": "总部核心交换机"},
    {"key": "ip", "title": "管理IP", "width": 15, "required": True, "example": "10.10.0.1"},
    {"key": "vendor", "title": "厂商", "width": 12, "required": True,
     "example": "h3c", "options": [v.key for v in VENDORS.values()],
     "options_label": {v.key: v.label for v in VENDORS.values()}},
    {"key": "device_type", "title": "设备类型", "width": 10, "example": "switch",
     "options": ["switch", "router", "firewall", "wlc", "ap", "gateway"]},
    {"key": "model", "title": "型号", "width": 16, "example": "S5560X-54C-EI"},
    {"key": "serial_number", "title": "序列号", "width": 16, "example": "210231A0ABC12345"},
    {"key": "store_code", "title": "门店编码", "width": 12, "example": "SH001"},
    {"key": "location", "title": "位置", "width": 18, "example": "总部机房 A 排 3 号机柜"},
    {"key": "snmp_version", "title": "SNMP版本", "width": 10, "example": "2c", "options": ["1", "2c", "3"]},
    {"key": "snmp_community", "title": "SNMP团体字/Community", "width": 16, "example": "public"},
    {"key": "snmp_port", "title": "SNMP端口", "width": 10, "example": "161"},
    {"key": "snmp_v3_user", "title": "SNMPv3用户", "width": 12, "example": ""},
    {"key": "snmp_v3_auth_proto", "title": "v3认证算法", "width": 10, "example": "SHA", "options": ["MD5", "SHA", "SHA224", "SHA256", "SHA384", "SHA512"]},
    {"key": "snmp_v3_auth_key", "title": "v3认证密钥", "width": 14, "example": ""},
    {"key": "snmp_v3_priv_proto", "title": "v3加密算法", "width": 10, "example": "AES", "options": ["DES", "3DES", "AES", "AES192", "AES256"]},
    {"key": "snmp_v3_priv_key", "title": "v3加密密钥", "width": 14, "example": ""},
    {"key": "ssh_port", "title": "SSH端口", "width": 10, "example": "22"},
    {"key": "ssh_username", "title": "SSH用户名", "width": 12, "example": "admin"},
    {"key": "ssh_password", "title": "SSH密码", "width": 14, "example": ""},
    {"key": "ssh_enable_password", "title": "SSH Enable密码", "width": 14, "example": ""},
    {"key": "poll_interval", "title": "采集间隔(秒)", "width": 12, "example": "60"},
    {"key": "asset_tag", "title": "资产编号", "width": 14, "example": "ASSET-0001"},
    {"key": "firmware_version", "title": "固件版本", "width": 14, "example": "V200R003C00"},
    {"key": "purchase_date", "title": "采购日期", "width": 12, "example": "2024-01-15"},
    {"key": "warranty_end", "title": "保修到期", "width": 12, "example": "2027-01-14"},
    {"key": "vendor_contact", "title": "厂商联系方式", "width": 16, "example": "400-800-1234"},
    {"key": "contract_no", "title": "合同编号", "width": 14, "example": "HT2024-001"},
    {"key": "price", "title": "价格", "width": 12, "example": "8600"},
    {"key": "remark", "title": "备注", "width": 24, "example": ""},
]

_KEY_BY_TITLE = {c["title"]: c["key"] for c in COLUMNS}
_KEY_BY_TITLE.update({c["key"]: c["key"] for c in COLUMNS})


def build_template_csv() -> str:
    """生成 CSV 导入模板（含表头 + 示例行 + 门店列说明）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([c["title"] for c in COLUMNS])
    writer.writerow([c.get("example", "") for c in COLUMNS])
    return buffer.getvalue()


def build_template_xlsx() -> bytes:
    """生成 Excel 导入模板（含下拉与示例）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "设备导入模板"

    ws.cell(row=1, column=1, value=f"设备导入模板 · 填写第 2 行起的数据（示例行请删除）· {datetime.now():%Y-%m-%d}")
    ws.cell(row=1, column=1).font = Font(bold=True, size=13)

    header_row = 3
    for i, col in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=header_row, column=i, value=col["title"])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="374151")
        ws.column_dimensions[get_column_letter(i)].width = col["width"]
        if col.get("options"):
            options = col["options"]
            if col.get("options_label"):
                formula = '"' + ",".join(col["options_label"].values()) + '"'
            else:
                formula = '"' + ",".join(options) + '"'
            dv = DataValidation(type="list", formula1=formula, allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{get_column_letter(i)}{header_row + 1}:{get_column_letter(i)}500")

    for i, col in enumerate(COLUMNS, start=1):
        ws.cell(row=header_row + 1, column=i, value=col.get("example", ""))

    # 说明页
    ws2 = wb.create_sheet("填写说明")
    ws2["A1"] = "字段"
    ws2["B1"] = "是否必填"
    ws2["C1"] = "说明"
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="374151")
    notes = {
        "vendor": "设备厂商，决定采集命令与 OID。可选：routeros / cisco / huawei / h3c / generic",
        "store_code": "门店编码。若系统中不存在，导入时自动创建同名门店",
        "snmp_version": "1 / 2c / 3。SNMPv3 需同时填写用户与密钥",
        "ssh_password": "用于配置备份、日志采集与基线巡检的 SSH 账号",
        "poll_interval": "采集间隔（秒），默认 60",
        "mock_mode": "演示模式，不连接真实设备",
    }
    for i, col in enumerate(COLUMNS, start=2):
        ws2.cell(row=i, column=1, value=col["title"])
        ws2.cell(row=i, column=2, value="是" if col.get("required") else "否")
        ws2.cell(row=i, column=3, value=notes.get(col["key"], ""))
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 10
    ws2.column_dimensions["C"].width = 70

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _norm_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """把中文表头映射成字段名。"""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        k = str(key).strip()
        target = _KEY_BY_TITLE.get(k)
        if target:
            out[target] = value
    return out


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def import_devices(db: Session, rows: List[Dict[str, Any]], update_existing: bool = False) -> Dict[str, Any]:
    """批量导入设备。"""
    created = 0
    updated = 0
    skipped = 0
    errors: List[str] = []
    stores_created = 0

    for idx, raw in enumerate(rows, start=1):
        data = _norm_row(raw)
        name = str(data.get("name") or "").strip()
        ip = str(data.get("ip") or "").strip()
        if not name or not ip:
            errors.append(f"第 {idx} 行：设备名称与管理IP不能为空")
            skipped += 1
            continue

        vendor = resolve_vendor(data.get("vendor"))
        store_id = None
        store_code = str(data.get("store_code") or "").strip()
        if store_code:
            store = db.query(Store).filter(Store.code == store_code).first()
            if store is None:
                store = Store(code=store_code, name=store_code)
                db.add(store)
                db.flush()
                stores_created += 1
            store_id = store.id

        existing = db.query(Device).filter(Device.ip == ip).first()
        if existing and not update_existing:
            errors.append(f"第 {idx} 行：IP {ip} 已存在（{existing.name}），跳过")
            skipped += 1
            continue

        device = existing or Device(ip=ip, name=name)
        device.name = name
        device.vendor = vendor.key
        device.hostname = str(data.get("hostname") or "") or name
        device.device_type = str(data.get("device_type") or "switch").strip().lower()
        device.model = str(data.get("model") or "").strip()
        device.serial_number = str(data.get("serial_number") or "").strip()
        device.mac = str(data.get("mac") or "").strip()
        device.location = str(data.get("location") or "").strip()
        device.store_id = store_id

        device.snmp_version = str(data.get("snmp_version") or "2c").strip()
        device.snmp_community = str(data.get("snmp_community") or "public").strip()
        device.snmp_port = _as_int(data.get("snmp_port"), 161) or 161
        device.snmp_v3_user = str(data.get("snmp_v3_user") or "").strip()
        device.snmp_v3_auth_proto = str(data.get("snmp_v3_auth_proto") or "SHA").strip()
        device.snmp_v3_auth_key = str(data.get("snmp_v3_auth_key") or "").strip()
        device.snmp_v3_priv_proto = str(data.get("snmp_v3_priv_proto") or "AES").strip()
        device.snmp_v3_priv_key = str(data.get("snmp_v3_priv_key") or "").strip()

        device.ssh_port = _as_int(data.get("ssh_port"), 22) or 22
        device.ssh_username = str(data.get("ssh_username") or "").strip()
        device.ssh_password = str(data.get("ssh_password") or "")
        device.ssh_enable_password = str(data.get("ssh_enable_password") or "")
        device.poll_interval = _as_int(data.get("poll_interval"), 60) or 60

        device.asset_tag = str(data.get("asset_tag") or "").strip()
        device.firmware_version = str(data.get("firmware_version") or "").strip()
        device.purchase_date = str(data.get("purchase_date") or "").strip()
        device.warranty_end = str(data.get("warranty_end") or "").strip()
        device.vendor_contact = str(data.get("vendor_contact") or "").strip()
        device.contract_no = str(data.get("contract_no") or "").strip()
        device.price = _as_float(data.get("price"), 0.0) or 0.0
        device.remark = str(data.get("remark") or "").strip()

        if existing:
            updated += 1
        else:
            db.add(device)
            created += 1

    db.commit()
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "stores_created": stores_created,
        "errors": errors,
        "total": len(rows),
    }


def parse_upload(filename: str, content: bytes) -> List[Dict[str, Any]]:
    """解析上传的 xlsx / csv 文件为行字典列表。"""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        return _rows_to_dicts(rows)
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader]
        return _rows_to_dicts(rows)
    raise ValueError("仅支持 .xlsx / .csv 文件")


def _rows_to_dicts(rows: List[List[Any]]) -> List[Dict[str, Any]]:
    """找到表头行并转为字典列表（自动跳过前导说明行）。"""
    if not rows:
        return []
    header_idx = 0
    for i, row in enumerate(rows[:8]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if "设备名称" in cells or "name" in [c.lower() for c in cells]:
            header_idx = i
            break
    headers = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    result: List[Dict[str, Any]] = []
    for row in rows[header_idx + 1 :]:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        record = {}
        for i, header in enumerate(headers):
            if not header:
                continue
            record[header] = row[i] if i < len(row) else None
        result.append(record)
    return result


def export_devices(db: Session, store_id: Optional[int] = None, vendor: Optional[str] = None) -> List[Dict[str, Any]]:
    """导出设备清单（字典列表）。"""
    q = db.query(Device)
    if store_id:
        q = q.filter(Device.store_id == store_id)
    if vendor:
        q = q.filter(Device.vendor == vendor)
    stores = {s.id: s for s in db.query(Store).all()}
    out: List[Dict[str, Any]] = []
    for d in q.order_by(Device.id).all():
        store = stores.get(d.store_id)
        out.append({
            "name": d.name,
            "ip": d.ip,
            "vendor": d.vendor,
            "device_type": d.device_type,
            "model": d.model,
            "serial_number": d.serial_number,
            "store_code": store.code if store else "",
            "location": d.location,
            "snmp_version": d.snmp_version,
            "snmp_community": d.snmp_community,
            "snmp_port": d.snmp_port,
            "ssh_port": d.ssh_port,
            "ssh_username": d.ssh_username,
            "poll_interval": d.poll_interval,
            "asset_tag": d.asset_tag,
            "firmware_version": d.firmware_version,
            "purchase_date": d.purchase_date,
            "warranty_end": d.warranty_end,
            "vendor_contact": d.vendor_contact,
            "contract_no": d.contract_no,
            "price": d.price,
            "status": d.status,
            "remark": d.remark,
        })
    return out


def export_csv(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    headers = list(rows[0].keys())
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
