# -*- coding: utf-8 -*-
"""Precise work-plan analysis: which FLK rows need insert / content fill / metadata update."""
import asyncio
import json
import sys
from collections import Counter

sys.path.insert(0, "/app")

from sqlalchemy import select, func

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle


async def main() -> None:
    with open("/tmp/flk_rows.json", encoding="utf-8") as f:
        rows = json.load(f)

    print("flxz distribution:", dict(Counter(r.get("flxz") for r in rows)))

    async with async_session_factory() as db:
        # name -> (id, article_count)
        result = await db.execute(
            select(
                Law.id,
                Law.name,
                Law.law_type,
                Law.status,
                func.count(LegalArticle.id).label("n_articles"),
            )
            .outerjoin(LegalArticle, LegalArticle.law_id == Law.id)
            .group_by(Law.id)
        )
        laws = result.all()

    by_name: dict[str, tuple[str, int, str]] = {}
    for lid, name, law_type, status, n in laws:
        by_name[name] = (lid, n, status)

    exact = prefix = miss = 0
    fill_content: list[dict] = []
    insert_new: list[dict] = []
    prefix_rows: list[dict] = []

    for row in rows:
        title = row["title"]
        if title in by_name:
            exact += 1
            lid, n, status = by_name[title]
            if n == 0:
                fill_content.append(row)
        elif any(e.startswith(title + "-") for e in by_name):
            prefix += 1
            prefix_rows.append(row)
        else:
            miss += 1
            insert_new.append(row)

    print(f"\nexact={exact} prefix={prefix} miss={miss}")
    print(f"exact-but-empty-articles (need content fill): {len(fill_content)}")
    print(f"prefix matches: {len(prefix_rows)}")

    print("\ninsert_new by sxx:", dict(Counter(r.get("sxx") for r in insert_new)))
    print("insert_new by flxz:", dict(Counter(r.get("flxz") for r in insert_new)))
    print("\nfirst 15 missing (sxx=3):")
    for r in [x for x in insert_new if x.get("sxx") == 3][:15]:
        print("  ", r["title"], "|", r.get("flxz"), "|", r.get("sxrq"))

    print("\nfill_content list:")
    for r in fill_content[:15]:
        print("  ", r["title"])

    print("\nprefix examples:")
    for r in prefix_rows[:10]:
        print("  ", r["title"])

    # how many of the 143 PG empty laws are covered by FLK rows?
    flk_titles = {r["title"] for r in rows}
    empty_laws = [name for name, (lid, n, s) in by_name.items() if n == 0]
    covered = sum(1 for n in empty_laws if n in flk_titles)
    print(f"\nPG empty laws: {len(empty_laws)}, covered by FLK exact title: {covered}")

    # status value conventions in PG
    print("PG status values:", dict(Counter(s for (_, n, s) in by_name.values())))
    print("PG law_type top:", Counter(t for (_, _, _), t in
          [(k, by_name[k][2]) for k in by_name]).most_common(1))


asyncio.run(main())
