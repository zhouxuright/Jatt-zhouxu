#!/usr/bin/env python3
"""
批量导入 mass_expansion 生成的合成数据到 PostgreSQL 数据库。

将 data/mass_expansion/ 目录下的 JSONL 文件批量导入到对应的数据库表。
使用断点续传和批量插入优化性能。

使用方法:
    cd backend
    python scripts/import_mass_expansion.py
    python scripts/import_mass_expansion.py --table qa_pairs
    python scripts/import_mass_expansion.py --table cases
    python scripts/import_mass_expansion.py --dry-run
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("import_mass_expansion.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mass_expansion"

# Batch size for bulk inserts
BATCH_SIZE = 1000


async def import_qa_pairs(dry_run: bool = False):
    """导入合成QA对到数据库"""
    qa_file = DATA_DIR / "synthetic_qa_pairs.jsonl"
    if not qa_file.exists():
        logger.warning(f"文件不存在: {qa_file}")
        return 0

    logger.info(f"开始导入QA对: {qa_file}")

    from app.core.database import async_session_factory
    from sqlalchemy import text

    count = 0
    batch = []

    with open(qa_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                batch.append(record)
                count += 1

                if len(batch) >= BATCH_SIZE:
                    if not dry_run:
                        await _bulk_insert_qa(batch)
                    batch = []

                    if count % 100000 == 0:
                        logger.info(f"  已处理 {count:,} 条QA对")

            except Exception as e:
                logger.warning(f"解析行失败: {e}")
                continue

    # Insert remaining
    if batch and not dry_run:
        await _bulk_insert_qa(batch)

    logger.info(f"QA对导入完成: {count:,} 条")
    return count


async def _bulk_insert_qa(records: list[dict]):
    """批量插入QA对"""
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as session:
        for r in records:
            try:
                await session.execute(
                    text("""
                        INSERT INTO legal_knowledge (id, type, domain, title, content, keywords, source, metadata_json)
                        VALUES (:id, :type, :domain, :title, :content, :keywords, :source, :metadata)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": r.get("id", str(uuid.uuid4())),
                        "type": r.get("type", "qa_pair"),
                        "domain": r.get("domain", ""),
                        "title": r.get("question", "")[:200],
                        "content": r.get("answer", ""),
                        "keywords": json.dumps(r.get("keywords", []), ensure_ascii=False),
                        "source": r.get("source", "synthetic"),
                        "metadata": json.dumps({
                            "question": r.get("question", ""),
                            "subarea": r.get("subarea", ""),
                            "quality_score": r.get("quality_score", 0),
                        }, ensure_ascii=False),
                    }
                )
            except Exception as e:
                logger.debug(f"插入失败(可能重复): {e}")
        await session.commit()


