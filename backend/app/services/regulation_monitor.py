"""Regulation change monitor (P1.5 core service).

Pipeline:
1. fetch  — pull the latest laws from the FLK v2 search API (first pages per category)
2. sync   — dedupe against regulation_changes and insert new RegulationChange rows
3. match  — match new changes against enabled watchlists (topic/domain keywords)
4. alert  — create ComplianceAlerts with bounded LLM risk analysis

A PostgreSQL advisory lock ensures only one backend replica runs a cycle
at any time; unique constraints make concurrent inserts idempotent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.agents.compliance_risk_agent import COMPLIANCE_DOMAINS
from app.core.database import async_session_factory
from app.models.compliance import ComplianceAlert, ComplianceWatchlist, RegulationChange
from app.services import flk_waf
from app.services.llm_service import get_raw_llm_service

logger = logging.getLogger(__name__)

FLK_BASE = "https://flk.npc.gov.cn"
FLK_SEARCH_API = f"{FLK_BASE}/law-search/search/list"
FLK_DETAIL_URL = f"{FLK_BASE}/detail.html"

_FLK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/search",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}

# flfgCodeId values per category (from FLK API reverse-engineering)
FLK_CATEGORIES: dict[str, list[int]] = {
    "法律": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
    "行政法规": [201, 210, 215],
    "监察法规": [220],
    "司法解释": [311, 320, 330, 340, 350],
}

SXX_STATUS = {1: "已废止", 2: "已修改", 3: "现行有效", 4: "尚未生效", -1: "失效"}

MAX_LLM_ANALYSES_PER_CYCLE = 15
_ADVISORY_LOCK_KEY = 987654321


def _search_payload(code_ids: list[int], page: int, size: int) -> dict[str, Any]:
    return {
        "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
        "gbrqYear": [], "flfgCodeId": code_ids, "zdjgCodeId": [],
        "searchContent": "",
        "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page, "pageSize": size,
    }


async def fetch_recent_flk_laws(
    client: httpx.AsyncClient, pages_per_category: int = 2, page_size: int = 50,
) -> list[dict[str, Any]]:
    """Fetch recent law rows from FLK (first pages per category, newest categories of law)."""
    rows: list[dict[str, Any]] = []
    for law_type, code_ids in FLK_CATEGORIES.items():
        for page in range(1, pages_per_category + 1):
            try:
                resp = await flk_waf.post_json_waf_aware(
                    client,
                    FLK_SEARCH_API,
                    _search_payload(code_ids, page, page_size),
                )
                if resp.status_code != 200:
                    logger.warning("FLK search %s page %d -> HTTP %d",
                                   law_type, page, resp.status_code)
                    break
                if flk_waf.is_challenge(resp):
                    logger.error("FLK search %s page %d still WAF-blocked after solve",
                                 law_type, page)
                    break
                data = resp.json()
                batch = data.get("rows") or []
                if not batch:
                    break
                for row in batch:
                    row["_law_type"] = law_type
                rows.extend(batch)
                if len(batch) < page_size:
                    break
            except Exception as exc:
                logger.warning("FLK search failed (%s page %d): %s", law_type, page, exc)
                break
    return rows


def _row_to_change(row: dict[str, Any]) -> RegulationChange:
    bbbs = str(row.get("bbbs") or row.get("id") or "")
    return RegulationChange(
        dedupe_key=f"flk:{bbbs}",
        title=str(row.get("title") or "")[:512] or "未命名法规",
        law_type=str(row.get("_law_type") or "")[:64],
        publish_date=str(row.get("gbrq") or "")[:32],
        effective_date=str(row.get("sxrq") or "")[:32],
        status=SXX_STATUS.get(row.get("sxx"), "")[:32],
        source="flk",
        source_id=bbbs[:128],
        url=f"{FLK_DETAIL_URL}?{bbbs}" if bbbs else None,
    )


def _within_days(change: RegulationChange, days_back: int) -> bool:
    if not change.publish_date:
        return False
    try:
        pub = datetime.strptime(change.publish_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc,
        )
    except ValueError:
        return False
    return pub >= datetime.now(timezone.utc) - timedelta(days=days_back)


async def sync_regulation_changes(days_back: int = 7) -> list[RegulationChange]:
    """Fetch FLK recent laws and insert unseen ones. Returns the new rows."""
    async with httpx.AsyncClient(headers=_FLK_HEADERS, timeout=30,
                                 follow_redirects=True) as client:
        await flk_waf.attach_shared_cid(client)
        rows = await fetch_recent_flk_laws(client)

    changes = [_row_to_change(r) for r in rows]
    changes = [c for c in changes if c.source_id and _within_days(c, days_back)]
    if not changes:
        return []

    new_changes: list[RegulationChange] = []
    async with async_session_factory() as db:
        keys = [c.dedupe_key for c in changes]
        existing = await db.execute(
            select(RegulationChange.dedupe_key).where(
                RegulationChange.dedupe_key.in_(keys)
            )
        )
        seen = {r[0] for r in existing.fetchall()}
        for c in changes:
            if c.dedupe_key in seen:
                continue
            db.add(c)
            new_changes.append(c)
        try:
            await db.commit()
        except IntegrityError:
            # A concurrent replica inserted some first; fall back to per-row commit
            await db.rollback()
            new_changes = []
            for c in changes:
                try:
                    async with async_session_factory() as db2:
                        db2.add(c)
                        await db2.commit()
                    new_changes.append(c)
                except IntegrityError:
                    continue
    logger.info("regulation monitor: %d new changes synced (%d fetched)",
                len(new_changes), len(changes))
    return new_changes


def _watchlist_keywords(watchlist: ComplianceWatchlist) -> list[tuple[str, str]]:
    """Return [(keyword, domain_label)] for a watchlist."""
    pairs: list[tuple[str, str]] = []
    try:
        for topic in json.loads(watchlist.topics or "[]"):
            if topic and topic.strip():
                pairs.append((topic.strip(), "自选主题"))
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        for domain_id in json.loads(watchlist.compliance_domains or "[]"):
            domain = COMPLIANCE_DOMAINS.get(domain_id)
            if domain:
                for kw in domain["keywords"]:
                    pairs.append((kw, domain["name"]))
    except (json.JSONDecodeError, TypeError):
        pass
    return pairs


def _match_change(
    change: RegulationChange, watchlist: ComplianceWatchlist,
) -> str:
    """Return the first matched keyword, or ''."""
    text = change.title
    for keyword, _label in _watchlist_keywords(watchlist):
        if keyword in text:
            return keyword
    return ""


async def _analyze_impact(
    change: RegulationChange, watchlist: ComplianceWatchlist,
) -> dict[str, Any]:
    """LLM risk analysis of one change for one watchlist. Bounded, best-effort."""
    llm = get_raw_llm_service()
    prompt = f"""新法规动态：{change.title}（{change.law_type}，公布日期 {change.publish_date}，状态：{change.status}）

