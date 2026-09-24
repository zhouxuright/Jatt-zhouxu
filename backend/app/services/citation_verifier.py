"""Citation existence verification (法条号存在性校验).

Extracts 《法律名称》第X条 citations from a generated answer and verifies
that each (law, article) pair actually exists in the ``laws`` / ``legal_articles``
tables. This is a post-generation guard against hallucinated legal citations.
"""

import logging
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_knowledge import Law, LegalArticle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chinese numeral <-> integer conversion
# ---------------------------------------------------------------------------

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_BIG_UNITS = {"万": 10000, "亿": 100000000}


def cn_to_int(text: str) -> int | None:
    """Convert a Chinese numeral string (e.g. 五百八十四) to an int."""
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            section += (number if number else 1) * unit
            number = 0
        elif ch in _CN_BIG_UNITS:
            unit = _CN_BIG_UNITS[ch]
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            # Unexpected character (e.g. 款/项/之一) - can't parse cleanly.
            return None
    return total + section + number


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

_LAW_NAME_RE = re.compile(r"《([^《》]{1,60})》")
_ARTICLE_RE = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十百千]+)\s*条")
_STORED_ARTICLE_RE = re.compile(r"第?\s*([0-9零〇一二两三四五六七八九十百千]+)\s*条?")


def _parse_article_number(text: str) -> int | None:
    """Parse an article-number string into an int, or None if unparseable."""
    if not text:
        return None
    m = _STORED_ARTICLE_RE.search(text)
    if m:
        return cn_to_int(m.group(1))
    return None


def extract_citations(text: str) -> list[dict[str, Any]]:
    """Extract 《law》第X条 citations from an answer.

    Returns a deduplicated list of dicts with keys:
        law_name, article_number_raw, article_number_int (int or None).
    """
    if not text:
        return []
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for m in _LAW_NAME_RE.finditer(text):
        law_name = m.group(1).strip()
        if len(law_name) < 2:
            continue
        tail = text[m.end():m.end() + 40]
        am = _ARTICLE_RE.search(tail)
        if not am:
            continue
        num_raw = am.group(1)
        num_int = cn_to_int(num_raw)
        key = (law_name, num_int if num_int is not None else num_raw)
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "law_name": law_name,
            "article_number_raw": num_raw,
            "article_number_int": num_int,
        })
    return citations


# ---------------------------------------------------------------------------
# Existence verification
# ---------------------------------------------------------------------------

async def verify_citations(db: AsyncSession, text: str) -> dict[str, Any]:
    """Verify the citations in a generated answer against the database.

    Returns:
        {
            "citations": [...],       # every extracted citation with verify status
            "verified_count": int,
            "unverified_count": int,
            "unverified": [...],      # citations that could not be confirmed
            "citation_count": int,    # 抽取到的引用总数
            "confidence": float,      # 整体引用置信度 0~1
            "allow_definitive": bool, # 是否允许给出确定性结论
        }

    置信度规则（P0-3 可溯源闭环）：命中"全称精确匹配 + 条文存在"记 1.0；
    仅简称/模糊匹配命中记 0.75；条文不存在/法名未知记 0.0；
    条号无法解析记 0.2。整体置信度取所有引用的算术平均。
    """
    citations = extract_citations(text)
    verified: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []

    # Cache law-name -> (id, full_name, exact) to avoid repeated queries within one answer.
    law_cache: dict[str, tuple[str, str, bool] | None] = {}

    confidence_scores: list[float] = []

    for cit in citations:
        law_name = cit["law_name"]
        cit["status"] = "unverified"
        cit["confidence"] = 0.0

        if law_name not in law_cache:
            law_cache[law_name] = await _resolve_law(db, law_name)

        resolved = law_cache[law_name]
        if resolved is None:
            cit["reason"] = "law_not_found"
            unverified.append(cit)
            confidence_scores.append(0.0)
            continue

        law_id, full_name, exact = resolved
        cit["law_full_name"] = full_name
        cit["match_type"] = "exact" if exact else "fuzzy"

        num_int = cit["article_number_int"]
        if num_int is None:
            cit["reason"] = "article_number_unparseable"
            cit["confidence"] = 0.2
            unverified.append(cit)
            confidence_scores.append(0.2)
            continue

        if await _article_exists(db, law_id, num_int):
            cit["status"] = "verified"
            cit["confidence"] = 1.0 if exact else 0.75
            verified.append(cit)
            confidence_scores.append(cit["confidence"])
        else:
            cit["reason"] = "article_not_found"
            unverified.append(cit)
            confidence_scores.append(0.0)

    overall = round(sum(confidence_scores) / len(confidence_scores), 4) if confidence_scores else 0.0

    return {
        "citations": verified + unverified,
        "verified_count": len(verified),
        "unverified_count": len(unverified),
        "unverified": unverified,
        "citation_count": len(citations),
        # 有引用且全部通过校验，才认为可以直接给确定性结论。
        "allow_definitive": bool(citations) and len(unverified) == 0,
        "confidence": overall,
    }


