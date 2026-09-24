"""AI content identification watermark service.

Implements China's "人工智能生成合成内容标识办法" (effective September 2025) requirements:
1. Explicit watermark: visible text indicator on all AI-generated content
2. Implicit watermark: invisible metadata embedded in content via Unicode zero-width characters
3. Content provenance: track generation metadata (model, timestamp, user hash)

Reference:
  - https://www.gov.cn (CAC regulations on AI-generated content labeling)
  - Effective date: 2025-09-01
"""

import hashlib
import json
import logging
import struct
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# =============================================================================
# Unicode zero-width characters used for implicit watermark encoding
# =============================================================================
# Binary encoding scheme:
#   00 -> U+200B (zero-width space)
#   01 -> U+200C (zero-width non-joiner)
#   10 -> U+200D (zero-width joiner)
#   11 -> U+FEFF (zero-width no-break space / BOM)

_ZW_CHARS = [
    "​",  # 00 - zero-width space
    "‌",  # 01 - zero-width non-joiner
    "‍",  # 10 - zero-width joiner
    "﻿",  # 11 - zero-width no-break space
]

# Set of all zero-width characters for detection/verification
_ZW_CHAR_SET = set(_ZW_CHARS)

# Magic header to identify our watermark: encodes "AI" (0x41, 0x49) using our scheme
# This is a unique sequence that signals the start of watermark data
_WATERMARK_MAGIC = "​‍‌​"  # 00 10 01 00

# -----------------------------------------------------------------------------
# 紧凑载荷格式（P2-6 瘦身）
# -----------------------------------------------------------------------------
# 旧实现把整段 metadata JSON 编码为二进制，150 字符的 JSON ≈ 1200 bit ≈
# 600 个零宽字符，对文本长度与下游复制粘贴都是明显负担。
#
# 新格式固定 14 字节（112 bit ≈ 56 个零宽字符，缩减约 10 倍）：
#   [0]      版本标记 0x02
#   [1:5]    uint32 BE 生成时间戳（秒）
#   [5:13]   sha256(canonical metadata)[:8] —— 用于完整性校验与去重
#   [13]     内容类型（0=chat 1=document 2=contract）
#
# 兼容性：verify_watermark 先看首字节 —— 0x02 走紧凑格式，否则按旧 JSON
# 格式解析，因此历史内容仍可验证。
_COMPACT_VERSION = 0x02
_COMPACT_LEN = 14
_CONTENT_TYPE_IDS = {"chat": 0, "document": 1, "contract": 2}
_CONTENT_TYPE_NAMES = {v: k for k, v in _CONTENT_TYPE_IDS.items()}