企业监控配置：行业「{watchlist.industry or '未指定'}」，命中关键词已匹配。

请简要分析该法规变化对此企业的影响，以JSON返回：
```json
{{"impact": "影响说明（1-2句）", "risk_level": "高/中/低", "suggested_actions": ["建议动作1", "建议动作2"]}}
```"""
    try:
        result = await llm.chat(
            messages=[
                {"role": "system", "content": "你是企业合规风险分析专家，回答精炼。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        match = re.search(r"\{[\s\S]*\}", result.get("content", ""))
        if match:
            return json.loads(match.group())
    except Exception as exc:
        logger.warning("alert analysis failed: %s", exc)
    return {
        "impact": f"《{change.title}》于{change.publish_date}公布，与监控关键词相关，建议核实适用性。",
        "risk_level": "中",
        "suggested_actions": ["查看法规原文", "评估业务影响"],
    }


async def match_watchlists(
    changes: list[RegulationChange] | None = None,
    only_user_id: str | None = None,
    days_back: int = 7,
) -> int:
    """Match regulation changes against enabled watchlists and create alerts.

    If changes is None, all changes published within days_back are considered.
    Returns the number of alerts created.
    """
    async with async_session_factory() as db:
        if changes is None:
            since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
            result = await db.execute(
                select(RegulationChange).where(
                    RegulationChange.publish_date >= since,
                ).order_by(RegulationChange.publish_date.desc()).limit(500),
            )
            changes = list(result.scalars().all())

        watch_conditions = [ComplianceWatchlist.enabled.is_(True)]
        if only_user_id:
            watch_conditions.append(ComplianceWatchlist.user_id == only_user_id)
        watchlists = list((await db.execute(
            select(ComplianceWatchlist).where(*watch_conditions)
        )).scalars().all())

        if not changes or not watchlists:
            return 0

        # Existing alert pairs (unique constraint makes inserts idempotent anyway)
        existing_pairs = await db.execute(
            select(ComplianceAlert.watchlist_id, ComplianceAlert.regulation_change_id)
        )
        seen_pairs = {(r[0], r[1]) for r in existing_pairs.fetchall()}

        created = 0
        llm_budget = MAX_LLM_ANALYSES_PER_CYCLE
        for change in changes:
            for watchlist in watchlists:
                keyword = _match_change(change, watchlist)
                if not keyword:
                    continue
                if (watchlist.id, change.id) in seen_pairs:
                    continue

                analysis: dict[str, Any] | None = None
                if llm_budget > 0:
                    analysis = await _analyze_impact(change, watchlist)
                    llm_budget -= 1

                db.add(ComplianceAlert(
                    user_id=watchlist.user_id,
                    watchlist_id=watchlist.id,
                    regulation_change_id=change.id,
                    risk_level=(analysis or {}).get("risk_level", "中"),
                    matched_keyword=keyword[:128],
                    analysis=json.dumps(analysis, ensure_ascii=False) if analysis else None,
                ))
                seen_pairs.add((watchlist.id, change.id))
                created += 1

        for watchlist in watchlists:
            watchlist.last_scan_at = datetime.now(timezone.utc)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            logger.warning("alert insert conflict, skipping remaining pairs")
            return created
    logger.info("regulation monitor: %d alerts created", created)
    return created


async def run_monitor_cycle(days_back: int = 7) -> dict[str, int]:
    """One full monitoring cycle, guarded by an advisory lock.

    Returns {"changes": n, "alerts": m}; zeros if another replica is running.
    """
    async with async_session_factory() as db:
        lock = await db.execute(
            select(func.pg_try_advisory_lock(_ADVISORY_LOCK_KEY))
        )
        acquired = lock.scalar()
        if not acquired:
            logger.info("regulation monitor: another replica holds the lock, skipping")
            return {"changes": 0, "alerts": 0, "skipped": 1}

        try:
            changes = await sync_regulation_changes(days_back=days_back)
            # Rematch the full window: newly created watchlists must also see
            # changes that were synced by an earlier cycle. Unique constraints
            # make re-matching idempotent.
            alerts = await match_watchlists(days_back=days_back)
            return {"changes": len(changes), "alerts": alerts}
        finally:
            await db.execute(select(func.pg_advisory_unlock(_ADVISORY_LOCK_KEY)))
            await db.commit()


async def regulation_monitor_loop(
    interval_hours: float, days_back: int, initial_delay_s: float = 120.0,
) -> None:
    """Background loop started from the app lifespan."""
    await asyncio.sleep(initial_delay_s)
    while True:
        try:
            stats = await run_monitor_cycle(days_back=days_back)
            logger.info("regulation monitor cycle done: %s", stats)
        except Exception as exc:
            logger.error("regulation monitor cycle failed: %s", exc)
        await asyncio.sleep(interval_hours * 3600)
