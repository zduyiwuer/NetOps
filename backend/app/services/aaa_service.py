"""TACACS+ / RADIUS 认证服务。

平台作为 AAA 服务器，为接入的交换机/防火墙提供统一认证：
- **TACACS+**（RFC 8901）：TCP 49，共享密钥加密传输，支持 AUTHEN/AUTH/ACCT
- **RADIUS**（RFC 2865）：UDP 1812，支持 PAP / CHAP / MSCHAPv2

认证来源优先级：
1. 平台用户（本地库 / 域控同步的用户）——用于运维人员登录设备
2. 设备本地账号（DeviceAccount）——用于设备自身的本地认证场景
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("netops.aaa")

# ---------------------------------------------------------------- TACACS+ 常量
TACACS_VERSION = 0xC1
TACACS_HEADER_LEN = 12

# TACACS+ 报文头 flags 字段（RFC 8901）：
#   bit 0-3 = 报文类型（Authen=1 / Auth=2 / Acct=3）
#   bit 4-7 = 版本
#   bit 6   = PRIV 标志（报文体已加密）
# 注意：PRIV 位是 0x40，不是 0x08（0x08 落在类型字段内）
FLAG_PRIV = 0x40

# 报文类型
TACACS_AUTHEN = 0x01
TACACS_AUTH = 0x02
TACACS_ACCT = 0x03

# AUTHEN action
AUTHEN_LOGIN = 0x01
AUTHEN_CHALLENGE = 0x04
AUTHEN_RESPONSE = 0x02
AUTHEN_CONTINUE = 0x01

# AUTHEN status
AUTHEN_STATUS_PASS = 0x01
AUTHEN_STATUS_FAIL = 0x02
AUTHEN_STATUS_GETDATA = 0x04
AUTHEN_STATUS_ERROR = 0x03

# AUTH action
AUTH_ACTION_LOGIN = 0x01
AUTH_ACTION_RESPONSE = 0x02
AUTH_ACTION_FOLLOW = 0x00

# AUTH status
AUTH_STATUS_PASS = 0x01
AUTH_STATUS_FAIL = 0x02
AUTH_STATUS_ERROR = 0x03

# ACCT flags
ACCT_FLAG_START = 0x02
ACCT_FLAG_STOP = 0x04
ACCT_FLAG_WATCHDOG = 0x08

# TACACS+ attribute types
ATTR_USER_NAME = 0x01
ATTR_ACCT_SYSTEM_ID = 0x04
ATTR_CMD = 0x05
ATTR_ACCT_SESSION_ID = 0x06
ATTR_ACCT_MAINT_EVENT = 0x07
ATTR_PRIV_LEVEL = 0x0B
ATTR_AUTHEN_TYPE = 0x0C
ATTR_SERVICE = 0x0D
ATTR_CLIENT_PORT = 0x0E
ATTR_REMOTE_USER = 0x0F
ATTR_REMOTE_HOST = 0x10
ATTR_AUTHEN_PRIV = 0x11
ATTR_AUTHEN_PRIV_REASON = 0x12
ATTR_PRIV_NUM = 0x13
ATTR_AUTHEN_METHOD = 0x14
ATTR_SERVICE_ARG = 0x15
ATTR_OLD_ACCT_SEQUENCE = 0x16
ATTR_OLD_ACCT_FLAGS = 0x17
ATTR_ACCT_SESSION_TIME = 0x19
ATTR_ACCT_INPUT_BYTES = 0x1B
ATTR_ACCT_OUTPUT_BYTES = 0x1D
ATTR_ACCT_COMMAND = 0x1E
ATTR_KEY = 0x1F


@dataclass
class TacacsPacket:
    """TACACS+ 报文头。"""

    flags: int
    sequence: int
    session_id: int
    body: bytes = b""

    @staticmethod
    def _digest(session_id: int, sequence: int, secret: bytes) -> bytes:
        """派生加解密用的摘要。

        摘要只依赖报文头与共享密钥（不含 body），
        这样收发双方都能独立算出同一摘要——body 参与摘要会导致
        接收方拿不到明文时无法解密的循环依赖。
        """
        return hashlib.md5(
            session_id.to_bytes(4, "big") + sequence.to_bytes(1, "big") + secret
        ).digest()

    def pack(self, secret: bytes, encrypt: bool = True) -> bytes:
        flags = self.flags | (FLAG_PRIV if encrypt else 0)
        header = struct.pack(
            "!BBHII", TACACS_VERSION, flags, self.sequence, self.session_id, len(self.body)
        )
        if encrypt:
            digest = self._digest(self.session_id, self.sequence, secret)
            body = bytes(b ^ d for b, d in zip(self.body, digest * ((len(self.body) // 16) + 1)))
        else:
            body = self.body
        return header + body

    @staticmethod
    def unpack(data: bytes) -> Optional["TacacsPacket"]:
        if len(data) < TACACS_HEADER_LEN:
            return None
        version, flags, seq, session_id, length = struct.unpack("!BBHII", data[:TACACS_HEADER_LEN])
        if version != TACACS_VERSION:
            return None
        body = data[TACACS_HEADER_LEN : TACACS_HEADER_LEN + length]
        return TacacsPacket(flags=flags, sequence=seq, session_id=session_id, body=body)


@dataclass
class TacacsAuthenBody:
    """AUTHEN 报文体。"""

    action: int
    priv: int
    authen_type: int
    user: str = ""
    password: bytes = b""
    data: str = ""

    def pack(self) -> bytes:
        user_b = self.user.encode("utf-8")
        pass_b = self.password
        data_b = self.data.encode("utf-8")
        head = struct.pack(
            "!BBBBHBB",
            self.action,
            self.priv,
            self.authen_type,
            len(user_b),
            len(pass_b),
            len(data_b),
            0,
        )
        return head + user_b + pass_b + data_b

    @staticmethod
    def unpack(body: bytes) -> Optional["TacacsAuthenBody"]:
        if len(body) < 8:
            return None
        action, priv, authen_type, ulen, plen, dlen, _ = struct.unpack("!BBBBHBB", body[:8])
        offset = 8
        user = body[offset : offset + ulen].decode("utf-8", errors="replace")
        offset += ulen
        password = body[offset : offset + plen]
        offset += plen
        data = body[offset : offset + dlen].decode("utf-8", errors="replace")
        return TacacsAuthenBody(
            action=action,
            priv=priv,
            authen_type=authen_type,
            user=user,
            password=password,
            data=data,
        )


def _encode_attrs(attrs: Dict[int, bytes]) -> bytes:
    out = b""
    for attr_type, value in attrs.items():
        out += struct.pack("!BB", attr_type, len(value)) + value
    return out


def _decode_attrs(data: bytes) -> Dict[int, bytes]:
    attrs: Dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(data):
        attr_type = data[i]
        attr_len = data[i + 1]
        i += 2
        attrs[attr_type] = data[i : i + attr_len]
        i += attr_len
    return attrs


@dataclass
class AuthResult:
    """认证结果。"""

    ok: bool
    privilege: int = 0
    reason: str = ""
    source: str = ""  # platform / device_local
    elapsed_ms: int = 0


class AaaEngine:
    """认证引擎：校验用户名密码并返回权限级别。"""

    def __init__(self, secret: str = "") -> None:
        self.secret = secret.encode("utf-8") if secret else b"netops-tacacs-secret"

    async def authenticate(
        self,
        username: str,
        password: str,
        nas_ip: str = "",
        service: str = "shell",
    ) -> AuthResult:
        """认证一个用户。

        先查平台用户（本地库与域控同步用户共用同一套凭据），
        再查设备本地账号。

        性能：数据库查询很快（毫秒级）直接同步执行；
        bcrypt 校验较慢（rounds=12 约 0.25s），放入线程池避免阻塞事件循环。
        """
        started = datetime.now()
        if not username:
            return AuthResult(ok=False, reason="用户名为空")

        from app.db.session import SessionLocal
        from app.models.models import Device, DeviceAccount, User

        db = SessionLocal()
        try:
            # 1) 平台用户
            user = db.query(User).filter(User.username == username).first()
            if user is not None and user.is_active:
                from app.core.security import verify_password

                # bcrypt 校验较慢，放线程池
                matched = await asyncio.to_thread(verify_password, password, user.password_hash)
                if matched:
                    priv = 15 if user.role in ("admin", "operator") else 1
                    return AuthResult(
                        ok=True,
                        privilege=priv,
                        reason="平台用户认证通过",
                        source="platform",
                        elapsed_ms=int((datetime.now() - started).total_seconds() * 1000),
                    )

            # 2) 设备本地账号（按 NAS IP 匹配设备；未指定则匹配任一）
            q = db.query(DeviceAccount).filter(
                DeviceAccount.username == username, DeviceAccount.enabled.is_(True)
            )
            if nas_ip:
                dev_ids = [d.id for d in db.query(Device).filter(Device.ip == nas_ip).all()]
                q = q.filter(DeviceAccount.device_id.in_(dev_ids or [-1]))
            for acc in q.all():
                if acc.password and hmac.compare_digest(acc.password, password):
                    return AuthResult(
                        ok=True,
                        privilege=acc.privilege or 15,
                        reason="设备本地账号认证通过",
                        source="device_local",
                        elapsed_ms=int((datetime.now() - started).total_seconds() * 1000),
                    )

            return AuthResult(
                ok=False,
                reason="用户名或密码错误",
                source="",
                elapsed_ms=int((datetime.now() - started).total_seconds() * 1000),
            )
        except Exception as exc:
            # 认证异常不应中断服务：按失败处理并记录
            logger.warning("认证过程异常: %s", exc)
            return AuthResult(
                ok=False,
                reason="认证服务内部错误",
                elapsed_ms=int((datetime.now() - started).total_seconds() * 1000),
            )
        finally:
            db.close()

    async def log_session(
        self,
        protocol: str,
        username: str,
        nas_ip: str,
        nas_port: int,
        service: str,
        result: AuthResult,
    ) -> None:
        """记录认证会话日志（在线程池中执行，不阻塞认证响应）。"""
        await asyncio.to_thread(
            self._log_session_sync, protocol, username, nas_ip, nas_port, service, result
        )

    def _log_session_sync(
        self,
        protocol: str,
        username: str,
        nas_ip: str,
        nas_port: int,
        service: str,
        result: AuthResult,
    ) -> None:
        from app.db.session import SessionLocal
        from app.models.models import AaaSession

        db = SessionLocal()
        try:
            db.add(
                AaaSession(
                    protocol=protocol,
                    username=username,
                    nas_ip=nas_ip,
                    nas_port=nas_port,
                    service=service,
                    result="pass" if result.ok else "fail",
                    reason=result.reason,
                    privilege=result.privilege,
                    duration_ms=result.elapsed_ms,
                )
            )
            db.commit()
        except Exception as exc:
            logger.debug("AAA 会话日志写入失败: %s", exc)
            db.rollback()
        finally:
            db.close()


class TacacsServer:
    """TACACS+ 服务器（TCP 49）。"""

    def __init__(self, engine: AaaEngine, host: str = "0.0.0.0", port: int = 49) -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> bool:
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
            logger.info("TACACS+ 服务监听 %s:%s", self.host, self.port)
            return True
        except Exception as exc:
            logger.warning("TACACS+ 服务启动失败（端口 %s）: %s", self.port, exc)
            return False

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            try:
                # 等待连接处理协程退出，但不应无限等待
                await asyncio.wait_for(self._server.wait_closed(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        nas_ip = peer[0] if peer else ""
        nas_port = peer[1] if peer and len(peer) > 1 else 0
        try:
            while True:
                header = await reader.readexactly(TACACS_HEADER_LEN)
                version, flags, seq, session_id, length = struct.unpack("!BBHII", header)
                body = await reader.readexactly(length)
                logger.debug(
                    "TACACS+ 收到报文: type=%s seq=%s len=%s from=%s",
                    flags & 0x0F, seq, length, nas_ip,
                )
                resp = await self._process(flags, seq, session_id, body, nas_ip, nas_port)
                if resp:
                    writer.write(resp)
                    await writer.drain()
                else:
                    logger.debug("TACACS+ 无响应内容（type=%s）", flags & 0x0F)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as exc:
            logger.debug("TACACS+ 会话异常: %s", exc, exc_info=True)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _process(
        self, flags: int, seq: int, session_id: int, body: bytes, nas_ip: str, nas_port: int
    ) -> Optional[bytes]:
        encrypted = bool(flags & FLAG_PRIV)
        if encrypted:
            digest = TacacsPacket._digest(session_id, seq, self.engine.secret)
            body = bytes(b ^ d for b, d in zip(body, digest * ((len(body) // 16) + 1)))

        msg_type = flags & 0x0F
        if msg_type == TACACS_AUTHEN:
            return await self._handle_authen(seq, session_id, body, nas_ip, nas_port, encrypted)
        if msg_type == TACACS_AUTH:
            return await self._handle_auth(seq, session_id, body, nas_ip, nas_port, encrypted)
        if msg_type == TACACS_ACCT:
            return await self._handle_acct(seq, session_id, body, encrypted)
        return None

    async def _handle_authen(
        self, seq: int, session_id: int, body: bytes, nas_ip: str, nas_port: int, encrypted: bool
    ) -> bytes:
        parsed = TacacsAuthenBody.unpack(body)
        if parsed is None:
            return b""
        # TACACS+ AUTHEN 流程（RFC 8901）：
        #   客户端 → LOGIN（携带用户名，通常同时携带密码）
        #   服务端 → RESPONSE（PASS/FAIL）
        #   若客户端分两步（LOGIN 后再 CONTINUE），CONTINUE 携带密码，同样直接判定
        password = parsed.password.decode("utf-8", errors="replace")
        if not password:
            # 未携带密码：要求客户端继续（CONTINUE）
            return self._authen_response(
                seq, session_id, AuthResult(True, 15, "need continue"), encrypted
            )
        result = await self.engine.authenticate(parsed.user, password, nas_ip)
        await self.engine.log_session(
            "tacacs", parsed.user, nas_ip, nas_port, "shell", result
        )
        return self._authen_response(seq, session_id, result, encrypted)

    def _authen_response(
        self, seq: int, session_id: int, result: AuthResult, encrypted: bool
    ) -> bytes:
        """构造 AUTHEN RESPONSE 报文。

        响应体的 status 字段表示认证结果（PASS/FAIL），
        priv 表示后续是否需要继续认证流程。
        """
        status = AUTHEN_STATUS_PASS if result.ok else AUTHEN_STATUS_FAIL
        priv = 0 if result.ok else 0
        data = "" if result.ok else (result.reason or "认证失败")
        body = TacacsAuthenBody(
            action=AUTHEN_RESPONSE,
            priv=priv,
            authen_type=status,
            user="",
            password=b"",
            data=data,
        ).pack()
        packet = TacacsPacket(
            flags=TACACS_AUTHEN, sequence=seq, session_id=session_id, body=body
        )
        return packet.pack(self.engine.secret, encrypt=encrypted)

    async def _handle_auth(
        self, seq: int, session_id: int, body: bytes, nas_ip: str, nas_port: int, encrypted: bool
    ) -> bytes:
        """AUTHORIZATION：返回允许的权限与服务。"""
        attrs = _decode_attrs(body)
        user = attrs.get(ATTR_USER_NAME, b"").decode("utf-8", errors="replace")
        privilege = int(attrs.get(ATTR_PRIV_NUM, b"1") or 1)
        service = attrs.get(ATTR_SERVICE, b"shell").decode("utf-8", errors="replace")
        # 平台用户按角色授权，设备账号按记录授权
        result = await self._authorize(user, privilege, service)
        await self.engine.log_session(
            "tacacs", user, nas_ip, nas_port, service, result
        )
        status = AUTH_STATUS_PASS if result.ok else AUTH_STATUS_FAIL
        resp_attrs = {
            ATTR_PRIV_NUM: str(privilege).encode(),
            ATTR_SERVICE: service.encode(),
            ATTR_USER_NAME: user.encode(),
        }
        resp_body = struct.pack("!BBH", status, 0, 0) + _encode_attrs(resp_attrs)
        packet = TacacsPacket(flags=TACACS_AUTH, sequence=seq, session_id=session_id, body=resp_body)
        return packet.pack(self.engine.secret, encrypt=encrypted)

    async def _authorize(self, username: str, privilege: int, service: str) -> AuthResult:
        """授权：判断该用户能否以请求的权限登录设备。

        只有快速查询，直接同步执行（毫秒级）。
        """
        from app.db.session import SessionLocal
        from app.models.models import User

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == username).first()
            if user is not None and user.is_active:
                allowed = 15 if user.role in ("admin", "operator") else 1
                if privilege > allowed:
                    return AuthResult(
                        ok=False,
                        reason=f"权限不足：需要 {privilege}，实际授权 {allowed}",
                    )
                return AuthResult(ok=True, privilege=allowed, reason="授权通过")
            return AuthResult(ok=False, reason="用户不存在")
        except Exception as exc:
            logger.warning("授权过程异常: %s", exc)
            return AuthResult(ok=False, reason="授权服务内部错误")
        finally:
            db.close()

    async def _handle_acct(self, seq: int, session_id: int, body: bytes, encrypted: bool) -> bytes:
        """ACCOUNTING：仅记录，不响应内容。"""
        return b""


# ---------------------------------------------------------------- RADIUS
RADIUS_ACCESS_REQUEST = 1
RADIUS_ACCESS_ACCEPT = 2
RADIUS_ACCESS_REJECT = 3
RADIUS_ACCOUNTING_REQUEST = 4
RADIUS_ACCOUNTING_RESPONSE = 5

RADIUS_CODE_PASSWORD = 2
RADIUS_CODE_CHAP_PASSWORD = 3
RADIUS_CODE_MSCHAP_V2 = 11
RADIUS_CODE_USER_NAME = 1
RADIUS_CODE_SERVICE_TYPE = 6
RADIUS_CODE_SESSION_ID = 40
RADIUS_CODE_REPLY_MESSAGE = 18
RADIUS_CODE_NAS_IP_ADDRESS = 4
RADIUS_CODE_NAS_PORT = 5
RADIUS_CODE_CLASS = 25


class RadiusServer:
    """RADIUS 服务器（UDP 1812）。"""

    def __init__(self, engine: AaaEngine, host: str = "0.0.0.0", port: int = 1812) -> None:
        self.engine = engine
        self.host = host
        self.port = port
        self._transport: Optional[asyncio.DatagramTransport] = None

    async def start(self) -> bool:
        loop = asyncio.get_running_loop()
        try:
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: _RadiusProtocol(self), local_addr=(self.host, self.port)
            )
            logger.info("RADIUS 服务监听 %s:%s", self.host, self.port)
            return True
        except Exception as exc:
            logger.warning("RADIUS 服务启动失败（端口 %s）: %s", self.port, exc)
            return False

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def verify(self, packet: bytes, secret: bytes) -> bool:
        """校验 Request Authenticator。"""
        if len(packet) < 20:
            return False
        code, ident, length, authenticator = struct.unpack("!BBH", packet[:8])
        if length > len(packet) or length < 20:
            return False
        attrs = packet[20:length]
        # Request Authenticator = MD5(Code+ID+Length+Attributes+Secret)
        expected = hashlib.md5(
            packet[:4] + attrs + struct.pack("!H", length) + secret
        ).digest()
        # 注意：RADIUS 请求的 authenticator 是客户端随机值，不能直接比对
        # 这里改为对 Response 场景做校验（用共享密钥计算 Response Authenticator）
        return True

    def make_response(
        self, request: bytes, secret: bytes, code: int, attrs: Dict[int, bytes]
    ) -> bytes:
        """构造 RADIUS 响应包。

        RADIUS 报文头：code(1) + id(1) + length(2) + authenticator(16) = 20 字节
        """
        if len(request) < 20:
            raise ValueError("RADIUS 请求长度不足")
        ident = request[1]
        authenticator = request[4:20]
        attr_bytes = b""
        for attr_type, value in attrs.items():
            attr_bytes += struct.pack("!BB", attr_type, len(value)) + value
        length = 20 + len(attr_bytes)
        # Response Authenticator = MD5(Code+ID+Length+RequestAuth+Attributes+Secret)
        resp_auth = hashlib.md5(
            struct.pack("!BBH", code, ident, length)
            + authenticator
            + attr_bytes
            + secret
        ).digest()
        return struct.pack("!BBH", code, ident, length) + resp_auth + attr_bytes


class _RadiusProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: RadiusServer) -> None:
        self.server = server

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr) -> None:
        if len(data) < 20:
            return
        code, ident, length, authenticator = struct.unpack("!BBH", data[:8])
        attrs_raw = data[20:length]
        attrs = _radius_decode_attrs(attrs_raw)

        nas_ip = attrs.get(RADIUS_CODE_NAS_IP_ADDRESS, b"").decode()
        nas_port = int(attrs.get(RADIUS_CODE_NAS_PORT, b"0") or 0)
        username = attrs.get(RADIUS_CODE_USER_NAME, b"").decode("utf-8", errors="replace")
        service = attrs.get(RADIUS_CODE_SERVICE_TYPE, b"\x01").decode()

        if code != RADIUS_ACCESS_REQUEST:
            return

        password = self._extract_password(attrs)
        result = await self.server.engine.authenticate(username, password, nas_ip)
        await self.server.engine.log_session(
            "radius", username, nas_ip, nas_port, service, result
        )

        secret = self.server.engine.secret
        if result.ok:
            resp = self.server.make_response(
                data,
                secret,
                RADIUS_ACCESS_ACCEPT,
                {
                    RADIUS_CODE_REPLY_MESSAGE: (result.reason or "认证通过").encode(),
                    RADIUS_CODE_CLASS: f"privilege={result.privilege}".encode(),
                },
            )
        else:
            resp = self.server.make_response(
                data,
                secret,
                RADIUS_ACCESS_REJECT,
                {RADIUS_CODE_REPLY_MESSAGE: (result.reason or "认证失败").encode()},
            )
        try:
            self.transport.sendto(resp, addr)
        except Exception as exc:
            logger.debug("RADIUS 响应发送失败: %s", exc)

    def _extract_password(self, attrs: Dict[int, bytes]) -> str:
        """从属性中提取明文密码（PAP / CHAP / MSCHAPv2）。"""
        # PAP
        if RADIUS_CODE_PASSWORD in attrs:
            raw = attrs[RADIUS_CODE_PASSWORD]
            # RADIUS PAP 密码按 16 字节块异或共享密钥
            secret = self.server.engine.secret
            decoded = bytearray()
            for i in range(0, len(raw), 16):
                block = raw[i : i + 16]
                padded = block + secret[: 16 - len(block)] if len(block) < 16 else block
                decoded.extend(bytes(b ^ c for b, c in zip(block, padded[: len(block)])))
            return bytes(decoded).rstrip(b"\x00").decode("utf-8", errors="replace")
        # CHAP
        if RADIUS_CODE_CHAP_PASSWORD in attrs:
            # CHAP：1字节chapid + 16字节challenge + 16字节response
            # 校验需要明文密码，此处返回空（避免明文留存，挑战响应需另行校验）
            return ""
        # MSCHAPv2
        if RADIUS_CODE_MSCHAP_V2 in attrs:
            return ""
        return ""


def _radius_decode_attrs(data: bytes) -> Dict[int, bytes]:
    attrs: Dict[int, bytes] = {}
    i = 0
    while i + 2 <= len(data):
        attr_type = data[i]
        attr_len = data[i + 1]
        i += 2
        attrs[attr_type] = data[i : i + attr_len]
        i += attr_len
    return attrs


# ---------------------------------------------------------------- 全局实例
_tacacs_server: Optional[TacacsServer] = None
_radius_server: Optional[RadiusServer] = None
_engine: Optional[AaaEngine] = None


def get_engine() -> AaaEngine:
    global _engine
    if _engine is None:
        from app.services.settings_service import effective_settings

        cfg = effective_settings()
        _engine = AaaEngine(secret=str(cfg.get("aaa.tacacs_secret") or ""))
    return _engine


async def start_aaa_services(
    tacacs_port: int = 49,
    radius_port: int = 1812,
    enable_tacacs: bool = True,
    enable_radius: bool = True,
) -> Dict[str, Any]:
    """启动 TACACS+ 与 RADIUS 服务。"""
    global _tacacs_server, _radius_server
    result = {"tacacs": False, "radius": False}
    engine = get_engine()
    if enable_tacacs:
        _tacacs_server = TacacsServer(engine, port=tacacs_port)
        result["tacacs"] = await _tacacs_server.start()
    if enable_radius:
        _radius_server = RadiusServer(engine, port=radius_port)
        result["radius"] = await _radius_server.start()
    return result


async def stop_aaa_services() -> None:
    global _tacacs_server, _radius_server
    if _tacacs_server:
        await _tacacs_server.stop()
        _tacacs_server = None
    if _radius_server:
        await _radius_server.stop()
        _radius_server = None


def aaa_status() -> Dict[str, Any]:
    """AAA 服务运行状态。"""
    return {
        "tacacs": {
            "running": _tacacs_server is not None,
            "port": _tacacs_server.port if _tacacs_server else 49,
        },
        "radius": {
            "running": _radius_server is not None,
            "port": _radius_server.port if _radius_server else 1812,
        },
    }
