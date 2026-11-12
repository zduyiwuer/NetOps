"""数据模型：门店、设备、资产、指标、告警、日志、备份、巡检、用户。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.core.crypto import EncryptedString


def _now() -> datetime:
    return datetime.now()


# ---------------------------------------------------------------- 门店 / 组织
class Store(Base):
    """门店/网点，用于设备归类与分域统计。"""

    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(String(64), default="")
    address: Mapped[str] = mapped_column(String(255), default="")
    contact: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    manager: Mapped[str] = mapped_column(String(64), default="")
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    devices: Mapped[list["Device"]] = relationship(back_populates="store")


# ---------------------------------------------------------------- 设备
class Device(Base):
    """网络设备（RouterOS / Cisco / 华为 / H3C 等），同时承载资产属性。"""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str] = mapped_column(String(64), index=True)
    vendor: Mapped[str] = mapped_column(String(32), index=True)  # routeros/cisco/huawei/h3c
    model: Mapped[str] = mapped_column(String(64), default="")
    serial_number: Mapped[str] = mapped_column(String(64), default="", index=True)
    mac: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(32), default="switch")  # switch/router/firewall/ap
    location: Mapped[str] = mapped_column(String(128), default="")
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="SET NULL"), nullable=True, index=True)

    # 连接参数
    snmp_version: Mapped[str] = mapped_column(String(8), default="2c")  # 1/v2c/v3
    snmp_community: Mapped[str] = mapped_column(EncryptedString, default="public")
    snmp_port: Mapped[int] = mapped_column(Integer, default=161)
    snmp_v3_user: Mapped[str] = mapped_column(String(64), default="")
    snmp_v3_auth_proto: Mapped[str] = mapped_column(String(32), default="SHA")
    snmp_v3_auth_key: Mapped[str] = mapped_column(EncryptedString, default="")
    snmp_v3_priv_proto: Mapped[str] = mapped_column(String(32), default="AES")
    snmp_v3_priv_key: Mapped[str] = mapped_column(EncryptedString, default="")

    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_username: Mapped[str] = mapped_column(String(64), default="")
    ssh_password: Mapped[str] = mapped_column(EncryptedString, default="")
    ssh_enable_password: Mapped[str] = mapped_column(EncryptedString, default="")
    ssh_timeout: Mapped[int] = mapped_column(Integer, default=20)

    # 监控策略
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval: Mapped[int] = mapped_column(Integer, default=60)
    collect_interface: Mapped[bool] = mapped_column(Boolean, default=True)
    collect_system: Mapped[bool] = mapped_column(Boolean, default=True)
    collect_log: Mapped[bool] = mapped_column(Boolean, default=True)
    collect_backup: Mapped[bool] = mapped_column(Boolean, default=True)
    mock_mode: Mapped[bool] = mapped_column(Boolean, default=False)  # 演示/离线模式
    # 采集通道：SNMP 轮询 / Telemetry 流式 / NetFlow 流量导出
    use_snmp: Mapped[bool] = mapped_column(Boolean, default=True)
    use_telemetry: Mapped[bool] = mapped_column(Boolean, default=False)
    use_netflow: Mapped[bool] = mapped_column(Boolean, default=False)

    # 资产属性
    asset_tag: Mapped[str] = mapped_column(String(64), default="", index=True)
    firmware_version: Mapped[str] = mapped_column(String(64), default="")
    purchase_date: Mapped[str] = mapped_column(String(32), default="")
    warranty_end: Mapped[str] = mapped_column(String(32), default="")
    vendor_contact: Mapped[str] = mapped_column(String(64), default="")
    contract_no: Mapped[str] = mapped_column(String(64), default="")
    price: Mapped[float] = mapped_column(Float, default=0.0)
    remark: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(16), default="unknown", index=True)  # online/offline/unknown
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    store: Mapped[Store | None] = relationship(back_populates="devices")


# ---------------------------------------------------------------- 指标
class DeviceMetric(Base):
    """设备级时序指标：CPU / 内存 / 温度 / 可用性。"""

    __tablename__ = "device_metrics"
    __table_args__ = (Index("ix_dm_device_time", "device_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    cpu_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_total_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_used_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    uptime_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    reachability: Mapped[bool] = mapped_column(Boolean, default=True)
    # 厂商特有指标（连接数 / AP 数 / 会话数等），JSON 存储
    extra_metrics: Mapped[str] = mapped_column(Text, default="{}")


class InterfaceMetric(Base):
    """接口级时序指标：速率、流量、错包、状态。"""

    __tablename__ = "interface_metrics"
    __table_args__ = (Index("ix_im_device_ts", "device_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    if_index: Mapped[int] = mapped_column(Integer)
    if_name: Mapped[str] = mapped_column(String(128), default="")
    if_descr: Mapped[str] = mapped_column(String(255), default="")
    if_alias: Mapped[str] = mapped_column(String(255), default="")
    if_type: Mapped[int] = mapped_column(Integer, default=0)
    speed_bps: Mapped[int] = mapped_column(Integer, default=0)  # 协商速率
    admin_status: Mapped[int] = mapped_column(Integer, default=0)
    oper_status: Mapped[int] = mapped_column(Integer, default=0)
    in_bps: Mapped[float] = mapped_column(Float, default=0.0)
    out_bps: Mapped[float] = mapped_column(Float, default=0.0)
    in_octets: Mapped[int] = mapped_column(Integer, default=0)
    out_octets: Mapped[int] = mapped_column(Integer, default=0)
    in_errors: Mapped[int] = mapped_column(Integer, default=0)
    out_errors: Mapped[int] = mapped_column(Integer, default=0)
    utilization: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ---------------------------------------------------------------- 告警
class AlertRule(Base):
    """告警规则：阈值型（CPU/内存/流量/丢包/可用性）。"""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), nullable=True)
    metric: Mapped[str] = mapped_column(String(32))  # cpu/mem/traffic/util/loss/offline/interface_down
    operator: Mapped[str] = mapped_column(String(8), default=">")  # > < >= <= ==
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)  # 持续满足多少秒才触发
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # critical/warning/info
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_channels: Mapped[str] = mapped_column(String(255), default="")  # 逗号分隔 channel 名
    silence_minutes: Mapped[int] = mapped_column(Integer, default=10)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AlertEvent(Base):
    """告警事件。"""

    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_ae_device_time", "device_id", "fired_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    store_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="")
    # 结构化明细（JSON）：接口名、各接口数值、排序等，便于前端展开查看
    detail: Mapped[str] = mapped_column(Text, default="{}")
    metric: Mapped[str] = mapped_column(String(32), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning", index=True)
    status: Mapped[str] = mapped_column(String(16), default="firing", index=True)  # firing/resolved/acked
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    ack_by: Mapped[str] = mapped_column(String(64), default="")
    ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------- 日志
class DeviceLog(Base):
    """交换机日志（syslog 接收 + SSH 主动采集）。"""

    __tablename__ = "device_logs"
    __table_args__ = (Index("ix_dl_device_time", "device_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    source_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    facility: Mapped[str] = mapped_column(String(32), default="")
    severity: Mapped[str] = mapped_column(String(16), default="info", index=True)  # emerg..debug
    priority: Mapped[int] = mapped_column(Integer, default=0)
    facility_code: Mapped[int] = mapped_column(Integer, default=0)
    severity_code: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[str] = mapped_column(Text, default="")
    protocol: Mapped[str] = mapped_column(String(16), default="syslog")  # syslog/ssh
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 备份
class ConfigBackup(Base):
    """设备配置备份记录。"""

    __tablename__ = "config_backups"
    __table_args__ = (Index("ix_cb_device_time", "device_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    md5: Mapped[str] = mapped_column(String(64), default="")
    method: Mapped[str] = mapped_column(String(16), default="ssh")  # ssh/snmp
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual")  # manual/scheduled
    status: Mapped[str] = mapped_column(String(16), default="success", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ---------------------------------------------------------------- 巡检
class InspectionTemplate(Base):
    """基线巡检模板（检查项定义）。"""

    __tablename__ = "inspection_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    vendor: Mapped[str] = mapped_column(String(32), default="")  # 空=通用
    description: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class InspectionTask(Base):
    """巡检任务与结果。"""

    __tablename__ = "inspection_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("inspection_templates.id", ondelete="SET NULL"), nullable=True)
    scope_type: Mapped[str] = mapped_column(String(16), default="all")  # all/store/device
    scope_value: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/running/success/failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    warn: Mapped[int] = mapped_column(Integer, default=0)
    operator: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(Text, default="[]")  # JSON 详情
    remark: Mapped[str] = mapped_column(Text, default="")  # 失败原因等说明
    report_path: Mapped[str] = mapped_column(String(512), default="")
    report_file: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ---------------------------------------------------------------- 用户
class User(Base):
    """平台用户（本地库 / 域控同步）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    # 主角色（向后兼容字段，等于 roles 中权限最高者）
    role: Mapped[str] = mapped_column(String(16), default="viewer", index=True)  # admin/operator/approver/viewer
    # 全部角色（JSON 数组）：支持一个用户同时具备多个角色，如 operator + approver
    roles: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    source: Mapped[str] = mapped_column(String(16), default="local", index=True)  # local/ldap
    dn: Mapped[str] = mapped_column(String(512), default="")
    department: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class LoginLog(Base):
    """登录审计。"""

    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16), default="local")
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    message: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class AuditLog(Base):
    """操作审计。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class SystemLog(Base):
    """平台自身的运行日志（系统级问题）。

    与设备日志（device_logs，设备侧产生的 syslog / SSH 日志）区分开：
    这里记录的是**平台自身**的问题，例如
    - 采集器因端口被占用 / 被系统排除端口范围而无法启动
    - 调度任务执行异常
    - 数据库、存储、配置等系统级故障

    目的：让「某个功能开不起来」这类问题在界面上直接可查，
    而不必去翻服务器上的日志文件。
    """

    __tablename__ = "system_logs"
    __table_args__ = (Index("ix_sl_level_time", "level", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    # info / warning / error / critical
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    # collector / port / scheduler / storage / database / network / security / system
    category: Mapped[str] = mapped_column(String(32), default="system", index=True)
    # 来源模块，如 gnmi_collector / netflow_collector / scheduler
    source: Mapped[str] = mapped_column(String(64), default="", index=True)
    # 一句话摘要
    message: Mapped[str] = mapped_column(Text, default="")
    # 详细错误信息（异常堆栈、原始报错等）
    detail: Mapped[str] = mapped_column(Text, default="")
    # 关联设备（可选）
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 附加上下文（端口号、配置项等），JSON 字符串
    context: Mapped[str] = mapped_column(Text, default="{}")


class MibFile(Base):
    """用户上传的 MIB 文件。"""

    __tablename__ = "mib_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 落盘文件名（已安全化）
    filename: Mapped[str] = mapped_column(String(200), default="")
    # 用户上传时的原始文件名
    original_name: Mapped[str] = mapped_column(String(200), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    # MIB 模块名（如 FORTINET-MIB）
    module_name: Mapped[str] = mapped_column(String(120), default="", index=True)
    # 解析出的指标数量
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    # ok / failed
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(64), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class MibNode(Base):
    """MIB 文件里解析出的一个指标定义。"""

    __tablename__ = "mib_nodes"
    __table_args__ = (Index("ix_mn_name", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mib_file_id: Mapped[int] = mapped_column(
        ForeignKey("mib_files.id", ondelete="CASCADE"), index=True
    )
    # 指标名，如 ciscoASAConnCount
    name: Mapped[str] = mapped_column(String(200), default="", index=True)
    # 数字 OID，如 1.3.6.1.4.1.9.9.166.1.1.1.1.0
    oid: Mapped[str] = mapped_column(String(200), default="", index=True)
    # 去掉点的 OID，便于按字典序排序（父节点在前）
    oid_tuple: Mapped[str] = mapped_column(String(200), default="")
    # 标量 / 组 / 通知
    type_label: Mapped[str] = mapped_column(String(32), default="")
    syntax: Mapped[str] = mapped_column(String(120), default="")
    access: Mapped[str] = mapped_column(String(32), default="")
    description: Mapped[str] = mapped_column(Text, default="")


class NotificationChannel(Base):
    """通知渠道：webhook / 邮件 / 企业微信 / 钉钉。"""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(24), default="webhook")  # webhook/wecom/dingtalk/email
    target: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_severity: Mapped[str] = mapped_column(String(16), default="warning")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Setting(Base):
    """键值型系统设置。"""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


# ================================================================ 拓扑
class TopologyNeighbor(Base):
    """LLDP / CDP 邻居关系（拓扑数据）。"""

    __tablename__ = "topology_neighbors"
    __table_args__ = (
        Index("ix_tn_local", "local_device_id", "local_interface"),
        UniqueConstraint(
            "local_device_id", "local_interface", "remote_device", "remote_interface",
            name="uq_topology_neighbor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    local_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    local_device_name: Mapped[str] = mapped_column(String(128), default="")
    local_device_ip: Mapped[str] = mapped_column(String(64), default="")
    local_interface: Mapped[str] = mapped_column(String(128), default="")
    remote_device: Mapped[str] = mapped_column(String(128), default="", index=True)
    remote_ip: Mapped[str] = mapped_column(String(64), default="")
    remote_interface: Mapped[str] = mapped_column(String(128), default="")
    platform: Mapped[str] = mapped_column(String(255), default="")
    protocol: Mapped[str] = mapped_column(String(16), default="lldp")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ================================================================ AI 对话
class AiConversation(Base):
    """AI 对话会话。"""

    __tablename__ = "ai_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    context_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AiMessage(Base):
    """AI 对话消息。"""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / assistant / system
    content: Mapped[str] = mapped_column(Text, default="")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ================================================================ 定时任务配置
class ScheduledJob(Base):
    """定时任务配置（可在 Web 界面调整调度策略）。"""

    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="采集")
    # 调度表达式：cron（5 段）或 interval（秒）。二者留空则用默认值
    cron: Mapped[str] = mapped_column(String(64), default="")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 运行统计
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="")
    last_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ================================================================ 设备接入与配置审批
class DeviceAccount(Base):
    """设备本地登录账号（用于平台代登录设备 / 设备侧本地认证）。"""

    __tablename__ = "device_accounts"
    __table_args__ = (UniqueConstraint("device_id", "username", name="uq_device_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    password: Mapped[str] = mapped_column(EncryptedString, default="")
    enable_password: Mapped[str] = mapped_column(EncryptedString, default="")
    privilege: Mapped[int] = mapped_column(Integer, default=15)  # Cisco 权限位，15=enable
    auth_type: Mapped[str] = mapped_column(String(16), default="local")  # local/tacacs/radius
    remark: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConfigChangeRequest(Base):
    """设备配置变更申请（需审批后方可下发）。"""

    __tablename__ = "config_change_requests"
    __table_args__ = (Index("ix_ccr_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(255))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device_name: Mapped[str] = mapped_column(String(128), default="")
    device_ip: Mapped[str] = mapped_column(String(64), default="")
    # 变更类型
    change_type: Mapped[str] = mapped_column(String(32), default="config")  # config/rollback/reboot/upgrade
    description: Mapped[str] = mapped_column(Text, default="")
    # 待下发的配置内容（config 类型）
    config_content: Mapped[str] = mapped_column(Text, default="")
    # 目标配置版本（rollback 类型：回滚到某个备份 id）
    target_backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 状态流转
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # pending → approved → rejected / applied / failed
    applicant: Mapped[str] = mapped_column(String(64), default="", index=True)
    reviewer: Mapped[str] = mapped_column(String(64), default="")
    review_comment: Mapped[str] = mapped_column(Text, default="")
    applied_result: Mapped[str] = mapped_column(Text, default="")
    backup_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 下发前自动备份
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AaaSession(Base):
    """TACACS+/RADIUS 认证会话日志（设备侧登录平台时记录）。"""

    __tablename__ = "aaa_sessions"
    __table_args__ = (Index("ix_aaa_time", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    protocol: Mapped[str] = mapped_column(String(16), default="tacacs")  # tacacs/radius
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    nas_ip: Mapped[str] = mapped_column(String(64), default="", index=True)  # 设备（NAS）IP
    nas_port: Mapped[int] = mapped_column(Integer, default=0)
    service: Mapped[str] = mapped_column(String(32), default="")  # shell/enable/live
    result: Mapped[str] = mapped_column(String(16), default="fail", index=True)  # pass/fail
    reason: Mapped[str] = mapped_column(String(255), default="")
    privilege: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


# ================================================================ NetFlow / IPFIX
class FlowRecord(Base):
    """NetFlow / IPFIX 流记录（单条会话）。"""

    __tablename__ = "flow_records"
    __table_args__ = (
        Index("ix_fr_device_ts", "device_id", "ts"),
        Index("ix_fr_src_ts", "src_ip", "ts"),
        Index("ix_fr_dst_ts", "dst_ip", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    exporter_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    version: Mapped[int] = mapped_column(Integer, default=9)  # 5/9/10
    template_id: Mapped[int] = mapped_column(Integer, default=0)
    observation_domain: Mapped[int] = mapped_column(Integer, default=0)

    src_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    dst_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    src_port: Mapped[int] = mapped_column(Integer, default=0)
    dst_port: Mapped[int] = mapped_column(Integer, default=0)
    protocol: Mapped[int] = mapped_column(Integer, default=0, index=True)
    application: Mapped[str] = mapped_column(String(64), default="", index=True)

    bytes_total: Mapped[int] = mapped_column(Integer, default=0)
    packets_total: Mapped[int] = mapped_column(Integer, default=0)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    tcp_flags: Mapped[int] = mapped_column(Integer, default=0)
    dscp: Mapped[int] = mapped_column(Integer, default=0)
    src_as: Mapped[int] = mapped_column(Integer, default=0)
    dst_as: Mapped[int] = mapped_column(Integer, default=0)
    src_mac: Mapped[str] = mapped_column(String(32), default="")
    dst_mac: Mapped[str] = mapped_column(String(32), default="")
    ingress_interface: Mapped[int] = mapped_column(Integer, default=0)
    egress_interface: Mapped[int] = mapped_column(Integer, default=0)
    vlan_id: Mapped[int] = mapped_column(Integer, default=0)
    flow_direction: Mapped[int] = mapped_column(Integer, default=0)

    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class FlowAggregate(Base):
    """流量聚合（按会话对汇总，用于 TopN 与趋势）。"""

    __tablename__ = "flow_aggregates"
    __table_args__ = (
        UniqueConstraint("device_id", "window_ts", "src_ip", "dst_ip", "protocol", name="uq_flow_agg"),
        Index("ix_fa_window", "window_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    window_ts: Mapped[datetime] = mapped_column(DateTime, index=True)  # 5 分钟粒度
    src_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    dst_ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    protocol: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, default=0)
    packets_total: Mapped[int] = mapped_column(Integer, default=0)
    flow_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class FlowExporter(Base):
    """NetFlow 导出器配置（设备侧 export destination）。"""

    __tablename__ = "flow_exporters"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[str] = mapped_column(String(16), default="v9")  # v5/v9/ipfix
    listen_port: Mapped[int] = mapped_column(Integer, default=2055, index=True)
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ================================================================ Telemetry
class TelemetryConfig(Base):
    """gNMI 遥测订阅配置。"""

    __tablename__ = "telemetry_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    mode: Mapped[str] = mapped_column(String(16), default="collector")  # collector / client
    target_addr: Mapped[str] = mapped_column(String(128), default="")
    target_port: Mapped[int] = mapped_column(Integer, default=57400)
    subscribe_paths: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    sample_interval_ms: Mapped[int] = mapped_column(Integer, default=10000)
    username: Mapped[str] = mapped_column(String(64), default="")
    password: Mapped[str] = mapped_column(EncryptedString, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    status_message: Mapped[str] = mapped_column(Text, default="")
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    last_update_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TelemetryPathMapping(Base):
    """用户自定义的遥测路径映射。

    内置映射表（app/collectors/telemetry.py 的 DEFAULT_MAPPINGS）覆盖
    OpenConfig 标准模型与主流厂商私有模型；本表用于补充厂商未适配、
    但用户自己知道路径的设备（如无线终端、专有控制器）。
    """

    __tablename__ = "telemetry_path_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    metric: Mapped[str] = mapped_column(String(48), default="")
    # 说明：该路径采集的是什么
    description: Mapped[str] = mapped_column(String(255), default="")
    # 设备级 / 接口级（接口级指标以 if_ 开头）
    per_interface: Mapped[bool] = mapped_column(Boolean, default=False)
    # 数值缩放（千分之一 → 实际值）
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TelemetrySample(Base):
    """遥测样本审计记录（保留原始路径，便于排障）。"""

    __tablename__ = "telemetry_samples"
    __table_args__ = (Index("ix_ts_device_time", "device_id", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    metric: Mapped[str] = mapped_column(String(48), default="", index=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(String(255), default="")
    interface: Mapped[str] = mapped_column(String(128), default="")
    source_path: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(16), default="push")  # push / subscribe
