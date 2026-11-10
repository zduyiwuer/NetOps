"""敏感凭据加密：对数据库中的密码 / 密钥做对称加密存储。

采用 SQLAlchemy TypeDecorator 实现，业务代码无需改动——
写入时自动加密，读取时自动解密，字段在 ORM 层面始终是明文字符串。
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_PREFIX = "enc:v1:"
_CIPHER: Optional["Fernet"] = None


def _get_cipher():
    """由 SECRET_KEY 派生 32 字节密钥并构造 Fernet 实例。"""
    global _CIPHER
    if _CIPHER is None:
        from cryptography.fernet import Fernet

        digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
        _CIPHER = Fernet(key)
    return _CIPHER


def encrypt_value(plain: str) -> str:
    """加密单个字符串（供迁移脚本使用）。"""
    if plain is None or plain == "":
        return plain
    if plain.startswith(_PREFIX):
        return plain
    token = _get_cipher().encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_value(stored: str) -> str:
    """解密字符串；若非本系统格式则原样返回（兼容历史明文数据）。"""
    if stored is None or stored == "":
        return stored
    if not stored.startswith(_PREFIX):
        return stored
    try:
        return _get_cipher().decrypt(stored[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except Exception:
        return stored


class EncryptedString(TypeDecorator):
    """自动加解密的字符串列。"""

    impl = String(512)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        return encrypt_value(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return decrypt_value(value)


MASK = "********"


def mask_secret(value: Optional[str]) -> str:
    """把密码字段脱敏为掩码，供接口返回。"""
    return MASK if value else ""
