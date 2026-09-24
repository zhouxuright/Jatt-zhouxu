"""法律数据全量导入 CLI 入口。

5 阶段管道：DOWNLOAD(可选) → PARSE → DEDUP → PG_IMPORT → EMBED → MILVUS

使用方法：
    python -m app.rag.mass_import                    # 全量导入
    python -m app.rag.mass_import --phase parse      # 仅解析
    python -m app.rag.mass_import --phase pg_import  # 仅 PostgreSQL
    python -m app.rag.mass_import --phase milvus     # 仅 Milvus
    python -m app.rag.mass_import --skip-milvus      # 跳过 Milvus
    python -m app.rag.mass_import --resume           # 从断点恢复
    python -m app.rag.mass_import --dry-run          # 仅统计
    python -m app.rag.mass_import --all-versions     # 保留所有版本
    python -m app.rag.mass_import --device cuda      # GPU 加速
    python -m app.rag.mass_import --download-datasets  # 先下载扩展数据集再导入
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.mass_import.checkpoint import ImportCheckpoint
from app.rag.mass_import.dedup import ArticleDeduplicator
from app.rag.mass_import.parser import (
    CrawlerDataParser,
    HuggingFaceParser,
    LawsJsonParser,
    SeedDataParser,
)
from app.rag.mass_import.pg_importer import OptimizedPGImporter
from app.rag.mass_import.progress import ImportProgress

logger = logging.getLogger(__name__)


# =========================================================================
# 阶段 1: 解析
# =========================================================================

def phase_parse(
    checkpoint: ImportCheckpoint,
    version_policy: str = "latest_active",
    dry_run: bool = False,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """阶段 1: 解析所有数据源。

    Returns:
        (laws_list, articles_list, cases_list, concepts_list)
    """
    if checkpoint.should_skip_phase("parse"):
        logger.info("Phase 'parse' already completed, skipping")
        return _load_from_intermediate(checkpoint)

    logger.info("=" * 60)
    logger.info("PHASE 1: PARSE")
    logger.info("=" * 60)

    all_articles: list[dict[str, Any]] = []
    laws_list: list[dict[str, Any]] = []
    cases_list: list[dict[str, Any]] = []
    concepts_list: list[dict[str, Any]] = []

    # --- 1a: laws.json (核心数据源, ~1,000,000 条文) ---
    logger.info("\n--- 1a: Parsing laws.json ---")
    try:
        parser = LawsJsonParser()
        laws, articles = parser.parse_all(version_policy=version_policy)
        for art in articles:
            art["_source"] = "laws_json"
        laws_list.extend(laws)
        all_articles.extend(articles)
        logger.info("  laws.json: %d laws, %d articles", len(laws), len(articles))
    except FileNotFoundError:
        logger.warning("laws.json not found, skipping")
    except Exception as exc:
        logger.error("Failed to parse laws.json: %s", exc)

    # --- 1b: HuggingFace 文件 ---
    logger.info("\n--- 1b: Parsing HuggingFace files ---")
    try:
        hf_parser = HuggingFaceParser()
        hf_articles = hf_parser.parse_all()
        for art in hf_articles:
            art["_source"] = "hf_laws"
        all_articles.extend(hf_articles)
        logger.info("  HuggingFace: %d articles", len(hf_articles))
    except Exception as exc:
        logger.error("Failed to parse HuggingFace files: %s", exc)

    # --- 1c: 爬虫数据 ---
    logger.info("\n--- 1c: Parsing crawler data ---")
    try:
        crawler_parser = CrawlerDataParser()
        crawler_articles = crawler_parser.parse_laws()
        for art in crawler_articles:
            art["_source"] = "crawler_data"
        all_articles.extend(crawler_articles)

        crawler_concepts = crawler_parser.parse_concepts()
        concepts_list.extend(crawler_concepts)
        logger.info("  Crawler: %d articles, %d concepts",
                     len(crawler_articles), len(crawler_concepts))
    except Exception as exc:
        logger.error("Failed to parse crawler data: %s", exc)

    # --- 1d: 种子数据 ---
    logger.info("\n--- 1d: Parsing seed data ---")
    try:
        seed_parser = SeedDataParser()
        seed_articles = seed_parser.parse_articles()
        for art in seed_articles:
            art["_source"] = "seed"
        all_articles.extend(seed_articles)

        seed_laws = seed_parser.parse_laws()
        laws_list.extend(seed_laws)

        seed_cases = seed_parser.parse_cases()
        cases_list.extend(seed_cases)

        seed_concepts = seed_parser.parse_concepts()
        concepts_list.extend(seed_concepts)
        logger.info("  Seed: %d laws, %d articles, %d cases, %d concepts",
                     len(seed_laws), len(seed_articles), len(seed_cases), len(seed_concepts))
    except Exception as exc:
        logger.error("Failed to parse seed data: %s", exc)

    # --- 1e: 扩展数据集（SFT、案例、问答对） ---
    logger.info("\n--- 1e: Parsing expanded datasets ---")
    try:
        from app.rag.data_expansion import DATASETS as EXPANDED_DATASETS
        from app.rag.data_expansion import DataExpansionManager
        exp_manager = DataExpansionManager()
        for key in EXPANDED_DATASETS:
            path = exp_manager._data_dir / f"{key}.jsonl"
            if path.exists():
                count = 0
                for record in exp_manager.iter_records(key):
                    record["_source"] = f"expanded_{key}"
                    all_articles.append(record)
                    count += 1
                if count > 0:
                    logger.info("  %s: %d records", key, count)
    except Exception as exc:
        logger.warning("Failed to load expanded datasets: %s", exc)

    logger.info("\n--- Parse Summary ---")
    logger.info("  Total: %d laws, %d articles, %d cases, %d concepts",
                 len(laws_list), len(all_articles), len(cases_list), len(concepts_list))

    if dry_run:
        # 统计分类
        category_counts: dict[str, int] = {}
        for art in all_articles:
            cat = art.get("category", "其他")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        logger.info("\n  Articles by category:")
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            logger.info("    %-15s: %6d", cat, count)
        return laws_list, all_articles, cases_list, concepts_list

    # 保存中间文件（避免重新解析 378MB）
    _save_intermediate(checkpoint, "parsed", all_articles)
    checkpoint.update_stats(
        total_laws=len(laws_list),
        total_articles_before_dedup=len(all_articles),
        total_cases=len(cases_list),
        total_concepts=len(concepts_list),
    )
    checkpoint.mark_phase_completed("parse")

    return laws_list, all_articles, cases_list, concepts_list


# =========================================================================
# 阶段 2: 去重
# =========================================================================

def phase_dedup(
    checkpoint: ImportCheckpoint,
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """阶段 2: 去重。"""
    if checkpoint.should_skip_phase("dedup"):
        logger.info("Phase 'dedup' already completed, loading from intermediate file")
        return _load_deduped_intermediate(checkpoint)

    logger.info("=" * 60)
    logger.info("PHASE 2: DEDUP")
    logger.info("=" * 60)

    deduplicator = ArticleDeduplicator()
    unique_articles = deduplicator.deduplicate(articles)

    logger.info("Dedup result: %d → %d unique articles",
                 len(articles), len(unique_articles))

    # 保存去重结果
    _save_intermediate(checkpoint, "deduped", unique_articles)
    checkpoint.update_stats(total_unique_articles=len(unique_articles))
    checkpoint.mark_phase_completed("dedup")

    return unique_articles


# =========================================================================
# 阶段 3: PostgreSQL 导入
# =========================================================================

async def phase_pg_import(
    checkpoint: ImportCheckpoint,
    laws: list[dict],
    articles: list[dict[str, Any]],
    cases: list[dict],
    concepts: list[dict],
) -> dict[str, int]:
    """阶段 3: 导入到 PostgreSQL。"""
    if checkpoint.should_skip_phase("pg_import"):
        logger.info("Phase 'pg_import' already completed, skipping")
        return checkpoint.stats

    logger.info("=" * 60)
    logger.info("PHASE 3: POSTGRESQL IMPORT")
    logger.info("=" * 60)

    from app.core.database import async_session_factory

    importer = OptimizedPGImporter(async_session_factory)

    # 导入法律
    if laws:
        logger.info("Importing %d laws...", len(laws))
        await importer.import_laws_bulk(laws)

    # 导入条文
    if articles:
        logger.info("Importing %d articles...", len(articles))
        await importer.import_articles_bulk(articles)

    # 导入案例（按案号去重 + ON CONFLICT 幂等）
    if cases:
        logger.info("Importing %d cases...", len(cases))
        from app.models.legal_knowledge import CourtCase
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        import uuid as uuid_mod

        deduped_cases: dict[str, dict[str, Any]] = {}
        for case in cases:
            cn = case.get("case_number", "")
            if cn:
                deduped_cases.setdefault(cn, case)
        logger.info("Cases after case_number dedup: %d", len(deduped_cases))

        case_values = []
        for cn, case in deduped_cases.items():
            case_values.append({
                "id": str(uuid_mod.uuid4()),
                "case_number": cn[:128],
                "title": (case.get("title") or "")[:512],
                "court_name": (case.get("court_name") or "")[:256],
                "case_type": (case.get("case_type") or "")[:64],
                "cause_of_action": (case.get("cause_of_action") or "")[:256],
                "decision_date": (case.get("decision_date") or "")[:32],
                "parties": (case.get("parties") or "")[:8000],
                "summary": (case.get("summary") or "")[:8000],
                "full_text": case.get("full_text") or "",
                "key_points": (case.get("key_points") or "")[:8000],
                "referenced_laws": (case.get("referenced_laws") or "")[:8000],
                "judgment_result": (case.get("judgment_result") or "")[:8000],
                "tags": (case.get("tags") or "")[:512],
            })

        if case_values:
            stmt = pg_insert(CourtCase).values(case_values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["case_number"])
            async with async_session_factory() as session:
                await session.execute(stmt)
                await session.commit()

    # 导入概念（按名称去重 + ON CONFLICT 幂等）
    if concepts:
        logger.info("Importing %d concepts...", len(concepts))
        from app.models.legal_knowledge import LegalConcept
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        import uuid as uuid_mod

        deduped_concepts: dict[str, dict[str, Any]] = {}
        for concept in concepts:
            name = concept.get("name", "")
            if name:
                deduped_concepts.setdefault(name, concept)
        logger.info("Concepts after name dedup: %d", len(deduped_concepts))

        concept_values = [
            {
                "id": str(uuid_mod.uuid4()),
                "name": name[:256],
                "definition": (c.get("definition") or "")[:8000],
                "category": (c.get("category") or "")[:128],
            }
            for name, c in deduped_concepts.items()
        ]

        if concept_values:
            stmt = pg_insert(LegalConcept).values(concept_values)
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
            async with async_session_factory() as session:
                await session.execute(stmt)
                await session.commit()

    stats = importer.get_stats()
    checkpoint.update_stats(**{f"pg_{k}": v for k, v in stats.items()})
    checkpoint.mark_phase_completed("pg_import")

    return stats


# =========================================================================
# 阶段 4: Milvus 向量导入
# =========================================================================

def phase_milvus_import(
    checkpoint: ImportCheckpoint,
    articles: list[dict[str, Any]],
    device: str = "cpu",
) -> dict[str, int]:
    """阶段 4: 向量化并导入 Milvus。"""
    if checkpoint.should_skip_phase("milvus"):
        logger.info("Phase 'milvus' already completed, skipping")
        return checkpoint.stats

    logger.info("=" * 60)
    logger.info("PHASE 4: MILVUS VECTOR IMPORT")
    logger.info("=" * 60)

    if device == "cuda":
        logger.info("GPU acceleration enabled")

    from app.rag.mass_import.milvus_importer import OptimizedMilvusImporter

    importer = OptimizedMilvusImporter()
    if not importer.connect():
        logger.error("Cannot connect to Milvus, aborting Milvus import")
        return {"error": "connection_failed"}

    # 断点续传：用实体数作为偏移起点（比全量 ID 查询高效）
    start_id = importer.count_entities()
    logger.info("Existing entities: %d, starting from ID %d", start_id, start_id)

    stats = importer.import_articles(
        articles,
        start_id=start_id,
        existing_ids=None,
    )

    checkpoint.update_stats(**{f"milvus_{k}": v for k, v in stats.items()})
    checkpoint.mark_phase_completed("milvus")

    return stats


# =========================================================================
# 中间文件 I/O
# =========================================================================

def _save_intermediate(
    checkpoint: ImportCheckpoint,
    stage: str,
    articles: list[dict[str, Any]],
) -> None:
    """保存中间结果到 JSONL 文件。"""
    if stage == "parsed":
        path = checkpoint.parsed_articles_path
    elif stage == "deduped":
        path = checkpoint.deduped_articles_path
    else:
        path = checkpoint._dir / f"{stage}.jsonl"

    logger.info("Saving %d articles to %s...", len(articles), path.name)
    with open(path, "w", encoding="utf-8") as f:
        for art in articles:
            # 清理不可序列化的字段
            clean = {k: v for k, v in art.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")
    logger.info("Saved %s (%.1f MB)", path.name, path.stat().st_size / 1024 / 1024)


def _load_from_intermediate(
    checkpoint: ImportCheckpoint,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """从中间文件加载解析结果。"""
    articles = _load_jsonl(checkpoint.parsed_articles_path)
    logger.info("Loaded %d articles from parsed intermediate file", len(articles))
    return [], articles, [], []


def _load_deduped_intermediate(checkpoint: ImportCheckpoint) -> list[dict[str, Any]]:
    """从中间文件加载去重结果。"""
    articles = _load_jsonl(checkpoint.deduped_articles_path)
    logger.info("Loaded %d articles from deduped intermediate file", len(articles))
    return articles


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """加载 JSONL 文件。"""
    if not path.exists():
        logger.warning("Intermediate file not found: %s", path)
        return []
    items: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return items


# =========================================================================
# 主流程
# =========================================================================

async def run_mass_import(
    phase: str | None = None,
    skip_milvus: bool = False,
    resume: bool = False,
    dry_run: bool = False,
    all_versions: bool = False,
    device: str = "cpu",
    run_id: str | None = None,
    download_datasets: bool = False,
) -> dict[str, Any]:
    """运行全量导入。

    Args:
        phase: 指定运行的阶段（None = 全部）
        skip_milvus: 跳过 Milvus 导入
        resume: 从上次中断处恢复
        dry_run: 仅统计不写入
        all_versions: 保留所有版本（不只最新有效版本）
        device: 嵌入模型设备 (cpu/cuda)
        run_id: 运行 ID（用于检查点）
    """
    results: dict[str, Any] = {"phases": {}}

    # 初始化检查点
    if run_id is None:
        if resume:
            # 查找最近的检查点
            checkpoints = ImportCheckpoint.list_checkpoints()
            if checkpoints:
                run_id = checkpoints[-1].get("run_id", ImportCheckpoint.generate_run_id())
            else:
                run_id = ImportCheckpoint.generate_run_id()
        else:
            run_id = ImportCheckpoint.generate_run_id()

    checkpoint = ImportCheckpoint(run_id)
    if not resume:
        checkpoint.reset()
    checkpoint._state["run_id"] = run_id

    version_policy = "all_versions" if all_versions else "latest_active"

    logger.info("=" * 60)
    logger.info("法律数据全量导入")
    logger.info("  Run ID: %s", run_id)
    logger.info("  Version policy: %s", version_policy)
    logger.info("  Phase: %s", phase or "all")
    logger.info("  Device: %s", device)
    logger.info("  Dry run: %s", dry_run)
    logger.info("=" * 60)

    start_time = time.time()

    # ---- Phase 0: Download expanded datasets (optional) ----
    if download_datasets:
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 0: DOWNLOAD EXPANDED DATASETS")
        logger.info("=" * 60)
        try:
            from app.rag.data_expansion import DataExpansionManager
            exp_manager = DataExpansionManager()
            dl_results = await exp_manager.download_all()
            results["phases"]["download"] = {
                "downloaded": len(dl_results),
                "datasets": list(dl_results.keys()),
            }
            logger.info("Downloaded %d expanded datasets", len(dl_results))
        except Exception as exc:
            logger.error("Failed to download expanded datasets: %s", exc)
            results["phases"]["download"] = {"error": str(exc)}

    # ---- Phase 1: Parse ----
    if phase in (None, "parse"):
        laws, articles, cases, concepts = phase_parse(
            checkpoint, version_policy, dry_run
        )
        results["phases"]["parse"] = {
            "laws": len(laws),
            "articles_before_dedup": len(articles),
            "cases": len(cases),
            "concepts": len(concepts),
        }

        if dry_run:
            results["dry_run"] = True
            return results
    else:
        laws, articles, cases, concepts = [], [], [], []
        # 从中间文件加载
        if checkpoint.is_phase_completed("parse"):
            articles = _load_deduped_intermediate(checkpoint) if checkpoint.is_phase_completed("dedup") else _load_from_intermediate(checkpoint)[1]

    # ---- Phase 2: Dedup ----
    if phase in (None, "dedup"):
        unique_articles = phase_dedup(checkpoint, articles)
        results["phases"]["dedup"] = {
            "before": len(articles),
            "after": len(unique_articles),
        }
    else:
        if checkpoint.is_phase_completed("dedup"):
            unique_articles = _load_deduped_intermediate(checkpoint)
        elif articles:
            unique_articles = phase_dedup(checkpoint, articles)
        else:
            unique_articles = _load_deduped_intermediate(checkpoint)

    results["phases"]["summary"] = {
        "unique_articles": len(unique_articles),
    }

    if dry_run:
        # 分类统计
        category_counts: dict[str, int] = {}
        for art in unique_articles:
            cat = art.get("category", "其他")
            category_counts[cat] = category_counts.get(cat, 0) + 1
        results["category_counts"] = category_counts
        results["dry_run"] = True
        return results

    # ---- Phase 3: PostgreSQL Import ----
    if not skip_milvus or phase == "pg_import":
        if phase in (None, "pg_import"):
            pg_stats = await phase_pg_import(
                checkpoint, laws, unique_articles, cases, concepts
            )
            results["phases"]["pg_import"] = pg_stats

    # ---- Phase 4: Milvus Import ----
    if not skip_milvus and phase in (None, "milvus"):
        milvus_stats = phase_milvus_import(checkpoint, unique_articles, device)
        results["phases"]["milvus"] = milvus_stats

    elapsed = time.time() - start_time
    results["total_elapsed_seconds"] = round(elapsed, 1)

    return results


# =========================================================================
# 输出摘要
# =========================================================================

def print_summary(results: dict[str, Any]) -> None:
    """打印导入结果摘要。"""
    print("\n" + "=" * 60)
    print("  法律数据全量导入 — 结果摘要")
    print("=" * 60)

    if results.get("dry_run"):
        print("\n[DRY RUN MODE]")

    phases = results.get("phases", {})

    if "parse" in phases:
        p = phases["parse"]
        print(f"\n[Phase 1: Parse]")
        print(f"  Laws: {p.get('laws', 0):,}")
        print(f"  Articles (before dedup): {p.get('articles_before_dedup', 0):,}")
        print(f"  Cases: {p.get('cases', 0):,}")
        print(f"  Concepts: {p.get('concepts', 0):,}")

    if "dedup" in phases:
        d = phases["dedup"]
        print(f"\n[Phase 2: Dedup]")
        print(f"  Before: {d.get('before', 0):,}")
        print(f"  After: {d.get('after', 0):,}")
        print(f"  Removed: {d.get('before', 0) - d.get('after', 0):,}")

    if "pg_import" in phases:
        pg = phases["pg_import"]
        if "error" not in pg:
            print(f"\n[Phase 3: PostgreSQL]")
            print(f"  Laws inserted: {pg.get('laws_inserted', 0):,}")
            print(f"  Laws skipped: {pg.get('laws_skipped', 0):,}")
            print(f"  Articles inserted: {pg.get('articles_inserted', 0):,}")
            print(f"  Articles skipped: {pg.get('articles_skipped', 0):,}")

    if "milvus" in phases:
        mv = phases["milvus"]
        if "error" not in mv:
            print(f"\n[Phase 4: Milvus]")
            print(f"  Vectors inserted: {mv.get('vectors_inserted', 0):,}")
            print(f"  Embeddings generated: {mv.get('embeddings_generated', 0):,}")
            print(f"  Batches processed: {mv.get('batches_processed', 0):,}")

    if "summary" in phases:
        print(f"\n[Summary]")
        print(f"  Total unique articles: {phases['summary'].get('unique_articles', 0):,}")

    if "category_counts" in results:
        print(f"\n[Category Distribution]")
        for cat, count in sorted(results["category_counts"].items(), key=lambda x: -x[1]):
            print(f"  {cat:20s}: {count:>10,}")

    elapsed = results.get("total_elapsed_seconds")
    if elapsed:
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        print(f"\nTotal time: {h}:{m:02d}:{s:02d}")

    print("\n" + "=" * 60)


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="法律数据全量导入工具 — 解析、去重、入库、向量化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m app.rag.mass_import                        # 全量导入
  python -m app.rag.mass_import --dry-run              # 仅统计
  python -m app.rag.mass_import --phase parse          # 仅解析
  python -m app.rag.mass_import --phase pg_import      # 仅 PostgreSQL
  python -m app.rag.mass_import --phase milvus         # 仅 Milvus
  python -m app.rag.mass_import --skip-milvus          # 跳过 Milvus
  python -m app.rag.mass_import --resume               # 从断点恢复
  python -m app.rag.mass_import --all-versions         # 保留所有版本
  python -m app.rag.mass_import --device cuda          # GPU 加速
  python -m app.rag.mass_import --download-datasets    # 先下载扩展数据集
        """,
    )
    parser.add_argument(
        "--phase",
        choices=["parse", "dedup", "pg_import", "milvus"],
        default=None,
        help="指定运行的阶段 (default: 全部)",
    )
    parser.add_argument(
        "--skip-milvus",
        action="store_true",
        help="跳过 Milvus 向量导入",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从上次中断处恢复",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计，不实际写入数据库",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="保留所有版本（默认仅保留最新有效版本）",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="嵌入模型运行设备 (default: cpu)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="运行 ID（用于检查点管理）",
    )
    parser.add_argument(
        "--download-datasets",
        action="store_true",
        help="下载扩展数据集（在解析阶段之前执行）",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    results = asyncio.run(run_mass_import(
        phase=args.phase,
        skip_milvus=args.skip_milvus,
        resume=args.resume,
        dry_run=args.dry_run,
        all_versions=args.all_versions,
        device=args.device,
        run_id=args.run_id,
        download_datasets=args.download_datasets,
    ))

    print_summary(results)


if __name__ == "__main__":
    main()
