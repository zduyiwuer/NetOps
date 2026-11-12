"""MIB 文件管理 API：上传 / 列表 / 删除 / 指标检索。"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import get_current_user, require_admin, require_operator
from app.db.session import get_db
from app.models.models import User
from app.services import mib_service

router = APIRouter(prefix="/api/mibs", tags=["MIB 文件"])

# 单个 MIB 文件大小上限（8 MB）
MAX_SIZE = 8 * 1024 * 1024
ALLOWED_SUFFIX = (".mib", ".txt", ".my", ".mib.txt")


@router.get("")
def list_files(_: User = Depends(get_current_user)) -> Dict[str, Any]:
    """MIB 文件列表。"""
    return {"items": mib_service.list_mib_files()}


@router.post("/upload")
async def upload_mib(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> Dict[str, Any]:
    """上传 MIB 文件并解析出 OID 索引。"""
    name = file.filename or "mib.txt"
    lower = name.lower()
    if not lower.endswith(ALLOWED_SUFFIX):
        raise HTTPException(
            status_code=400,
            detail=f"只支持 .mib / .txt 格式的 MIB 文件（收到 {name}）",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > MAX_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大（{len(content) // 1024} KB），上限 8 MB",
        )

    result = mib_service.save_mib_file(name, content, user.username)
    if result["status"] != "ok":
        # 解析失败也保留文件，方便用户查看内容后重传
        raise HTTPException(
            status_code=400,
            detail=result["error"] or "MIB 解析失败",
        )
    return {"ok": True, "item": result}


@router.delete("/{mib_id}")
def delete_file(
    mib_id: int,
    _: User = Depends(require_operator),
) -> Dict[str, Any]:
    """删除 MIB 文件及其索引。"""
    if not mib_service.delete_mib_file(mib_id):
        raise HTTPException(status_code=404, detail="MIB 文件不存在")
    return {"ok": True}


@router.get("/nodes")
def list_nodes(
    keyword: str = Query("", description="按名称 / OID / 描述搜索"),
    mib_file_id: Optional[int] = Query(None, description="限定某个 MIB 文件"),
    limit: int = Query(200, ge=1, le=2000),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """检索已上传 MIB 里的指标定义。"""
    items = mib_service.search_nodes(keyword, mib_file_id, limit)
    return {"items": items, "total": len(items)}


@router.get("/resolve")
def resolve_oid(
    oid: str = Query("", description="数字 OID，如 1.3.6.1.4.1.9.9.166.1.1.1.1.0"),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """按数字 OID 反查指标名。"""
    node = mib_service.resolve_oid(oid)
    return {"item": node, "found": node is not None}


@router.post("/probe/{device_id}")
async def probe_device(
    device_id: int,
    keyword: str = Query("", description="按关键字筛选指标"),
    node_ids: str = Query("", description="逗号分隔的指标 ID，留空则按关键字筛选"),
    limit: int = Query(60, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """探测设备对 MIB 指标的支持情况。

    上传厂商 MIB 后，用它来确认这台设备到底支持哪些指标——
    平台内置的厂商 OID 未必覆盖某个型号/固件。
    """
    from app.models.models import Device
    from app.services import mib_probe

    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")

    ids: List[int] = []
    if node_ids:
        for part in node_ids.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    return await mib_probe.probe_device_mibs(device, ids, keyword, limit)
