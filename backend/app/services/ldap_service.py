"""LDAP / Active Directory 域控对接。

支持：
1. 域控认证（用户可用 sAMAccountName / userPrincipalName 登录）
2. 用户属性同步（姓名、邮箱、电话、部门）
3. AD 组 → 平台角色映射（memberOf）
4. 用户同步（列出 AD 用户批量导入平台）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import User

logger = logging.getLogger("netops.ldap")


class LdapError(Exception):
    """LDAP 操作失败。"""


def _runtime() -> dict:
    """读取运行时生效的 LDAP 配置（数据库优先，环境变量兜底）。"""
    from app.services.settings_service import effective_settings

    return effective_settings()


def _server():
    from ldap3 import Server, Tls

    cfg = _runtime()
    tls = None
    if cfg.get("ldap.use_ssl") or cfg.get("ldap.start_tls"):
        import ssl

        tls = Tls(validate=ssl.CERT_NONE)
    return Server(
        cfg.get("ldap.server") or "",
        port=int(cfg.get("ldap.port") or 389),
        use_ssl=bool(cfg.get("ldap.use_ssl")),
        tls=tls,
    )


def _user_dn(username: str, conn=None) -> Optional[str]:
    """定位用户 DN：优先按模板，其次按过滤器搜索。"""
    cfg = _runtime()
    template = (cfg.get("ldap.user_dn_template") or "").strip()
    if template:
        return template.replace("{username}", username).replace("{user}", username)

    if not cfg.get("ldap.base_dn"):
        return None
    try:
        from ldap3 import Connection

        conn = Connection(
            _server(),
            user=cfg.get("ldap.bind_dn") or None,
            password=cfg.get("ldap.bind_password") or None,
            auto_bind=True,
            raise_exceptions=False,
        )
        flt = (cfg.get("ldap.user_filter") or "(sAMAccountName={username})").format(username=username)
        conn.search(cfg.get("ldap.base_dn"), flt, attributes=["distinguishedName"])
        if conn.entries:
            return str(conn.entries[0].entry_dn)
        conn.unbind()
    except Exception as exc:
        logger.warning("LDAP 用户搜索失败: %s", exc)
    return None


def _map_roles_from_groups(groups: List[str]) -> List[str]:
    """按 AD 组映射平台角色，返回角色列表。

    ``ldap.group_role_map`` 的值支持两种写法：
    - 字符串：``{"网络运维组": "operator"}``
    - 数组：``{"网络运维组": ["operator", "approver"]}``

    命中多个组时**累加**所有角色（去重），因此一个域账号可同时
    具备操作员与审批员等多个角色。
    """
    from app.core.roles import normalize_roles

    cfg = _runtime()
    try:
        mapping = json.loads(cfg.get("ldap.group_role_map") or "{}")
    except Exception:
        mapping = {}

    matched: List[str] = []
    for group in groups or []:
        lowered = group.lower()
        for key, value in mapping.items():
            if key.lower() not in lowered:
                continue
            if isinstance(value, (list, tuple)):
                matched.extend(str(v) for v in value)
            else:
                matched.append(str(value))

    roles = normalize_roles(matched)
    if roles:
        return roles
    default_role = cfg.get("ldap.default_role") or "viewer"
    return normalize_roles([default_role])


def _map_role_from_groups(groups: List[str]) -> str:
    """按 AD 组映射平台主角色（取权限最高者）。"""
    from app.core.roles import primary_role

    return primary_role(_map_roles_from_groups(groups))


def _extract_groups(entry) -> List[str]:
    raw = getattr(entry, "memberOf", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    try:
        return [str(g) for g in raw]
    except TypeError:
        return [str(raw)]


def _attr(entry, name: str) -> str:
    try:
        value = getattr(entry, name, None)
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value)
    except Exception:
        return ""


def test_connection() -> Dict[str, Any]:
    """测试域控连通性。"""
    cfg = _runtime()
    if not cfg.get("ldap.enabled"):
        return {"ok": False, "message": "域控对接未启用"}
    if not cfg.get("ldap.server"):
        return {"ok": False, "message": "未配置域控服务器地址"}
    try:
        from ldap3 import Connection

        conn = Connection(
            _server(),
            user=cfg.get("ldap.bind_dn") or None,
            password=cfg.get("ldap.bind_password") or None,
            auto_bind=True,
            raise_exceptions=True,
            receive_timeout=8,
        )
        who = conn.info.server or ""
        conn.unbind()
        return {
            "ok": True,
            "message": f"连接成功：{cfg.get('ldap.server')}:{cfg.get('ldap.port')}",
            "server": who,
        }
    except Exception as exc:
        return {"ok": False, "message": f"连接失败：{exc}"}


def authenticate(username: str, password: str) -> Dict[str, Any]:
    """域控认证。成功返回用户属性字典，失败返回 {"ok": False, ...}。"""
    cfg = _runtime()
    if not cfg.get("ldap.enabled"):
        return {"ok": False, "message": "域控登录未启用"}
    if not password:
        return {"ok": False, "message": "密码不能为空"}

    try:
        from ldap3 import Connection

        dn = _user_dn(username)
        if not dn:
            return {"ok": False, "message": f"未找到域账号 {username}"}

        conn = Connection(
            _server(),
            user=dn,
            password=password,
            auto_bind=True,
            raise_exceptions=True,
            receive_timeout=10,
        )
        attributes = ["cn", "displayName", "mail", "sAMAccountName", "userPrincipalName",
                      "department", "title", "memberOf", "telephoneNumber"]
        conn.search(dn, "(objectClass=*)", attributes=attributes, search_scope="BASE")
        entry = conn.entries[0] if conn.entries else None
        conn.unbind()

        groups = _extract_groups(entry) if entry is not None else []
        return {
            "ok": True,
            "dn": dn,
            "username": username,
            "display_name": _attr(entry, "displayName") or _attr(entry, "cn") or username,
            "email": _attr(entry, "mail"),
            "phone": _attr(entry, "telephoneNumber"),
            "department": _attr(entry, "department"),
            "groups": groups,
            "role": _map_role_from_groups(groups),
            "roles": _map_roles_from_groups(groups),
        }
    except Exception as exc:
        return {"ok": False, "message": f"域认证失败：{exc}"}


def sync_user(db: Session, profile: Dict[str, Any]) -> User:
    """把域用户同步到平台（不存在则创建）。"""
    username = profile["username"]
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        user = User(username=username, source="ldap")
        db.add(user)
    user.display_name = profile.get("display_name") or username
    user.email = profile.get("email") or ""
    user.phone = profile.get("phone") or ""
    user.department = profile.get("department") or ""
    user.dn = profile.get("dn") or ""
    # 域用户角色：优先用多角色映射，历史 profile 无 roles 时按单角色回退
    from app.core.roles import dump_roles, effective_roles, normalize_roles, primary_role

    mapped = profile.get("roles")
    if mapped:
        roles = normalize_roles(mapped)
    else:
        roles = normalize_roles([profile.get("role") or _runtime().get("ldap.default_role") or "viewer"])
    user.role = primary_role(roles)
    user.roles = dump_roles(roles)
    user.source = "ldap"
    user.is_active = True
    user.last_sync_at = datetime.now()
    db.commit()
    return user


def list_domain_users(limit: int = 100) -> Dict[str, Any]:
    """列出域用户（用于批量同步）。"""
    cfg = _runtime()
    if not cfg.get("ldap.enabled") or not cfg.get("ldap.base_dn"):
        return {"ok": False, "message": "域控未启用或未配置 Base DN", "users": []}
    try:
        from ldap3 import Connection

        conn = Connection(
            _server(),
            user=cfg.get("ldap.bind_dn") or None,
            password=cfg.get("ldap.bind_password") or None,
            auto_bind=True,
            raise_exceptions=True,
            receive_timeout=15,
        )
        flt = "(&(objectClass=user)(objectCategory=person)(sAMAccountName=*))"
        conn.search(
            cfg.get("ldap.base_dn"),
            flt,
            attributes=["cn", "displayName", "mail", "sAMAccountName", "department", "memberOf"],
            paged_size=500,
            size_limit=limit,
        )
        users: List[Dict[str, Any]] = []
        for entry in conn.entries:
            username = _attr(entry, "sAMAccountName")
            if not username:
                continue
            groups = _extract_groups(entry)
            users.append({
                "username": username,
                "display_name": _attr(entry, "displayName") or _attr(entry, "cn") or username,
                "email": _attr(entry, "mail"),
                "department": _attr(entry, "department"),
                "groups": groups,
                "role": _map_role_from_groups(groups),
                "roles": _map_roles_from_groups(groups),
            })
        conn.unbind()
        return {"ok": True, "message": f"获取到 {len(users)} 个域用户", "users": users}
    except Exception as exc:
        return {"ok": False, "message": f"获取域用户失败：{exc}", "users": []}


def sync_all_users(db: Session) -> Dict[str, Any]:
    """批量同步域用户到平台。"""
    result = list_domain_users(limit=500)
    if not result.get("ok"):
        return result
    created = 0
    updated = 0
    for item in result["users"]:
        exists = db.query(User).filter(User.username == item["username"]).first()
        sync_user(db, {**item, "dn": ""})
        if exists:
            updated += 1
        else:
            created += 1
    return {"ok": True, "message": f"同步完成：新增 {created}，更新 {updated}", "created": created, "updated": updated}
