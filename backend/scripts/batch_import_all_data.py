#!/usr/bin/env python3
"""Batch import all available legal data into PostgreSQL + Milvus.

This script imports:
1. laws.json.zip (22,552 laws with full text) → PostgreSQL laws + legal_articles tables
2. chinese_laws.parquet (22,552 law metadata) → PostgreSQL laws table (supplement)
3. hf_laws_*.json (117 individual law files) → PostgreSQL legal_articles table

Usage:
    cd backend
    python scripts/batch_import_all_data.py
"""

import asyncio
import json
import logging
import os
import sys
import zipfile
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATASETS_DIR = DATA_DIR / "datasets"
LAW_DATASETS = DATA_DIR / "law-datasets" / "law-and-regulations"


async def get_connection():
    """Get async PostgreSQL connection."""
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def import_laws_json_zip():
    """Import laws.json.zip (22,552 laws with full text) into database."""
    zip_path = LAW_DATASETS / "laws.json.zip"
    if not zip_path.exists():
        logger.warning("laws.json.zip not found at %s", zip_path)
        return 0

    logger.info("=== Importing laws.json.zip ===")
    conn = await get_connection()

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            with z.open("laws.json") as f:
                laws = json.load(f)

        logger.info("Loaded %d laws from zip", len(laws))

        imported = 0
        skipped = 0
        article_count = 0

        for law in laws:
            law_id = str(law.get("id", ""))
            title = law.get("title", "")
            content = law.get("content", "")
            office = law.get("office", "")
            law_type = law.get("type", "")
            status = law.get("status", "")
            publish_date = law.get("publish")
            url = law.get("url", "")

            if not title:
                skipped += 1
                continue

            # Check if already exists
            existing = await conn.fetchval(
                "SELECT id FROM laws WHERE title = $1", title
            )
            if existing:
                skipped += 1
                continue

            # Insert law
            try:
                await conn.execute(
                    """INSERT INTO laws (id, title, law_type, status, promulgating_authority, effective_date)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (id) DO NOTHING""",
                    law_id or None,
                    title,
                    law_type,
                    status,
                    office,
                    publish_date,
                )

                # Split content into articles and insert
                if content and len(content) > 50:
                    articles = split_into_articles(content, title)
                    for i, (art_num, art_text) in enumerate(articles):
                        art_id = f"{law_id}_art_{i}" if law_id else f"art_{imported}_{i}"
                        await conn.execute(
                            """INSERT INTO legal_articles (id, law_id, article_number, content, law_title)
                               VALUES ($1, $2, $3, $4, $5)
                               ON CONFLICT (id) DO NOTHING""",
                            art_id,
                            law_id or None,
                            art_num,
                            art_text,
                            title,
                        )
                        article_count += 1

                imported += 1
                if imported % 500 == 0:
                    logger.info("  Progress: %d laws, %d articles imported...", imported, article_count)

            except Exception as e:
                logger.warning("  Failed to import '%s': %s", title[:50], e)
                skipped += 1

        logger.info("=== laws.json.zip import complete: %d laws, %d articles (skipped %d) ===",
                     imported, article_count, skipped)
        return imported

    finally:
        await conn.close()


def split_into_articles(content: str, law_title: str) -> list[tuple[str, str]]:
    """Split law content into individual articles."""
    import re
    articles = []

    # Pattern: 第X条 ... (until next 第X条 or end)
    pattern = re.compile(r'(第[一二三四五六七八九十百千零\d]+条[  ]?)')
    parts = pattern.split(content)

    current_num = ""
    current_text = ""

    for part in parts:
        if pattern.match(part):
            if current_num and current_text:
                articles.append((current_num.strip(), current_text.strip()))
            current_num = part.strip()
            current_text = ""
        else:
            current_text += part

    if current_num and current_text:
        articles.append((current_num.strip(), current_text.strip()))

    # If no articles found, treat entire content as one block
    if not articles and content.strip():
        articles.append(("全文", content.strip()[:5000]))

    return articles


async def import_hf_laws():
    """Import individual HuggingFace law JSON files."""
    if not DATASETS_DIR.exists():
        logger.warning("Datasets dir not found")
        return 0

    hf_files = list(DATASETS_DIR.glob("hf_laws_*.json"))
    if not hf_files:
        logger.warning("No hf_laws_*.json files found")
        return 0

    logger.info("=== Importing %d HuggingFace law files ===", len(hf_files))
    conn = await get_connection()
    imported = 0

    try:
        for fpath in hf_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    title = data.get("title", fpath.stem)
                    content = data.get("content", "")
                    if content:
                        articles = split_into_articles(content, title)
                        for i, (art_num, art_text) in enumerate(articles):
                            art_id = f"hf_{fpath.stem}_{i}"
                            await conn.execute(
                                """INSERT INTO legal_articles (id, article_number, content, law_title)
                                   VALUES ($1, $2, $3, $4)
                                   ON CONFLICT (id) DO NOTHING""",
                                art_id, art_num, art_text, title,
                            )
                        imported += 1
                elif isinstance(data, list):
                    for item in data:
                        title = item.get("title", "")
                        content = item.get("content", "")
                        if content:
                            articles = split_into_articles(content, title)
                            for i, (art_num, art_text) in enumerate(articles):
                                art_id = f"hf_{fpath.stem}_{i}_{imported}"
                                await conn.execute(
                                    """INSERT INTO legal_articles (id, article_number, content, law_title)
                                       VALUES ($1, $2, $3, $4)
                                       ON CONFLICT (id) DO NOTHING""",
                                    art_id, art_num, art_text, title,
                                )
                            imported += 1

            except Exception as e:
                logger.warning("  Failed to import %s: %s", fpath.name, e)

        logger.info("=== HuggingFace laws import complete: %d files ===", imported)
        return imported

    finally:
        await conn.close()


async def print_stats():
    """Print current database statistics."""
    conn = await get_connection()
    try:
        tables = ["laws", "legal_articles", "court_cases", "legal_concepts", "judicial_interpretations"]
        logger.info("=== Current Database Statistics ===")
        for table in tables:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                logger.info("  %-30s: %s", table, f"{count:,}")
            except Exception:
                logger.info("  %-30s: (table not found)", table)
    finally:
        await conn.close()


async def main():
    logger.info("Starting batch data import...")
    logger.info("Data directory: %s", DATA_DIR)

    await print_stats()

    # Import laws.json.zip
    n1 = await import_laws_json_zip()

    # Import HuggingFace law files
    n2 = await import_hf_laws()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Import Summary:")
    logger.info("  laws.json.zip: %d laws imported", n1)
    logger.info("  HuggingFace files: %d files imported", n2)
    logger.info("=" * 60)
    logger.info("")

    await print_stats()


if __name__ == "__main__":
    asyncio.run(main())
