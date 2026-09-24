# -*- coding: utf-8 -*-
"""Million-scale legal corpus importer (three resumable stages).

  Part A  local data/datasets/hf_laws_*.json -> laws + legal_articles
  Part B  HuggingFace streaming case datasets -> court_cases (top up to target)
          candidates: coastalcph/fairlex#cail, FudanDISC/Leven,
                      SunSpace0923/Refined-Chinese-Legal-Dataset
  Part C  court_cases -> Milvus legal_cases vector collection (BGE-M3, GPU first)

Runnable as a module:
    python -m app.rag.million_import --target-total-cases 1000000 --embed-limit 100000

Security: every DB statement is a SQLAlchemy ORM expression read via
AsyncSession.scalars()/scalar(); dataset files are restricted to
data/datasets and validated with a resolved-path containment check; CLI
inputs are operator-provided integers only.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("million_import")

# Dataset root: fixed relative to the backend package, not operator-controlled.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = (BACKEND_ROOT / "data" / "datasets").resolve()
CHECKPOINT_DIR = (BACKEND_ROOT / "data" / "import_checkpoints").resolve()
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

LAW_CHECKPOINT = CHECKPOINT_DIR / "million_laws.done"
CASES_CHECKPOINT = CHECKPOINT_DIR / "million_cases.count"
EMBED_CHECKPOINT = CHECKPOINT_DIR / "million_embed.count"

ARTICLE_NO_RE = re.compile(r"第[一二三四五六七八九十百千零〇两0-9]+条")
# Allow-list for law dataset filenames: hf_laws_<chinese/alnum title>.json
LAW_FILE_RE = re.compile(r"^hf_laws_[A-Za-z0-9\u4e00-\u9fff（）()\-—、.,，]+\.json$")

# Static page size for keyset pagination (no dynamic LIMIT values)
FETCH_PAGE_SIZE = 64
LAW_FLUSH_SIZE = 2000
CASE_FLUSH_SIZE = 5000

HF_CASE_SOURCES = [
    # (repo, split) -- parquet-based, script-free datasets only
    ("china-ai-law-challenge/cail2018", "first_stage_train"),      # ~2.4M criminal cases
    ("china-ai-law-challenge/cail2018", "exercise_contest_train"),
    ("china-ai-law-challenge/cail2018", "final_test"),
]


def _validated_law_files():
    """List data/datasets/hf_laws_*.json with resolved-path containment check."""
    files = []
    for name in sorted(os.listdir(DATASETS_DIR)):
        if not LAW_FILE_RE.match(name):
            continue
        resolved = (DATASETS_DIR / name).resolve()
        if resolved.parent != DATASETS_DIR:
            # Any escape from the datasets directory is rejected outright.
            logger.warning("Skipping path outside datasets dir: %s", name)
            continue
        files.append(resolved)
    return files


def _load_law_records(dataset_path: Path):
    with open(dataset_path, encoding="utf-8") as fh:
        records = json.load(fh)
    if not isinstance(records, list) or not records:
        return None
    return records


# ===========================================================================
# Part A: local hf_laws_*.json -> laws + legal_articles
# ===========================================================================

async def import_law_files():
    """Import every validated hf_laws_*.json file into laws/legal_articles."""
    from sqlalchemy import select
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law, LegalArticle

    files = _validated_law_files()
    if not files:
        logger.warning("Part A: no hf_laws_*.json files found")
        return {"laws": 0, "articles": 0}

    stats = {"laws": 0, "articles": 0}
    async with async_session_factory() as session:
        law_scalars = await session.scalars(select(Law))
        existing_laws = {law.name: law.id for law in law_scalars.all()}
        logger.info("Part A: %d files, %d laws already in DB", len(files), len(existing_laws))

        pending_articles = []

        async def flush_articles():
            if pending_articles:
                session.add_all(pending_articles)
                await session.commit()
                pending_articles.clear()

        for dataset_path in files:
            fname = dataset_path.name
            law_title = fname[len("hf_laws_"):-len(".json")]
            records = _load_law_records(dataset_path)
            if records is None:
                continue

            law_id = existing_laws.get(law_title)
            if law_id is None:
                first_content = (records[0].get("content") or "")[:2000]
                law = Law(
                    name=law_title,
                    law_type="法律",
                    category=None,
                    effective_date="",
                    status="active",
                    issuing_authority="",
                    abstract=first_content,
                )
                session.add(law)
                await session.flush()
                law_id = law.id
                existing_laws[law_title] = law_id
                stats["laws"] += 1

            seen_in_file = set()
            for rec in records:
                content = (rec.get("content") or "").strip()
                if not content:
                    continue
                match = ARTICLE_NO_RE.search(content[:60])
                article_number = match.group(0) if match else "条文"
                content_key = article_number + ":" + content[:120]
                if content_key in seen_in_file:
                    continue
                seen_in_file.add(content_key)
                pending_articles.append(LegalArticle(
                    law_id=law_id,
                    article_number=article_number,
                    title=None,
                    content=content[:8000],
                    chapter="",
                    tags="hf_lawrefbook",
                ))
                stats["articles"] += 1
                if len(pending_articles) >= LAW_FLUSH_SIZE:
                    await flush_articles()

            logger.info("Part A progress: %d laws, %d articles so far (%s)",
                        stats["laws"], stats["articles"], law_title[:30])

        await flush_articles()
        await session.commit()

    LAW_CHECKPOINT.write_text(json.dumps(stats), encoding="utf-8")
    logger.info("Part A complete: %d new laws, %d new articles", stats["laws"], stats["articles"])
    return stats


# ===========================================================================
# Part B: stream HF case datasets -> court_cases (top up to target)
# ===========================================================================

def _extract_case(rec, idx):
    """Adaptively extract a normalized case dict from an HF record."""
    fact = rec.get("fact") or rec.get("text") or rec.get("content") or ""
    meta = rec.get("meta") or {}
    x_field = rec.get("x")
    if not fact and isinstance(x_field, dict):
        fact = x_field.get("fact") or ""
        meta = x_field.get("meta") or meta
    if not fact or not str(fact).strip():
        return None
    fact = str(fact)

    accusation = rec.get("accusation") or meta.get("accusation") or rec.get("labels") or []
    if isinstance(accusation, str):
        accusation = [accusation] if accusation else []
    articles = rec.get("relevant_articles") or meta.get("relevant_articles") or rec.get("article") or []
    if isinstance(articles, (int, str)):
        articles = [articles]
    term = rec.get("term_of_imprisonment") or meta.get("term_of_imprisonment") or {}
    money = rec.get("punish_of_money", meta.get("punish_of_money", 0)) or 0
    criminals = rec.get("criminals") or meta.get("criminals") or []

    judgment_parts = []
    if isinstance(term, dict):
        if term.get("death_penalty"):
            judgment_parts.append("死刑")
        if term.get("life_imprisonment"):
            judgment_parts.append("无期徒刑")
        if term.get("imprisonment"):
            judgment_parts.append("有期徒刑%s年" % term["imprisonment"])
    if money:
        judgment_parts.append("罚金%s元" % money)

    return {
        "fact": fact,
        "cause_of_action": "、".join(str(a) for a in accusation[:5]),
        "referenced_laws": "、".join(str(a) for a in list(articles)[:20]),
        "judgment_result": "、".join(judgment_parts),
        "parties": "、".join(str(c) for c in criminals[:5]),
    }


async def count_cases():
    """Total rows in court_cases (ORM count via session.scalar)."""
    from sqlalchemy import select, func as sa_func
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import CourtCase
    async with async_session_factory() as session:
        return await session.scalar(select(sa_func.count(CourtCase.id))) or 0


def _iter_parquet_records(repo: str, split: str, token: str | None):
    """Download a dataset split's parquet files via hf_hub_download (with
    built-in retries) and iterate records in batches -- avoids the flaky
    streaming connection path of `datasets.load_dataset`."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi()
    repo_files = api.list_repo_files(repo, repo_type="dataset")
    split_files = sorted(
        f for f in repo_files
        if f.startswith("data/" + split + "-") and f.endswith(".parquet")
    )
    if not split_files:
        logger.warning("No parquet files for split '%s' in %s", split, repo)
        return
    logger.info("Downloading %d parquet files for %s#%s", len(split_files), repo, split)
    for fname in split_files:
        local_path = hf_hub_download(
            repo_id=repo, filename=fname, repo_type="dataset",
            token=token, endpoint=os.getenv("HF_ENDPOINT") or None,
        )
        parquet_file = pq.ParquetFile(local_path)
        for batch in parquet_file.iter_batches(batch_size=CASE_FLUSH_SIZE):
            for rec in batch.to_pylist():
                yield rec