class ContentWatermarkService:
    """AI content identification watermark service.

    Implements China's "人工智能生成合成内容标识办法" requirements:
    1. Explicit watermark: visible text indicator on all AI-generated content
    2. Implicit watermark: invisible metadata embedded in content
    3. Content provenance: track generation metadata
    """

    EXPLICIT_MARKERS = [
        "🤖 本内容由 AI 生成，仅供参考",
        "⚠️ AI 辅助生成内容，不构成法律建议",
    ]

    DOCUMENT_WATERMARK_HEADER = (
        "【AI生成标识】本文书由 AI 辅助生成，仅供法律参考，不构成正式法律建议。"
    )

    DOCUMENT_WATERMARK_FOOTER = (
        "\n\n---\n"
        "📋 本文件由 AI 辅助生成，生成时间：{timestamp}。"
        "内容仅供法律参考，不构成正式法律建议。如需法律意见，请咨询持证律师。"
    )

    CHAT_DISCLAIMER = (
        "\n\n---\n"
        "⚠️ 以上内容由 AI 生成，仅供法律参考，不构成正式法律建议。"
    )

    CONTRACT_DISCLAIMER = (
        "\n\n⚠️ 【AI生成标识】本风险分析由 AI 辅助生成，仅供参考，不构成法律意见。"
    )

    # -------------------------------------------------------------------------
    # Explicit watermark methods
    # -------------------------------------------------------------------------

    def add_explicit_watermark(self, text: str, content_type: str = "chat") -> str:
        """Add visible watermark to AI-generated text.

        Args:
            text: The AI-generated text content.
            content_type: One of 'chat', 'document', 'contract'.

        Returns:
            Text with visible watermark appended/prepended.
        """
        if not text:
            return text

        if content_type == "document":
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            return self.DOCUMENT_WATERMARK_HEADER + "\n\n" + text + self.DOCUMENT_WATERMARK_FOOTER.format(
                timestamp=timestamp
            )
        elif content_type == "contract":
            return text + self.CONTRACT_DISCLAIMER
        else:
            # Default: chat
            return text + self.CHAT_DISCLAIMER

    # -------------------------------------------------------------------------
    # Implicit watermark methods (zero-width character encoding)
    # -------------------------------------------------------------------------

    def add_implicit_watermark(self, text: str, metadata: dict) -> str:
        """Add invisible watermark using Unicode zero-width characters.

        使用紧凑二进制载荷（14 字节 / 56 个零宽字符），仅编码
        **时间戳 + 元数据指纹 + 内容类型**，不再把整段 JSON 塞进正文。

        Args:
            text: The text to embed the watermark in.
            metadata: Dict with keys like 'model', 'generated_at', 'user_hash',
                'content_type'. 仅取其指纹，原值不落盘。

        Returns:
            Text with invisible watermark embedded.
        """
        if not text:
            return text

        payload = self._pack_compact(metadata)

        # Convert binary string to zero-width character sequence
        zw_sequence = _WATERMARK_MAGIC  # Magic header to identify the watermark
        binary_str = "".join(format(byte, "08b") for byte in payload)
        for i in range(0, len(binary_str), 2):
            pair = binary_str[i:i + 2]
            idx = int(pair, 2)
            zw_sequence += _ZW_CHARS[idx]

        # Embed the watermark after the first paragraph or at position 50
        # This distributes the watermark naturally within the text
        insert_pos = self._find_insert_position(text)
        watermarked = text[:insert_pos] + zw_sequence + text[insert_pos:]

        return watermarked

    @staticmethod
    def _pack_compact(metadata: dict) -> bytes:
        """把 metadata 压成 14 字节紧凑载荷（详见模块头部格式说明）。"""
        canonical = json.dumps(
            metadata or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).digest()[:8]

        ts_raw = (metadata or {}).get("generated_at_ts")
        if isinstance(ts_raw, (int, float)) and ts_raw > 0:
            ts = int(ts_raw)
        else:
            # 允许调用方传 ISO 字符串；解析失败则取当前时间
            ts = 0
            iso = (metadata or {}).get("generated_at")
            if isinstance(iso, str) and iso:
                try:
                    ts = int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
                except ValueError:
                    ts = 0
            if not ts:
                ts = int(datetime.now(timezone.utc).timestamp())

        ctype = _CONTENT_TYPE_IDS.get(
            str((metadata or {}).get("content_type", "chat")), 0,
        )
        return (
            bytes([_COMPACT_VERSION])
            + struct.pack(">I", ts & 0xFFFFFFFF)
            + digest
            + bytes([ctype])
        )

    def verify_watermark(self, text: str) -> dict:
        """Extract and verify watermark from text.

        Scans for the magic header, then decodes the zero-width character
        sequence. 支持紧凑格式（14 字节，首字节 0x02）与旧 JSON 格式。

        Args:
            text: Text to verify.

        Returns:
            Dict with keys:
                is_ai_generated (bool): Whether the watermark was found.
                format (str): "compact" | "legacy-json".
                generated_at (str): ISO timestamp of generation.
                user_hash (str): 元数据指纹（前 16 位十六进制）。
                content_type (str): Type of content (chat, document, contract).
                model (str): 仅旧格式可还原，紧凑格式为 "unknown"。
        """
        if not text:
            return {"is_ai_generated": False}

        # Find the magic header
        magic_pos = text.find(_WATERMARK_MAGIC)
        if magic_pos == -1:
            return {"is_ai_generated": False}

        # Extract zero-width characters after the magic header
        start = magic_pos + len(_WATERMARK_MAGIC)
        binary_str = ""
        for ch in text[start:]:
            if ch in _ZW_CHAR_SET:
                idx = _ZW_CHARS.index(ch)
                binary_str += format(idx, "02b")
            else:
                # Stop at the first non-zero-width character after the watermark
                break

        if len(binary_str) < 8:
            return {"is_ai_generated": False}

        # Convert binary string back to characters
        # Only use complete 8-bit groups
        complete_bits = (len(binary_str) // 8) * 8
        binary_str = binary_str[:complete_bits]

        raw = bytes(
            int(binary_str[i:i + 8], 2)
            for i in range(0, len(binary_str), 8)
            if len(binary_str[i:i + 8]) == 8
        )

        # 紧凑格式：首字节为版本标记，且长度足够
        if raw and raw[0] == _COMPACT_VERSION and len(raw) >= _COMPACT_LEN:
            return self._unpack_compact(raw)

        # 旧格式：整体是 JSON 文本
        try:
            metadata = json.loads(raw.decode("utf-8", errors="ignore"))
            return {
                "is_ai_generated": True,
                "format": "legacy-json",
                "model": metadata.get("model", "unknown"),
                "generated_at": metadata.get("generated_at", ""),
                "user_hash": metadata.get("user_hash", ""),
                "content_type": metadata.get("content_type", "chat"),
            }
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            logger.warning("Watermark found but metadata could not be decoded")
            return {"is_ai_generated": True, "format": "unknown", "model": "unknown"}

    @staticmethod
    def _unpack_compact(raw: bytes) -> dict:
        """解析紧凑载荷。"""
        ts = struct.unpack(">I", raw[1:5])[0]
        digest = raw[5:13].hex()
        ctype = raw[13]
        try:
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            iso = ""
        return {
            "is_ai_generated": True,
            "format": "compact",
            "model": "unknown",
            "generated_at": iso,
            "user_hash": digest,
            "content_type": _CONTENT_TYPE_NAMES.get(ctype, "chat"),
        }

    # -------------------------------------------------------------------------
    # Metadata builder
    # -------------------------------------------------------------------------

    def get_watermark_metadata(
        self,
        model: str,
        user_id: str,
        content_type: str,
    ) -> dict:
        """Build watermark metadata dict.

        Args:
            model: The LLM model name (e.g., 'deepseek-chat').
            user_id: The user's ID (will be hashed for privacy).
            content_type: Type of content ('chat', 'document', 'contract').

        Returns:
            Dict with model, generated_at, user_hash, content_type.
        """
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
        return {
            "model": model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "user_hash": user_hash,
            "content_type": content_type,
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _find_insert_position(text: str, preferred: int = 50) -> int:
        """Find a good position to insert the watermark.

        Tries to insert after the first newline or at the preferred position,
        whichever comes first. Falls back to position 0 if text is too short.

        Args:
            text: The text to find an insert position in.
            preferred: Preferred insert position.

        Returns:
            The insert position index.
        """
        if len(text) <= preferred:
            # For short texts, insert after first newline or at end
            newline_pos = text.find("\n")
            if newline_pos != -1:
                return newline_pos + 1
            return len(text)

        # For longer texts, try the preferred position
        newline_pos = text.find("\n", 0, preferred + 1)
        if newline_pos != -1:
            return newline_pos + 1
        return preferred


# =============================================================================
# Singleton accessor
# =============================================================================

_watermark_service: ContentWatermarkService | None = None


def get_content_watermark_service() -> ContentWatermarkService:
    """Return a singleton ContentWatermarkService instance."""
    global _watermark_service
    if _watermark_service is None:
        _watermark_service = ContentWatermarkService()
    return _watermark_service
