"""SNMP 采集封装（pysnmp v7 异步 API），支持 SNMPv1/v2c/v3。"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd,
    get_cmd,
    usm3DESEDEPrivProtocol,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    walk_cmd,
)

from app.core.config import settings

AUTH_PROTOCOLS = {
    "MD5": usmHMACMD5AuthProtocol,
    "SHA": usmHMACSHAAuthProtocol,
    "SHA224": usmHMAC128SHA224AuthProtocol,
    "SHA256": usmHMAC192SHA256AuthProtocol,
    "SHA384": usmHMAC256SHA384AuthProtocol,
    "SHA512": usmHMAC384SHA512AuthProtocol,
}

PRIV_PROTOCOLS = {
    "DES": usmDESPrivProtocol,
    "3DES": usm3DESEDEPrivProtocol,
    "AES": usmAesCfb128Protocol,
    "AES192": usmAesCfb192Protocol,
    "AES256": usmAesCfb256Protocol,
}

logger = logging.getLogger("netops.snmp")


class SnmpError(Exception):
    """SNMP 采集异常。"""


def _strip_access_suffix(community: str) -> str:
    """去掉 community 尾部冗余的访问级别后缀（" ro" / " rw"）。

    很多设备配置里会写成 ``xxx ro`` 这种带访问级别的形式，
    但 SNMP community 本身只能是纯字符串——设备收到 ``xxx ro``
    会当成错误的 community 直接丢弃，表现为**超时**而不是认证失败，
    现场极难定位。返回清洗后的值；无需清洗时返回空串。
    """
    if not community:
        return ""
    stripped = community.rstrip()
    lowered = stripped.lower()
    for suffix in (" ro", " rw"):
        if lowered.endswith(suffix):
            return stripped[: -len(suffix)].rstrip()
    return ""


@dataclass
class SnmpTarget:
    """一次 SNMP 会话所需的连接参数。"""

    host: str
    port: int = 161
    version: str = "2c"
    community: str = "public"
    v3_user: str = ""
    v3_auth_proto: str = "SHA"
    v3_auth_key: str = ""
    v3_priv_proto: str = "AES"
    v3_priv_key: str = ""
    timeout: float = 3.0
    retries: int = 1

    def auth_data(self):
        if self.version == "3":
            return UsmUserData(
                self.v3_user,
                self.v3_auth_key or None,
                self.v3_priv_key or None,
                authProtocol=AUTH_PROTOCOLS.get((self.v3_auth_proto or "SHA").upper()),
                privProtocol=PRIV_PROTOCOLS.get((self.v3_priv_proto or "AES").upper()),
            )
        mp_model = 0 if self.version in ("1", "v1") else 1
        return CommunityData(self.community or "public", mpModel=mp_model)


@dataclass
class SnmpResult:
    ok: bool
    values: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0.0
    # 实际使用的 community——当配置值带冗余后缀而被自动清洗时，
    # 这里记录清洗后的值，供上层修正设备配置
    effective_community: str = ""


def _decode(value: Any) -> Any:
    """把 pysnmp/pyasn1 值转成原生 Python 类型。

    pysnmp 7 使用 pyasn1 类型体系（OctetString / Integer / Counter64 / IpAddress ...），
    需要按类型分派，不能依赖统一的取属性接口。
    """
    if value is None:
        return None
    # 原生类型直接返回
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace").strip("\x00")

    try:
        from pysnmp.proto import rfc1902

        # IpAddress → 可读字符串
        if hasattr(value, "isSameTypeWith") and value.isSameTypeWith(rfc1902.IpAddress):
            try:
                return ".".join(str(b) for b in value.asOctets())
            except Exception:
                return value.prettyPrint()

        # OctetString / DisplayString 等字节型 → utf-8 字符串
        if hasattr(value, "asOctets"):
            raw = value.asOctets()
            if raw:
                try:
                    text = raw.decode("utf-8", errors="replace").strip("\x00")
                    # 非文本内容（如二进制 MAC）保留十六进制
                    if text and all(ch.isprintable() or ch in "\t" for ch in text):
                        return text.strip()
                    return raw.hex()
                except Exception:
                    return raw.hex()

        # 整型家族：Integer / Counter / Gauge / TimeTicks / Counter64
        if hasattr(value, "isSameTypeWith") and value.isSameTypeWith(rfc1902.Integer):
            try:
                return int(value)
            except Exception:
                pass

        # 兜底：尝试转数字，再退回 prettyPrint
        try:
            return int(value)
        except Exception:
            pass
        try:
            return float(value)
        except Exception:
            pass
        if hasattr(value, "prettyPrint"):
            return value.prettyPrint()
        return value
    except Exception:
        return value


class SnmpClient:
    """轻量 SNMP 客户端，一次连接完成多次 GET/WALK。"""

    def __init__(self, target: SnmpTarget):
        self.target = target
        # community 被自动清洗后记住它，后续 WALK 也用清洗后的值
        self._effective_community = ""

    def _auth_data(self):
        """构造认证信息（优先用清洗后的 community）。"""
        community = self._effective_community or self.target.community
        if self.target.version == "3":
            return self.target.auth_data()
        mp_model = 0 if self.target.version in ("1", "v1") else 1
        return CommunityData(community or "public", mpModel=mp_model)

    async def _transport(self) -> UdpTransportTarget:
        return await UdpTransportTarget.create(
            (self.target.host, self.target.port),
            timeout=self.target.timeout,
            retries=self.target.retries,
        )

    async def get(self, oids: Iterable[str]) -> SnmpResult:
        """批量 GET。返回 {oid: value}，不存在的 OID 不会出现在结果中。"""
        oid_list = list(oids)
        if not oid_list:
            return SnmpResult(ok=True, values={})
        result = await self._get_once(oid_list)
        # community 尾部带 " ro"/" rw" 时（从别处复制配置常带上的习惯）
        # 设备会当作错误的 community 直接丢弃，表现是**超时**而非认证失败，
        # 很难自行定位。这里自动去掉尾缀再试一次。
        if not result.ok and self.target.version != "3":
            cleaned = _strip_access_suffix(self.target.community)
            if cleaned:
                retry = await self._get_once(oid_list, community=cleaned)
                if retry.ok:
                    logger.warning(
                        "SNMP community 尾部含冗余的访问级别后缀，已自动改用 %s 采集 %s",
                        cleaned, self.target.host,
                    )
                    retry.effective_community = cleaned
                    # 记住清洗后的值，后续 WALK 也用它
                    self._effective_community = cleaned
                    try:
                        from app.services import system_log_service

                        system_log_service.write_log(
                            system_log_service.LEVEL_WARNING,
                            system_log_service.CAT_COLLECTOR,
                            "snmp",
                            f"设备 {self.target.host} 的 SNMP community 尾部含冗余后缀，已自动去除",
                            f"配置值：{self.target.community!r}；实际使用：{cleaned!r}。"
                            "SNMP community 只能是纯字符串，设备收到带后缀的值会当作"
                            "错误的 community 直接丢弃（表现为超时而非认证失败）。"
                            "平台已自动改用清洗后的值，建议同步更新设备配置。",
                            context={
                                "host": self.target.host,
                                "configured_community": self.target.community,
                                "effective_community": cleaned,
                            },
                        )
                    except Exception:
                        pass
                    return retry
        return result

    async def _get_once(
        self, oid_list: List[str], community: Optional[str] = None
    ) -> SnmpResult:
        """执行一次 GET（可指定临时 community）。"""
        started = time.perf_counter()
        try:
            engine = SnmpEngine()
            transport = await self._transport()
            auth = self._auth_data()
            if community is not None and self.target.version != "3":
                # 用临时 community 重新构造认证信息
                mp_model = 0 if self.target.version in ("1", "v1") else 1
                auth = CommunityData(community, mpModel=mp_model)
            err_ind, err_status, err_idx, var_binds = await get_cmd(
                engine,
                auth,
                transport,
                ContextData(),
                *[ObjectType(ObjectIdentity(oid)) for oid in oid_list],
                lookupMib=False,
            )
            elapsed = (time.perf_counter() - started) * 1000
            if err_ind:
                return SnmpResult(ok=False, error=str(err_ind), elapsed_ms=elapsed)
            if err_status:
                return SnmpResult(ok=False, error=f"{err_status.prettyPrint()} @{err_idx}", elapsed_ms=elapsed)
            values: Dict[str, Any] = {}
            for var_bind in var_binds:
                name = str(var_bind[0])
                raw = var_bind[1]
                if raw is not None and not getattr(raw, "isSameTypeWith", None):
                    continue
                if hasattr(raw, "isSameTypeWith") and raw.isSameTypeWith(rfc1902_null):
                    continue
                values[name] = _decode(raw)
            return SnmpResult(ok=True, values=values, elapsed_ms=elapsed)
        except Exception as exc:  # 网络异常统一转为 SnmpError
            raise SnmpError(f"SNMP GET 失败: {exc}") from exc

    async def getnext(self, oids: Iterable[str]) -> SnmpResult:
        """批量 GETNEXT：取每个 OID 的**下一个**实例。

        探测设备是否支持某个子树用——GETNEXT(oid) 返回的 OID
        若仍是该 oid 的后代（前缀匹配），说明设备实现了这个子树；
        若跳到了别处（大于该 oid 的下一个兄弟子树），说明不支持。
        这比 GET(oid.0) 通用：标识节点 / 表 / 表列 / 标量都能测。
        """
        from pysnmp.hlapi.asyncio import next_cmd

        oid_list = [o.strip().lstrip(".") for o in oids if o]
        if not oid_list:
            return SnmpResult(ok=True, values={})
        started = time.perf_counter()
        try:
            engine = SnmpEngine()
            transport = await self._transport()
            err_ind, err_status, err_idx, var_binds = await next_cmd(
                engine,
                self._auth_data(),
                transport,
                ContextData(),
                *[ObjectType(ObjectIdentity(oid)) for oid in oid_list],
                lookupMib=False,
            )
            elapsed = (time.perf_counter() - started) * 1000
            if err_ind:
                return SnmpResult(ok=False, error=str(err_ind), elapsed_ms=elapsed)
            if err_status:
                return SnmpResult(ok=False, error=f"{err_status.prettyPrint()} @{err_idx}", elapsed_ms=elapsed)
            values: Dict[str, Any] = {}
            for var_bind in var_binds:
                name = str(var_bind[0])
                raw = var_bind[1]
                if hasattr(raw, "isSameTypeWith") and raw.isSameTypeWith(rfc1902_null):
                    continue
                values[name] = _decode(raw)
            return SnmpResult(ok=True, values=values, elapsed_ms=elapsed)
        except Exception as exc:
            raise SnmpError(f"SNMP GETNEXT 失败: {exc}") from exc

    async def walk(self, root_oid: str) -> SnmpResult:
        """遍历子树，返回 {oid: value}。"""
        started = time.perf_counter()
        values: Dict[str, Any] = {}
        try:
            engine = SnmpEngine()
            transport = await self._transport()
            async for err_ind, err_status, err_idx, var_binds in walk_cmd(
                engine,
                self._auth_data(),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(root_oid)),
                lexicographicMode=False,
                lookupMib=False,
            ):
                if err_ind:
                    raise SnmpError(f"SNMP WALK 失败: {err_ind}")
                if err_status:
                    raise SnmpError(f"SNMP WALK 失败: {err_status.prettyPrint()}")
                for var_bind in var_binds:
                    name = str(var_bind[0])
                    values[name] = _decode(var_bind[1])
            return SnmpResult(ok=True, values=values, elapsed_ms=(time.perf_counter() - started) * 1000)
        except SnmpError:
            raise
        except Exception as exc:
            raise SnmpError(f"SNMP WALK 失败: {exc}") from exc

    async def bulk(self, root_oid: str, max_repetitions: int = 50) -> SnmpResult:
        """使用 BULK 遍历一棵子树。

        pysnmp 7 的 bulk_cmd 是「单次请求」协程而非异步生成器，
        因此这里自行实现游标式遍历：以上一次返回的最后一个 OID 作为下次请求起点。

        与 WALK 语义一致：结果按 OID 升序，遇到子树外的 OID 即停止。
        """
        started = time.perf_counter()
        values: Dict[str, Any] = {}
        cursor = root_oid
        prefix = root_oid + "."
        max_rounds = 500  # 防止异常数据导致死循环

        try:
            engine = SnmpEngine()
            transport = await self._transport()
            for _ in range(max_rounds):
                err_ind, err_status, err_idx, var_binds = await bulk_cmd(
                    engine,
                    self._auth_data(),
                    transport,
                    ContextData(),
                    0,
                    max_repetitions,
                    ObjectType(ObjectIdentity(cursor)),
                    lexicographicMode=False,
                    lookupMib=False,
                )
                if err_ind:
                    raise SnmpError(f"SNMP BULK 失败: {err_ind}")
                if err_status:
                    raise SnmpError(f"SNMP BULK 失败: {err_status.prettyPrint()}")
                if not var_binds:
                    break

                reached_end = False
                for vb in var_binds:
                    name = str(vb[0])
                    if not name.startswith(prefix):
                        # 已越出子树，遍历结束
                        reached_end = True
                        break
                    values[name] = _decode(vb[1])
                    cursor = name

                if reached_end:
                    break
            return SnmpResult(ok=True, values=values, elapsed_ms=(time.perf_counter() - started) * 1000)
        except SnmpError:
            raise
        except Exception as exc:
            raise SnmpError(f"SNMP BULK 失败: {exc}") from exc


from pysnmp.proto.rfc1902 import Null as rfc1902_null  # noqa: E402


def target_from_device(device) -> SnmpTarget:
    """从 ORM Device 对象构造 SnmpTarget。"""
    return SnmpTarget(
        host=device.ip,
        port=device.snmp_port or 161,
        version=(device.snmp_version or "2c"),
        community=device.snmp_community or "public",
        v3_user=device.snmp_v3_user or "",
        v3_auth_proto=device.snmp_v3_auth_proto or "SHA",
        v3_auth_key=device.snmp_v3_auth_key or "",
        v3_priv_proto=device.snmp_v3_priv_proto or "AES",
        v3_priv_key=device.snmp_v3_priv_key or "",
        timeout=settings.snmp_timeout,
        retries=settings.snmp_retries,
    )
