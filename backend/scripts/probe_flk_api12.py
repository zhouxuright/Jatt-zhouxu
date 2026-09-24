# -*- coding: utf-8 -*-
"""Probe 12: overlap analysis — how many FLK laws already exist in PG laws table?"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law


async def main() -> None:
    with open("/tmp/flk_rows.json", encoding="utf-8") as f:
        rows = json.load(f)
    print(f"FLK rows: {len(rows)}")

    async with async_session_factory() as db:
        result = await db.execute(select(Law.name))
        existing = {r[0] for r in result.fetchall()}
    print(f"PG laws: {len(existing)}")

    exact = prefix = miss = 0
    miss_active = 0
    for row in rows:
        title = row["title"]
        if title in existing:
            exact += 1
        elif any(e.startswith(title + "-") for e in existing):
            prefix += 1
        else:
            miss += 1
            if row.get("sxx") == 3:
                miss_active += 1

    total_active = sum(1 for r in rows if r.get("sxx") == 3)
    print(f"exact match: {exact}")
    print(f"prefix match (split versions): {prefix}")
    print(f"missing: {miss} (of which sxx=3 有效: {miss_active} / {total_active} active)")


asyncio.run(main())
