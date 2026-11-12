"""MIB 指标探测：验证设备实际支持哪些指标。

上传厂商 MIB 后，用户最想知道的是「这台设备到底支持哪些指标」——
与其一个个试 OID，不如拿 MIB 里的指标去设备上批量探测。

实现：对 MIB 中的标量型指标批量 GET（一次请求多个 OID），
能取到值的就是设备支持的；再对表型指标做一次 WALK 看是否有数据。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from app.collectors.snmp import SnmpClient, target_from_device


async def probe_device_mibs(
    device,
    node_ids: Optional[List[int]] = None,
    keyword: str = "",
    limit: int = 60,
) -> Dict[str, Any]:
    """探测设备对指定 MIB 指标的支持情况。

    node_ids 为空时按 keyword 在已上传的 MIB 里筛选指标。
    返回每个指标的：OID、名称、是否支持、当前值、单位描述。
    """
    from app.db.session import SessionLocal
    from app.models.models import MibNode

    db = SessionLocal()
    try:
        q = db.query(MibNode)
        if node_ids:
            q = q.filter(MibNode.id.in_(node_ids))
        if keyword:
            like = f"%{keyword.strip()}%"
            q = q.filter(
                (MibNode.name.like(like)) | (MibNode.oid.like(like))
            )
        # 只探测标量型（表格节点的 OID 是表头，探测无意义）
        rows = q.filter(MibNode.type_label == "标量").order_by(
            MibNode.oid_tuple
        ).limit(limit).all()
        nodes = [
            {
                "id": r.id,
                "name": r.name,
                "oid": r.oid,
                "syntax": r.syntax,
                "description": r.description,
                "access": r.access,
            }
            for r in rows
        ]
    finally:
        db.close()

    if not nodes:
        return {
            "total": 0,
            "supported": 0,
            "items": [],
            "message": "没有可探测的标量指标（可换个关键字，或上传的 MIB 里缺少标量定义）",
        }

    client = SnmpClient(target_from_device(device))
    # 标量指标的**实例** OID 是在定义 OID 后加 ".0"——这是 SNMP 的约定。
    # MIB 里定义的是 1.3.6.1.4.1.12356.101.1.1，
    # 而设备上可读的是 1.3.6.1.4.1.12356.101.1.1.0。
    # 查询用的 OID 不能带前导点（SNMP 要求点分数字形式）。
    query_oids: List[str] = []
    for n in nodes:
        base = n["oid"].strip().lstrip(".").rstrip(".")
        query_oids.append(base + ".0" if not base.endswith(".0") else base)

    values: Dict[str, Any] = {}
    try:
        result = await client.get(query_oids)
        if result.ok:
            values = result.values
        else:
            # SNMP 整体失败（认证/超时），返回错误信息
            return {
                "total": len(nodes),
                "supported": 0,
                "items": [],
                "error": result.error or "SNMP 请求失败",
            }
    except Exception as exc:
        return {
            "total": len(nodes),
            "supported": 0,
            "items": [],
            "error": f"SNMP 请求异常: {exc}",
        }

    items = []
    supported = 0
    for n, q in zip(nodes, query_oids):
        raw = values.get(q)
        # "No Such Object/Instance" 表示设备不支持该 OID
        is_supported = raw is not None and not str(raw).startswith("No Such")
        if is_supported:
            supported += 1
        items.append(
            {
                **n,
                "query_oid": q,
                "supported": is_supported,
                "value": raw if is_supported else None,
            }
        )

    return {
        "total": len(nodes),
        "supported": supported,
        "items": items,
        "device": {"id": device.id, "name": device.name, "ip": device.ip},
    }