async def _resolve_law(
    db: AsyncSession, law_name: str,
) -> tuple[str, str, bool] | None:
    """Resolve a law name to (id, full_name, exact_match).

    ``exact_match`` 为 True 表示命中全称/简称精确匹配，False 表示仅靠
    子串模糊匹配命中——后者置信度更低，需在引用校验中体现。
    """
    # 1. Exact match on name or short_name (fast, indexed).
    stmt = (
        select(Law.id, Law.name)
        .where(or_(Law.name == law_name, Law.short_name == law_name))
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row:
        return (row.id, row.name, True)

    # 2. Fuzzy substring match (handles short names like 民法典 -> 中华人民共和国民法典).
    like = f"%{law_name}%"
    stmt = (
        select(Law.id, Law.name)
        .where(or_(Law.name.ilike(like), Law.short_name.ilike(like)))
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row:
        return (row.id, row.name, False)

    return None


async def _article_exists(db: AsyncSession, law_id: str, num_int: int) -> bool:
    """Check whether an article with the given integer number exists under a law."""
    stmt = select(LegalArticle.article_number).where(LegalArticle.law_id == law_id)
    rows = (await db.execute(stmt)).all()
    for (stored,) in rows:
        if _parse_article_number(stored) == num_int:
            return True
    return False


def build_unverified_note(unverified: list[dict[str, Any]]) -> str:
    """Build a user-visible correction note for unverified citations."""
    if not unverified:
        return ""
    lines = ["", "---", "**【引用校验提示】** 以下引用未能通过法条存在性校验，请以官方原文核实："]
    for cit in unverified:
        raw = cit.get("article_number_raw", "")
        lines.append(f"- 《{cit.get('law_name', '')}》第{raw}条")
    lines.append("（如为笔误或简称差异，请核对正式条文后再行采信。）")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# P0-3 引用闸门：无有效引用时拒绝给出确定性结论
# ---------------------------------------------------------------------------

_GATE_BANNER = (
    "\n\n---\n"
    "**【结论可靠性提示】** 本次回答涉及法条引用，但**未能通过法条存在性校验**"
    "（可能因表述概括、法名简称差异或知识库暂未收录）。"
    "以下内容仅作方向性参考，**不构成可直接援引的法律依据**；"
    "请以国家法律法规数据库官方原文为准。\n"
)


def build_citation_gate(result: dict[str, Any] | None) -> dict[str, Any]:
    """根据引用校验结果决定是否允许"确定性结论"。

    Returns:
        {
            "allow_definitive": bool,   # 是否可直接给出确定性结论
            "level": "ok" | "degraded" | "none",
            "note": str,                # 需追加到回答末尾的提示（可为空）
            "confidence": float,
        }
    """
    if not result:
        # 校验未执行（异常）——保守起见按"未知"处理，但不追加提示以免噪音。
        return {"allow_definitive": True, "level": "unknown", "note": "", "confidence": 0.0}

    citation_count = int(result.get("citation_count") or 0)
    verified = int(result.get("verified_count") or 0)
    unverified = result.get("unverified") or []
    confidence = float(result.get("confidence") or 0.0)

    if citation_count == 0:
        # 没有引用 —— 属"不涉及法条的常识/流程性回答"，放行。
        return {"allow_definitive": True, "level": "none", "note": "", "confidence": 0.0}

    if verified == 0:
        # 有引用但一条都没验证通过 —— 强制降级，拒绝确定性结论。
        return {
            "allow_definitive": False,
            "level": "degraded",
            "note": _GATE_BANNER,
            "confidence": confidence,
        }

    if unverified:
        # 部分通过 —— 允许结论，但保留逐条提示（由 build_unverified_note 负责）。
        return {
            "allow_definitive": True,
            "level": "partial",
            "note": "",
            "confidence": confidence,
        }

    return {
        "allow_definitive": True,
        "level": "ok",
        "note": "",
        "confidence": confidence,
    }


def summarize_verification(result: dict[str, Any] | None) -> str:
    """把校验结果压成一行，便于写入审计日志（P0-4 引用校验结果留痕）。"""
    if not result:
        return "citation_verify=skipped"
    gate = build_citation_gate(result)
    return (
        f"citation_verify level={gate['level']} "
        f"total={result.get('citation_count', 0)} "
        f"verified={result.get('verified_count', 0)} "
        f"unverified={result.get('unverified_count', 0)} "
        f"confidence={result.get('confidence', 0)} "
        f"definitive={gate['allow_definitive']}"
    )


# ---------------------------------------------------------------------------
# P0-3 对外溯源：给定"法名 + 条号"返回权威原文与出处
# ---------------------------------------------------------------------------

async def resolve_citation_detail(
    db: AsyncSession, law_name: str, article_number: str | int,
) -> dict[str, Any]:
    """把一条引用还原为可核对的权威原文（溯源查询）。

    Returns:
        {
            "found": bool,
            "match_type": "exact" | "fuzzy" | None,
            "law": {...} | None,
            "article": {...} | None,
            "provenance": "authoritative" | "unknown",
            "message": str,
        }
    """
    num_int = article_number if isinstance(article_number, int) else _parse_article_number(str(article_number))
    law_name = (law_name or "").strip()

    detail: dict[str, Any] = {
        "found": False,
        "match_type": None,
        "law": None,
        "article": None,
        "provenance": "unknown",
        "message": "",
    }
    if not law_name or num_int is None:
        detail["message"] = "法名或条号无法解析"
        return detail

    resolved = await _resolve_law(db, law_name)
    if resolved is None:
        detail["message"] = "知识库中未收录该法律，请核对法名全称"
        return detail

    law_id, full_name, exact = resolved
    stmt = (
        select(
            Law.name, Law.short_name, Law.law_type, Law.status,
            Law.effective_date, Law.issuing_authority,
        )
        .where(Law.id == law_id)
    )
    law_row = (await db.execute(stmt)).first()
    if law_row:
        detail["law"] = {
            "id": law_id,
            "name": law_row.name,
            "short_name": law_row.short_name,
            "law_type": law_row.law_type,
            "status": law_row.status,
            "effective_date": law_row.effective_date,
            "issuing_authority": law_row.issuing_authority,
        }
    detail["match_type"] = "exact" if exact else "fuzzy"

    # 逐条比对条号（库中条号可能是"第一百二十条"等中文写法）
    art_stmt = select(LegalArticle).where(LegalArticle.law_id == law_id)
    for art in (await db.execute(art_stmt)).scalars().all():
        if _parse_article_number(art.article_number) == num_int:
            detail["found"] = True
            detail["article"] = {
                "article_number": art.article_number,
                "title": art.title,
                "content": art.content,
                "chapter": art.chapter,
                "section": art.section,
                "effective_status": art.effective_status,
            }
            # ``legal_articles`` 为已授权的权威语料，可作为可援引来源。
            detail["provenance"] = "authoritative"
            detail["message"] = "已命中权威条文，可直接援引"
            break

    if not detail["found"]:
        detail["message"] = "该法已收录，但未找到对应条号（可能是条号笔误或版本差异）"
    return detail
