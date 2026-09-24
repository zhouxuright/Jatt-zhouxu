"""
CAIL Dataset Download & Import Script

Downloads open-source CAIL legal datasets and imports them into the database.
Usage:
    cd backend
    python scripts/download_and_import_data.py

Data sources:
- CAIL 2018: 268万 criminal case documents
- CAIL 2019: Judicial exam questions
- CAIL 2021: Legal reasoning samples
- Open-source law articles from GitHub
"""
import asyncio
import json
import os
import sys
import hashlib
import zipfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


# =============================================================================
# CAIL 2018 - Criminal Case Documents (268万)
# =============================================================================

CAIL2018_URLS = [
    "https://github.com/china-ai-law-challenge/CAIL/raw/master/data/train.json",
    "https://github.com/china-ai-law-challenge/CAIL/raw/master/data/valid.json",
    "https://github.com/china-ai-law-challenge/CAIL/raw/master/data/test.json",
]

# Alternative: HuggingFace datasets
CAIL2018_HF = "https://huggingface.co/datasets/china-ai-law-challenge/CAIL/resolve/main/data/train.json"


async def download_cail2018(data_dir: Path) -> bool:
    """Download CAIL 2018 dataset."""
    print("\n" + "=" * 60)
    print("Downloading CAIL 2018 Dataset (Criminal Cases)")
    print("=" * 60)

    data_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        for url in CAIL2018_URLS:
            filename = url.split("/")[-1]
            filepath = data_dir / filename

            if filepath.exists():
                size_mb = filepath.stat().st_size / (1024 * 1024)
                print(f"  [SKIP] {filename} already exists ({size_mb:.1f} MB)")
                continue

            print(f"  [DOWNLOAD] {filename} ...")
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    filepath.write_bytes(resp.content)
                    size_mb = len(resp.content) / (1024 * 1024)
                    print(f"  [OK] {filename} downloaded ({size_mb:.1f} MB)")
                else:
                    print(f"  [FAIL] {filename} - HTTP {resp.status_code}")
            except Exception as exc:
                print(f"  [FAIL] {filename} - {exc}")

    return True


async def download_open_law_data(data_dir: Path) -> bool:
    """Download open-source Chinese law data from GitHub."""
    print("\n" + "=" * 60)
    print("Downloading Open-Source Law Data")
    print("=" * 60)

    data_dir.mkdir(parents=True, exist_ok=True)

    # Download Chinese law articles from awesome-chinese-legal-resources
    law_urls = [
        ("https://raw.githubusercontent.com/pengxiao-song/awesome-chinese-legal-resources/main/README.md",
         "awesome_chinese_legal_resources.md"),
    ]

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        for url, filename in law_urls:
            filepath = data_dir / filename
            if filepath.exists():
                print(f"  [SKIP] {filename} already exists")
                continue

            print(f"  [DOWNLOAD] {filename} ...")
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    filepath.write_bytes(resp.content)
                    print(f"  [OK] {filename} downloaded")
            except Exception as exc:
                print(f"  [FAIL] {filename} - {exc}")

    return True


async def import_cail_data():
    """Import downloaded CAIL data into the database."""
    print("\n" + "=" * 60)
    print("Importing Data into Database")
    print("=" * 60)

    try:
        from app.rag.data_expansion_pipeline import CAILDatasetImporter

        importer = CAILDatasetImporter()

        for dataset_id in ["cail2018", "cail2019", "cail2021"]:
            print(f"\n  Importing {dataset_id}...")
            result = await importer.import_dataset(
                dataset_id=dataset_id,
                data_dir=f"data/{dataset_id}",
                batch_size=200,
            )
            if result.get("success"):
                print(f"  [OK] {dataset_id}: {result.get('total_imported', 0)} imported, "
                      f"{result.get('total_failed', 0)} failed")
            else:
                print(f"  [INFO] {dataset_id}: {result.get('error', 'Unknown error')}")
                if result.get("instructions"):
                    print(f"         {result['instructions']}")

    except Exception as exc:
        print(f"  [ERROR] Import failed: {exc}")