async def import_cases_to_target(target_total: int, max_per_source: int = 1500000):
    """Download parquet case datasets and insert cases until target_total reached.

    Dedup strategy: uuid5 over the FULL fact text (template-heavy documents
    share identical prefixes, so prefix hashes collide). Batch commits fall
    back to per-row inserts on unique-key conflicts so one duplicate never
    poisons a 5000-row batch.
    """
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import CourtCase
    from sqlalchemy.exc import IntegrityError

    current = await count_cases()
    logger.info("Part B: current court_cases=%d, target=%d", current, target_total)
    if current >= target_total:
        logger.info("Part B: target already reached, skipping download")
        return 0

    need = target_total - current
    inserted = 0
    hf_token = os.getenv("HF_TOKEN") or None

    async def commit_batch(session, batch_rows):
        """Insert a batch; on unique-key conflict retry rows individually."""
        nonlocal inserted
        try:
            session.add_all(batch_rows)
            await session.commit()
            inserted += len(batch_rows)
        except IntegrityError:
            await session.rollback()
            saved = 0
            for case_row in batch_rows:
                try:
                    session.add(case_row)
                    await session.commit()
                    saved += 1
                except IntegrityError:
                    await session.rollback()
            inserted += saved

    for repo, split in HF_CASE_SOURCES:
        if inserted >= need:
            break
        logger.info("Part B: source %s#%s", repo, split)
        batch = []
        seen_facts = set()
        seen = 0
        try:
            async with async_session_factory() as session:
                for rec in _iter_parquet_records(repo, split, hf_token):
                    seen += 1
                    if seen > max_per_source or inserted >= need:
                        break
                    parsed = _extract_case(rec, seen)
                    if parsed is None:
                        continue
                    fact = parsed["fact"]
                    content_hash = uuid.uuid5(uuid.NAMESPACE_DNS, "million_" + fact)
                    fact_key = content_hash.hex
                    if fact_key in seen_facts:
                        continue
                    seen_facts.add(fact_key)
                    source_tag = repo.split("/")[-1].lower()
                    batch.append(CourtCase(
                        case_number="HF-" + fact_key[:16],
                        title="刑事案件-" + parsed["cause_of_action"] if parsed["cause_of_action"] else "刑事案件",
                        court_name="",
                        case_type="刑事",
                        cause_of_action=parsed["cause_of_action"][:256],
                        decision_date="",
                        parties=parsed["parties"][:2000],
                        summary=fact[:500],
                        full_text=fact[:60000],
                        key_points="",
                        referenced_laws=parsed["referenced_laws"][:2000],
                        judgment_result=parsed["judgment_result"][:500],
                        tags="huggingface," + source_tag,
                    ))
                    if len(batch) >= CASE_FLUSH_SIZE:
                        await commit_batch(session, batch)
                        batch = []
                        logger.info("Part B: +%d from %s#%s (total %d/%d, seen %d)",
                                    CASE_FLUSH_SIZE, repo, split, inserted, need, seen)
                        CASES_CHECKPOINT.write_text(str(inserted), encoding="utf-8")
                if batch:
                    await commit_batch(session, batch)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.warning("Part B: source %s#%s interrupted: %s", repo, split, exc)

    logger.info("Part B complete: inserted %d new cases", inserted)
    return inserted


