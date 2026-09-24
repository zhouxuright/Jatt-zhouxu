"""
HuggingFace 数据集导入工具

将下载的 HuggingFace 中国法律数据集导入到 PostgreSQL 和 ChromaDB:

1. Refined-Chinese-Legal-Dataset (122,925 条真实案例)
   - fact: 案件事实描述
   - meta: 相关法条、罪名、刑罚等
   -> PostgreSQL: court_cases 表
   -> ChromaDB: legal_cases collection (BGE-M3 embeddings)

2. chinese-legal-sft (19,332 条法律 Q&A)
   - instruction / input / output: 法律问答对
   -> ChromaDB: legal_qa collection (BGE-M3 embeddings)

使用方法:
    python scripts/import_huggingface_datasets.py
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import select, func
from app.core.database import async_session_factory, engine, Base
from app.models.legal_knowledge import CourtCase, LegalArticle, Law
from app.services.model_registry import ModelRegistry

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REFINED_DATASET_PATH = "data/datasets/refined_legal_train.json"
SFT_TRAIN_PATH = "data/datasets/chinese_law_sft_train.json"
SFT_TEST_PATH = "data/datasets/chinese_law_sft_test.json"
CHROMA_PATH = "./chroma_data"

# ChromaDB collection names
CASES_COLLECTION = "legal_cases"
QA_COLLECTION = "legal_qa"

# Embedding batch size (BGE-M3 on CPU: 64 is a good balance of speed vs memory)
BATCH_SIZE = 64

# Progress reporting interval
PROGRESS_INTERVAL = 1000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def truncate(s: str, max_len: int) -> str:
    """Truncate string to max_len characters."""
    if s and len(s) > max_len:
        return s[:max_len]
    return s


def safe_id(prefix: str, index: int, extra: str = "") -> str:
    """Generate a ChromaDB-safe unique ID (max 256 chars)."""
    raw = f"{prefix}_{extra}_{index}" if extra else f"{prefix}_{index}"
    return raw[:256]


# ---------------------------------------------------------------------------
# Part 1: Import Refined Chinese Legal Dataset into PostgreSQL
# ---------------------------------------------------------------------------
async def import_refined_cases_to_pg():
    """Import Refined-Chinese-Legal-Dataset records into PostgreSQL court_cases table."""
    if not os.path.exists(REFINED_DATASET_PATH):
        logger.warning(f"Refined dataset not found at {REFINED_DATASET_PATH}, skipping PG import")
        return 0

    logger.info("=" * 60)
    logger.info("Part 1a: Importing Refined-Chinese-Legal-Dataset into PostgreSQL")
    logger.info("=" * 60)

    with open(REFINED_DATASET_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    logger.info(f"Loaded {len(records)} records from {REFINED_DATASET_PATH}")

    imported = 0
    skipped = 0
    errors = 0

    async with async_session_factory() as session:
        for i, record in enumerate(records):
            try:
                fact = record.get("fact", "")
                meta = record.get("meta", {})

                if not fact or not fact.strip():
                    skipped += 1
                    continue

                # Build a unique case_number from content hash
                content_hash = uuid.uuid5(uuid.NAMESPACE_DNS, f"refined_{i}_{fact[:50]}")
                case_number = f"REFINED-{content_hash.hex[:16]}"

                # Check if already exists
                result = await session.execute(
                    select(CourtCase).where(CourtCase.case_number == case_number)
                )
                if result.scalar_one_or_none():
                    skipped += 1
                    continue

                # Extract metadata
                accusation = meta.get("accusation", [])
                cause_of_action = "、".join(accusation) if accusation else ""

                relevant_articles = meta.get("relevant_articles", [])
                referenced_laws = "、".join(str(a) for a in relevant_articles[:20]) if relevant_articles else ""

                term_info = meta.get("term_of_imprisonment", {})
                judgment_parts = []
                if term_info.get("death_penalty"):
                    judgment_parts.append("死刑")
                if term_info.get("life_imprisonment"):
                    judgment_parts.append("无期徒刑")
                imprisonment = term_info.get("imprisonment", 0)
                if imprisonment:
                    judgment_parts.append(f"有期徒刑{imprisonment}年")
                punish_money = meta.get("punish_of_money", 0)
                if punish_money:
                    judgment_parts.append(f"罚金{punish_money}元")
                judgment_result = "、".join(judgment_parts) if judgment_parts else ""

                criminals = meta.get("criminals", [])

                # Determine case type from accusation
                case_type = "刑事"  # This dataset is primarily criminal cases

                case = CourtCase(
                    case_number=case_number,
                    title=f"刑事案件-{cause_of_action}" if cause_of_action else f"刑事案件-{i}",
                    court_name="",
                    case_type=case_type,
                    cause_of_action=cause_of_action,
                    decision_date="",
                    parties="、".join(criminals) if criminals else "",
                    summary=fact[:500],
                    full_text=fact,
                    key_points="",
                    referenced_laws=referenced_laws,
                    judgment_result=judgment_result,
                    tags="huggingface,refined-chinese-legal",
                )
                session.add(case)
                imported += 1

                if imported % PROGRESS_INTERVAL == 0:
                    logger.info(f"  PostgreSQL progress: {imported} imported, {skipped} skipped, {errors} errors (total {i+1}/{len(records)})")

            except Exception as e:
                errors += 1
                if errors <= 10:
                    logger.error(f"  Error processing record {i}: {e}")

        # Commit in one batch for performance
        await session.commit()

    logger.info(f"PostgreSQL import complete: {imported} imported, {skipped} skipped, {errors} errors")
    return imported


# ---------------------------------------------------------------------------
# Part 2: Import Refined Cases into ChromaDB (legal_cases collection)
# ---------------------------------------------------------------------------
def import_refined_cases_to_chroma():
    """Import Refined-Chinese-Legal-Dataset fact embeddings into ChromaDB legal_cases collection."""
    if not os.path.exists(REFINED_DATASET_PATH):
        logger.warning(f"Refined dataset not found at {REFINED_DATASET_PATH}, skipping ChromaDB import")
        return 0

    import chromadb

    logger.info("=" * 60)
    logger.info("Part 1b: Importing Refined-Chinese-Legal-Dataset into ChromaDB (legal_cases)")
    logger.info("=" * 60)

    with open(REFINED_DATASET_PATH, "r", encoding="utf-8") as f:
        records = json.load(f)

    logger.info(f"Loaded {len(records)} records")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        CASES_COLLECTION, metadata={"hnsw:space": "cosine"}
    )
    existing_count = collection.count()
    logger.info(f"ChromaDB collection '{CASES_COLLECTION}' exists with {existing_count} items")

    model = ModelRegistry.get_embedding_model()

    total_imported = 0
    total_skipped = 0
    total_errors = 0

    # Check what's already imported by looking at last checkpoint
    # Use a checkpoint file to track progress for resume capability
    checkpoint_file = "data/datasets/.refined_cases_chroma_checkpoint.txt"
    start_index = 0
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file, "r") as f:
                start_index = int(f.read().strip())
            logger.info(f"Resuming from checkpoint: index {start_index}")
        except Exception:
            start_index = 0

    batch_ids = []
    batch_docs = []
    batch_metas = []
    batch_texts_for_embed = []

    for i, record in enumerate(records):
        if i < start_index:
            continue

        try:
            fact = record.get("fact", "")
            meta = record.get("meta", {})

            if not fact or not fact.strip():
                total_skipped += 1
                continue

            rec_id = safe_id("refined", i)

            # Build document text for embedding (fact text, truncated for search)
            # Full text stored as document, but embedding uses shorter text for speed
            doc_text = fact[:5000]  # ChromaDB document
            embed_text = fact[:1000]  # Text used for embedding generation

            accusation = meta.get("accusation", [])
            relevant_articles = meta.get("relevant_articles", [])
            term_info = meta.get("term_of_imprisonment", {})

            metadata = {
                "source": "refined_chinese_legal",
                "index": i,
                "accusation": truncate("、".join(accusation), 200),
                "articles": truncate("、".join(str(a) for a in relevant_articles[:10]), 200),
                "imprisonment": term_info.get("imprisonment", 0) or 0,
                "case_type": "刑事",
            }
            # ChromaDB metadata values must be str/int/float/bool
            for k, v in metadata.items():
                if not isinstance(v, (str, int, float, bool)):
                    metadata[k] = str(v)

            batch_ids.append(rec_id)
            batch_docs.append(doc_text)
            batch_metas.append(metadata)
            batch_texts_for_embed.append(embed_text)

            # Process batch
            if len(batch_ids) >= BATCH_SIZE:
                # Generate embeddings
                output = model.encode(batch_texts_for_embed, return_dense=True)
                embeddings = output["dense_vecs"].tolist()

                collection.upsert(
                    ids=batch_ids,
                    documents=batch_docs,
                    metadatas=batch_metas,
                    embeddings=embeddings,
                )

                total_imported += len(batch_ids)
                batch_ids = []
                batch_docs = []
                batch_metas = []
                batch_texts_for_embed = []

                # Save checkpoint for resume capability
                with open(checkpoint_file, "w") as cf:
                    cf.write(str(i + 1))

                if total_imported % PROGRESS_INTERVAL == 0:
                    logger.info(f"  ChromaDB progress: {total_imported} imported (total {i+1}/{len(records)})")

        except Exception as e:
            total_errors += 1
            if total_errors <= 10:
                logger.error(f"  ChromaDB error at record {i}: {e}")
            batch_ids = []
            batch_docs = []
            batch_metas = []
            batch_texts_for_embed = []

    # Process remaining
    if batch_ids:
        try:
            output = model.encode(batch_texts_for_embed, return_dense=True)
            embeddings = output["dense_vecs"].tolist()
            collection.upsert(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas,
                embeddings=embeddings,
            )
            total_imported += len(batch_ids)
        except Exception as e:
            logger.error(f"  ChromaDB final batch error: {e}")
            total_errors += len(batch_ids)

    final_count = collection.count()
    logger.info(f"ChromaDB '{CASES_COLLECTION}' complete: {total_imported} upserted, "
                f"{total_skipped} skipped, {total_errors} errors")
    logger.info(f"Collection now has {final_count} items")
    return total_imported


# ---------------------------------------------------------------------------
# Part 3: Import Legal Q&A into ChromaDB (legal_qa collection)
# ---------------------------------------------------------------------------
def import_legal_qa_to_chroma():
    """Import chinese-legal-sft Q&A pairs into ChromaDB legal_qa collection."""
    qa_files = []
    for path in [SFT_TRAIN_PATH, SFT_TEST_PATH]:
        if os.path.exists(path):
            qa_files.append(path)

    if not qa_files:
        logger.warning("No legal SFT dataset files found, skipping Q&A import")
        return 0

    import chromadb

    logger.info("=" * 60)
    logger.info("Part 2: Importing Legal Q&A into ChromaDB (legal_qa)")
    logger.info("=" * 60)

    # Load all Q&A records
    all_records = []
    for path in qa_files:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        logger.info(f"Loaded {len(records)} records from {path}")
        all_records.extend(records)

    logger.info(f"Total Q&A records: {len(all_records)}")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(
        QA_COLLECTION, metadata={"hnsw:space": "cosine"}
    )
    existing_count = collection.count()
    logger.info(f"ChromaDB collection '{QA_COLLECTION}' exists with {existing_count} items")

    model = ModelRegistry.get_embedding_model()

    total_imported = 0
    total_skipped = 0
    total_errors = 0

    batch_ids = []
    batch_docs = []
    batch_metas = []
    batch_texts_for_embed = []

    record_idx = 0
    for file_idx, path in enumerate(qa_files):
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)

        for record in records:
            try:
                instruction = record.get("instruction", "")
                input_text = record.get("input", "")
                output_text = record.get("output", "")

                if not output_text or not output_text.strip():
                    total_skipped += 1
                    continue

                # Build the document text: combine instruction + output for embedding
                if input_text and input_text.strip():
                    doc_text = f"问题：{instruction}\n背景：{input_text}\n回答：{output_text}"
                else:
                    doc_text = f"问题：{instruction}\n回答：{output_text}"

                # Truncate to ChromaDB limits
                doc_text = doc_text[:30000]

                rec_id = safe_id("qa", record_idx, f"f{file_idx}")

                metadata = {
                    "source": "chinese_legal_sft",
                    "file_index": file_idx,
                    "record_index": record_idx,
                    "instruction": truncate(instruction, 200),
                    "has_input": bool(input_text and input_text.strip()),
                }
                for k, v in metadata.items():
                    if not isinstance(v, (str, int, float, bool)):
                        metadata[k] = str(v)

                batch_ids.append(rec_id)
                batch_docs.append(doc_text)
                batch_metas.append(metadata)
                batch_texts_for_embed.append(doc_text)

                record_idx += 1

                if len(batch_ids) >= BATCH_SIZE:
                    output = model.encode(batch_texts_for_embed, return_dense=True)
                    embeddings = output["dense_vecs"].tolist()

                    collection.upsert(
                        ids=batch_ids,
                        documents=batch_docs,
                        metadatas=batch_metas,
                        embeddings=embeddings,
                    )

                    total_imported += len(batch_ids)
                    batch_ids = []
                    batch_docs = []
                    batch_metas = []
                    batch_texts_for_embed = []

                    if total_imported % PROGRESS_INTERVAL == 0:
                        logger.info(f"  Q&A ChromaDB progress: {total_imported} imported (total {record_idx}/{len(all_records)})")

            except Exception as e:
                total_errors += 1
                if total_errors <= 10:
                    logger.error(f"  Q&A error at record {record_idx}: {e}")
                batch_ids = []
                batch_docs = []
                batch_metas = []
                batch_texts_for_embed = []
                record_idx += 1

    # Process remaining
    if batch_ids:
        try:
            output = model.encode(batch_texts_for_embed, return_dense=True)
            embeddings = output["dense_vecs"].tolist()
            collection.upsert(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas,
                embeddings=embeddings,
            )
            total_imported += len(batch_ids)
        except Exception as e:
            logger.error(f"  Q&A final batch error: {e}")
            total_errors += len(batch_ids)

    final_count = collection.count()
    logger.info(f"ChromaDB '{QA_COLLECTION}' complete: {total_imported} upserted, "
                f"{total_skipped} skipped, {total_errors} errors")
    logger.info(f"Collection now has {final_count} items")
    return total_imported


# ---------------------------------------------------------------------------
# Part 4: Print statistics
# ---------------------------------------------------------------------------
async def print_statistics():
    """Print summary statistics."""
    logger.info("=" * 60)
    logger.info("Import Statistics Summary")
    logger.info("=" * 60)

    # PostgreSQL stats
    async with async_session_factory() as session:
        result = await session.execute(select(func.count()).select_from(CourtCase))
        case_count = result.scalar() or 0

        result = await session.execute(select(func.count()).select_from(LegalArticle))
        article_count = result.scalar() or 0

        result = await session.execute(select(func.count()).select_from(Law))
        law_count = result.scalar() or 0

    logger.info(f"PostgreSQL:")
    logger.info(f"  Laws:        {law_count}")
    logger.info(f"  Articles:    {article_count}")
    logger.info(f"  Court Cases: {case_count}")

    # ChromaDB stats
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        for col_name in [CASES_COLLECTION, QA_COLLECTION, "legal_articles"]:
            try:
                col = client.get_collection(col_name)
                logger.info(f"ChromaDB '{col_name}': {col.count()} embeddings")
            except Exception:
                logger.info(f"ChromaDB '{col_name}': not found")
    except Exception as e:
        logger.error(f"ChromaDB stats error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    """Main entry point."""
    start_time = time.time()
    logger.info("HuggingFace Datasets Import Tool")
    logger.info("Importing Refined-Chinese-Legal-Dataset and Legal Q&A into knowledge base")
    logger.info("")

    # Ensure DB tables exist
    logger.info("Checking database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")
    logger.info("")

    try:
        # Step 1: Import refined cases into PostgreSQL
        pg_count = await import_refined_cases_to_pg()
        logger.info("")

        # Step 2: Import refined cases into ChromaDB (legal_cases)
        cases_count = import_refined_cases_to_chroma()
        logger.info("")

        # Step 3: Import legal Q&A into ChromaDB (legal_qa)
        qa_count = import_legal_qa_to_chroma()
        logger.info("")

        # Step 4: Print statistics
        await print_statistics()

        elapsed = time.time() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Import complete in {elapsed:.1f}s")
        logger.info(f"  PostgreSQL court_cases: {pg_count} new records")
        logger.info(f"  ChromaDB legal_cases:   {cases_count} embeddings")
        logger.info(f"  ChromaDB legal_qa:      {qa_count} embeddings")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Import failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
