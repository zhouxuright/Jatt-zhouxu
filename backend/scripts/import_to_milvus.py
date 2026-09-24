#!/usr/bin/env python3
"""Import synthetic data from data/mass_expansion/ into Milvus vector database.

Reads synthetic_qa_pairs.jsonl -> legal_articles collection
Reads synthetic_cases.jsonl    -> legal_cases collection

Features:
  - Batched processing (default 1000) for memory efficiency
  - --dry-run mode: count records without inserting
  - --limit N: only import the first N records per file
  - Checkpoint/resume: tracks progress in a JSON file so interrupted runs
    can skip already-imported records
  - Graceful Milvus connection error handling

Usage:
    python -m scripts.import_to_milvus                    # import everything
    python -m scripts.import_to_milvus --dry-run          # count only
    python -m scripts.import_to_milvus --limit 5000       # first 5k per file
    python -m scripts.import_to_milvus --reset-checkpoint # start from scratch
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup: make sure the project root is on sys.path so that `app.*` imports
# work when this script is invoked from the backend/ directory.
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent  # .../backend
sys.path.insert(0, str(BACKEND_DIR))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = BACKEND_DIR / "data" / "mass_expansion"
QA_FILE = DATA_DIR / "synthetic_qa_pairs.jsonl"
CASES_FILE = DATA_DIR / "synthetic_cases.jsonl"
CHECKPOINT_FILE = DATA_DIR / ".import_checkpoint.json"

EMBEDDING_DIM = 1024  # BGE-M3 dense dimension (must match milvus_service)
BATCH_SIZE = 1000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("import_to_milvus")


# ============================================================================
# Checkpoint helpers
# ============================================================================

def _load_checkpoint() -> dict[str, Any]:
    """Load checkpoint state from disk. Returns dict with last-imported offsets."""
    if CHECKPOINT_FILE.exists():
        try:
            data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            logger.info("Loaded checkpoint: %s", data)
            return data
        except Exception as exc:
            logger.warning("Could not read checkpoint file: %s", exc)
    return {
        "qa_offset": 0,
        "case_offset": 0,
        "qa_imported": 0,
        "case_imported": 0,
    }


def _save_checkpoint(state: dict[str, Any]) -> None:
    """Persist checkpoint state to disk."""
    try:
        CHECKPOINT_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not write checkpoint file: %s", exc)


# ============================================================================
# Data reading
# ============================================================================

def _iter_jsonl(path: Path, limit: int | None = None, skip: int = 0):
    """Lazily yield parsed JSON objects from a JSONL file.

    Args:
        path: Path to the JSONL file.
        limit: Maximum number of records to yield (None = all).
        skip: Number of leading records to skip (for checkpoint resume).
    """
    count = 0
    skipped = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if skipped < skip:
                skipped += 1
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed line in %s: %s", path.name, exc)
                continue
            count += 1
            if limit is not None and count >= limit:
                return


def _count_lines(path: Path) -> int:
    """Fast line count for progress estimation."""
    count = 0
    with open(path, "r", encoding="utf-8") as fh:
        for _ in fh:
            count += 1
    return count


# ============================================================================
# Embedding generation (BGE-M3 via the existing service)
# ============================================================================

def _get_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate dense embeddings using BGE-M3 via ModelRegistry (same as milvus_service)."""
    from app.services.model_registry import ModelRegistry
    model = ModelRegistry.get_embedding_model()
    output = model.encode(texts, return_dense=True)
    return [e.tolist() for e in output["dense_vecs"]]


# ============================================================================
# Milvus connection / collection helpers
# ============================================================================

def _ensure_milvus_connection() -> bool:
    """Connect to Milvus. Returns True on success."""
    try:
        from pymilvus import connections, utility
        from app.core.config import settings
        connections.connect(
            alias="default",
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
        )
        # Verify connectivity with a lightweight call
        utility.list_collections()
        logger.info("Connected to Milvus at %s:%s", settings.MILVUS_HOST, settings.MILVUS_PORT)
        return True
    except Exception as exc:
        logger.error("Failed to connect to Milvus: %s", exc)
        return False


def _ensure_collection(collection_name: str, fields_factory) -> bool:
    """Create the collection if it does not exist. Uses the provided factory."""
    try:
        from pymilvus import utility
        if utility.has_collection(collection_name):
            logger.info("Collection '%s' already exists", collection_name)
            return True
        fields_factory()
        logger.info("Created collection '%s'", collection_name)
        return True
    except Exception as exc:
        logger.error("Failed to ensure collection '%s': %s", collection_name, exc)
        return False


