"""SQLAlchemy 透明加密列类型。

把 ``EncryptedText`` / ``EncryptedString`` / ``EncryptedJSON`` 挂到 ORM 模型上后，
业务代码无需感知加解密：写入自动加密、读取自动解密，密文只在数据库落盘时存在。

配合 ``app.core.crypto`` 的向后兼容解密，可实现零停机灰度迁移——上线
代码后历史明文照常读取，再由回填脚本批量加密。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from app.core.crypto import CIPHER_PREFIX, decrypt, encrypt

logger = logging.getLogger(__name__)


class EncryptedText(TypeDecorator):
    """加密的 ``Text`` 列（用于正文、摘要、分析结果等长文本）。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None or value == "":
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None or value == "":
            return value
        return decrypt(value)


class EncryptedString(TypeDecorator):
    """加密的 ``String`` 列（用于 email 等短敏感字段）。

    注意：Fernet 密文比明文长约 100 字符（base64 包装），因此长度上限
    预留为 ``512``，足以覆盖 254 字符的最长合法邮箱。
    """

    impl = String(512)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None or value == "":
            return value
        return encrypt(value)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None or value == "":
            return value
        return decrypt(value)


class EncryptedJSON(TypeDecorator):
    """加密的 JSON 列（用于事实档案、话题分段等结构化敏感数据）。

    落盘为 ``Text`` 密文，读取时自动反序列化回 ``dict`` / ``list``。

    兼容性：历史明文 JSON（未加密）读取时走 ``decrypt()`` 的直通分支，
    仍可正常反序列化，因此可灰度迁移。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if isinstance(value, str):
            # 允许调用方直接传已序列化的字符串
            return encrypt(value)
        return encrypt(json.dumps(value, ensure_ascii=False))

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None or value == "":
            return None
        raw = decrypt(value)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "EncryptedJSON 反序列化失败（密文损坏或密钥不匹配），已返回 None"
            )
            return None


__all__ = ["EncryptedText", "EncryptedString", "EncryptedJSON", "CIPHER_PREFIX"]
