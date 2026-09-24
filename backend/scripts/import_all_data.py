#!/usr/bin/env python3
"""Import all available legal data into PostgreSQL + Milvus.

Imports:
1. chinese_law_sft_train.json (18,832 legal Q&A pairs) → court_cases table (as Q&A knowledge)
2. chinese_law_sft_test.json (500 legal Q&A pairs) → court_cases table
3. hf_laws_*.json (117 full-text laws) → legal_articles table
4. laws.json.zip (22,552 laws) → laws + legal_articles tables (skip existing)
"""

import asyncio
import json
import logging
import os
import re
import sys
import uuid
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATASETS_DIR = DATA_DIR / "datasets"
LAW_DATASETS = DATA_DIR / "law-datasets" / "law-and-regulations"

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/legal_assistant").replace(
    "postgresql+asyncpg://", "postgresql://"
)


async def get_conn():
    return await asyncpg.connect(DB_DSN)


def split_into_articles(content: str) -> list[tuple[str, str]]:
    """Split law content into individual articles."""
    articles = []
    pattern = re.compile(r'(第[一二三四五六七八九十百千零\d]+条[\s ]?)')
    parts = pattern.split(content)
    current_num = ""
    current_text = ""
    for part in parts:
        if pattern.match(part):
            if current_num and current_text.strip():
                articles.append((current_num.strip(), current_text.strip()[:5000]))
            current_num = part.strip()
            current_text = ""
        else:
            current_text += part
    if current_num and current_text.strip():
        articles.append((current_num.strip(), current_text.strip()[:5000]))
    if not articles and content.strip():
        articles.append(("全文", content.strip()[:5000]))
    return articles


async def import_sft_data():
    """Import SFT training/test data as legal Q&A knowledge into legal_qa table."""
    conn = await get_conn()
    total = 0

    for filename, source_tag in [("chinese_law_sft_train.json", "sft_train"), ("chinese_law_sft_test.json", "sft_test")]:
        fpath = DATASETS_DIR / filename
        if not fpath.exists():
            logger.warning("Not found: %s", fpath)
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)

        logger.info("Importing %s: %d records", filename, len(data))
        imported = 0

        for item in data:
            instruction = item.get("instruction", "")
            input_text = item.get("input", "")
            output_text = item.get("output", "")
            if not output_text:
                continue

            question = instruction
            if input_text:
                question = f"{instruction}\n{input_text}"

            qa_id = f"{source_tag}_{uuid.uuid4().hex[:12]}"
            try:
                await conn.execute(
                    """INSERT INTO legal_qa (id, question, answer, category, source, tags)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       ON CONFLICT (id) DO NOTHING""",
                    qa_id,
                    question,
                    output_text,
                    "legal_qa",
                    source_tag,
                    "sft,legal_knowledge",
                )
                imported += 1
            except Exception as e:
                if "duplicate" not in str(e).lower():
                    logger.warning("SFT import error: %s", str(e)[:100])

        logger.info("  Imported: %d / %d", imported, len(data))
        total += imported

    await conn.close()
    return total


async def import_hf_laws():
    """Import HuggingFace full-text law files."""
    conn = await get_conn()
    hf_files = sorted(DATASETS_DIR.glob("hf_laws_*.json"))
    if not hf_files:
        logger.warning("No hf_laws_*.json files found")
        return 0

    logger.info("Importing %d HuggingFace law files", len(hf_files))
    total_laws = 0
    total_articles = 0

    for fpath in hf_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                title = data.get("title", fpath.stem.replace("hf_laws_", ""))
                content = data.get("content", "")
            elif isinstance(data, list) and len(data) > 0:
                title = data[0].get("title", fpath.stem.replace("hf_laws_", ""))
                content = "\n".join(item.get("content", "") for item in data if item.get("content"))
            else:
                continue

            if not content or len(content) < 50:
                continue

            # Check if law already exists
            exists = await conn.fetchval("SELECT 1 FROM laws WHERE name = $1", title)
            if exists:
                continue

            law_id = f"hf_{uuid.uuid4().hex[:12]}"
            await conn.execute(
                """INSERT INTO laws (id, name, law_type, status)
                   VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING""",
                law_id, title, "法律", "有效",
            )

            # Split into articles
            articles = split_into_articles(content)
            for i, (art_num, art_text) in enumerate(articles):
                art_id = f"{law_id}_art_{i}"
                await conn.execute(
                    """INSERT INTO legal_articles (id, law_id, article_number, content, effective_status)
                       VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING""",
                    art_id, law_id, art_num, art_text, "有效",
                )
                total_articles += 1

            total_laws += 1
            if total_laws % 20 == 0:
                logger.info("  Progress: %d laws, %d articles", total_laws, total_articles)

        except Exception as e:
            logger.warning("  Error importing %s: %s", fpath.name, e)

    await conn.close()
    logger.info("HuggingFace import complete: %d laws, %d articles", total_laws, total_articles)
    return total_laws


