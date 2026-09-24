# -*- coding: utf-8 -*-
"""FLK (flk.npc.gov.cn) 全量法规下载入库 v2 — 版本感知。

PG laws 表以 (name, effective_date) 唯一约束存多版本；日期口径混杂（公布/施行）。
匹配规则（每个 PG 行绑定至多一个 FLK 版本）：
  1. PG.effective_date == FLK.sxrq  （施行日期口径）
  2. PG.effective_date == FLK.gbrq  （公布日期口径兜底）
  3. PG.effective_date 为 NULL     → 绑定该标题最新版本，并回填日期
未被任何 PG 行绑定的 FLK 版本 → 插入新 Law 行（effective_date = sxrq or gbrq）。

Phases:
  meta     - 版本匹配 + 元数据更新 + 缺失版本插入，写 manifest
  download - 全量下载所有版本 DOCX → /tmp/flk_texts/{bbbs}.json（断点续传）
  insert   - 为无条文的 Law 行解析快照并写入 LegalArticle
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import random
import re
import sys
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/tmp")  # flk_waf_solver lives there
sys.path.insert(0, "/tmp/ddddocr_lib")  # ddddocr for WAF auto-solve

import httpx
from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flk_import")

BASE = "https://flk.npc.gov.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

ROWS_FILE = Path("/tmp/flk_rows.json")
TEXT_DIR = Path("/tmp/flk_texts")
CHECKPOINT_FILE = Path("/tmp/flk_download_checkpoint.json")
MANIFEST_FILE = Path("/tmp/flk_import_manifest.json")

SXX_STATUS = {1: "repealed", 2: "amended", 3: "active", 4: "not_yet_effective"}
LAW_TYPE_MAP = {
    "宪法": "宪法",
    "法律": "法律",
    "法律解释": "法律解释",
    "修正案": "修正案",
    "行政法规": "行政法规",
    "监察法规": "监察法规",
    "司法解释": "司法解释",
    "修改、废止的决定": "修改、废止的决定",
    "有关法律问题和重大问题的决定（部分）": "有关法律问题的决定",
}

ARTICLE_PATTERN = re.compile(r"(第[一二三四五六七八九十百零千\d]+条\s)")
CHAPTER_PATTERN = re.compile(r"^(第[一二三四五六七八九十百零]+[编章节]\s.+)$", re.MULTILINE)


def edition_key(row: dict) -> str | None:
    return row.get("sxrq") or row.get("gbrq")


def load_rows() -> tuple[list[dict], dict[str, list[dict]]]:
    with open(ROWS_FILE, encoding="utf-8") as f:
        rows = json.load(f)
    by_title: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("title"):
            by_title[r["title"]].append(r)
    for t in by_title:
        by_title[t].sort(key=lambda r: (r.get("gbrq") or "", r.get("bbbs")))
    return rows, by_title


async def load_pg_laws() -> list[tuple]:
    """(id, name, effective_date, law_type, status, issuing_authority, n_articles)"""
    async with async_session_factory() as db:
        result = await db.execute(
            select(
                Law.id, Law.name, Law.effective_date, Law.law_type,
                Law.status, Law.issuing_authority,
                func.count(LegalArticle.id).label("n"),
            )
            .outerjoin(LegalArticle, LegalArticle.law_id == Law.id)
            .group_by(Law.id)
        )
        return result.all()


def match_editions(pg_laws, by_title):
    """Deterministic edition matching.

    Returns (matches, inserts, skipped_editions):
      matches: pg_id -> {"row": flk_row, "fill_date": sxrq_or_None}
      inserts: list of flk rows whose edition is absent in PG
    """
    existing_dates: dict[str, set] = defaultdict(set)
    for _, name, eff, *_ in pg_laws:
        if eff:
            existing_dates[name].add(eff)

    matches: dict[str, dict] = {}
    seen: set[tuple[str, str | None]] = set()

    for lid, name, eff, *_ in pg_laws:
        eds = by_title.get(name)
        if not eds:
            continue
        m, fill_date = None, None
        if eff:
            for r in eds:
                if r.get("sxrq") == eff:
                    m = r
                    break
            if m is None:
                for r in eds:
                    if r.get("gbrq") == eff:
                        m = r
                        break
        else:
            m = eds[-1]
            sxrq = m.get("sxrq")
            if sxrq and sxrq not in existing_dates[name]:
                fill_date = sxrq
        if m is not None:
            matches[lid] = {"row": m, "fill_date": fill_date}
            key = edition_key(m)
            if key:
                seen.add((name, key))

    inserts: list[dict] = []
    for title, eds in by_title.items():
        for r in eds:
            key = edition_key(r)
            if (title, key) in seen:
                continue
            if key and key in existing_dates[title]:
                continue  # ambiguous date ownership; PG row already holds it
            inserts.append(r)
            if key:
                seen.add((title, key))
    return matches, inserts


# ---------------------------------------------------------------- phase: meta

async def phase_meta(by_title) -> dict:
    pg_laws = await load_pg_laws()
    matches, inserts = match_editions(pg_laws, by_title)
    logger.info("meta: %d pg rows matched, %d editions to insert (pg rows total %d)",
                len(matches), len(inserts), len(pg_laws))

    stats = {"updated": 0, "unchanged": 0, "inserted": 0}
    dates_being_set: dict[str, set] = defaultdict(set)

    async with async_session_factory() as db:
        for i, (lid, m) in enumerate(matches.items()):
            row = m["row"]
            law = await db.get(Law, lid)
            changed = False
            status = SXX_STATUS.get(row.get("sxx"))
            if status and status != law.status:
                law.status = status
                changed = True
            if row.get("zdjgName") and row.get("zdjgName") != law.issuing_authority:
                law.issuing_authority = row["zdjgName"]
                changed = True
            flxz = LAW_TYPE_MAP.get(row.get("flxz"))
            if law.law_type in (None, "", "其他") and flxz:
                law.law_type = flxz
                law.category = flxz
                changed = True
            fill = m["fill_date"]
            if fill and fill not in dates_being_set[law.name]:
                law.effective_date = fill
                dates_being_set[law.name].add(fill)
                changed = True
            stats["updated" if changed else "unchanged"] += 1
            if (i + 1) % 500 == 0:
                await db.commit()
                logger.info("meta update progress %d/%d", i + 1, len(matches))

        for row in inserts:
            title = row["title"]
            short = title.replace("中华人民共和国", "") or title
            db.add(Law(
                name=title,
                short_name=short[:256],
                law_type=LAW_TYPE_MAP.get(row.get("flxz"), "其他"),
                category=LAW_TYPE_MAP.get(row.get("flxz"), "其他"),
                status=SXX_STATUS.get(row.get("sxx"), "unknown"),
                effective_date=edition_key(row),
                issuing_authority=row.get("zdjgName"),
            ))
            stats["inserted"] += 1
        await db.commit()

    # rebuild manifest: law ids for inserted rows + matched-empty rows
    async with async_session_factory() as db:
        result = await db.execute(
            select(Law.id, Law.name, Law.effective_date, Law.status,
                   func.count(LegalArticle.id).label("n"))
            .outerjoin(LegalArticle, LegalArticle.law_id == Law.id)
            .group_by(Law.id)
        )
        rows2 = result.all()

    by_name_date = {}
    article_counts = {}
    for lid, name, eff, status, n in rows2:
        by_name_date.setdefault((name, eff), lid)
        article_counts[lid] = n

    matched_empty = []
    for lid, m in matches.items():
        if article_counts.get(lid, 0) == 0:
            matched_empty.append({
                "law_id": lid,
                "bbbs": m["row"]["bbbs"],
                "title": m["row"]["title"],
                "flxz": m["row"].get("flxz"),
            })

    inserted_targets = []
    for row in inserts:
        key = (row["title"], edition_key(row))
        hit = by_name_date.get(key)
        if hit is None:
            logger.warning("inserted law not found after commit: %s %s", key[0], key[1])
            continue
        inserted_targets.append({
            "law_id": hit,
            "bbbs": row["bbbs"],
            "title": row["title"],
            "flxz": row.get("flxz"),
        })

    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "matched_empty": matched_empty,
            "inserted": inserted_targets,
            "stats": stats,
        }, f, ensure_ascii=False, indent=1)
    logger.info("meta done: %s | matched_empty=%d inserted_targets=%d",
                stats, len(matched_empty), len(inserted_targets))
    return stats


# ---------------------------------------------------------------- phase: download

def docx_text(data: bytes) -> str:
    zf = zipfile.ZipFile(io.BytesIO(data))
    xml = zf.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def fetch_one(client: httpx.Client, bbbs: str, delay: float = 0.0) -> tuple[str, str, str]:
    """Returns (bbbs, status, detail); status in {ok, fail, waf}."""
    if delay:
        time.sleep(delay * (0.5 + random.random()))
    for attempt in range(3):
        try:
            r = client.get(f"{BASE}/law-search/download/pc", params={"format": "docx", "bbbs": bbbs})
            if "text/html" in r.headers.get("content-type", ""):
                return bbbs, "waf", "challenge page"
            j = r.json()
            url = (j.get("data") or {}).get("url")
            if not url:
                return bbbs, "fail", f"no-url: {j.get('msg')}"
            r2 = client.get(url, timeout=60)
            if r2.status_code != 200:
                return bbbs, "fail", f"http {r2.status_code}"
            if r2.content[:2] != b"PK":
                return bbbs, "fail", f"not docx (len={len(r2.content)})"
            text = docx_text(r2.content)
            if len(text.strip()) < 30:
                return bbbs, "fail", f"empty text (len={len(text)})"
            return bbbs, "ok", text
        except Exception as exc:
            if attempt == 2:
                return bbbs, "fail", f"{type(exc).__name__}: {str(exc)[:120]}"
            time.sleep(2 * (attempt + 1))
    return bbbs, "fail", "unreachable"


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"done": {}, "failed": {}}


def save_checkpoint(cp: dict) -> None:
    tmp = CHECKPOINT_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False)
    tmp.replace(CHECKPOINT_FILE)


def phase_download(by_title, workers: int, limit: int, retry_failed: bool,
                   delay: float = 0.5, chunk_size: int = 120, max_solves: int = 40) -> dict:
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    cp = load_checkpoint()

    row_by_bbbs = {}
    for title, eds in by_title.items():
        for r in eds:
            row_by_bbbs[r["bbbs"]] = r

    todo = []
    for bbbs, row in row_by_bbbs.items():
        if bbbs in cp["done"]:
            continue
        if bbbs in cp["failed"] and not retry_failed:
            continue
        todo.append(bbbs)
    if limit:
        todo = todo[:limit]
    logger.info("download: %d pending (done=%d failed=%d)",
                len(todo), len(cp["done"]), len(cp["failed"]))
    if not todo:
        return {"ok": len(cp["done"]), "failed": len(cp["failed"])}

    try:
        from flk_waf_solver import solve_waf
    except Exception as exc:  # pragma: no cover
        solve_waf = None
        logger.warning("WAF solver unavailable (%s); challenge will abort run", exc)

    cookies: dict[str, str] | None = None
    solves_used = 0
    t0 = time.time()
    processed = 0
    ok_total = len(cp["done"])
    consecutive_bad_solve = 0

    while todo:
        chunk, todo = todo[:chunk_size], todo[chunk_size:]
        client = httpx.Client(
            headers=HEADERS, timeout=30, follow_redirects=True, cookies=cookies,
        )
        redo: list[str] = []
        waf_hits = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_one, client, bbbs, delay): bbbs for bbbs in chunk}
            for fut in as_completed(futures):
                bbbs = futures[fut]
                _, status, detail = fut.result()
                row = row_by_bbbs[bbbs]
                if status == "ok":
                    payload = {
                        "title": row["title"], "bbbs": bbbs,
                        "gbrq": row.get("gbrq"), "sxrq": row.get("sxrq"),
                        "sxx": row.get("sxx"), "flxz": row.get("flxz"),
                        "text": detail,
                    }
                    with open(TEXT_DIR / f"{bbbs}.json", "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False)
                    cp["done"][bbbs] = {"title": row["title"], "chars": len(detail)}
                    cp["failed"].pop(bbbs, None)
                    ok_total += 1
                elif status == "waf":
                    waf_hits += 1
                    redo.append(bbbs)
                else:
                    cp["failed"][bbbs] = {"title": row["title"], "reason": detail}
                    logger.warning("FAIL %s: %s", row["title"], detail)
                processed += 1
                if processed % 25 == 0:
                    save_checkpoint(cp)
                    rate = processed / (time.time() - t0)
                    logger.info("download %d done this run (%.1f/s) ok_total=%d fail=%d pending=%d",
                                processed, rate, ok_total, len(cp["failed"]), len(todo) + len(redo))
        client.close()
        save_checkpoint(cp)

        if redo:
            if solve_waf is None or solves_used >= max_solves:
                logger.error("WAF blocked %d items; solver unavailable/exhausted. "
                             "Remaining items stay pending.", len(redo))
                todo = redo + todo
                break
            solves_used += 1
            logger.info("WAF challenge on %d/%d items; auto-solving (solve %d/%d)...",
                        len(redo), len(chunk), solves_used, max_solves)
            try:
                new_cid = solve_waf()
                cookies = {"wzws_cid": new_cid}
                logger.info("WAF solved, resuming with fresh cookie")
            except Exception as exc:
                logger.error("auto-solve failed: %s", exc)
                todo = redo + todo
                break
            # guard: if a freshly solved cookie still gets challenged heavily, stop
            if waf_hits >= len(chunk) * 0.9:
                consecutive_bad_solve += 1
                if consecutive_bad_solve >= 2:
                    logger.error("WAF keeps challenging right after solve; aborting to "
                                 "preserve checkpoint")
                    todo = redo + todo
                    break
            else:
                consecutive_bad_solve = 0
            todo = redo + todo

    save_checkpoint(cp)
    logger.info("download finished in %.0fs: ok_total=%d fail=%d waf_solves=%d",
                time.time() - t0, ok_total, len(cp["failed"]), solves_used)
    return {"ok": ok_total, "failed": len(cp["failed"])}


# ---------------------------------------------------------------- phase: insert

def parse_articles(content: str) -> list[dict]:
    parts = ARTICLE_PATTERN.split(content)
    if len(parts) <= 1:
        cleaned = re.sub(r"\s+", "", content).strip()
        if cleaned and len(cleaned) > 10:
            return [{"article_number": "", "content": cleaned[:16000], "chapter": ""}]
        return []
    chapter_positions = [(m.start(), m.group(1).strip()) for m in CHAPTER_PATTERN.finditer(content)]
    articles = []
    current_chapter = ""
    for i in range(1, len(parts) - 1, 2):
        raw = CHAPTER_PATTERN.sub("", parts[i + 1] if i + 1 < len(parts) else "")
        cleaned = re.sub(r"\s+", "", raw).strip()
        if not cleaned or len(cleaned) < 5:
            continue
        pos = content.find(parts[i])
        for ch_pos, ch_name in chapter_positions:
            if ch_pos <= pos:
                current_chapter = ch_name
            else:
                break
        articles.append({
            "article_number": parts[i].strip(),
            "content": cleaned[:16000],
            "chapter": current_chapter,
        })
    return articles


async def phase_insert() -> dict:
    with open(MANIFEST_FILE, encoding="utf-8") as f:
        manifest = json.load(f)

    targets = manifest.get("matched_empty", []) + manifest.get("inserted", [])
    logger.info("insert: %d laws to fill (matched_empty=%d inserted=%d)",
                len(targets), len(manifest.get("matched_empty", [])),
                len(manifest.get("inserted", [])))

    stats = {"laws": 0, "articles": 0, "no_snapshot": [], "no_articles": [], "errors": []}
    async with async_session_factory() as db:
        for i, t in enumerate(targets):
            title, lid, bbbs = t["title"], t["law_id"], t["bbbs"]
            path = TEXT_DIR / f"{bbbs}.json"
            if not path.exists():
                stats["no_snapshot"].append(title)
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                articles = parse_articles(payload.get("text", ""))
                if not articles:
                    stats["no_articles"].append(title)
                    continue
                law_type = LAW_TYPE_MAP.get(payload.get("flxz"), "其他")
                short = title.replace("中华人民共和国", "")
                seen: set[str] = set()
                rows = []
                for art in articles:
                    if art["article_number"] in seen:
                        continue
                    seen.add(art["article_number"])
                    rows.append(LegalArticle(
                        law_id=lid,
                        article_number=art["article_number"] or "全文",
                        content=art["content"],
                        chapter=art["chapter"] or None,
                        effective_status="active",
                        tags=f"{law_type},{short}"[:512],
                    ))
                db.add_all(rows)
                await db.commit()
                stats["laws"] += 1
                stats["articles"] += len(rows)
            except Exception as exc:
                await db.rollback()
                stats["errors"].append(f"{title}: {str(exc)[:150]}")
                logger.error("insert failed %s: %s", title, exc)
            if (i + 1) % 20 == 0:
                logger.info("insert progress %d/%d (articles=%d)", i + 1, len(targets), stats["articles"])

    logger.info("insert done: laws=%d articles=%d no_snapshot=%d no_articles=%d errors=%d",
                stats["laws"], stats["articles"], len(stats["no_snapshot"]),
                len(stats["no_articles"]), len(stats["errors"]))
    return stats


# ---------------------------------------------------------------- main

async def run(phase: str, workers: int, limit: int, retry_failed: bool,
             delay: float, chunk: int, max_solves: int) -> None:
    rows, by_title = load_rows()
    n_editions = sum(len(v) for v in by_title.values())
    logger.info("loaded %d rows / %d titles / %d editions", len(rows), len(by_title), n_editions)

    if phase in ("meta", "all"):
        await phase_meta(by_title)
    if phase in ("download", "all"):
        phase_download(by_title, workers, limit, retry_failed,
                       delay=delay, chunk_size=chunk, max_solves=max_solves)
    if phase in ("insert", "all"):
        await phase_insert()

    cp = load_checkpoint()
    print("\n===== SUMMARY =====")
    print(f"downloaded ok: {len(cp['done'])}, failed: {len(cp['failed'])}")
    for bbbs, info in list(cp["failed"].items())[:15]:
        print(f"  FAIL {info['title']}: {info['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["meta", "download", "insert", "all"], default="all")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="per-request throttle seconds (randomized 0.5-1.5x)")
    parser.add_argument("--chunk", type=int, default=120)
    parser.add_argument("--max-solves", type=int, default=40)
    args = parser.parse_args()
    asyncio.run(run(args.phase, args.workers, args.limit, args.retry_failed,
                    args.delay, args.chunk, args.max_solves))


if __name__ == "__main__":
    main()
