"""数据加密服务（facade）——委托给 ``app.core.crypto``。

历史实现存在严重缺陷：未配置 ``ENCRYPTION_KEY`` 时每个进程各自随机
生成密钥，导致多副本部署下互相无法解密。现已统一由 ``app.core.crypto``
负责密钥解析（确定性、副本间一致、生产环境快速失败）。

本模块保留原有导入路径与 API，避免破坏既有引用。
"""

from __future__ import annotations

from app.core.crypto import (
    CIPHER_PREFIX,
    blind_index,
    decrypt,
    encrypt,
    get_fernet,
    hash_text,
    is_encrypted,
    resolve_key,
    selftest,
)


class EncryptionService:
    """加解密与哈希的薄封装（向后兼容 API）。"""

    #: 供调用方判断某字段是否已加密
    CIPHER_PREFIX = CIPHER_PREFIX

    def encrypt(self, text: str) -> str:
        """加密明文（幂等：已是密文时原样返回）。"""
        return encrypt(text)

    def decrypt(self, encrypted: str) -> str:
        """解密；遗留明文原样返回。"""
        return decrypt(encrypted)

    def is_encrypted(self, value: str | None) -> bool:
        return is_encrypted(value)

    @staticmethod
    def hash_text(text: str) -> str:
        """无密钥 SHA-256 十六进制摘要。"""
        return hash_text(text)

    @staticmethod
    def blind_index(text: str, namespace: str = "") -> str | None:
        """确定性盲索引，用于加密字段的等值检索。"""
        return blind_index(text, namespace)


encryption_service = EncryptionService()

__all__ = [
    "EncryptionService",
    "encryption_service",
    "encrypt",
    "decrypt",
    "blind_index",
    "hash_text",
    "is_encrypted",
    "resolve_key",
    "get_fernet",
    "selftest",
]
