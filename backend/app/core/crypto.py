"""字段级加解密与盲索引（数据静态加密，PIPL 合规基线）。

本模块是**唯一**的字段级密钥来源，解决历史缺陷
``services/encryption.py`` 中"未配置 ``ENCRYPTION_KEY`` 时每个进程
各自 ``Fernet.generate_key()`` 随机生成"的问题——在双副本部署下，
副本 A 写入的密文副本 B 无法解密，等同于静默数据损坏。

密钥解析策略（确定性、副本间一致）
----------------------------------
1. 配置了 ``ENCRYPTION_KEY`` → 直接使用（推荐，生产环境应配置）。
2. 未配置 → 由 ``SECRET_KEY`` 经 SHA-256 派生（``SECRET_KEY`` 在所有
   副本间共享，因此派生结果一致）。
3. 生产环境且 ``SECRET_KEY`` 仍是出厂默认值 → **启动即失败**（快速失败），
   避免"看似可用、实则不可恢复"的静默降级。

兼容性
------
历史明文数据（不含 ``enc::v1::`` 前缀）在读取时原样返回，因此可以
灰度迁移：先上线代码（旧数据照常可读），再运行
``scripts/backfill_encryption.py`` 批量加密存量数据。
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 密文前缀标记。用于区分"已加密"与"遗留明文"，支持渐进式迁移与幂等加密。
CIPHER_PREFIX = "enc::v1::"

#: 出厂默认密钥（``config.py`` 中的默认值），生产环境下视为"未配置"。
_PLACEHOLDER_SECRETS = {
    "",
    "change-me-to-a-secure-random-string",
    "change-me",
    "secret",
}

_RAW_ARTICLE = re.compile(r"^[0-9]+$")


# ---------------------------------------------------------------------------
# 密钥解析
# ---------------------------------------------------------------------------

def _is_production() -> bool:
    return (settings.APP_ENV or "").lower() == "production"


def _normalise_key(key: str) -> bytes:
    """把任意字符串密钥规整为合法的 Fernet key（32 字节 url-safe base64）。

    已经是合法 Fernet key 的原样返回；否则按 SHA-256 派生。
    """
    raw = key.encode() if isinstance(key, str) else key
    try:
        if len(base64.urlsafe_b64decode(raw)) == 32:
            return raw
    except Exception:  # noqa: BLE001 - 非法 base64 走派生分支
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _derive_from_secret(secret: str, purpose: str) -> bytes:
    """由 ``SECRET_KEY`` 确定性地派生某用途的 32 字节密钥。"""
    material = f"legal-assistant:{purpose}:v1:{secret}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest())


@functools.lru_cache(maxsize=1)
def resolve_key() -> bytes:
    """解析并缓存 Fernet 密钥（进程内只解析一次，副本间结果一致）。"""
    configured = (settings.ENCRYPTION_KEY or "").strip()
    if configured:
        return _normalise_key(configured)

    secret = (settings.SECRET_KEY or "").strip()
    if secret not in _PLACEHOLDER_SECRETS:
        logger.info(
            "ENCRYPTION_KEY 未配置，已由 SECRET_KEY 确定性派生字段加密密钥"
            "（副本间一致，无需落盘）。生产环境建议显式配置 ENCRYPTION_KEY。"
        )
        return _derive_from_secret(secret, "field-encryption")

    # 既没有 ENCRYPTION_KEY，SECRET_KEY 也是出厂默认值。
    if _is_production():
        raise RuntimeError(
            "生产环境必须配置 ENCRYPTION_KEY（或至少一个非默认的 SECRET_KEY）"
            "——拒绝以随机/默认密钥启动，否则多副本间无法互相解密。"
        )
    logger.warning(
        "开发环境未配置 ENCRYPTION_KEY 与 SECRET_KEY，已使用固定开发密钥。"
        "该密钥不具备安全性，仅用于本地调试。"
    )
    return _derive_from_secret("dev-insecure-fallback", "field-encryption")


@functools.lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    """返回进程级复用的 Fernet 实例。"""
    return Fernet(resolve_key())


@functools.lru_cache(maxsize=1)
def _blind_key() -> bytes:
    """盲索引所用的 HMAC 密钥（与加密密钥域分离）。"""
    return hashlib.sha256(b"legal-assistant:blind-index:v1:" + resolve_key()).digest()


# ---------------------------------------------------------------------------
# 加解密
# ---------------------------------------------------------------------------

def is_encrypted(value: str | None) -> bool:
    """判断字符串是否已是本模块产出的密文。"""
    return bool(value) and isinstance(value, str) and value.startswith(CIPHER_PREFIX)


def encrypt(text: str | None) -> str | None:
    """加密明文。空值原样返回；已加密值幂等返回（不会二次加密）。"""
    if text is None or text == "":
        return text
    if not isinstance(text, str):
        raise TypeError(f"encrypt() 仅接受 str，收到 {type(text)!r}")
    if is_encrypted(text):
        return text
    token = get_fernet().encrypt(text.encode("utf-8")).decode("ascii")
    return CIPHER_PREFIX + token


def decrypt(value: str | None) -> str | None:
    """解密。**向后兼容**：非本模块密文（历史明文）原样返回。"""
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        return value
    if not value.startswith(CIPHER_PREFIX):
        # 遗留明文数据 —— 直接返回，等待回填脚本加密。
        return value
    token = value[len(CIPHER_PREFIX):]
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error(
            "字段解密失败：密钥不匹配或密文损坏（可能更换过 ENCRYPTION_KEY）。"
            "为避免数据丢失，返回占位符。"
        )
        return "[解密失败]"


# ---------------------------------------------------------------------------
# 盲索引（可检索加密 / searchable encryption）
# ---------------------------------------------------------------------------

def blind_index(text: str | None, namespace: str = "") -> str | None:
    """为需要等值查询的敏感字段生成确定性盲索引（64 位十六进制）。

    值本身不可逆，但同一明文（大小写/首尾空白归一化后）必然得到同一摘要，
    因此可用于 ``WHERE email_bidx = :bidx`` 之类的精确检索。
    """
    if text is None or text == "":
        return text
    normalised = " ".join(str(text).split()).lower()
    mac = hmac.new(_blind_key(), f"{namespace}:{normalised}".encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def hash_text(text: str) -> str:
    """无密钥 SHA-256 摘要（用于非敏感、仅需去重的场景，如请求体指纹）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def selftest() -> dict[str, object]:
    """启动期自检：验证加解密与盲索引一致（返回结果供健康检查使用）。"""
    sample = "张三<zhangsan@example.com>合同标的额 100 万元"
    token = encrypt(sample)
    ok_roundtrip = decrypt(token) == sample
    ok_legacy = decrypt("plain-legacy-value") == "plain-legacy-value"
    ok_blind = blind_index("A@B.com ") == blind_index("a@b.com")
    ok_idempotent = encrypt(token) == token
    return {
        "key_source": "env" if (settings.ENCRYPTION_KEY or "").strip() else "derived",
        "roundtrip": ok_roundtrip,
        "legacy_passthrough": ok_legacy,
        "blind_index_stable": ok_blind,
        "idempotent": ok_idempotent,
        "ok": all((ok_roundtrip, ok_legacy, ok_blind, ok_idempotent)),
    }