async def import_cases(dry_run: bool = False):
    """导入合成案例到数据库"""
    case_file = DATA_DIR / "synthetic_cases.jsonl"
    if not case_file.exists():
        logger.warning(f"文件不存在: {case_file}")
        return 0

    logger.info(f"开始导入案例: {case_file}")

    from app.core.database import async_session_factory
    from sqlalchemy import text

    count = 0
    batch = []

    with open(case_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                batch.append(record)
                count += 1

                if len(batch) >= BATCH_SIZE:
                    if not dry_run:
                        await _bulk_insert_cases(batch)
                    batch = []

                    if count % 100000 == 0:
                        logger.info(f"  已处理 {count:,} 条案例")

            except Exception as e:
                logger.warning(f"解析行失败: {e}")
                continue

    if batch and not dry_run:
        await _bulk_insert_cases(batch)

    logger.info(f"案例导入完成: {count:,} 条")
    return count


async def _bulk_insert_cases(records: list[dict]):
    """批量插入案例"""
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as session:
        for r in records:
            try:
                await session.execute(
                    text("""
                        INSERT INTO legal_knowledge (id, type, domain, title, content, keywords, source, metadata_json)
                        VALUES (:id, :type, :domain, :title, :content, :keywords, :source, :metadata)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": r.get("id", str(uuid.uuid4())),
                        "type": r.get("type", "court_case"),
                        "domain": r.get("domain", ""),
                        "title": r.get("title", "")[:200],
                        "content": json.dumps({
                            "summary": r.get("summary", ""),
                            "facts": r.get("facts", ""),
                            "judgment": r.get("judgment", ""),
                        }, ensure_ascii=False),
                        "keywords": json.dumps(r.get("keywords", []), ensure_ascii=False),
                        "source": r.get("source", "synthetic_case"),
                        "metadata": json.dumps({
                            "case_number": r.get("case_number", ""),
                            "court": r.get("court", ""),
                            "year": r.get("year", ""),
                            "subarea": r.get("subarea", ""),
                        }, ensure_ascii=False),
                    }
                )
            except Exception as e:
                logger.debug(f"插入失败(可能重复): {e}")
        await session.commit()


async def import_article_explanations(dry_run: bool = False):
    """导入法条解析"""
    expl_file = DATA_DIR / "article_explanations.jsonl"
    if not expl_file.exists():
        logger.warning(f"文件不存在: {expl_file}")
        return 0

    logger.info(f"开始导入法条解析: {expl_file}")
    count = 0

    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as session:
        with open(expl_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line.strip())
                    if not dry_run:
                        await session.execute(
                            text("""
                                INSERT INTO legal_knowledge (id, type, domain, title, content, keywords, source, metadata_json)
                                VALUES (:id, :type, :domain, :title, :content, :keywords, :source, :metadata)
                                ON CONFLICT (id) DO NOTHING
                            """),
                            {
                                "id": record.get("id", str(uuid.uuid4())),
                                "type": "article_explanation",
                                "domain": record.get("domain", ""),
                                "title": f"{record.get('article_ref', '')} - {record.get('explanation_type', '')}",
                                "content": record.get("explanation", ""),
                                "keywords": json.dumps([record.get("domain", ""), record.get("explanation_type", "")], ensure_ascii=False),
                                "source": record.get("source", "article_explanation"),
                                "metadata": json.dumps({
                                    "article_ref": record.get("article_ref", ""),
                                    "article_text": record.get("article_text", ""),
                                    "explanation_type": record.get("explanation_type", ""),
                                }, ensure_ascii=False),
                            }
                        )
                    count += 1
                except Exception as e:
                    logger.warning(f"解析行失败: {e}")
        await session.commit()

    logger.info(f"法条解析导入完成: {count:,} 条")
    return count


async def print_db_stats():
    """打印数据库统计"""
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT type, COUNT(*) as cnt FROM legal_knowledge GROUP BY type ORDER BY cnt DESC")
        )
        rows = result.fetchall()
        total = 0
        print("\n=== 数据库 legal_knowledge 表统计 ===")
        for row in rows:
            print(f"  {row[0]}: {row[1]:>12,} 条")
            total += row[1]
        print(f"  {'总计':>10}: {total:>12,} 条")


async def main():
    parser = argparse.ArgumentParser(description="导入mass_expansion数据到数据库")
    parser.add_argument("--table", choices=["qa_pairs", "cases", "explanations", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="只统计不导入")
    parser.add_argument("--stats", action="store_true", help="打印数据库统计")
    args = parser.parse_args()

    if args.stats:
        await print_db_stats()
        return

    total = 0
    if args.table in ("qa_pairs", "all"):
        total += await import_qa_pairs(args.dry_run)
    if args.table in ("cases", "all"):
        total += await import_cases(args.dry_run)
    if args.table in ("explanations", "all"):
        total += await import_article_explanations(args.dry_run)

    logger.info(f"\n总导入: {total:,} 条")

    if not args.dry_run:
        await print_db_stats()


if __name__ == "__main__":
    asyncio.run(main())
