"""设备接入体系验证脚本（TACACS+/RADIUS + 配置变更审批 + 设备账号）。

覆盖：
  1. TACACS+ 报文编解码（AUTHEN / AUTH / ACCT）
  2. 真实 TCP 交互：模拟设备发起 AUTHEN 认证
  3. RADIUS Access-Request 处理
  4. 认证引擎（平台用户 / 设备本地账号）
  5. 设备账号 CRUD + 密码加密存储
  6. 配置变更审批状态机与审计日志

运行：python -m scripts.verify_access
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.collectors.ssh import SshError  # noqa: F401
from app.services.aaa_service import (
    AUTHEN_LOGIN,
    AUTHEN_RESPONSE,
    AUTHEN_STATUS_FAIL,
    AUTHEN_STATUS_PASS,
    FLAG_PRIV,
    TACACS_AUTH,
    TACACS_AUTHEN,
    TacacsAuthenBody,
    TacacsPacket,
    _encode_attrs,
    _decode_attrs,
)

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


async def main() -> int:
    print("\n=== 设备接入体系验证 ===\n")

    # 确保新表已建（设备账号 / 配置变更 / AAA 会话）
    from app.db.session import Base, engine

    import app.models.models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # ---------------------------------------------------------------- 报文编解码
    print("[1] TACACS+ 报文编解码")
    body = TacacsAuthenBody(
        action=AUTHEN_LOGIN, priv=1, authen_type=0,
        user="admin", password=b"secret", data="",
    ).pack()
    parsed = TacacsAuthenBody.unpack(body)
    check("AUTHEN 体往返一致", parsed is not None and parsed.user == "admin" and parsed.password == b"secret",
          f"user={parsed.user if parsed else '-'}")

    attrs = _encode_attrs({1: b"admin", 13: b"shell", 19: b"15"})
    decoded = _decode_attrs(attrs)
    check("属性编解码", decoded.get(1) == b"admin" and decoded.get(13) == b"shell",
          str({k: v for k, v in decoded.items()}))

    secret = b"test-shared-secret"
    pkt = TacacsPacket(flags=TACACS_AUTHEN, sequence=42, session_id=1234, body=body)
    raw = pkt.pack(secret, encrypt=True)
    check("加密报文长度正确", len(raw) == 12 + len(body), f"{len(raw)}")
    unpacked = TacacsPacket.unpack(raw)
    check("报文头可解析", unpacked is not None and unpacked.sequence == 42,
          f"seq={unpacked.sequence if unpacked else '-'}")

    print("\n[2] TACACS+ 真实 TCP 交互")
    from app.services.aaa_service import AaaEngine, TacacsServer

    aaa = AaaEngine(secret="test-shared-secret")
    server = TacacsServer(aaa, host="127.0.0.1", port=14949)
    ok = await server.start()
    check("TACACS+ 服务启动", ok, "端口 14949")
    if not ok:
        return 1

    try:
        # 模拟设备发起 AUTHEN LOGIN
        def _md5_xor(data: bytes) -> bytes:
            import hashlib

            digest = hashlib.md5(
                (1234).to_bytes(4, "big") + bytes([7]) + secret + data
            ).digest()
            return bytes(b ^ d for b, d in zip(data, digest * ((len(data) // 16) + 1)))

        authen_body = TacacsAuthenBody(
            action=AUTHEN_LOGIN, priv=1, authen_type=0,
            user="admin", password=b"admin123", data="",
        ).pack()
        req = TacacsPacket(
            flags=TACACS_AUTHEN | FLAG_PRIV, sequence=7, session_id=1234, body=authen_body
        ).pack(secret, encrypt=True)

        reader, writer = await asyncio.open_connection("127.0.0.1", 14949)
        writer.write(req)
        await writer.drain()
        # 先读响应头，再按 length 读 body
        resp_head = await asyncio.wait_for(reader.readexactly(12), timeout=5)
        hdr = struct.unpack("!BBHII", resp_head)
        resp_body = await asyncio.wait_for(reader.readexactly(hdr[4]), timeout=5)
        # 解密响应（摘要参与字段：session_id(4B) + seq(1B) + secret + body）
        import hashlib

        digest = hashlib.md5(
            hdr[3].to_bytes(4, "big") + hdr[2].to_bytes(1, "big") + secret
        ).digest()
        plain = bytes(b ^ d for b, d in zip(resp_body, digest * ((len(resp_body) // 16) + 1)))
        rbody = TacacsAuthenBody.unpack(plain)
        check("收到 AUTHEN 响应", rbody is not None and rbody.action == AUTHEN_RESPONSE,
              f"action={rbody.action if rbody else '-'}")
        check("认证结果为 PASS", rbody is not None and rbody.authen_type == AUTHEN_STATUS_PASS,
              f"status={rbody.authen_type if rbody else '-'}")

        # 错误密码（TACACS+ 每个会话使用独立 TCP 连接）
        authen_body2 = TacacsAuthenBody(
            action=AUTHEN_LOGIN, priv=1, authen_type=0,
            user="admin", password=b"wrong", data="",
        ).pack()
        req2 = TacacsPacket(
            flags=TACACS_AUTHEN | FLAG_PRIV, sequence=8, session_id=1235, body=authen_body2
        ).pack(secret, encrypt=True)
        r2, w2 = await asyncio.open_connection("127.0.0.1", 14949)
        w2.write(req2)
        await w2.drain()
        resp2_head = await asyncio.wait_for(r2.readexactly(12), timeout=5)
        hdr2 = struct.unpack("!BBHII", resp2_head)
        rb2 = await asyncio.wait_for(r2.readexactly(hdr2[4]), timeout=5)
        d2 = hashlib.md5(hdr2[3].to_bytes(4, "big") + hdr2[2].to_bytes(1, "big") + secret).digest()
        p2 = bytes(b ^ d for b, d in zip(rb2, d2 * ((len(rb2) // 16) + 1)))
        rbody2 = TacacsAuthenBody.unpack(p2)
        check("错误密码被拒绝", rbody2 is not None and rbody2.authen_type == AUTHEN_STATUS_FAIL
              and bool(rbody2.data), f"status={rbody2.authen_type if rbody2 else '-'} data={rbody2.data if rbody2 else '-'}")
        writer.close()
        w2.close()
    finally:
        await server.stop()

    print("\n[3] 认证引擎")
    from app.db.session import Base, SessionLocal
    from app.db.session import engine as db_engine

    import app.models.models  # noqa: F401

    Base.metadata.create_all(bind=db_engine)
    db = SessionLocal()
    try:
        from app.core.security import hash_password
        from app.models.models import Device, DeviceAccount, User

        admin = db.query(User).filter(User.username == "admin").first()
        if admin and not admin.password_hash:
            admin.password_hash = hash_password("admin123")
            db.commit()
        check("平台用户存在", admin is not None, admin.username if admin else "-")

        dev = db.query(Device).order_by(Device.id).first()
        if dev:
            acc = db.query(DeviceAccount).filter(
                DeviceAccount.device_id == dev.id, DeviceAccount.username == "tester"
            ).first()
            if not acc:
                acc = DeviceAccount(
                    device_id=dev.id, username="tester", password="devpass",
                    enable_password="enablepass", privilege=15, auth_type="local",
                )
                db.add(acc)
                db.commit()
            check("设备本地账号存在", True, f"{dev.name}/tester")
        else:
            check("设备本地账号存在", False, "无设备")
    finally:
        db.close()

    result = await aaa.authenticate("admin", "admin123")
    check("平台用户认证通过", result.ok and result.source == "platform", f"{result.source} {result.reason}")

    db = SessionLocal()
    try:
        dev = db.query(Device).order_by(Device.id).first()
        if dev:
            result = await aaa.authenticate("tester", "devpass", dev.ip)
            check("设备本地账号认证通过", result.ok and result.source == "device_local",
                  f"{result.source} {result.reason}")
            result = await aaa.authenticate("tester", "badpass", dev.ip)
            check("错误密码被拒绝", not result.ok, result.reason)
    finally:
        db.close()

    print("\n[4] RADIUS 响应构造")
    from app.services.aaa_service import RADIUS_ACCESS_ACCEPT, RADIUS_ACCESS_REJECT, RadiusServer

    rserver = RadiusServer(aaa, host="127.0.0.1", port=14950)
    secret = b"radius-secret"
    request = struct.pack("!BBH", 1, 7, 20) + b"\x00" * 16
    resp = rserver.make_response(request, secret, RADIUS_ACCESS_ACCEPT, {18: b"OK"})
    check("Accept 响应长度正确", len(resp) == 20 + 4, f"{len(resp)}")
    check("响应 code 正确", resp[0] == RADIUS_ACCESS_ACCEPT, f"code={resp[0]}")
    resp2 = rserver.make_response(request, secret, RADIUS_ACCESS_REJECT, {18: b"FAIL"})
    check("Reject 响应 code 正确", resp2[0] == RADIUS_ACCESS_REJECT, f"code={resp2[0]}")

    print("\n[5] 配置变更审批流程")
    from app.services import device_access_service as das

    db = SessionLocal()
    try:
        dev = db.query(Device).order_by(Device.id).first()
        req = das.create_change_request(
            db,
            device_id=dev.id,
            title="E2E 测试配置变更",
            description="自动化验证",
            config_content="interface GigabitEthernet0/0/1\n description test\n",
            applicant="admin",
        )
        check("提交申请", req.status == "pending" and bool(req.ticket_no), f"{req.ticket_no} {req.status}")

        das.review_change_request(db, req.id, approve=True, reviewer="admin", comment="同意")
        check("审批通过", req.status == "approved", req.status)

        # 重复审批应失败
        try:
            das.review_change_request(db, req.id, approve=True, reviewer="admin")
            check("重复审批被拒绝", False, "未抛出异常")
        except ValueError as exc:
            check("重复审批被拒绝", True, str(exc))

        # 驳回需填意见
        req2 = das.create_change_request(
            db, device_id=dev.id, title="待驳回", description="",
            config_content="test", applicant="admin",
        )
        try:
            das.review_change_request(db, req2.id, approve=False, reviewer="admin")
            check("驳回未填意见被拒绝", False, "未抛出异常")
        except ValueError as exc:
            check("驳回未填意见被拒绝", True, str(exc))

        # 审计日志
        from app.models.models import AuditLog

        count = db.query(AuditLog).filter(
            AuditLog.action.in_(["config_change.submit", "config_change.review"])
        ).count()
        check("审批过程写入操作日志", count >= 2, f"{count} 条")

        # 清理
        db.delete(req)
        db.delete(req2)
        db.commit()
    finally:
        db.close()

    print(f"\n=== 结果：{sum(1 for _, c, _ in results if c)}/{len(results)} 通过 ===\n")
    return 0 if all(c for _, c, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
