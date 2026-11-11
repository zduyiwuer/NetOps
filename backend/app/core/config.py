"""全局配置：环境变量驱动，默认零外部依赖（SQLite）。"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETOPS_", env_file=".env", extra="ignore")

    app_name: str = "网络设备运维平台"
    app_version: str = "1.0.0"
    debug: bool = False

    # ---- 存储 ----
    database_url: str = "sqlite:///./netops.db"
    backup_dir: str = "./data/backups"
    report_dir: str = "./data/reports"
    import_export_dir: str = "./data/imports"

    # ---- 安全 ----
    secret_key: str = Field(default="change-me-in-production-0123456789abcdef")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    # ---- 采集调度 ----
    scheduler_enabled: bool = True
    default_poll_interval: int = 60
    max_concurrent_poll: int = 20
    snmp_timeout: float = 3.0
    snmp_retries: int = 1
    ssh_timeout: int = 20
    metric_retention_days: int = 30
    log_retention_days: int = 60
    enable_mock_collector: bool = True

    # ---- Syslog 接收 ----
    syslog_enabled: bool = True
    syslog_host: str = "0.0.0.0"
    syslog_udp_port: int = 5514
    syslog_tcp_port: int = 5514

    # ---- NetFlow / IPFIX 接收 ----
    netflow_enabled: bool = True
    netflow_udp_ports: List[int] = [2055, 4739, 9995]
    netflow_tcp_ports: List[int] = []
    netflow_retention_days: int = 7

    # ---- gNMI Telemetry ----
    telemetry_enabled: bool = True
    gnmi_listen_port: int = 57400
    telemetry_sample_interval_ms: int = 10000

    # ---- 域控 / LDAP ----
    ldap_enabled: bool = False
    ldap_server: str = ""
    ldap_port: int = 389
    ldap_use_ssl: bool = False
    ldap_start_tls: bool = False
    ldap_base_dn: str = ""
    ldap_user_dn_template: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_filter: str = "(sAMAccountName={username})"
    ldap_domain: str = ""
    ldap_group_attribute: str = "memberOf"
    ldap_default_role: str = "viewer"
    ldap_group_role_map: str = "{}"  # JSON: {"CN=admins,DC=x": "admin"}

    # ---- 告警通知 ----
    webhook_timeout: float = 8.0

    cors_origins: List[str] = ["*"]

    @property
    def ldap_group_role_map_dict(self) -> dict:
        import json

        try:
            return json.loads(self.ldap_group_role_map or "{}")
        except Exception:
            return {}

    def ensure_dirs(self) -> None:
        import os

        for path in (self.backup_dir, self.report_dir, self.import_export_dir):
            os.makedirs(path, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