async def import_laws_zip():
    """Import laws.json.zip (22,552 laws)."""
    zip_path = LAW_DATASETS / "laws.json.zip"
    if not zip_path.exists():
        logger.warning("laws.json.zip not found")
        return 0, 0

    conn = await get_conn()
    with zipfile.ZipFile(zip_path, "r") as z:
        with z.open("laws.json") as f:
            laws = json.load(f)

    logger.info("Importing laws.json.zip: %d laws", len(laws))
    imported = 0
    skipped = 0
    art_total = 0

    for law in laws:
        title = law.get("title", "")
        content = law.get("content", "")
        office = law.get("office", "")
        law_type = law.get("type", "")
        status = law.get("status", "")
        law_id = str(law.get("id", ""))

        if not title:
            skipped += 1
            continue

        exists = await conn.fetchval("SELECT 1 FROM laws WHERE name = $1", title)
        if exists:
            skipped += 1
            continue

        try:
            new_id = law_id if law_id else str(uuid.uuid4())
            await conn.execute(
                """INSERT INTO laws (id, name, law_type, status, issuing_authority)
                   VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING""",
                new_id, title, law_type or "法律", status or "有效", office,
            )

            if content and len(content) > 50:
                articles = split_into_articles(content)
                for i, (art_num, art_text) in enumerate(articles):
                    art_id = f"{new_id}_art_{i}"
                    await conn.execute(
                        """INSERT INTO legal_articles (id, law_id, article_number, content, effective_status)
                           VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING""",
                        art_id, new_id, art_num, art_text, status or "有效",
                    )
                    art_total += 1

            imported += 1
            if imported % 1000 == 0:
                logger.info("  Progress: %d laws, %d articles", imported, art_total)
        except Exception:
            skipped += 1

    await conn.close()
    logger.info("laws.json.zip import: %d imported, %d skipped, %d articles", imported, skipped, art_total)
    return imported, art_total


async def print_stats():
    """Print database statistics."""
    conn = await get_conn()
    tables = [
        ("laws", "法律法规"),
        ("legal_articles", "法律条文"),
        ("court_cases", "裁判文书"),
        ("legal_qa", "法律问答"),
        ("legal_concepts", "法律概念"),
        ("users", "用户"),
    ]
    total = 0
    logger.info("=" * 50)
    logger.info("  数据库统计")
    logger.info("=" * 50)
    for table, label in tables:
        try:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            total += count
            logger.info("  %-20s %12s", label, f"{count:,}")
        except Exception:
            logger.info("  %-20s %12s", label, "(error)")
    logger.info("  %-20s %12s", "合计", f"{total:,}")
    logger.info("=" * 50)
    await conn.close()
    return total


async def main():
    logger.info("=== 法律数据批量导入开始 ===")
    logger.info("Database: %s", DB_DSN.split("@")[-1] if "@" in DB_DSN else DB_DSN)

    await print_stats()

    # 1. Import SFT training data (18,832 Q&A pairs)
    logger.info("")
    logger.info("--- Phase 1: SFT Training Data ---")
    n_sft = await import_sft_data()

    # 2. Import HuggingFace law files (117 laws)
    logger.info("")
    logger.info("--- Phase 2: HuggingFace Law Files ---")
    n_hf = await import_hf_laws()

    # 3. Import laws.json.zip (22,552 laws)
    logger.info("")
    logger.info("--- Phase 3: Laws JSON Zip ---")
    n_zip, n_zip_art = await import_laws_zip()

    # Final stats
    logger.info("")
    logger.info("=== Import Summary ===")
    logger.info("  SFT Q&A pairs:     %s", f"{n_sft:,}")
    logger.info("  HuggingFace laws:  %s", f"{n_hf:,}")
    logger.info("  Laws from zip:     %s (+%s articles)", f"{n_zip:,}", f"{n_zip_art:,}")
    logger.info("")

    await print_stats()


if __name__ == "__main__":
    asyncio.run(main())
