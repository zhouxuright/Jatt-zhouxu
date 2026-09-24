# -*- coding: utf-8 -*-
"""全量数据扩充统一运行入口 — 阶段1+2+3。

将法律数据从 ~3.7M 扩充至上亿级别。

阶段1: 本地数据全量导入 (laws.json + expanded_datasets + refined_train)
阶段2: 开源数据集下载与导入 (HuggingFace/GitHub ~20M条)
阶段3: 多源大规模采集 (裁判文书网 + 合成数据 ~50M条)

使用方法:
    cd backend
    python scripts/run_full_expansion.py              # 运行全部阶段
    python scripts/run_full_expansion.py --phase 1    # 只运行阶段1
    python scripts/run_full_expansion.py --phase 2    # 只运行阶段2
    python scripts/run_full_expansion.py --phase 3    # 只运行阶段3
"""

import argparse
import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("full_expansion")


def run_script(script_path: str, description: str):
    """运行一个 Python 脚本。"""
    full_path = BACKEND_ROOT / script_path
    if not full_path.exists():
        logger.error("脚本不存在: %s", full_path)
        return False

    logger.info("=" * 60)
    logger.info("运行: %s", description)
    logger.info("脚本: %s", script_path)
    logger.info("=" * 60)

    t0 = time.time()
    result = subprocess.run(
        [sys.executable, str(full_path)],
        cwd=str(BACKEND_ROOT),
    )
    elapsed = time.time() - t0

    if result.returncode == 0:
        logger.info("[OK] %s 完成 (%.1f 分钟)", description, elapsed / 60)
        return True
    else:
        logger.error("[FAIL] %s 失败 (exit code %d)", description, result.returncode)
        return False


async def print_total_stats():
    """打印最终数据库统计。"""
    try:
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

        print("\n" + "=" * 70)
        print("  法律数据扩充 — 最终统计")
        print("=" * 70)
        for table_name, count in sorted(counts.items(), key=lambda x: -x[1]):
            bar_len = min(count // 20000, 50)
            bar = "█" * bar_len if bar_len > 0 else ""
            print(f"  {table_name:30s}  {count:>12,}  {bar}")
        print("-" * 70)
        print(f"  {'总计':30s}  {total:>12,}")

        if total >= 100_000_000:
            print(f"\n  ★★★ 目标达成: {total:,} >= 100,000,000 ★★★")
        elif total >= 10_000_000:
            print(f"\n  ★★ 千万级: {total:,} (目标1亿)")
        elif total >= 1_000_000:
            print(f"\n  ★ 百万级: {total:,} (目标1亿)")
        else:
            print(f"\n  当前: {total:,} (目标1亿)")

        print("=" * 70)
        return total

    except Exception as exc:
        logger.warning("无法查询数据库: %s", exc)
        return 0


def main():
    parser = argparse.ArgumentParser(description="法律数据全量扩充")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], default=0,
                        help="只运行指定阶段 (1/2/3)，默认运行全部")
    args = parser.parse_args()

    t0 = time.time()

    logger.info("=" * 70)
    logger.info("  法律智能辅助系统 — 数据全量扩充")
    logger.info("  目标: 上亿级别法律行业数据")
    logger.info("=" * 70)

    phases_to_run = [args.phase] if args.phase else [1, 2, 3]

    # 阶段1: 本地数据全量导入
    if 1 in phases_to_run:
        # 先确保数据库表已创建
        try:
            asyncio.run(_ensure_tables())
        except Exception as exc:
            logger.warning("建表失败 (可能已存在): %s", exc)

        run_script("scripts/phase1_import.py", "阶段1: 本地数据全量导入")

    # 阶段2: 开源数据集下载与导入
    if 2 in phases_to_run:
        run_script("scripts/phase2_download_import.py", "阶段2: 开源数据集下载与导入")

    # 阶段3: 多源大规模采集
    if 3 in phases_to_run:
        run_script("scripts/phase3_mass_crawler.py", "阶段3: 多源大规模采集")

    # 最终统计
    elapsed = time.time() - t0
    logger.info("=" * 70)
    logger.info("全部阶段完成 (总耗时 %.1f 分钟)", elapsed / 60)
    logger.info("=" * 70)

    try:
        asyncio.run(print_total_stats())
    except Exception as exc:
        logger.warning("统计失败: %s", exc)


async def _ensure_tables():
    """确保所有数据库表已创建。"""
    from app.core.database import engine, Base
    from app.models import legal_knowledge  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表已就绪")


if __name__ == "__main__":
    main()