# ===========================================================================
# Part C: embed court_cases -> Milvus legal_cases (GPU when available)
# ===========================================================================

def _torch_cuda_available():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ensure_case_collection():
    """Connect to Milvus and create+load the legal_cases collection."""
    from pymilvus import connections, Collection, CollectionSchema, DataType, FieldSchema, utility

    milvus_host = os.getenv("MILVUS_HOST", "localhost")
    milvus_port = os.getenv("MILVUS_PORT", "19530")
    connections.connect(alias="default", host=milvus_host, port=milvus_port)

    collection_name = "legal_cases"
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="case_number", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="court_name", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="case_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="cause_of_action", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="decision_date", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=8192),
            FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024),
        ]
        schema = CollectionSchema(fields=fields, description="Court cases for semantic search")
        col = Collection(collection_name, schema)
        index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 512}}
        col.create_index("embedding", index_params)
    collection = Collection(collection_name)
    collection.load()
    return collection


def _read_cursor(path: Path) -> dict[str, Any]:
    """Read the embed cursor {count, after_ts, after_id}.

    JSON is the current format; a bare integer (legacy) yields count-only.
    """
    if not path.exists():
        return {"count": 0, "after_ts": None, "after_id": None}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"count": 0, "after_ts": None, "after_id": None}
    try:
        data = json.loads(raw)
        return {
            "count": int(data.get("count") or 0),
            "after_ts": data.get("after_ts"),
            "after_id": data.get("after_id"),
        }
    except (ValueError, json.JSONDecodeError):
        try:
            return {"count": int(raw), "after_ts": None, "after_id": None}
        except ValueError:
            return {"count": 0, "after_ts": None, "after_id": None}


