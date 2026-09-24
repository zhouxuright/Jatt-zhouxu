"""
AIGC合规管理模块 — 生成式AI服务合规检查与内容安全

功能:
1. 输入内容安全检查（政治敏感/违法/暴力等）
2. 输出内容水印注入（AI生成标识）
3. 用户协议与隐私政策版本管理
4. 算法备案信息维护
5. 内容审核日志
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Content Safety Check
# ============================================================================

class SafetyLevel(str, Enum):
    SAFE = "safe"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass
class SafetyResult:
    """内容安全检查结果"""
    level: SafetyLevel = SafetyLevel.SAFE
    score: float = 0.0  # 0-1, 越高越危险
    reasons: list[str] = field(default_factory=list)
    flagged_terms: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# 敏感词库（简化版 — 生产环境应使用更完整的词库和ML分类器）
SENSITIVE_CATEGORIES = {
    "political": [
        # 政治敏感词 — 简化示例
    ],
    "violence": [
        "杀人", "爆炸", "枪击", "恐怖袭击",
    ],
    "illegal": [
        "贩毒", "制毒", "洗钱", "行贿",
    ],
    "fraud": [
        "假币", "伪造", "诈骗方法",
    ],
}

# 法律免责声明关键词
DISCLAIMER_KEYWORDS = [
    "不构成法律意见", "仅供参考", "建议咨询律师",
    "请以法律法规原文为准", "AI生成内容",
]


class ContentSafetyChecker:
    """内容安全检查器"""

    def __init__(self):
        self._patterns: dict[str, list[re.Pattern]] = {}
        for category, terms in SENSITIVE_CATEGORIES.items():
            self._patterns[category] = [
                re.compile(re.escape(term), re.IGNORECASE) for term in terms
            ]

    def check_input(self, text: str) -> SafetyResult:
        """检查用户输入内容"""
        if not text:
            return SafetyResult()

        result = SafetyResult()
        flagged = []
        reasons = []

        for category, patterns in self._patterns.items():
            for pattern in patterns:
                matches = pattern.findall(text)
                if matches:
                    flagged.extend(matches)
                    reasons.append(f"触发{category}类别: {', '.join(matches[:3])}")

        result.flagged_terms = list(set(flagged))
        result.reasons = reasons

        if len(flagged) > 5:
            result.level = SafetyLevel.BLOCKED
            result.score = 0.9
        elif len(flagged) > 0:
            result.level = SafetyLevel.WARNING
            result.score = 0.5
        else:
            result.level = SafetyLevel.SAFE
            result.score = 0.0

        return result

    def check_output(self, text: str) -> SafetyResult:
        """检查AI输出内容"""
        result = self.check_input(text)

        # 额外检查：确保输出包含免责声明
        has_disclaimer = any(kw in text for kw in DISCLAIMER_KEYWORDS)
        if not has_disclaimer and len(text) > 200:
            result.reasons.append("输出缺少法律免责声明")

        return result


# ============================================================================
# Content Watermarking
# ============================================================================

class ContentWatermarker:
    """AI生成内容水印注入"""

    WATERMARK_PREFIX = "【AI生成内容】"
    WATERMARK_SUFFIX = "\n\n---\n*本内容由AI智能生成，仅供参考，不构成正式法律意见。如有需要，请咨询持证执业律师。*"

    def inject_watermark(self, text: str, metadata: dict | None = None) -> str:
        """为AI生成的内容注入水印标识"""
        if not text:
            return text

        # 添加可见水印
        watermarked = f"{self.WATERMARK_PREFIX}\n\n{text}{self.WATERMARK_SUFFIX}"

        # 添加不可见水印（基于时间戳的hash）
        ts = datetime.now(timezone.utc).isoformat()
        hash_val = hashlib.sha256(f"{ts}:{len(text)}".encode()).hexdigest()[:16]
        invisible = f"\n<!-- aigc:{hash_val} -->"

        return watermarked + invisible

    def verify_watermark(self, text: str) -> dict:
        """验证内容是否包含AI水印"""
        has_visible = self.WATERMARK_PREFIX in text
        invisible_match = re.search(r'<!-- aigc:([a-f0-9]+) -->', text)

        return {
            "has_visible_watermark": has_visible,
            "has_invisible_watermark": invisible_match is not None,
            "watermark_hash": invisible_match.group(1) if invisible_match else None,
            "is_ai_generated": has_visible or invisible_match is not None,
        }


# ============================================================================
# Compliance Registry (算法备案)
# ============================================================================

@dataclass
class AlgorithmFiling:
    """算法备案信息"""
    filing_id: str = ""
    algorithm_name: str = "法律智能辅助系统"
    algorithm_type: str = "生成式人工智能"
    version: str = "1.0.0"
    provider: str = ""
    filing_date: str = ""
    status: str = "待备案"  # 待备案 / 已备案 / 审核中
    description: str = "基于大语言模型的法律智能问答、合同审查、文书生成系统"
    training_data_source: str = "公开法律法规数据 + 开源法律数据集 + 合成数据"
    safety_mechanism: str = "内容安全检查 + 法律免责声明 + 人工审核"


# ============================================================================
# Audit Log
# ============================================================================

@dataclass
class ComplianceAuditEntry:
    """合规审计日志条目"""
    timestamp: str
    user_id: str
    action: str  # "input_check" / "output_check" / "watermark_inject" / "content_blocked"
    input_summary: str = ""
    result: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ComplianceManager:
    """AIGC合规管理器 — 统一管理内容安全、水印、审计"""

    def __init__(self):
        self._safety_checker = ContentSafetyChecker()
        self._watermarker = ContentWatermarker()
        self._filing = AlgorithmFiling()
        self._audit_log: list[ComplianceAuditEntry] = []

    def check_and_process_input(self, text: str, user_id: str = "") -> tuple[SafetyResult, str]:
        """
        检查用户输入并返回处理后的文本。
        如果内容被阻止，返回空字符串。
        """
        result = self._safety_checker.check_input(text)

        self._audit_log.append(ComplianceAuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            action="input_check",
            input_summary=text[:100],
            result=result.level.value,
            metadata={"score": result.score, "flagged": result.flagged_terms},
        ))

        if result.level == SafetyLevel.BLOCKED:
            self._audit_log.append(ComplianceAuditEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_id=user_id,
                action="content_blocked",
                input_summary=text[:100],
                result="blocked",
                metadata={"reasons": result.reasons},
            ))
            return result, ""

        return result, text

    def process_output(self, text: str, user_id: str = "") -> str:
        """处理AI输出：安全检查 + 水印注入"""
        # 安全检查
        safety = self._safety_checker.check_output(text)

        self._audit_log.append(ComplianceAuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            action="output_check",
            result=safety.level.value,
            metadata={"score": safety.score},
        ))

        # 注入水印
        watermarked = self._watermarker.inject_watermark(text)

        self._audit_log.append(ComplianceAuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id=user_id,
            action="watermark_inject",
            result="injected",
        ))

        return watermarked

    def get_filing_info(self) -> dict:
        """获取算法备案信息"""
        return {
            "filing_id": self._filing.filing_id,
            "algorithm_name": self._filing.algorithm_name,
            "algorithm_type": self._filing.algorithm_type,
            "version": self._filing.version,
            "status": self._filing.status,
            "description": self._filing.description,
        }

    def get_audit_summary(self, limit: int = 100) -> dict:
        """获取审计日志摘要"""
        recent = self._audit_log[-limit:]
        blocked = sum(1 for e in recent if e.action == "content_blocked")
        warnings = sum(1 for e in recent if e.result == "warning")

        return {
            "total_entries": len(recent),
            "blocked_count": blocked,
            "warning_count": warnings,
            "latest_entries": [
                {
                    "timestamp": e.timestamp,
                    "user_id": e.user_id[:8] + "..." if e.user_id else "",
                    "action": e.action,
                    "result": e.result,
                }
                for e in recent[-10:]
            ],
        }


# ============================================================================
# Singleton
# ============================================================================

_compliance_manager: ComplianceManager | None = None


def get_compliance_manager() -> ComplianceManager:
    global _compliance_manager
    if _compliance_manager is None:
        _compliance_manager = ComplianceManager()
    return _compliance_manager
