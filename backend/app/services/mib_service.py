"""MIB 文件管理与 OID 索引服务。

为什么自己解析而不用 pysmi 编译
--------------------------------
pysmi 编译 MIB 需要联网下载 SNMPv2-SMI / SNMPv2-TC 等标准 MIB
（企业内网常常不通），且 pysmi 2.x 的 API 仍在变动。
而用户上传的厂商 MIB 通常是**自包含**的——只 IMPORTS 标准定义，
自己解析即可得到「名称 ↔ OID」映射。

因此这里实现一个轻量的 ASN.1 OID 提取器：
1. 扫描文件里所有 ``::= { parent index }`` 形式的赋值；
2. 向上回溯找出该定义的名字与类型；
3. 递归展开 parent 引用（名字或数字），得到完整数字 OID。

标准根 OID（enterprises / private / mgmt / mib-2 等）内置一份，
足以把绝大多数厂商 MIB 解析成完整 OID。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.session import SessionLocal
from app.models.models import MibFile, MibNode

# MIB 存储目录（原始文件 + 解析结果）
# app/services/mib_service.py -> app/services -> app -> backend
MIB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "mibs",
)

# 内置的标准根 OID —— 覆盖绝大多数厂商 MIB 的 IMPORTS
BASE_OIDS: Dict[str, Tuple[int, ...]] = {
    "iso": (1,),
    "ccitt": (0,),
    "joint-iso-ccitt": (2,),
    "identified-organization": (3,),
    "internet": (1, 3, 6, 1),
    "directory": (1, 3, 6, 1, 1),
    "mgmt": (1, 3, 6, 1, 2),
    "mib-2": (1, 3, 6, 1, 2, 1),
    "transmission": (1, 3, 6, 1, 2, 1, 10),
    "experimental": (1, 3, 6, 1, 3),
    "private": (1, 3, 6, 1, 4, 1, 42),
    "enterprises": (1, 3, 6, 1, 4, 1),
    "security": (1, 3, 6, 1, 5),
    "snmpV2": (1, 3, 6, 1, 6, 3),
    "snmpDomains": (1, 3, 6, 1, 6, 3, 1),
    "snmpProxys": (1, 3, 6, 1, 6, 3, 2),
    "snmpModules": (1, 3, 6, 1, 6, 3, 3),
    "zeroDotZero": (0, 0),
    "system": (1, 3, 6, 1, 2, 1, 1),
    "interfaces": (1, 3, 6, 1, 2, 1, 2),
    "at": (1, 3, 6, 1, 2, 1, 3),
    "ip": (1, 3, 6, 1, 2, 1, 4),
    "icmp": (1, 3, 6, 1, 2, 1, 5),
    "tcp": (1, 3, 6, 1, 2, 1, 6),
    "udp": (1, 3, 6, 1, 2, 1, 7),
    "snmp": (1, 3, 6, 1, 2, 1, 11),
    "host": (1, 3, 6, 1, 2, 1, 25),
    "hrDevice": (1, 3, 6, 1, 2, 1, 25, 3),
    "dot3": (1, 3, 6, 1, 2, 1, 25, 10),
    "snmpMIB": (1, 3, 6, 1, 6, 3, 1),
    "snmpMIBObjects": (1, 3, 6, 1, 6, 3, 1, 1),
    "snmpTrap": (1, 3, 6, 1, 6, 3, 1, 1),
    "snmpTrapObjects": (1, 3, 6, 1, 6, 3, 1, 1, 4),
    "ifMIB": (1, 3, 6, 1, 2, 1, 31),
    "ifMIBObjects": (1, 3, 6, 1, 2, 1, 31, 1),
    "cisco": (1, 3, 6, 1, 4, 1, 9),
    "ciscoMgmt": (1, 3, 6, 1, 4, 1, 9, 9),
    "ciscoConfigMgmt": (1, 3, 6, 1, 4, 1, 9, 9, 91),
    "ciscoProcessMIB": (1, 3, 6, 1, 4, 1, 9, 9, 109),
    "ciscoMemoryPoolMIB": (1, 3, 6, 1, 4, 1, 9, 9, 48),
    "fortinet": (1, 3, 6, 1, 4, 1, 12356),
    "fortinetMIB": (1, 3, 6, 1, 4, 1, 12356, 101),
    "huawei": (1, 3, 6, 1, 4, 1, 2011),
    "huaweiMIB": (1, 3, 6, 1, 4, 1, 2011, 2),
    "h3c": (1, 3, 6, 1, 4, 1, 25506),
    "juniper": (1, 3, 6, 1, 4, 1, 2636),
    "aruba": (1, 3, 6, 1, 4, 1, 14823),
    "mikrotik": (1, 3, 6, 1, 4, 1, 14988),
    "mikrotikMIB": (1, 3, 6, 1, 4, 1, 14988, 1),
    "ruijie": (1, 3, 6, 1, 4, 1, 25506),
}

# 定义类型（关键字 → 展示名）
TYPE_LABELS = {
    "OBJECT-TYPE": "标量",
    "OBJECT-IDENTITY": "对象",
    "OBJECT IDENTIFIER": "标识",
    "MODULE-IDENTITY": "模块",
    "OBJECT-GROUP": "组",
    "NOTIFICATION-TYPE": "通知",
    "TYPE": "类型",
    "": "定义",
}

# "名字 关键字" 的定义头
# 关键字有多种写法：OBJECT-TYPE / OBJECT-IDENTITY / OBJECT IDENTIFIER
_DEF_HEAD = re.compile(
    r"([A-Za-z][A-Za-z0-9_-]*)\s+"
    r"(OBJECT-TYPE|OBJECT-IDENTITY|OBJECT\s+IDENTIFIER|MODULE-IDENTITY|"
    r"OBJECT-GROUP|NOTIFICATION-TYPE|TRAP-TYPE|TYPE)"
)
# "::= { parent index }"
# index 可以是单个数字、多级数字（点分隔如 { fgSysMem 1.3 }）、
# 也可以是空格分隔（{ mib-2 1 3 }）——真实厂商 MIB 里三种写法都常见
_ASSIGN = re.compile(
    r"::=\s*\{\s*([A-Za-z][A-Za-z0-9_-]*|\d+)\s*([\d\s.]*)\s*\}"
)
# 描述
_DESC = re.compile(r'DESCRIPTION\s+"([^"]*)"', re.IGNORECASE | re.DOTALL)
_SYNTAX = re.compile(r"SYNTAX\s+([A-Za-z][A-Za-z0-9_-]*)", re.IGNORECASE)
_ACCESS = re.compile(r"MAX-ACCESS\s+([A-Za-z-]+)", re.IGNORECASE)
_MODULE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s+DEFINITIONS\s*::=", re.MULTILINE)


def _clean_desc(text: str) -> str:
    """把多行 DESCRIPTION 压成一行。"""
    return re.sub(r"\s+", " ", text).strip()[:500]


def _parse_index(raw: Optional[str]) -> Tuple[int, ...]:
    """解析 OID 索引。

    支持三种写法：
      ``::= { parent 3 }``          → (3,)
      ``::= { parent 1.3 }``        → (1, 3)     多级索引
      ``::= { parent 1 3 }``        → (1, 3)
    空索引按 0 处理。
    """
    if not raw:
        return (0,)
    parts = re.split(r"[.\s]+", raw.strip())
    nums: List[int] = []
    for p in parts:
        if p.isdigit():
            nums.append(int(p))
    return tuple(nums) if nums else (0,)


def parse_mib_text(text: str) -> Dict[str, Any]:
    """解析 MIB 文本，返回 {name: {...}} 的定义表。

    每项包含 oid / oid_str / type / syntax / access / description。
    """
    # 去掉注释（-- 行注释 与 /* 块注释 */），但保留字符串里的内容
    stripped = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    stripped = re.sub(r"--[^\n]*", " ", stripped)

    module_match = _MODULE.search(stripped)
    module_name = module_match.group(1) if module_match else ""

    # 收集所有赋值位置
    assignments: List[Tuple[int, str, Optional[str]]] = []
    for m in _ASSIGN.finditer(stripped):
        assignments.append((m.start(), m.group(1), m.group(2)))

    # 预扫所有定义头的位置，供二分回溯使用
    heads: List[Tuple[int, str, str]] = [
        (m.start(), m.group(1), m.group(2)) for m in _DEF_HEAD.finditer(stripped)
    ]

    defs: Dict[str, Dict[str, Any]] = {}

    for pos, parent, index in assignments:
        # 向上回溯找定义头：取 pos 之前最近的那个
        head = None
        for h in heads:
            if h[0] < pos:
                head = h
            else:
                break
        if head is None:
            continue
        name, kind = head[1], head[2]
        kind = re.sub(r"\s+", " ", kind).strip()

        block = stripped[head[0]: pos]
        desc_m = _DESC.search(block)
        syn_m = _SYNTAX.search(block)
        acc_m = _ACCESS.search(block)

        defs[name] = {
            "parent": parent,
            "index": index,
            "type": kind,
            "type_label": TYPE_LABELS.get(kind, kind),
            "syntax": syn_m.group(1) if syn_m else "",
            "access": acc_m.group(1) if acc_m else "",
            "description": _clean_desc(desc_m.group(1)) if desc_m else "",
            "module": module_name,
        }

    # 递归展开 OID
    resolved: Dict[str, Dict[str, Any]] = {}
    visiting: set = set()

    def resolve(name: str) -> Optional[Tuple[int, ...]]:
        # 文件内定义优先于内置的标准根 OID——
        # 厂商 MIB 里常与内置同名（如 fortinetMIB），
        # 若用内置值会多出一段 OID
        if name in defs:
            if name in resolved:
                return resolved[name]["oid_tuple"]
            if name in visiting:
                return None  # 循环引用
            d = defs[name]
            visiting.add(name)
            try:
                parent_oid = resolve(d["parent"]) if d["parent"] else None
                if parent_oid is None:
                    # parent 可能是纯数字（如 12345 表示 enterprises 下的私有号）
                    if d["parent"].isdigit():
                        parent_oid = BASE_OIDS["enterprises"] + (int(d["parent"]),)
                    else:
                        return None
                idx = _parse_index(d["index"])
                oid = parent_oid + idx
                resolved[name] = {**d, "oid_tuple": oid}
                return oid
            finally:
                visiting.discard(name)
        if name in BASE_OIDS:
            return BASE_OIDS[name]
        return None

    for name in list(defs.keys()):
        resolve(name)

    # 组装结果
    out: Dict[str, Dict[str, Any]] = {}
    for name, d in resolved.items():
        oid_tuple = d["oid_tuple"]
        out[name] = {
            "oid": "." + ".".join(str(x) for x in oid_tuple),
            "oid_tuple": oid_tuple,
            "type": d["type"],
            "type_label": d["type_label"],
            "syntax": d["syntax"],
            "access": d["access"],
            "description": d["description"],
            "module": d["module"],
        }
    return out


def save_mib_file(
    original_name: str,
    content: bytes,
    user: str = "",
) -> Dict[str, Any]:
    """保存并解析一个 MIB 文件，返回结果摘要。"""
    os.makedirs(MIB_DIR, exist_ok=True)

    # 文件名安全化
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", original_name or "mib.txt")
    if not safe.lower().endswith((".mib", ".txt", ".my")):
        safe += ".txt"
    path = os.path.join(MIB_DIR, safe)

    with open(path, "wb") as fh:
        fh.write(content)

    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        text = content.decode("latin-1", errors="replace")

    parsed = parse_mib_text(text)
    module_name = ""
    for d in parsed.values():
        if d.get("module"):
            module_name = d["module"]
            break

    status = "ok" if parsed else "failed"
    error = "" if parsed else "未能从文件中解析出任何 OID 定义（请确认是标准 ASN.1 MIB 文本）"

    db = SessionLocal()
    try:
        # 同名文件视为「更新」而不是新增——
        # 否则重复上传同一份 MIB 会在列表里堆出一串重复项，
        # 指标清单与探测结果也会跟着重复。
        row = (
            db.query(MibFile)
            .filter(MibFile.filename == safe)
            .order_by(MibFile.id.desc())
            .first()
        )
        replaced = row is not None
        if row is None:
            row = MibFile(
                filename=safe,
                original_name=original_name or safe,
                size=len(content),
                module_name=module_name,
                node_count=len(parsed),
                status=status,
                error=error,
                uploaded_by=user,
                uploaded_at=datetime.now(),
            )
            db.add(row)
        else:
            row.original_name = original_name or safe
            row.size = len(content)
            row.module_name = module_name
            row.node_count = len(parsed)
            row.status = status
            row.error = error
            row.uploaded_by = user
            row.uploaded_at = datetime.now()
            # 清掉旧的指标索引
            db.query(MibNode).filter(MibNode.mib_file_id == row.id).delete(
                synchronize_session=False
            )
        db.flush()
        # 写入节点索引
        for name, d in parsed.items():
            db.add(
                MibNode(
                    mib_file_id=row.id,
                    name=name,
                    oid=d["oid"],
                    oid_tuple=".".join(str(x) for x in d["oid_tuple"]),
                    type_label=d["type_label"],
                    syntax=d["syntax"],
                    access=d["access"],
                    description=d["description"],
                )
            )
        db.commit()
        return {
            "id": row.id,
            "filename": safe,
            "original_name": row.original_name,
            "size": row.size,
            "module_name": module_name,
            "node_count": len(parsed),
            "status": status,
            "error": error,
            "replaced": replaced,
            "uploaded_at": row.uploaded_at.isoformat(),
        }
    finally:
        db.close()


def delete_mib_file(mib_id: int) -> bool:
    """删除 MIB 文件及其索引。"""
    db = SessionLocal()
    try:
        row = db.get(MibFile, mib_id)
        if row is None:
            return False
        path = os.path.join(MIB_DIR, row.filename)
        db.query(MibNode).filter(MibNode.mib_file_id == mib_id).delete(
            synchronize_session=False
        )
        db.delete(row)
        db.commit()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        return True
    finally:
        db.close()


def list_mib_files() -> List[Dict[str, Any]]:
    """MIB 文件列表。"""
    db = SessionLocal()
    try:
        rows = db.query(MibFile).order_by(MibFile.uploaded_at.desc()).all()
        return [
            {
                "id": r.id,
                "filename": r.filename,
                "original_name": r.original_name,
                "size": r.size,
                "module_name": r.module_name,
                "node_count": r.node_count,
                "status": r.status,
                "error": r.error,
                "uploaded_by": r.uploaded_by,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else "",
            }
            for r in rows
        ]
    finally:
        db.close()


def search_nodes(
    keyword: str = "",
    mib_file_id: Optional[int] = None,
    limit: int = 200,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """按关键字搜索指标（名称 / OID / 描述），返回 (items, 总数)。

    总数是筛选后的完整数量（不受 limit 截断）——
    一个 MIB 可能有上千个指标，前端要靠 total 分页展示，
    否则用户会以为只导入了一部分。
    """
    db = SessionLocal()
    try:
        q = db.query(MibNode)
        if mib_file_id:
            q = q.filter(MibNode.mib_file_id == mib_file_id)
        if keyword:
            like = f"%{keyword.strip()}%"
            q = q.filter(
                (MibNode.name.like(like))
                | (MibNode.oid.like(like))
                | (MibNode.description.like(like))
            )
        total = q.count()
        rows = (
            q.order_by(MibNode.oid_tuple)
            .offset(offset)
            .limit(limit)
            .all()
        )
        names = {r.mib_file_id for r in rows}
        files = {f.id: f.original_name for f in db.query(MibFile).filter(
            MibFile.id.in_(names)
        ).all()} if names else {}
        items = [
            {
                "id": r.id,
                "name": r.name,
                "oid": r.oid,
                "type_label": r.type_label,
                "syntax": r.syntax,
                "access": r.access,
                "description": r.description,
                "mib_file_id": r.mib_file_id,
                "mib_name": files.get(r.mib_file_id, ""),
            }
            for r in rows
        ]
        return items, total
    finally:
        db.close()


def resolve_oid(oid: str) -> Optional[Dict[str, Any]]:
    """按数字 OID 反查指标名。"""
    if not oid:
        return None
    norm = oid.strip().lstrip(".")
    db = SessionLocal()
    try:
        row = (
            db.query(MibNode)
            .filter(MibNode.oid == "." + norm)
            .order_by(MibNode.id)
            .first()
        )
        if row is None:
            return None
        return {
            "name": row.name,
            "oid": row.oid,
            "type_label": row.type_label,
            "syntax": row.syntax,
            "description": row.description,
        }
    finally:
        db.close()
