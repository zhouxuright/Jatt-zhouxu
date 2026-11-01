"""Claim-level grounding（主张级引用绑定）。

``citation_verifier`` 解决的是"法条号是否存在"，本模块解决的是
"这句话的结论能不能被证据支撑"——即把答案切成主张句，逐句与法条原文
（或检索片段）做词面覆盖度匹配，输出 ``supported / weak / unsupported``
三态。

设计约束（性能）：
- 仅对**含明确法条引用**或**带检索片段**的主张句做校验，其余跳过；
- 每条主张最多关联 2 条证据，法条原文按 ``law_id`` 一次性读取并缓存；
- 单次校验的主张数由 ``GROUNDING_MAX_CLAIMS`` 上限控制；
- 整体为 best-effort：任何异常都不得影响主回答。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.legal_knowledge import LegalArticle
from app.services.citation_verifier import (
    _parse_article_number,
    _resolve_law,
    extract_citations,
)

logger = logging.getLogger(__name__)

# 含结论/义务/权利/效力的句式才需要证据支撑；纯过渡句直接跳过。
_ASSERTION_RE = re.compile(
    r"应当|必须|不得|可以|有权|无权|享有|承担|构成|无效|有效|解除|终止|"
    r"违约|赔偿|责任|罚款|处罚|期限|届满|适用|依据|根据|判定|认定|属于|视为"
)
_CLAIM_SPLIT_RE = re.compile(r"[。！？\n]+")
_NOISE_RE = re.compile(r"[\s，。！？、；：“”‘’（）()《》【】\-—_·…,.!?;:0-9]+")

# 证据等级（与 citation_verifier 保持一致）
EVIDENCE_LEVELS: dict[str, str] = {
    "statute": "法条原文",
    "judgment": "裁判文书",
    "secondary": "二手解读",
    "none": "无来源",
}


def _bigrams(text: str) -> set[str]:
    """中文字符二元组集合（去掉标点与数字，降低格式差异干扰）。"""
    clean = _NOISE_RE.sub("", text or "")
    if len(clean) < 2:
        return set()
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def _support_score(claim: str, evidence_text: str) -> float:
    """主张二元组被证据覆盖的比例（0~1）。

    用"主张侧覆盖率"而非 Jaccard，因为证据原文通常远长于主张句。
    """
    claim_grams = _bigrams(claim)
    if not claim_grams:
        return 0.0
    evidence_grams = _bigrams(evidence_text)
    if not evidence_grams:
        return 0.0
    return round(len(claim_grams & evidence_grams) / len(claim_grams), 4)


def split_claims(text: str) -> list[str]:
    """把答案切成需要 grounding 的主张句（有上限）。"""
    max_claims = max(1, int(settings.GROUNDING_MAX_CLAIMS))
    min_chars = max(6, int(settings.GROUNDING_MIN_CLAIM_CHARS))
    claims: list[str] = []
    for raw in _CLAIM_SPLIT_RE.split(text or ""):
        sentence = raw.strip().lstrip("#*-• ").strip()
        if len(sentence) < min_chars:
            continue
        if not _ASSERTION_RE.search(sentence) and "《" not in sentence:
            continue
        claims.append(sentence)
        if len(claims) >= max_claims:
            break
    return claims


class _EvidenceStore:
    """按需加载法条原文，law_id 级缓存，避免逐条查询打爆数据库。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._law_cache: dict[str, tuple[str, str, bool] | None] = {}
        self._articles: dict[str, dict[int, str]] = {}

    async def court(self, law_name: str, article_number: int) -> dict[str, Any] | None:
        if law_name not in self._law_cache:
            self._law_cache[law_name] = await _resolve_law(self._db, law_name)
        resolved = self._law_cache[law_name]
        if resolved is None:
            return None
        law_id, full_name, exact = resolved

        if law_id not in self._articles:
            stmt = select(
                LegalArticle.article_number, LegalArticle.content,
            ).where(LegalArticle.law_id == law_id)
            articles: dict[int, str] = {}
            for stored, content in (await self._db.execute(stmt)).all():
                number = _parse_article_number(stored)
                if number is not None and content:
                    articles[number] = content
            self._articles[law_id] = articles

        content = self._articles[law_id].get(article_number)
        if not content:
            return None
        return {
            "law_name": full_name,
            "article_number": article_number,
            "content": content,
            "match_type": "exact" if exact else "fuzzy",
            "level": "statute",
        }


def _classify(score: float) -> str:
    if score >= float(settings.GROUNDING_SUPPORT_THRESHOLD):
        return "supported"
    if score >= float(settings.GROUNDING_WEAK_THRESHOLD):
        return "weak"
    return "unsupported"


