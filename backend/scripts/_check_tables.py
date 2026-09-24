import asyncio
from sqlalchemy import text
from app.core.database import async_session_factory


async def main():
    async with async_session_factory() as db:
        r = await db.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename IN ('contracts','contract_versions','contract_key_dates',"
            "'contract_reviews','compliance_watchlists','regulation_changes',"
            "'compliance_alerts') ORDER BY tablename"
        ))
        print('tables:', [row[0] for row in r.fetchall()])
        r2 = await db.execute(text('SELECT version_num FROM alembic_version'))
        print('alembic:', [row[0] for row in r2.fetchall()])


asyncio.run(main())
