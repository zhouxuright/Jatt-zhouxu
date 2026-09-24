import asyncio
import asyncpg


async def main():
    targets = [
        ("DOCKER-5433", "postgresql://postgres:postgres@localhost:5433/legal_assistant"),
        ("HOST-5432", "postgresql://postgres:zhouxu@localhost:5432/legal_assistant"),
    ]
    for name, dsn in targets:
        print("=" * 60)
        print("=== ", name, " ===")
        try:
            c = await asyncpg.connect(dsn)
            tabs = await c.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
            print("tables:", ", ".join(t["tablename"] for t in tabs))
            for t in ("laws", "legal_articles", "court_cases"):
                try:
                    n = await c.fetchval(f"SELECT count(*) FROM {t}")
                    print(f"  {t} = {n}")
                except Exception as e:
                    print(f"  {t} ERR {e}")
            await c.close()
        except Exception as e:
            print("CONN ERR", e)


asyncio.run(main())