import asyncio
import asyncpg


async def main():
    c = await asyncpg.connect("postgresql://postgres:zhouxu@localhost:5432/legal_assistant")
    before = await c.fetchval("SELECT count(*) FROM court_cases WHERE tags LIKE '%claimgen%'")
    await c.execute("DELETE FROM court_cases WHERE tags LIKE '%claimgen%'")
    after = await c.fetchval("SELECT count(*) FROM court_cases WHERE tags LIKE '%claimgen%'")
    total = await c.fetchval("SELECT count(*) FROM court_cases")
    print(f"[cleanup host-5432] deleted claimgen rows: {before} -> {after}; court_cases total now = {total}")
    await c.close()


asyncio.run(main())