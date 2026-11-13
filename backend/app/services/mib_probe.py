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
    用 **GETNEXT** 探测（而非 GET oid.0）：返回的 OID 仍是候选的
    后代就说明设备实现了该子树——标识节点 / 表 / 表列 / 标量
    都能测，不会再出现「搜到的指标是标识类型、探测却是 0 个」
    的情况（如 FortiGate 的 fgt301E 型号标识）。
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
        # 标量优先排前（探测结果更有意义），其余类型也参与
        rows = (
            q.order_by(
                # type_label == "标量" 的排前（False > True 升序反转）
                MibNode.type_label != "标量",
                MibNode.oid_tuple,
            )
            .limit(limit)
            .all()
        )
        nodes = [
            {
                "id": r.id,
                "name": r.name,
                "oid": r.oid,
                "syntax": r.syntax,
                "description": r.description,
                "access": r.access,
                "type_label": r.type_label,
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
            "message": "没有匹配的指标（可换个关键字，或上传对应的 MIB 文件）",
        }

    client = SnmpClient(target_from_device(device))
    # GETNEXT 探测：查候选 OID 的下一个实例
    query_oids = [n["oid"].strip().lstrip(".") for n in nodes]

    try:
        result = await client.getnext(query_oids)
        if not result.ok:
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

    values = result.values

    def _prefix(oid_a: str, oid_b: str) -> bool:
        """oid_a 是 oid_b 的后代（或相等）。"""
        a = oid_a.strip(".").split(".")
        b = oid_b.strip(".").split(".")
        return a[: len(b)] == b

    items = []
    supported = 0
    for n, base in zip(nodes, query_oids):
        # GETNEXT 响应里找到「>= base 的第一个 OID」
        # next_cmd 已按请求顺序返回，但响应 dict 无序——遍历找前缀匹配
        hit_oid = None
        hit_val = None
        for resp_oid, val in values.items():
            if _prefix(resp_oid, base):
                # 取最靠近 base 的（最深前缀）
                if hit_oid is None or resp_oid < hit_oid:
                    hit_oid, hit_val = resp_oid, val
        is_supported = hit_oid is not None and not str(hit_val).startswith("No Such")
        if is_supported:
            supported += 1
        items.append(
            {
                **n,
                "query_oid": base,
                "supported": is_supported,
                # 命中的实例 OID（比定义多出的部分就是索引/.0）
                "hit_oid": hit_oid,
                "value": hit_val if is_supported else None,
            }
        )

    return {
        "total": len(nodes),
        "supported": supported,
        "items": items,
        "device": {"id": device.id, "name": device.name, "ip": device.ip},
    }
