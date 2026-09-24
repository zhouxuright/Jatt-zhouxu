"""
Content safety filtering and sanitization for user inputs and AI outputs.

Provides protection against prompt injection, harmful content requests,
and ensures legal disclaimers are present in responses.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ContentTooLongError(ValueError):
    """输入超过允许长度。

    **为什么单独建一个异常类型**：长度超限与"内容不安全"是两回事。此前长度
    校验被塞在 ``check_input`` 里，超长会被当成"未通过安全检查"返回 400，调用方
    与用户都无从分辨是"内容违规"还是"文本太长"。分开之后，文档类接口可以把
    超长映射为语义正确的 413 并给出可操作提示。
    """

    def __init__(self, length: int, max_length: int) -> None:
        self.length = length
        self.max_length = max_length
        super().__init__(f"输入内容过长：{length:,} 字符，最大允许 {max_length:,} 字符")


class ContentSafetyFilter:
    """Content safety filter for user inputs and AI outputs."""

    # Pattern for detecting prompt injection attempts
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"you\s+are\s+now",
        r"system\s*prompt",
        r"disregard\s+(all\s+)?above",
        r"new\s+instructions",
        r"forget\s+(all\s+)?your\s+rules",
        r"you\s+must\s+now",
        r"override\s+(all\s+)?instructions",
        r"act\s+as\s+if",
        r"pretend\s+you\s+are",
        r"from\s+now\s+on",
        r"new\s+role",
        r"change\s+your\s+instructions",
    ]

    # Harmful content patterns
    HARMFUL_PATTERNS = [
        r"bomb.*mak",
        r"how\s+to\s+kill",
        r"violence\s+instructions",
        r"drug\s+manufactur",
        r"weapon.*mak",
        r"hack.*into",
        r"steal.*identity",
        r"commit.*crime",
        r"money\s+launder",
    ]

    # PII patterns (phone numbers, emails, ID numbers)
    PII_PATTERNS = {
        "phone": r"\b(?:1[3-9]\d{9})\b",  # Chinese mobile numbers
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "id_card": r"\b(?:\d{15}|\d{17}[\dXx])\b",  # Chinese ID cards
    }

    # Maximum allowed input length for **interactive** input (chat question).
    # 文档类接口（合同审查等）传入的正文远长于此，必须显式传 max_length 覆盖，
    # 否则会被误判为"内容不安全"——这正是合同审查曾对 10150 字法规报 400 的原因。
    MAX_INPUT_LENGTH = 10000

    def validate_length(self, text: str, max_length: int) -> None:
        """仅做长度校验；超限抛 :class:`ContentTooLongError`。

        与内容安全解耦，供文档类接口在调用 ``check_input`` 之前先做一次
        语义明确的长度检查（映射为 413 + 可操作提示）。
        """
        if len(text) > max_length:
            raise ContentTooLongError(len(text), max_length)

    def check_input(
        self,
        text: str,
        max_length: int | None = None,
    ) -> tuple[bool, Optional[str]]:
        """Check user input for safety concerns.

        Args:
            text: User input text to check
            max_length: Length limit for this call. ``None``（默认）使用
                ``MAX_INPUT_LENGTH``（聊天场景）；文档类接口应显式传文档级上限。

        Returns:
            Tuple of (is_safe, reason) where reason is provided if not safe
        """
        limit = self.MAX_INPUT_LENGTH if max_length is None else max_length

        if not text or not text.strip():
            return False, "输入内容不能为空"

        # Check length
        if len(text) > limit:
            return False, f"输入内容过长：{len(text):,} 字符，最大允许 {limit:,} 字符"

        # Check for injection attempts
        for pattern in self.INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"Prompt injection attempt detected: {pattern}")
                return False, "检测到潜在的提示注入行为，请重新表述您的问题"

        # Check for harmful content
        for pattern in self.HARMFUL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"Harmful content detected: {pattern}")
                return False, "抱歉，我无法提供此类信息。请提出合法的法律咨询问题"

        return True, None

    def check_output(self, text: str) -> str:
        """Post-process AI output to ensure safety and compliance.

        Args:
            text: AI-generated output text

        Returns:
            Sanitized output text with disclaimer ensured
        """
        if not text:
            return text

        # Strip any leaked system prompts
        text = self._strip_system_prompts(text)

        # Remove any PII that might have been generated
        text = self._remove_pii(text)

        # Ensure disclaimer is present
        text = self._ensure_disclaimer(text)

        return text

    def sanitize_prompt(self, prompt: str) -> str:
        """Clean and sanitize user prompt before sending to LLM.

        Args:
            prompt: User prompt to sanitize

        Returns:
            Sanitized prompt
        """
        if not prompt:
            return ""

        # Remove injection patterns
        sanitized = prompt
        for pattern in self.INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

        # Normalize whitespace
        sanitized = re.sub(r"\s+", " ", sanitized)
        sanitized = sanitized.strip()

        # Limit length
        if len(sanitized) > self.MAX_INPUT_LENGTH:
            sanitized = sanitized[: self.MAX_INPUT_LENGTH]

        return sanitized

    def _strip_system_prompts(self, text: str) -> str:
        """Remove any accidentally leaked system prompts from output."""
        # Common system prompt leakage patterns
        leakage_patterns = [
            r"(?i)system:\s*你是.*?(?=\n\n|$)",
            r"(?i)assistant:\s*system\s*prompt.*?(?=\n\n|$)",
            r"(?i)\[system\].*?(?=\n\n|$)",
        ]

        for pattern in leakage_patterns:
            text = re.sub(pattern, "", text)

        return text.strip()

    def _remove_pii(self, text: str) -> str:
        """Remove personally identifiable information from text."""
        sanitized = text

        # Replace phone numbers with placeholder
        sanitized = re.sub(
            self.PII_PATTERNS["phone"],
            "[电话号码已隐藏]",
            sanitized,
        )

        # Replace emails with placeholder
        sanitized = re.sub(
            self.PII_PATTERNS["email"],
            "[邮箱已隐藏]",
            sanitized,
        )

        # Replace ID card numbers with placeholder
        sanitized = re.sub(
            self.PII_PATTERNS["id_card"],
            "[身份证号已隐藏]",
            sanitized,
        )

        return sanitized

    def _ensure_disclaimer(self, text: str) -> str:
        """Ensure legal disclaimer is present in the output."""
        from app.prompts.legal_prompts import DISCLAIMER_PREFIX

        disclaimer = f"\n\n---\n**【AI 生成内容声明】**\n{DISCLAIMER_PREFIX}"

        # Check if disclaimer already exists (various forms)
        disclaimer_markers = [
            "AI 生成",
            "仅供参考",
            "不构成正式法律",
            "咨询持证律师",
            "【法律声明】",
        ]

        has_disclaimer = any(marker in text for marker in disclaimer_markers)

        if not has_disclaimer:
            text += disclaimer

        return text


# Singleton instance
_safety_filter_instance: Optional[ContentSafetyFilter] = None


def get_content_safety_filter() -> ContentSafetyFilter:
    """Get singleton ContentSafetyFilter instance."""
    global _safety_filter_instance
    if _safety_filter_instance is None:
        _safety_filter_instance = ContentSafetyFilter()
    return _safety_filter_instance