def _write_cursor(path: Path, count: int, after_ts, after_id) -> None:
    """Persist the keyset cursor atomically so a resume never re-embeds rows."""
    payload = {
        "count": count,
        "after_ts": after_ts.isoformat() if after_ts is not None else None,
        "after_id": str(after_id) if after_id is not None else None,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def embed_cases_into_milvus(embed_limit: int):
    """Embed court_cases rows (keyset-paginated) and insert vectors into Milvus.

    Resume is cursor-based: the checkpoint stores the last (created_at, id) so a
    restart continues exactly where it left off -- never re-embedding rows and
    never skipping them. `upsert` makes the write idempotent on the UUID primary
    key, so even a crash between flush and cursor-write cannot duplicate data.
    """
    from sqlalchemy import select
    from FlagEmbedding import BGEM3FlagModel
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import CourtCase
    from datetime import datetime as _dt

    collection = _ensure_case_collection()

    cursor = _read_cursor(EMBED_CHECKPOINT)
    inserted = cursor["count"]
    after_ts = after_id = None
    if cursor["after_ts"]:
        try:
            after_ts = _dt.fromisoformat(cursor["after_ts"])
            after_id = cursor["after_id"]
        except ValueError:
            inserted = 0
    logger.info("Part C: embedding up to %d cases (resume from #%d)", embed_limit, inserted)

    device = "cuda" if _torch_cuda_available() else "cpu"
    logger.info("Loading BGE-M3 on device=%s ...", device)
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=(device == "cuda"), device=device)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def fetch_page(after_ts, after_id):
        """Keyset pagination: rows strictly after (after_ts, after_id), fixed page size."""
        stmt = select(CourtCase).order_by(CourtCase.created_at.asc(), CourtCase.id.asc())
        if after_ts is not None:
            stmt = stmt.where(
                (CourtCase.created_at > after_ts) |
                ((CourtCase.created_at == after_ts) & (CourtCase.id > after_id))
            )
        stmt = stmt.limit(FETCH_PAGE_SIZE)
        async with async_session_factory() as session:
            page_scalars = await session.scalars(stmt)
            return page_scalars.all()

    while inserted < embed_limit:
        rows = loop.run_until_complete(fetch_page(after_ts, after_id))
        if not rows:
            break
        texts = []
        for r in rows:
            texts.append((r.title or "") + " " + (r.cause_of_action or "") + " " + (r.summary or "")[:800])
        output = model.encode(texts, return_dense=True)
        vecs = output["dense_vecs"].tolist()

        data = [
            [str(r.id)[:64] for r in rows],
            [(r.case_number or "")[:128] for r in rows],
            [(r.title or "")[:512] for r in rows],
            [(r.court_name or "")[:256] for r in rows],
            [(r.case_type or "")[:64] for r in rows],
            [(r.cause_of_action or "")[:256] for r in rows],
            [(r.decision_date or "")[:32] for r in rows],
            [(r.summary or "")[:8192] for r in rows],
            [(r.tags or "")[:512] for r in rows],
            vecs,
        ]
        collection.upsert(data)
        collection.flush()
        inserted += len(rows)
        last_row = rows[-1]
        after_ts = last_row.created_at
        after_id = last_row.id
        _write_cursor(EMBED_CHECKPOINT, inserted, after_ts, after_id)
        if inserted % 1280 < FETCH_PAGE_SIZE:
            logger.info("Part C: embedded %d/%d (milvus entities=%d)",
                        inserted, embed_limit, collection.num_entities)

    collection.load()
    logger.info("Part C complete: +%d vectors, collection now has %d entities",
                inserted, collection.num_entities)
    return inserted


