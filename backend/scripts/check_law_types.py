# -*- coding: utf-8 -*-
"""Check PG law_type / category conventions."""
import asyncio
import sys

sys.path.insert(0, "/app")

from collections import Counter

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.legal_knowledge import Law


async def main() -> None:
    async with async_session_factory() as db:
        result = await db.execute(select(Law.law_type, Law.category))
        rows = result.all()
    print("law_type:", Counter(r[0] for r in rows).most_common(20))
    print("\ncategory:", Counter(r[1] for r in rows).most_common(20))


asyncio.run(main())
