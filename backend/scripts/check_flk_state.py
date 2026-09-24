# -*- coding: utf-8 -*-
"""Check current PG/Milvus state and /tmp/flk_rows.json for the FLK import."""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

from sqlalchemy import select, func

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle


async def main() -> None:
    if os.path.exists("/tmp/flk_rows.json"):
        with open("/tmp/flk_rows.json", encoding="utf-8") as f:
            rows = json.load(f)
        print(f"/tmp/flk_rows.json: {len(rows)} rows")
    else:
        print("/tmp/flk_rows.json: MISSING (need to re-enumerate)")

    async with async_session_factory() as db:
        laws = (await db.execute(select(func.count(Law.id)))).scalar()
        arts = (await db.execute(select(func.count(LegalArticle.id)))).scalar()
        empty = (
            await db.execute(
                select(func.count())
                .select_from(Law)
                .where(~Law.id.in_(select(LegalArticle.law_id).distinct()))
            )
        ).scalar()
        active = (
            await db.execute(select(func.count(Law.id)).where(Law.status == "active"))
        ).scalar()
        print(f"PG laws: {laws} (active: {active}), articles: {arts}, laws_without_articles: {empty}")

    try:
        from pymilvus import Collection, connections, utility

        connections.connect(host="legal_milvus", port="19530")
        for name in utility.list_collections():
            c = Collection(name)
            print(f"Milvus collection {name}: {c.num_entities} entities")
    except Exception as exc:
        print("Milvus check failed:", str(exc)[:200])


asyncio.run(main())