# ===========================================================================
# Part D: laws.json full-text parse -> laws + legal_articles
# ===========================================================================

LAW_JSON_CHECKPOINT = CHECKPOINT_DIR / "million_laws_json.done"


async def import_laws_json_full():
    """Parse data/datasets/laws.json (22K+ laws) into laws + legal_articles.

    Splits each law's ``content`` field on article markers (第X条) and inserts
    one ``LegalArticle`` row per article.  Deduplication relies on the
    (name, effective_date) unique constraint on ``laws`` and the
    (law_id, article_number) unique constraint on ``legal_articles``.

    Returns:
        dict with ``laws`` and ``articles`` counts.
    """
    from sqlalchemy import select
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law, LegalArticle

    laws_json_path = DATASETS_DIR / "laws.json"
    if not laws_json_path.exists():
        logger.warning("Part D: laws.json not found at %s", laws_json_path)
        return {"laws": 0, "articles": 0}

    logger.info("Part D: loading laws.json ...")
    with open(laws_json_path, encoding="utf-8") as fh:
        all_laws = json.load(fh)
    logger.info("Part D: %d laws to process", len(all_laws))

    stats = {"laws": 0, "articles": 0, "skipped": 0}
    article_flush_size = 2000

    async with async_session_factory() as session:
        # Build name -> id mapping for existing laws
        law_scalars = await session.scalars(select(Law))
        existing_laws = {law.name: law.id for law in law_scalars.all()}
        logger.info("Part D: %d laws already in DB", len(existing_laws))

        pending_articles: list[LegalArticle] = []

        async def flush():
            if pending_articles:
                session.add_all(pending_articles)
                await session.commit()
                pending_articles.clear()

        for idx, law_rec in enumerate(all_laws):
            title = (law_rec.get("title") or "").strip()
            if not title:
                stats["skipped"] += 1
                continue

            law_type = (law_rec.get("type") or "法律").strip()[:64]
            office = (law_rec.get("office") or "").strip()[:256]
            publish = (law_rec.get("publish") or "").strip()[:32]
            if publish and " " in publish:
                publish = publish.split(" ")[0]  # "2018-03-11 00:00:00" -> "2018-03-11"
            status_raw = (law_rec.get("status") or "").strip()
            status = "active" if status_raw in ("有效", "现行") else "repealed"
            content = (law_rec.get("content") or "").strip()

            # Get or create the law
            law_id = existing_laws.get(title)
            if law_id is None:
                law = Law(
                    name=title[:512],
                    law_type=law_type,
                    category=None,
                    effective_date=publish,
                    status=status,
                    issuing_authority=office,
                    abstract=content[:2000] if content else "",
                )
                session.add(law)
                await session.flush()
                law_id = law.id
                existing_laws[title] = law_id
                stats["laws"] += 1

            # Split content into articles
            if not content:
                continue

            # Use regex to find article boundaries
            article_splits = ARTICLE_NO_RE.split(content)
            article_markers = ARTICLE_NO_RE.findall(content)

            if article_markers:
                for i, marker in enumerate(article_markers):
                    article_content = article_splits[i + 1].strip() if (i + 1) < len(article_splits) else ""
                    if not article_content:
                        continue
                    # Deduplicate by content prefix
                    pending_articles.append(LegalArticle(
                        law_id=law_id,
                        article_number=marker[:64],
                        title=None,
                        content=article_content[:8000],
                        chapter="",
                        tags="laws_json_full",
                    ))
                    stats["articles"] += 1
                    if len(pending_articles) >= article_flush_size:
                        await flush()
            else:
                # No article markers — store the entire content as one article
                pending_articles.append(LegalArticle(
                    law_id=law_id,
                    article_number="全文",
                    title=None,
                    content=content[:8000],
                    chapter="",
                    tags="laws_json_full",
                ))
                stats["articles"] += 1
                if len(pending_articles) >= article_flush_size:
                    await flush()

            if (idx + 1) % 500 == 0:
                logger.info(
                    "Part D progress: %d/%d laws, %d articles so far",
                    idx + 1, len(all_laws), stats["articles"],
                )

        await flush()
        await session.commit()

    LAW_JSON_CHECKPOINT.write_text(json.dumps(stats), encoding="utf-8")
    logger.info("Part D complete: +%d laws, +%d articles", stats["laws"], stats["articles"])
    return stats


