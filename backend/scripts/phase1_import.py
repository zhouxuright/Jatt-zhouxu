# -*- coding: utf-8 -*-
"""阶段1：本地数据全量导入 — 统一运行入口。

将所有磁盘上已有的法律数据文件导入 PostgreSQL：

  1. laws.json 全量解析（22K+ 法律，~100万条文）
  2. expanded_datasets JSONL 导入（QA对/知识条目/交叉引用，~109万条）
  3. refined_legal_train.json 刑事案件导入（~12万条）
  4. SFT 问答数据导入（~1.9万条）

支持断点续跑：每个步骤完成后写入 checkpoint，跳过已完成的步骤。

使用方法:
    cd backend
    python scripts/phase1_import.py

或者分步运行:
    python -m app.rag.million_import  # Part A-D (已有)
    python scripts/import_expanded_datasets.py
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1_import")

CHECKPOINT_DIR = BACKEND_ROOT / "data" / "import_checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


async def ensure_tables():
    """确保数据库表已创建（通过 Alembic 迁移或直接建表）。"""
    from app.core.database import engine, Base
    from app.models import legal_knowledge  # noqa: F401 — 触发模型注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表已就绪")


async def step1_laws_json_full():
    """步骤1：laws.json 全量解析导入。"""
    checkpoint = CHECKPOINT_DIR / "million_laws_json.done"
    if checkpoint.exists():
        stats = json.loads(checkpoint.read_text(encoding="utf-8"))
        logger.info("步骤1 已完成: %s (laws=%d, articles=%d)",
                     checkpoint.name, stats.get("laws", 0), stats.get("articles", 0))
        return stats

    logger.info("=" * 60)
    logger.info("步骤1: laws.json 全量解析导入")
    logger.info("=" * 60)

    from app.rag.million_import import import_laws_json_full
    stats = await import_laws_json_full()

    logger.info("步骤1 完成: laws=+%d, articles=+%d",
                stats.get("laws", 0), stats.get("articles", 0))
    return stats


async def step2_expanded_datasets():
    """步骤2：导入 expanded_datasets JSONL 文件。"""
    from scripts.import_expanded_datasets import (
        import_qa_pairs_jsonl,
        import_sft_qa_data,
        import_knowledge_entries,
        import_cross_references,
    )

    logger.info("=" * 60)
    logger.info("步骤2: expanded_datasets JSONL 导入")
    logger.info("=" * 60)

    qa_count = await import_qa_pairs_jsonl()
    knowledge_count = await import_knowledge_entries()
    cross_count = await import_cross_references()
    sft_count = await import_sft_qa_data()

    total = qa_count + knowledge_count + cross_count + sft_count
    logger.info("步骤2 完成: 新增 %d 条", total)
    return {"total": total, "qa": qa_count, "knowledge": knowledge_count,
            "cross": cross_count, "sft": sft_count}


async def step3_refined_legal_train():
    """步骤3：refined_legal_train.json 刑事案件导入。"""
    checkpoint = CHECKPOINT_DIR / "refined_legal_train.done"
    if checkpoint.exists():
        count = checkpoint.read_text(encoding="utf-8").strip()
        logger.info("步骤3 已完成: %s cases", count)
        return int(count)

    logger.info("=" * 60)
    logger.info("步骤3: refined_legal_train.json 刑事案件导入")
    logger.info("=" * 60)

    from app.rag.million_import import import_refined_legal_train
    count = await import_refined_legal_train()

    logger.info("步骤3 完成: +%d cases", count)
    return count


async def final_statistics():
    """输出最终统计信息。"""
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
    print("阶段1 完成 — 数据库数据量统计")
    print("=" * 60)
    for table_name, count in sorted(counts.items()):
        bar = "█" * min(count // 10000, 50)
        print(f"  {table_name:30s}  {count:>12,}  {bar}")
    print("-" * 60)
    print(f"  {'总计':30s}  {total:>12,}")
    print("=" * 60)

    if total >= 7_000_000:
        print("\n  ✓ 目标达成: 总量 >= 7,000,000")
    else:
        print(f"\n  距离目标 7,000,000 还差 {7_000_000 - total:,} 条")

    return counts


async def main():
    """主函数 — 按顺序执行所有导入步骤。"""
    logger.info("=" * 60)
    logger.info("阶段1: 本地数据全量导入")
    logger.info("目标: 将 ~3.5M 条未入库数据导入 PostgreSQL")
    logger.info("=" * 60)

    t0 = time.time()

    # 确保表结构
    await ensure_tables()

    # 步骤1: laws.json 全量解析
    stats1 = await step1_laws_json_full()

    # 步骤2: expanded_datasets
    stats2 = await step2_expanded_datasets()

    # 步骤3: refined_legal_train
    stats3 = await step3_refined_legal_train()

    elapsed = time.time() - t0

    logger.info("\n" + "=" * 60)
    logger.info("阶段1 导入完成 (%.1f 分钟)", elapsed / 60)
    logger.info("=" * 60)
    if isinstance(stats1, dict):
        logger.info("  laws.json:    laws=+%d, articles=+%d",
                     stats1.get("laws", 0), stats1.get("articles", 0))
    logger.info("  expanded:     +%s 条", f"{stats2.get('total', 0):,}")
    logger.info("  refined:      +%s cases", f"{stats3:,}")
    logger.info("=" * 60)

    # 最终统计
    await final_statistics()


if __name__ == "__main__":
    asyncio.run(main())