async def import_seed_laws():
    """Import seed law data into PostgreSQL."""
    print("\n" + "=" * 60)
    print("Importing Seed Law Data")
    print("=" * 60)

    try:
        from app.core.database import async_session_factory
        from sqlalchemy import text, select

        async with async_session_factory() as session:
            # Check existing count
            result = await session.execute(text("SELECT COUNT(*) FROM laws"))
            count = result.scalar() or 0
            print(f"  Existing laws: {count}")

            if count > 10:
                print("  [SKIP] Laws already populated")
                return

            # Import comprehensive seed data
            from app.rag.milvus_service import SEED_LEGAL_ARTICLES
            print(f"  Seed articles to import: {len(SEED_LEGAL_ARTICLES)}")

            # Group articles by law
            laws_map: dict[str, list] = {}
            for article in SEED_LEGAL_ARTICLES:
                law_name = article.get("law_name", "未知法律")
                if law_name not in laws_map:
                    laws_map[law_name] = []
                laws_map[law_name].append(article)

            imported_laws = 0
            imported_articles = 0

            for law_name, articles in laws_map.items():
                # Check if law already exists
                existing = await session.execute(
                    text("SELECT id FROM laws WHERE name = :name"),
                    {"name": law_name}
                )
                existing_row = existing.scalar_one_or_none()

                if existing_row:
                    law_id = existing_row
                else:
                    # Determine law type
                    law_type = "civil"
                    if "刑法" in law_name or "刑事" in law_name:
                        law_type = "criminal"
                    elif "宪法" in law_name:
                        law_type = "constitutional"
                    elif "行政" in law_name:
                        law_type = "administrative"
                    elif "劳动" in law_name or "社会" in law_name:
                        law_type = "social"
                    elif "合同" in law_name or "物权" in law_name or "侵权" in law_name:
                        law_type = "civil"

                    # Insert law
                    await session.execute(
                        text("""
                            INSERT INTO laws (id, name, short_name, law_type, category, status)
                            VALUES (:id, :name, :short_name, :type, :category, 'effective')
                            ON CONFLICT DO NOTHING
                        """),
                        {
                            "id": hashlib.md5(law_name.encode()).hexdigest()[:36],
                            "name": law_name,
                            "short_name": law_name[:20],
                            "type": law_type,
                            "category": law_type,
                        }
                    )
                    law_id = hashlib.md5(law_name.encode()).hexdigest()[:36]
                    imported_laws += 1

                # Insert articles
                for article in articles:
                    article_num = article.get("article_number", "")
                    content = article.get("content", "")

                    await session.execute(
                        text("""
                            INSERT INTO legal_articles (id, law_id, article_number, content, title)
                            VALUES (:id, :law_id, :article_number, :content, :title)
                            ON CONFLICT DO NOTHING
                        """),
                        {
                            "id": hashlib.md5(f"{law_name}_{article_num}".encode()).hexdigest()[:36],
                            "law_id": law_id,
                            "article_number": article_num,
                            "content": content,
                            "title": f"{law_name} {article_num}",
                        }
                    )
                    imported_articles += 1

            await session.commit()
            print(f"  [OK] Imported {imported_laws} laws, {imported_articles} articles")

    except Exception as exc:
        print(f"  [ERROR] Seed import failed: {exc}")


async def print_data_summary():
    """Print summary of data in the database."""
    print("\n" + "=" * 60)
    print("Database Data Summary")
    print("=" * 60)

    try:
        from app.core.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as session:
            tables = [
                ("laws", "法律法规"),
                ("legal_articles", "法律条文"),
                ("court_cases", "裁判文书"),
                ("judicial_interpretations", "司法解释"),
                ("legal_concepts", "法律概念"),
                ("users", "用户"),
                ("conversations", "对话"),
                ("messages", "消息"),
            ]

            for table, label in tables:
                try:
                    result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = result.scalar() or 0
                    print(f"  {label}: {count:,}")
                except Exception:
                    print(f"  {label}: (table not found)")

    except Exception as exc:
        print(f"  [ERROR] Summary failed: {exc}")


# =============================================================================
# Main
# =============================================================================

async def main():
    print("=" * 60)
    print("  Legal AI System - Data Download & Import")
    print("=" * 60)

    base_dir = Path(__file__).resolve().parent.parent.parent / "data"

    # Step 1: Download data
    await download_cail2018(base_dir / "cail2018")
    await download_open_law_data(base_dir / "open_law")

    # Step 2: Import seed laws (always run)
    await import_seed_laws()

    # Step 3: Import CAIL datasets
    await import_cail_data()

    # Step 4: Print summary
    await print_data_summary()

    print("\n" + "=" * 60)
    print("  Data download & import complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Run the project: start.bat")
    print("  2. Open browser: http://localhost:8000/docs")
    print("  3. Frontend: http://localhost:3000")
    print("\nTo download more data:")
    print("  - CAIL datasets: https://github.com/china-ai-law-challenge/CAIL")
    print("  - Law data: https://github.com/pengxiao-song/awesome-chinese-legal-resources")
    print("  - Wenshu: https://wenshu.court.gov.cn (requires proxy)")


if __name__ == "__main__":
    asyncio.run(main())
