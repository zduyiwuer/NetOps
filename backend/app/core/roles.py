"""平台角色定义与多角色判定。

平台支持 4 种角色，一个用户可同时拥有多个角色：

============  ==========  ================================================
角色          层级        能力
============  ==========  ================================================
``admin``     3           全部权限，含审批权
``operator``  2           查看 + 设备增删改、采集、备份、巡检、提交变更申请
``approver``  2           查看 + 审批配置变更（**不含**设备操作权）
``viewer``    1           仅查看
============  ==========  ================================================

设计要点：审批权是**独立的能力维度**，不是层级链上的一环。

- 纯 ``approver`` 不会因为"层级 2"而获得设备增删改权限（那属于 operator）；
- ``admin`` 天然含审批权，因此 admin 依旧可以审批；
- 一个人同时是 ``operator`` + ``approver`` 时，既能提交变更申请，也能审批他人申请。

数据兼容：``User.role`` 保留为主角色字段（等于 roles 中权限最高者），
``User.roles`` 存全部角色。历史数据 ``roles`` 为空时按 ``[role]`` 回退。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

# 角色层级：数值越大权限越高。approver 与 operator 同级，
# 因为「审批」和「设备操作」是两种不同性质的能力，不能互相覆盖。
ROLE_LEVEL: Dict[str, int] = {
    "viewer": 1,
    "approver": 2,
    "operator": 2,
    "admin": 3,
}

# 全部合法角色
ALL_ROLES: List[str] = ["admin", "operator", "approver", "viewer"]

# 角色中文名与说明（前端下拉与展示用）
ROLE_META: Dict[str, Dict[str, str]] = {
    "admin": {"label": "管理员", "desc": "全部权限，含审批权"},
    "operator": {"label": "操作员", "desc": "设备增删改、执行采集、备份、巡检、提交变更申请"},
    "approver": {"label": "审批员", "desc": "审批配置变更申请，不含设备操作权"},
    "viewer": {"label": "只读", "desc": "仅查看，不可修改任何配置"},
}

# 拥有审批权的角色
APPROVER_ROLES: List[str] = ["admin", "approver"]

# 默认角色
DEFAULT_ROLE = "viewer"


def normalize_roles(roles: Optional[Iterable[str]], primary: Optional[str] = None) -> List[str]:
    """规范化角色集合。

    - 过滤非法角色
    - 去重并保持稳定顺序（admin > operator > approver > viewer）
    - 若给了主角色且合法，保证它一定包含在结果中

    返回空列表时由调用方决定兜底策略。
    """
    seen = {r for r in (roles or []) if isinstance(r, str) and r in ROLE_LEVEL}
    if primary and primary in ROLE_LEVEL:
        seen.add(primary)
    if not seen:
        return []
    # 按固定优先级排序，保证输出稳定
    return sorted(seen, key=lambda r: (-ROLE_LEVEL.get(r, 0), ALL_ROLES.index(r)))


def primary_role(roles: Optional[Iterable[str]]) -> str:
    """取角色集合中权限最高者，作为主角色。"""
    normalized = normalize_roles(roles)
    return normalized[0] if normalized else DEFAULT_ROLE


def parse_roles(raw: Any) -> List[str]:
    """解析存储层的 roles 字段。

    兼容三种形态：JSON 数组字符串、逗号分隔字符串、已是 list。
    解析失败一律回退为空列表，由上层按主角色兜底。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return normalize_roles([str(r) for r in raw])
    text = str(raw).strip()
    if not text or text == "[]":
        return []
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # 兼容逗号分隔写法
        return normalize_roles([p.strip() for p in text.split(",") if p.strip()])
    if isinstance(data, list):
        return normalize_roles([str(r) for r in data])
    if isinstance(data, str):
        return normalize_roles([data])
    return []


def dump_roles(roles: Optional[Iterable[str]]) -> str:
    """把角色集合序列化为 JSON 字符串，供数据库存储。"""
    return json.dumps(normalize_roles(roles), ensure_ascii=False)


def effective_roles(user: Any) -> List[str]:
    """取用户的实际角色集合。

    优先读 ``roles``；为空（历史数据）时回退到 ``role`` 单值。
    """
    normalized = parse_roles(getattr(user, "roles", None))
    if normalized:
        return normalized
    single = getattr(user, "role", None)
    return normalize_roles([single]) if single else [DEFAULT_ROLE]


def user_level(user: Any) -> int:
    """用户在所有角色中的最高层级。"""
    return max((ROLE_LEVEL.get(r, 0) for r in effective_roles(user)), default=0)


def has_role(user: Any, role: str) -> bool:
    """用户是否拥有指定角色。"""
    return role in effective_roles(user)


def can_approve(user: Any) -> bool:
    """用户是否具备审批权（approver 或 admin）。"""
    return any(r in APPROVER_ROLES for r in effective_roles(user))


def can_operate(user: Any) -> bool:
    """用户是否具备设备操作权（operator 或 admin）。"""
    return any(r in ("operator", "admin") for r in effective_roles(user))


def role_descriptions() -> List[Dict[str, Any]]:
    """返回角色清单，供前端渲染下拉选项。"""
    return [
        {
            "key": key,
            "label": meta["label"],
            "desc": meta["desc"],
            "level": ROLE_LEVEL[key],
        }
        for key, meta in ROLE_META.items()
    ]


def describe_roles(roles: Optional[Iterable[str]]) -> str:
    """把角色集合渲染为中文描述，如「操作员、审批员」。"""
    normalized = normalize_roles(roles)
    if not normalized:
        return ROLE_META[DEFAULT_ROLE]["label"]
    return "、".join(ROLE_META.get(r, {}).get("label", r) for r in normalized)


__all__ = [
    "ROLE_LEVEL",
    "ALL_ROLES",
    "ROLE_META",
    "APPROVER_ROLES",
    "DEFAULT_ROLE",
    "normalize_roles",
    "primary_role",
    "parse_roles",
    "dump_roles",
    "effective_roles",
    "user_level",
    "has_role",
    "can_approve",
    "can_operate",
    "role_descriptions",
    "describe_roles",
]