async def ground_claims(
    db: AsyncSession,
    text: str,
    passages: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """对答案做主张级 grounding。

    Args:
        db: 数据库会话（用于取法条原文）。
        text: 已生成的回答正文。
        passages: 可选检索片段（dict 需含 ``content``），用于无法条引用的主张句。

    Returns:
        ``None`` 表示未启用/无需校验；否则返回::

            {
              "checked": int,               # 实际校验的主张数
              "skipped": int,               # 无法条引用且无片段而跳过的数量
              "supported_count": int,
              "weak_count": int,
              "unsupported_count": int,
              "grounding_score": float,     # supported / checked
              "level": "ok" | "partial" | "weak" | "none",
              "claims": [...],              # 每条主张的状态与证据摘要
            }
    """
    if not settings.GROUNDING_ENABLED:
        return None

    claims = split_claims(text)
    if not claims:
        return None

    store = _EvidenceStore(db)
    results: list[dict[str, Any]] = []
    skipped = 0

    for claim in claims:
        evidence: list[dict[str, Any]] = []

        # ① 主张句内自带的法条引用优先
        for cit in extract_citations(claim)[:2]:
            number = cit.get("article_number_int")
            if number is None:
                continue
            found = await store.court(cit.get("law_name", ""), number)
            if found:
                evidence.append({
                    "level": "statute",
                    "law_name": found["law_name"],
                    "article_number": number,
                    "snippet": found["content"][:160],
                })

        # ② 无法条引用时，用检索片段兜底
        if not evidence and passages:
            for passage in passages[:3]:
                content = str(passage.get("content") or "")[:2000]
                if content:
                    evidence.append({
                        "level": "judgment" if passage.get("source_type") == "case" else "secondary",
                        "law_name": str(passage.get("law_name") or ""),
                        "article_number": passage.get("article_number"),
                        "snippet": content[:160],
                        "_content": content,
                    })

        if not evidence:
            skipped += 1
            continue

        best_score = 0.0
        for item in evidence:
            source_text = item.pop("_content", None) or item.get("snippet", "")
            best_score = max(best_score, _support_score(claim, str(source_text)))

        status = _classify(best_score)
        results.append({
            "text": claim[:200],
            "status": status,
            "score": best_score,
            "evidence": [
                {k: v for k, v in item.items() if not k.startswith("_")}
                for item in evidence
            ],
        })

    checked = len(results)
    if checked == 0:
        return {
            "checked": 0, "skipped": skipped, "supported_count": 0,
            "weak_count": 0, "unsupported_count": 0, "grounding_score": 0.0,
            "level": "none", "claims": [],
        }

    supported = sum(1 for r in results if r["status"] == "supported")
    weak = sum(1 for r in results if r["status"] == "weak")
    unsupported = checked - supported - weak
    score = round(supported / checked, 4)

    if unsupported == 0:
        level = "ok"
    elif supported == 0:
        level = "weak"
    else:
        level = "partial"

    payload = {
        "checked": checked,
        "skipped": skipped,
        "supported_count": supported,
        "weak_count": weak,
        "unsupported_count": unsupported,
        "grounding_score": score,
        "level": level,
        "claims": results,
    }

    try:
        from app.middleware import metrics
        metrics.GROUNDING_CHECKS.labels(status=level).inc()
    except Exception:  # pragma: no cover - 指标失败不影响主流程
        pass

    return payload


def build_grounding_note(result: dict[str, Any] | None) -> str:
    """把 grounding 结果转成追加到回答末尾的提示（无问题返回空串）。"""
    if not result:
        return ""
    if result.get("level") in ("ok", "none"):
        return ""

    unsupported = [c for c in result.get("claims", []) if c.get("status") == "unsupported"]
    if not unsupported:
        return ""

    lines = [
        "",
        "---",
        "**【证据支撑提示】** 以下结论未能在知识库原文中找到足够支撑，请谨慎采信：",
    ]
    for claim in unsupported[:3]:
        lines.append(f"- {claim.get('text', '')}")
    if result.get("grounding_score", 0) == 0:
        lines.append("（本次回答涉及的法条引用均未通过主张级证据校验。）")
    else:
        lines.append(
            f"（已校验 {result.get('checked', 0)} 条主张，"
            f"其中 {len(unsupported)} 条证据不足。）"
        )
    return "\n".join(lines)


def summarize_grounding(result: dict[str, Any] | None) -> str:
    """压成一行写审计日志。"""
    if not result:
        return "grounding=skipped"
    return (
        f"grounding level={result.get('level')} "
        f"checked={result.get('checked', 0)} "
        f"supported={result.get('supported_count', 0)} "
        f"weak={result.get('weak_count', 0)} "
        f"unsupported={result.get('unsupported_count', 0)} "
        f"score={result.get('grounding_score', 0)}"
    )
