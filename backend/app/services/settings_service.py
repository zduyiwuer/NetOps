"""运行时设置服务。

平台的关键配置（域控参数、AI 接口、采集服务开关等）持久化在数据库，
修改后无需重启即可生效。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.models import Setting

logger = logging.getLogger("netops.settings")

# 允许在线修改的配置项（key → 类型）
SETTING_TYPES: Dict[str, str] = {
    # 域控
    "ldap.enabled": "bool",
    "ldap.server": "str",
    "ldap.port": "int",
    "ldap.use_ssl": "bool",
    "ldap.start_tls": "bool",
    "ldap.base_dn": "str",
    "ldap.bind_dn": "str",
    "ldap.bind_password": "str",
    "ldap.user_filter": "str",
    "ldap.user_dn_template": "str",
    "ldap.domain": "str",
    "ldap.default_role": "str",
    "ldap.group_role_map": "str",
    # AI
    "ai.enabled": "bool",
    "ai.provider": "str",
    "ai.base_url": "str",
    "ai.api_key": "str",
    "ai.model": "str",
    "ai.temperature": "float",
    "ai.max_tokens": "int",
    "ai.timeout": "int",
    "ai.context_enabled": "bool",
    "ai.system_prompt": "str",
    # 采集服务开关
    "collector.snmp_enabled": "bool",
    "collector.telemetry_enabled": "bool",
    "collector.netflow_enabled": "bool",
    "collector.syslog_enabled": "bool",
    "collector.gnmi_port": "int",
    "collector.netflow_ports": "str",
    # AAA（TACACS+/RADIUS）
    "aaa.enabled": "bool",
    "aaa.tacacs_secret": "str",
    "aaa.tacacs_port": "int",
    "aaa.radius_port": "int",
    # 界面
    "ui.default_theme": "str",
    "ui.dashboard_layout": "str",
}

# 不向接口回传的敏感项
SECRET_KEYS = {"ldap.bind_password", "ai.api_key", "aaa.tacacs_secret"}


def _cast(value: str, kind: str) -> Any:
    if kind == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    return value


def get_raw(db: Session, key: str, default: str = "") -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def get(db: Session, key: str, default: Any = None) -> Any:
    """读取单个设置项（按声明类型转换）。"""
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is None:
        return default
    kind = SETTING_TYPES.get(key, "str")
    return _cast(row.value, kind)


def set_value(db: Session, key: str, value: Any) -> None:
    """写入单个设置项。"""
    kind = SETTING_TYPES.get(key, "str")
    if kind == "bool":
        text = "true" if value else "false"
    elif kind == "str":
        text = str(value)
    else:
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = text
    else:
        db.add(Setting(key=key, value=text))
    db.commit()


def get_all(db: Session, include_secrets: bool = False) -> Dict[str, Any]:
    """读取全部设置项（数据库值 + 环境变量默认值，敏感项脱敏）。"""
    merged = effective_settings(db)
    if not include_secrets:
        for key in SECRET_KEYS:
            if key in merged:
                merged[key] = "********" if merged.get(key) else ""
    return merged


def update_many(db: Session, payload: Dict[str, Any]) -> List[str]:
    """批量更新设置项，返回实际变更的 key 列表。"""
    changed: List[str] = []
    for key, value in (payload or {}).items():
        if key not in SETTING_TYPES:
            continue
        # 掩码值表示不修改
        if key in SECRET_KEYS and value in ("", "********"):
            continue
        set_value(db, key, value)
        changed.append(key)
    return changed


def explicit_keys(db: Session) -> List[str]:
    """返回在数据库中显式设置过的配置项 key（区别于环境变量默认值）。"""
    rows = db.query(Setting).all()
    return [r.key for r in rows if r.key in SETTING_TYPES]


def effective_settings(db: Optional[Session] = None) -> Dict[str, Any]:
    """合并「数据库设置」与「环境变量默认值」，数据库优先。"""
    from app.core.config import settings as env

    own = db is None
    session = db or SessionLocal()
    try:
        merged: Dict[str, Any] = {
            "ldap.enabled": env.ldap_enabled,
            "ldap.server": env.ldap_server,
            "ldap.port": env.ldap_port,
            "ldap.use_ssl": env.ldap_use_ssl,
            "ldap.start_tls": env.ldap_start_tls,
            "ldap.base_dn": env.ldap_base_dn,
            "ldap.bind_dn": env.ldap_bind_dn,
            "ldap.bind_password": env.ldap_bind_password,
            "ldap.user_filter": env.ldap_user_filter,
            "ldap.user_dn_template": env.ldap_user_dn_template,
            "ldap.domain": env.ldap_domain,
            "ldap.default_role": env.ldap_default_role,
            "ldap.group_role_map": env.ldap_group_role_map,
            "ai.enabled": False,
            "ai.provider": "openai",
            "ai.base_url": "",
            "ai.api_key": "",
            "ai.model": "gpt-4o-mini",
            "ai.temperature": 0.3,
            "ai.max_tokens": 2048,
            "ai.timeout": 60,
            "ai.context_enabled": True,
            "ai.system_prompt": "",
            "collector.snmp_enabled": env.scheduler_enabled,
            "collector.telemetry_enabled": env.telemetry_enabled,
            "collector.netflow_enabled": env.netflow_enabled,
            "collector.syslog_enabled": env.syslog_enabled,
            "collector.gnmi_port": env.gnmi_listen_port,
            "collector.netflow_ports": ",".join(str(p) for p in env.netflow_udp_ports),
            "aaa.enabled": False,
            "aaa.tacacs_secret": "",
            "aaa.tacacs_port": 49,
            "aaa.radius_port": 1812,
            "ui.default_theme": "system",
            "ui.dashboard_layout": "",
        }
        for key in SETTING_TYPES:
            row = session.query(Setting).filter(Setting.key == key).first()
            if row is None:
                continue
            merged[key] = _cast(row.value, SETTING_TYPES.get(key, "str"))
        return merged
    finally:
        if own:
            session.close()