def _create_legal_articles_collection():
    """Create the legal_articles collection (QA pairs stored here)."""
    from pymilvus import CollectionSchema, DataType, FieldSchema, Collection

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
        FieldSchema(name="domain", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="subarea", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="question", dtype=DataType.VARCHAR, max_length=2048),
        FieldSchema(name="answer", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="keywords", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="quality_score", dtype=DataType.FLOAT),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields=fields, description="Synthetic QA pairs for legal RAG")
    collection = Collection(name="legal_articles", schema=schema)
    index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 2048}}
    collection.create_index(field_name="embedding", index_params=index_params)
    collection.load()


def _create_legal_cases_collection():
    """Create the legal_cases collection."""
    from pymilvus import CollectionSchema, DataType, FieldSchema, Collection

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=128),
        FieldSchema(name="case_number", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="court_name", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="case_type", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="cause_of_action", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="decision_date", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
    ]
    schema = CollectionSchema(fields=fields, description="Court cases for semantic similarity search")
    collection = Collection(name="legal_cases", schema=schema)
    index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 512}}
    collection.create_index(field_name="embedding", index_params=index_params)
    collection.load()


# ============================================================================
# Truncation helper (Milvus VARCHAR max_length is in bytes)
# ============================================================================

def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Truncate a string so its UTF-8 encoding fits within max_bytes."""
    if not value:
        return ""
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    return data[:max_bytes].decode("utf-8", errors="ignore")


# ============================================================================
# Progress bar (simple, no external dependency)
# ============================================================================

def _print_progress(current: int, total: int, prefix: str = "", bar_width: int = 40):
    """Print an inline progress bar to stdout."""
    if total <= 0:
        pct = 100.0
    else:
        pct = min(current / total * 100, 100.0)
    filled = int(bar_width * current / max(total, 1))
    bar = "#" * filled + "-" * (bar_width - filled)
    sys.stdout.write(f"\r{prefix} [{bar}] {pct:6.2f}%  ({current}/{total})")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


# ============================================================================
# Import: QA pairs -> legal_articles
# ============================================================================

def import_qa_pairs(
    limit: int | None,
    checkpoint: dict[str, Any],
    dry_run: bool = False,
    embed_batch: int = 64,
) -> int:
    """Read synthetic_qa_pairs.jsonl and insert into the legal_articles collection.

    Returns the number of records inserted.
    """
    if not QA_FILE.exists():
        logger.error("QA file not found: %s", QA_FILE)
        return 0

    total_lines = _count_lines(QA_FILE)
    skip = checkpoint.get("qa_offset", 0)
    effective_limit = limit
    if effective_limit is not None:
        to_process = min(effective_limit, max(total_lines - skip, 0))
    else:
        to_process = max(total_lines - skip, 0)

    logger.info(
        "QA pairs: total=%d, skip=%d, to_process=%d%s",
        total_lines, skip, to_process,
        " [DRY RUN]" if dry_run else "",
    )

    if to_process <= 0:
        logger.info("No QA pairs to process (already fully imported or limit reached)")
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would import %d QA pair records", to_process)
        return to_process

    # Ensure collection exists
    _ensure_collection("legal_articles", _create_legal_articles_collection)

    from pymilvus import Collection
    collection = Collection("legal_articles")
    collection.load()

    inserted = 0
    batch_records: list[dict] = []
    reader = _iter_jsonl(QA_FILE, limit=to_process, skip=0)

    for record in reader:
        batch_records.append(record)

        if len(batch_records) >= embed_batch:
            inserted += _insert_qa_batch(collection, batch_records)
            batch_records.clear()
            _print_progress(inserted, to_process, prefix="QA import")
            # Update checkpoint after each successful batch
            checkpoint["qa_offset"] = skip + inserted
            checkpoint["qa_imported"] = checkpoint.get("qa_imported", 0) + inserted
            _save_checkpoint(checkpoint)

    # Flush remaining
    if batch_records:
        inserted += _insert_qa_batch(collection, batch_records)
        _print_progress(inserted, to_process, prefix="QA import")

    # Final checkpoint
    checkpoint["qa_offset"] = skip + inserted
    _save_checkpoint(checkpoint)
    logger.info("QA pairs import complete: %d inserted", inserted)
    return inserted


def _insert_qa_batch(collection, batch: list[dict]) -> int:
    """Embed and insert one batch of QA records. Returns count inserted."""
    try:
        # Build embedding text: question + first 800 chars of answer
        texts = [
            f"{r.get('question', '')} {(r.get('answer') or '')[:800]}"
            for r in batch
        ]
        embeddings = _get_embeddings(texts)

        ids = [_truncate_utf8(str(r["id"]), 128) for r in batch]
        domains = [_truncate_utf8(r.get("domain") or "", 128) for r in batch]
        subareas = [_truncate_utf8(r.get("subarea") or "", 128) for r in batch]
        questions = [_truncate_utf8(r.get("question") or "", 2048) for r in batch]
        answers = [_truncate_utf8(r.get("answer") or "", 8192) for r in batch]
        keywords = [_truncate_utf8(",".join(r.get("keywords") or []), 512) for r in batch]
        quality_scores = [float(r.get("quality_score", 0.0)) for r in batch]

        data = [ids, domains, subareas, questions, answers, keywords, quality_scores, embeddings]
        collection.insert(data)
        collection.flush()
        return len(batch)
    except Exception as exc:
        logger.error("Failed to insert QA batch (%d records): %s", len(batch), exc)
        return 0


# ============================================================================
# Import: Cases -> legal_cases
# ============================================================================

def import_cases(
    limit: int | None,
    checkpoint: dict[str, Any],
    dry_run: bool = False,
    embed_batch: int = 64,
) -> int:
    """Read synthetic_cases.jsonl and insert into the legal_cases collection.

    Returns the number of records inserted.
    """
    if not CASES_FILE.exists():
        logger.error("Cases file not found: %s", CASES_FILE)
        return 0

    total_lines = _count_lines(CASES_FILE)
    skip = checkpoint.get("case_offset", 0)
    effective_limit = limit
    if effective_limit is not None:
        to_process = min(effective_limit, max(total_lines - skip, 0))
    else:
        to_process = max(total_lines - skip, 0)

    logger.info(
        "Cases: total=%d, skip=%d, to_process=%d%s",
        total_lines, skip, to_process,
        " [DRY RUN]" if dry_run else "",
    )

    if to_process <= 0:
        logger.info("No cases to process (already fully imported or limit reached)")
        return 0

    if dry_run:
        logger.info("[DRY RUN] Would import %d case records", to_process)
        return to_process

    # Ensure collection exists
    _ensure_collection("legal_cases", _create_legal_cases_collection)

    from pymilvus import Collection
    collection = Collection("legal_cases")
    collection.load()

    inserted = 0
    batch_records: list[dict] = []
    reader = _iter_jsonl(CASES_FILE, limit=to_process, skip=0)

    for record in reader:
        batch_records.append(record)

        if len(batch_records) >= embed_batch:
            inserted += _insert_case_batch(collection, batch_records)
            batch_records.clear()
            _print_progress(inserted, to_process, prefix="Case import")
            checkpoint["case_offset"] = skip + inserted
            checkpoint["case_imported"] = checkpoint.get("case_imported", 0) + inserted
            _save_checkpoint(checkpoint)

    if batch_records:
        inserted += _insert_case_batch(collection, batch_records)
        _print_progress(inserted, to_process, prefix="Case import")

    checkpoint["case_offset"] = skip + inserted
    _save_checkpoint(checkpoint)
    logger.info("Cases import complete: %d inserted", inserted)
    return inserted


def _insert_case_batch(collection, batch: list[dict]) -> int:
    """Embed and insert one batch of case records. Returns count inserted."""
    try:
        # Build embedding text: title + summary snippet (matches milvus_service pattern)
        texts = [
            f"{r.get('title', '')} {r.get('subarea', '')} {(r.get('summary') or '')[:800]}"
            for r in batch
        ]
        embeddings = _get_embeddings(texts)

        ids = [_truncate_utf8(str(r["id"]), 128) for r in batch]
        case_numbers = [_truncate_utf8(r.get("case_number") or "", 128) for r in batch]
        titles = [_truncate_utf8(r.get("title") or "", 512) for r in batch]
        court_names = [_truncate_utf8(r.get("court") or "", 256) for r in batch]
        case_types = [_truncate_utf8(r.get("domain") or "", 64) for r in batch]
        causes = [_truncate_utf8(r.get("subarea") or "", 256) for r in batch]
        decision_dates = [_truncate_utf8(str(r.get("year") or ""), 32) for r in batch]
        summaries = [_truncate_utf8(r.get("summary") or "", 8192) for r in batch]
        tags = [_truncate_utf8(",".join(r.get("keywords") or []), 512) for r in batch]

        data = [ids, case_numbers, titles, court_names, case_types,
                causes, decision_dates, summaries, tags, embeddings]
        collection.insert(data)
        collection.flush()
        return len(batch)
    except Exception as exc:
        logger.error("Failed to insert case batch (%d records): %s", len(batch), exc)
        return 0


# ============================================================================
# Main entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Import synthetic legal data into Milvus vector database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count records without importing (no Milvus connection needed)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to import per file (QA + cases each)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Embedding batch size (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Delete the checkpoint file and start importing from the beginning",
    )
    parser.add_argument(
        "--qa-only",
        action="store_true",
        help="Only import QA pairs (skip cases)",
    )
    parser.add_argument(
        "--cases-only",
        action="store_true",
        help="Only import cases (skip QA pairs)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Milvus Synthetic Data Importer")
    logger.info("=" * 60)
    logger.info("Data directory: %s", DATA_DIR)
    logger.info("QA file: %s", QA_FILE)
    logger.info("Cases file: %s", CASES_FILE)

    # Validate data files exist
    if not QA_FILE.exists():
        logger.error("QA pairs file not found: %s", QA_FILE)
        sys.exit(1)
    if not CASES_FILE.exists():
        logger.error("Cases file not found: %s", CASES_FILE)
        sys.exit(1)

    # Reset checkpoint if requested
    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Checkpoint file deleted")

    # Load checkpoint
    checkpoint = _load_checkpoint()

    # Dry-run mode: just count
    if args.dry_run:
        qa_total = _count_lines(QA_FILE)
        case_total = _count_lines(CASES_FILE)
        qa_skip = checkpoint.get("qa_offset", 0)
        case_skip = checkpoint.get("case_offset", 0)

        logger.info("-" * 40)
        logger.info("[DRY RUN] Record counts:")
        logger.info("  QA pairs:   %d total, %d already imported, %d remaining",
                     qa_total, qa_skip, qa_total - qa_skip)
        logger.info("  Cases:      %d total, %d already imported, %d remaining",
                     case_total, case_skip, case_total - case_skip)

        if args.limit:
            logger.info("  --limit=%d applied per file", args.limit)

        logger.info("  Checkpoint: %s", CHECKPOINT_FILE)
        logger.info("-" * 40)
        return

    # Connect to Milvus
    logger.info("Connecting to Milvus...")
    if not _ensure_milvus_connection():
        logger.error("Cannot connect to Milvus. Aborting.")
        logger.error("Make sure Milvus is running. Check MILVUS_HOST and MILVUS_PORT in config.")
        sys.exit(1)

    start_time = time.time()
    total_qa_inserted = 0
    total_case_inserted = 0

    try:
        # Import QA pairs
        if not args.cases_only:
            logger.info("-" * 40)
            logger.info("Starting QA pairs import...")
            total_qa_inserted = import_qa_pairs(
                limit=args.limit,
                checkpoint=checkpoint,
                dry_run=False,
                embed_batch=args.batch_size,
            )

        # Import cases
        if not args.qa_only:
            logger.info("-" * 40)
            logger.info("Starting cases import...")
            total_case_inserted = import_cases(
                limit=args.limit,
                checkpoint=checkpoint,
                dry_run=False,
                embed_batch=args.batch_size,
            )

    except KeyboardInterrupt:
        logger.warning("\nImport interrupted by user. Progress saved to checkpoint.")
    except Exception as exc:
        logger.error("Import error: %s", exc)
        logger.warning("Progress up to this point is saved in checkpoint.")

    elapsed = time.time() - start_time

    # Summary
    logger.info("=" * 60)
    logger.info("Import Summary")
    logger.info("=" * 60)
    logger.info("  QA pairs inserted:   %d", total_qa_inserted)
    logger.info("  Cases inserted:      %d", total_case_inserted)
    logger.info("  Total:               %d", total_qa_inserted + total_case_inserted)
    logger.info("  Elapsed time:        %.1f seconds", elapsed)
    logger.info("  Checkpoint:          %s", CHECKPOINT_FILE)
    if elapsed > 0:
        total = total_qa_inserted + total_case_inserted
        logger.info("  Throughput:          %.1f records/sec", total / elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
