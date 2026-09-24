# -*- coding: utf-8 -*-
"""导入 expanded_datasets 和 SFT 数据集到 PostgreSQL。

导入目标表:
- legal_qa_pairs ← laws_qa_pairs.jsonl + chinese_law_sft_{train,test}.json
- legal_knowledge_entries ← law_knowledge_entries.jsonl
- legal_cross_references ← law_cross_references.jsonl

支持断点续跑：每个文件导入完成后写入 checkpoint。

使用方法:
    python scripts/import_expanded_datasets.py
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_expanded")

DATA_DIR = BACKEND_ROOT / "data" / "expanded_datasets"
DATASETS_DIR = BACKEND_ROOT / "data" / "datasets"
CHECKPOINT_DIR = BACKEND_ROOT / "data" / "import_checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE = 500


# ===========================================================================
# laws_qa_pairs.jsonl -> legal_qa_pairs
# ===========================================================================

async def import_qa_pairs_jsonl():
    """导入 laws_qa_pairs.jsonl (873K 条) 到 legal_qa_pairs 表。"""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import LegalQAPair

    filepath = DATA_DIR / "laws_qa_pairs.jsonl"
    checkpoint = CHECKPOINT_DIR / "qa_pairs_jsonl.done"

    if checkpoint.exists():
        logger.info("QA pairs JSONL already imported: %s", checkpoint.read_text(encoding="utf-8"))
        return 0
    if not filepath.exists():
        logger.warning("File not found: %s", filepath)
        return 0

    logger.info("Importing laws_qa_pairs.jsonl ...")
    inserted = 0
    batch: list[Any] = []

    async with async_session_factory() as session:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                question = str(rec.get("question") or "").strip()
                answer = str(rec.get("answer") or "").strip()
                if not question or not answer:
                    continue

                batch.append(LegalQAPair(
                    question=question,
                    answer=answer,
                    law_name=str(rec.get("law_name") or "")[:512],
                    article_number=str(rec.get("article_number") or "")[:64],
                    law_type=str(rec.get("law_type") or "")[:64],
                    content=str(rec.get("content") or ""),
                    category=str(rec.get("category") or "")[:128],
                    source=str(rec.get("source") or "laws_json_qa")[:128],
                ))
                inserted += 1

                if len(batch) >= BATCH_SIZE:
                    session.add_all(batch)
                    await session.commit()
                    batch.clear()
                    if inserted % 50000 == 0:
                        logger.info("  QA pairs: %d rows imported ...", inserted)

        if batch:
            session.add_all(batch)
            await session.commit()

    checkpoint.write_text(str(inserted), encoding="utf-8")
    logger.info("QA pairs JSONL: +%d rows", inserted)
    return inserted


# ===========================================================================
# chinese_law_sft_{train,test}.json -> legal_qa_pairs
# ===========================================================================

async def import_sft_qa_data():
    """导入 SFT 问答数据到 legal_qa_pairs 表。"""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import LegalQAPair

    files = [
        (DATASETS_DIR / "chinese_law_sft_train.json", "chinese_law_sft_train"),
        (DATASETS_DIR / "chinese_law_sft_test.json", "chinese_law_sft_test"),
    ]
    checkpoint = CHECKPOINT_DIR / "sft_qa.done"

    if checkpoint.exists():
        logger.info("SFT QA already imported: %s", checkpoint.read_text(encoding="utf-8"))
        return 0

    total_inserted = 0

    async with async_session_factory() as session:
        for filepath, source_tag in files:
            if not filepath.exists():
                logger.warning("File not found: %s", filepath)
                continue

            logger.info("Importing %s ...", filepath.name)
            with open(filepath, "r", encoding="utf-8") as f:
                records = json.load(f)

            batch: list[Any] = []
            for rec in records:
                question = str(rec.get("instruction") or "").strip()
                answer = str(rec.get("output") or "").strip()
                if not question or not answer:
                    continue

                input_text = str(rec.get("input") or "")
                batch.append(LegalQAPair(
                    question=question,
                    answer=answer,
                    law_name="",
                    article_number="",
                    law_type="",
                    content=input_text,
                    category="法律问答",
                    source=source_tag[:128],
                ))

                if len(batch) >= BATCH_SIZE:
                    session.add_all(batch)
                    await session.commit()
                    total_inserted += len(batch)
                    batch.clear()

            if batch:
                session.add_all(batch)
                await session.commit()
                total_inserted += len(batch)

            logger.info("  %s: imported", filepath.name)

    checkpoint.write_text(str(total_inserted), encoding="utf-8")
    logger.info("SFT QA data: +%d rows total", total_inserted)
    return total_inserted


# ===========================================================================
# law_knowledge_entries.jsonl -> legal_knowledge_entries
# ===========================================================================

async def import_knowledge_entries():
    """导入 law_knowledge_entries.jsonl (194K 条) 到 legal_knowledge_entries 表。"""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import LegalKnowledgeEntry

    filepath = DATA_DIR / "law_knowledge_entries.jsonl"
    checkpoint = CHECKPOINT_DIR / "knowledge_entries.done"

    if checkpoint.exists():
        logger.info("Knowledge entries already imported: %s", checkpoint.read_text(encoding="utf-8"))
        return 0
    if not filepath.exists():
        logger.warning("File not found: %s", filepath)
        return 0

    logger.info("Importing law_knowledge_entries.jsonl ...")
    inserted = 0
    batch: list[Any] = []

    async with async_session_factory() as session:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                content = str(rec.get("content") or "").strip()
                if not content:
                    continue

                metadata = rec.get("metadata")
                metadata_str = json.dumps(metadata, ensure_ascii=False) if metadata else None

                batch.append(LegalKnowledgeEntry(
                    entry_type=str(rec.get("type") or "")[:64],
                    content=content,
                    law_name=str(rec.get("law_name") or "")[:512],
                    law_type=str(rec.get("law_type") or "")[:64],
                    category=str(rec.get("category") or "")[:128],
                    source=str(rec.get("source") or "")[:128],
                    metadata_json=metadata_str,
                ))
                inserted += 1

                if len(batch) >= BATCH_SIZE:
                    session.add_all(batch)
                    await session.commit()
                    batch.clear()
                    if inserted % 50000 == 0:
                        logger.info("  Knowledge entries: %d rows imported ...", inserted)

        if batch:
            session.add_all(batch)
            await session.commit()

    checkpoint.write_text(str(inserted), encoding="utf-8")
    logger.info("Knowledge entries: +%d rows", inserted)
    return inserted


# ===========================================================================
# law_cross_references.jsonl -> legal_cross_references
# ===========================================================================

async def import_cross_references():
    """导入 law_cross_references.jsonl (20K 条) 到 legal_cross_references 表。"""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import LegalCrossReference

    filepath = DATA_DIR / "law_cross_references.jsonl"
    checkpoint = CHECKPOINT_DIR / "cross_references.done"

    if checkpoint.exists():
        logger.info("Cross references already imported: %s", checkpoint.read_text(encoding="utf-8"))
        return 0
    if not filepath.exists():
        logger.warning("File not found: %s", filepath)
        return 0

    logger.info("Importing law_cross_references.jsonl ...")
    inserted = 0
    batch: list[Any] = []

    async with async_session_factory() as session:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                law_name = str(rec.get("law_name") or "").strip()
                if not law_name:
                    continue

                referenced = rec.get("referenced_laws")
                if isinstance(referenced, list):
                    ref_str = json.dumps(referenced, ensure_ascii=False)
                else:
                    ref_str = str(referenced or "")

                batch.append(LegalCrossReference(
                    law_name=law_name[:512],
                    referenced_laws=ref_str,
                    content=str(rec.get("content") or ""),
                    ref_type=str(rec.get("type") or "")[:64],
                    category=str(rec.get("category") or "")[:128],
                    source=str(rec.get("source") or "")[:128],
                ))
                inserted += 1

                if len(batch) >= BATCH_SIZE:
                    session.add_all(batch)
                    await session.commit()
                    batch.clear()

        if batch:
            session.add_all(batch)
            await session.commit()

    checkpoint.write_text(str(inserted), encoding="utf-8")
    logger.info("Cross references: +%d rows", inserted)
    return inserted


# ===========================================================================
# Statistics
# ===========================================================================

async def print_stats():
    """打印所有表的行数统计。"""
    from sqlalchemy import select, func as sa_func
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import (
        Law, LegalArticle, CourtCase, LegalQAPair,
        LegalKnowledgeEntry, LegalCrossReference, LegalConcept,
    )

    async with async_session_factory() as session:
        counts = {}
        for model_cls in [Law, LegalArticle, CourtCase, LegalQAPair,
                          LegalKnowledgeEntry, LegalCrossReference, LegalConcept]:
            total = await session.scalar(select(sa_func.count(model_cls.id))) or 0
            counts[model_cls.__tablename__] = total

    total = sum(counts.values())
    print("\n" + "=" * 60)
    print("数据库数据量统计")
    print("=" * 60)
    for table_name, count in sorted(counts.items()):
        print(f"  {table_name:30s}  {count:>12,}")
    print("-" * 60)
    print(f"  {'总计':30s}  {total:>12,}")
    print("=" * 60)
    return counts


# ===========================================================================
# Main
# ===========================================================================

async def main():
    """主函数 — 按顺序导入所有 expanded datasets。"""
    logger.info("=" * 60)
    logger.info("法律数据扩展导入 — expanded_datasets + SFT")
    logger.info("=" * 60)

    t0 = time.time()

    # Step 1: QA pairs from laws_qa_pairs.jsonl
    qa_count = await import_qa_pairs_jsonl()

    # Step 2: SFT QA data
    sft_count = await import_sft_qa_data()

    # Step 3: Knowledge entries
    knowledge_count = await import_knowledge_entries()

    # Step 4: Cross references
    cross_count = await import_cross_references()

    elapsed = time.time() - t0

    logger.info("=" * 60)
    logger.info("导入完成 (%.1f 分钟):", elapsed / 60)
    logger.info("  QA pairs:          +%s", f"{qa_count:,}")
    logger.info("  SFT QA:            +%s", f"{sft_count:,}")
    logger.info("  Knowledge entries: +%s", f"{knowledge_count:,}")
    logger.info("  Cross references:  +%s", f"{cross_count:,}")
    total_new = qa_count + sft_count + knowledge_count + cross_count
    logger.info("  新增总计:          +%s", f"{total_new:,}")
    logger.info("=" * 60)

    await print_stats()


if __name__ == "__main__":
    asyncio.run(main())