# ===========================================================================
# Part E: refined_legal_train.json -> court_cases
# ===========================================================================

REFINED_CHECKPOINT = CHECKPOINT_DIR / "refined_legal_train.done"


async def import_refined_legal_train():
    """Import refined_legal_train.json (122K+ criminal cases) into court_cases.

    Each record has ``fact`` (case description) and ``meta`` (structured tags
    including accusation, relevant_articles, term_of_imprisonment, etc.).

    Dedup via uuid5 over the fact text, same strategy as Part B.

    Returns:
        int — number of new cases inserted.
    """
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import CourtCase
    from sqlalchemy.exc import IntegrityError

    train_path = DATASETS_DIR / "refined_legal_train.json"
    if not train_path.exists():
        logger.warning("Part E: refined_legal_train.json not found")
        return 0

    logger.info("Part E: loading refined_legal_train.json ...")
    with open(train_path, encoding="utf-8") as fh:
        records = json.load(fh)
    logger.info("Part E: %d records to process", len(records))

    inserted = 0
    seen_facts: set[str] = set()
    batch: list[CourtCase] = []
    flush_size = 5000

    async def commit_batch(session, batch_rows):
        nonlocal inserted
        try:
            session.add_all(batch_rows)
            await session.commit()
            inserted += len(batch_rows)
        except IntegrityError:
            await session.rollback()
            saved = 0
            for case_row in batch_rows:
                try:
                    session.add(case_row)
                    await session.commit()
                    saved += 1
                except IntegrityError:
                    await session.rollback()
            inserted += saved

    async with async_session_factory() as session:
        for idx, rec in enumerate(records):
            fact = str(rec.get("fact") or "").strip()
            if not fact:
                continue

            # Dedup
            fact_hash = uuid.uuid5(uuid.NAMESPACE_DNS, "refined_" + fact)
            fact_key = fact_hash.hex
            if fact_key in seen_facts:
                continue
            seen_facts.add(fact_key)

            meta = rec.get("meta") or {}
            accusation = meta.get("accusation") or rec.get("accusation") or []
            if isinstance(accusation, str):
                accusation = [accusation] if accusation else []
            articles = meta.get("relevant_articles") or rec.get("relevant_articles") or []
            if isinstance(articles, (int, str)):
                articles = [articles]
            term = meta.get("term_of_imprisonment") or rec.get("term_of_imprisonment") or {}
            money = meta.get("punish_of_money", 0) or rec.get("punish_of_money", 0) or 0
            criminals = meta.get("criminals") or rec.get("criminals") or []

            # Build judgment result string
            judgment_parts = []
            if isinstance(term, dict):
                if term.get("death_penalty"):
                    judgment_parts.append("死刑")
                if term.get("life_imprisonment"):
                    judgment_parts.append("无期徒刑")
                if term.get("imprisonment"):
                    judgment_parts.append("有期徒刑%s年" % term["imprisonment"])
            if money:
                judgment_parts.append("罚金%s元" % money)

            cause = "、".join(str(a) for a in accusation[:5])
            batch.append(CourtCase(
                case_number="RT-" + fact_key[:16],
                title="刑事案件-" + cause if cause else "刑事案件",
                court_name="",
                case_type="刑事",
                cause_of_action=cause[:256],
                decision_date="",
                parties="、".join(str(c) for c in criminals[:5])[:2000],
                summary=fact[:500],
                full_text=fact[:60000],
                key_points="",
                referenced_laws="、".join(str(a) for a in list(articles)[:20])[:2000],
                judgment_result="、".join(judgment_parts)[:500],
                tags="refined_legal_train",
            ))

            if len(batch) >= flush_size:
                await commit_batch(session, batch)
                batch = []
                if (inserted % 10000) < flush_size:
                    logger.info("Part E: +%d cases (total %d/%d)",
                                flush_size, inserted, len(records))
                REFINED_CHECKPOINT.write_text(str(inserted), encoding="utf-8")

        if batch:
            await commit_batch(session, batch)

    REFINED_CHECKPOINT.write_text(str(inserted), encoding="utf-8")
    logger.info("Part E complete: +%d cases", inserted)
    return inserted


