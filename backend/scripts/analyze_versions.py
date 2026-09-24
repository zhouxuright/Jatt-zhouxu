# -*- coding: utf-8 -*-
"""Analyze multi-version rows in PG laws vs FLK (name, sxrq) pairs."""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from collections import Counter, defaultdict

from sqlalchemy import select, func

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle


async def main() -> None:
    with open("/tmp/flk_rows.json", encoding="utf-8") as f:
        rows = json.load(f)

    # latest row per title
    sxx_prio = {3: 0, 4: 1, 2: 2, None: 3, 1: 4, -1: 5}
    latest = {}
    for r in rows:
        t = r.get("title")
        if not t:
            continue
        key = (r.get("gbrq") or "", -sxx_prio.get(r.get("sxx"), 9))
        if t not in latest or key > latest[t][0]:
            latest[t] = (key, r)
    latest = {t: v[1] for t, v in latest.items()}
    print(f"FLK latest unique titles: {len(latest)}")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Law.id, Law.name, Law.effective_date, Law.status,
                   func.count(LegalArticle.id).label("n"))
            .outerjoin(LegalArticle, LegalArticle.law_id == Law.id)
            .group_by(Law.id)
        )
        pg_rows = result.all()

    by_name: dict[str, list] = defaultdict(list)
    for lid, name, eff, status, n in pg_rows:
        by_name[name].append((lid, eff, status, n))

    multi = sum(1 for v in by_name.values() if len(v) > 1)
    print(f"PG names: {len(by_name)}, names with multiple rows: {multi}")
    print("rows-per-name distribution:", dict(Counter(len(v) for v in by_name.values())))

    # FLK (title, sxrq) vs PG (name, effective_date)
    ver_match = date_null = date_diff = no_name = 0
    insert_as_version = []
    for title, r in latest.items():
        sxrq = r.get("sxrq")
        pg = by_name.get(title)
        if not pg:
            no_name += 1
            continue
        if any(eff == sxrq for (_, eff, _, _) in pg):
            ver_match += 1
        elif any(eff is None for (_, eff, _, _) in pg):
            date_null += 1
        else:
            date_diff += 1
            insert_as_version.append((title, sxrq, [e for (_, e, _, _) in pg]))

    print(f"\nversion match (name+date): {ver_match}")
    print(f"name match, PG row has NULL date: {date_null}")
    print(f"name match but different dates: {date_diff}")
    print(f"no name at all: {no_name}")
    print("\nsample different-date cases:")
    for t, s, pgs in insert_as_version[:15]:
        print(f"  {t}: flk_sxrq={s} pg_dates={pgs}")


asyncio.run(main())