# ===========================================================================
# Main
# ===========================================================================

async def final_stats():
    from sqlalchemy import select, func as sa_func
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import CourtCase, Law, LegalArticle
    async with async_session_factory() as session:
        laws_total = await session.scalar(select(sa_func.count(Law.id))) or 0
        articles_total = await session.scalar(select(sa_func.count(LegalArticle.id))) or 0
        cases_total = await session.scalar(select(sa_func.count(CourtCase.id))) or 0
        return {"laws": laws_total, "articles": articles_total, "cases": cases_total}


def main():
    parser = argparse.ArgumentParser(description="Million-scale legal corpus importer")
    parser.add_argument("--skip-laws", action="store_true", help="Skip Part A (law files)")
    parser.add_argument("--skip-cases", action="store_true", help="Skip Part B (HF case download)")
    parser.add_argument("--skip-embed", action="store_true", help="Skip Part C (Milvus vectors)")
    parser.add_argument("--target-total-cases", type=int, default=1000000)
    parser.add_argument("--embed-limit", type=int, default=100000)
    args = parser.parse_args()

    t0 = time.time()

    if not args.skip_laws and not LAW_CHECKPOINT.exists():
        asyncio.get_event_loop().run_until_complete(import_law_files())
    elif LAW_CHECKPOINT.exists():
        logger.info("Part A already done: %s", LAW_CHECKPOINT.read_text(encoding="utf-8"))

    if not args.skip_cases:
        asyncio.get_event_loop().run_until_complete(
            import_cases_to_target(args.target_total_cases)
        )

    if not args.skip_embed:
        embed_cases_into_milvus(args.embed_limit)

    loop = asyncio.get_event_loop()
    stats = loop.run_until_complete(final_stats())
    total = stats["laws"] + stats["articles"] + stats["cases"]
    logger.info("=" * 60)
    logger.info("FINAL STATS: laws=%s articles=%s cases=%s  =>  %s records total",
                stats["laws"], stats["articles"], stats["cases"], total)
    logger.info("Elapsed: %.1f min", (time.time() - t0) / 60)


if __name__ == "__main__":
    main()
