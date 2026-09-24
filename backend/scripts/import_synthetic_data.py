"""
合成数据导入脚本 - 导入mass_expansion生成的1500万条合成数据

导入内容:
1. synthetic_qa_pairs.jsonl (1000万条) -> legal_qa_pairs表
2. synthetic_cases.jsonl (500万条) -> court_cases表

使用方法:
    python scripts/import_synthetic_data.py --type qa          # 仅导入QA对
    python scripts/import_synthetic_data.py --type cases       # 仅导入案例
    python scripts/import_synthetic_data.py --type all         # 全部导入
    python scripts/import_synthetic_data.py --dry-run          # 仅统计不导入
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# 确保项目根目录在sys.path中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import async_session_factory
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import text
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 数据文件路径
DATA_DIR = PROJECT_ROOT / "data" / "mass_expansion"
QA_FILE = DATA_DIR / "synthetic_qa_pairs.jsonl"
CASES_FILE = DATA_DIR / "synthetic_cases.jsonl"

# 批次大小
BATCH_SIZE = 1000


async def import_qa_pairs(dry_run: bool = False) -> dict[str, int]:
    """导入合成QA对到legal_qa_pairs表"""
    if not QA_FILE.exists():
        logger.error(f"QA文件不存在: {QA_FILE}")
        return {"error": "file_not_found"}

    logger.info("=" * 60)
    logger.info("导入合成QA对")
    logger.info(f"文件: {QA_FILE}")
    logger.info(f"批次大小: {BATCH_SIZE}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    stats = {
        "total_lines": 0,
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
    }

    start_time = time.time()
    batch = []

    async with async_session_factory() as session:
        # 确保表存在
        try:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS legal_qa_pairs (
                    id UUID PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    domain VARCHAR(128),
                    subarea VARCHAR(128),
                    keywords TEXT,
                    source VARCHAR(256),
                    quality_score FLOAT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            await session.commit()
        except Exception as e:
            logger.warning(f"表创建/检查失败: {e}")
            await session.rollback()

        with open(QA_FILE, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                stats["total_lines"] += 1

                try:
                    record = json.loads(line.strip())

                    # 提取字段
                    qa_data = {
                        "id": str(uuid.uuid4()),
                        "question": (record.get("question") or "")[:10000],
                        "answer": (record.get("answer") or "")[:50000],
                        "domain": (record.get("domain") or "")[:128],
                        "subarea": (record.get("subarea") or "")[:128],
                        "keywords": json.dumps(record.get("keywords", []), ensure_ascii=False)[:1000],
                        "source": (record.get("source") or "")[:256],
                        "quality_score": record.get("quality_score"),
                    }

                    batch.append(qa_data)

                    # 批次插入
                    if len(batch) >= BATCH_SIZE:
                        if not dry_run:
                            stmt = pg_insert(text("legal_qa_pairs")).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
                            result = await session.execute(stmt)
                            await session.commit()
                            stats["inserted"] += result.rowcount
                            stats["skipped"] += len(batch) - result.rowcount
                        else:
                            stats["inserted"] += len(batch)

                        batch = []

                        # 进度报告
                        if stats["total_lines"] % 100000 == 0:
                            elapsed = time.time() - start_time
                            rate = stats["total_lines"] / elapsed if elapsed > 0 else 0
                            logger.info(
                                f"进度: {stats['total_lines']:,} 条 | "
                                f"已插入: {stats['inserted']:,} | "
                                f"速度: {rate:.0f} 条/秒"
                            )

                except json.JSONDecodeError as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        logger.warning(f"JSON解析错误 (行 {line_num}): {e}")
                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        logger.error(f"处理错误 (行 {line_num}): {e}")

        # 处理剩余数据
        if batch and not dry_run:
            stmt = pg_insert(text("legal_qa_pairs")).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            result = await session.execute(stmt)
            await session.commit()
            stats["inserted"] += result.rowcount
            stats["skipped"] += len(batch) - result.rowcount
        elif batch and dry_run:
            stats["inserted"] += len(batch)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("QA对导入完成")
    logger.info(f"总行数: {stats['total_lines']:,}")
    logger.info(f"插入: {stats['inserted']:,}")
    logger.info(f"跳过: {stats['skipped']:,}")
    logger.info(f"错误: {stats['errors']:,}")
    logger.info(f"耗时: {elapsed:.1f}秒 ({elapsed/60:.1f}分钟)")
    if elapsed > 0:
        logger.info(f"平均速度: {stats['total_lines']/elapsed:.0f} 条/秒")
    logger.info("=" * 60)

    return stats


async def import_cases(dry_run: bool = False) -> dict[str, int]:
    """导入合成案例到court_cases表"""
    if not CASES_FILE.exists():
        logger.error(f"案例文件不存在: {CASES_FILE}")
        return {"error": "file_not_found"}

    logger.info("=" * 60)
    logger.info("导入合成案例")
    logger.info(f"文件: {CASES_FILE}")
    logger.info(f"批次大小: {BATCH_SIZE}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    stats = {
        "total_lines": 0,
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
    }

    start_time = time.time()
    batch = []

    async with async_session_factory() as session:
        with open(CASES_FILE, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                stats["total_lines"] += 1

                try:
                    record = json.loads(line.strip())

                    # 提取字段 - court_cases表结构
                    case_data = {
                        "id": str(uuid.uuid4()),
                        "case_number": (record.get("case_number") or "")[:128],
                        "title": (record.get("title") or "")[:512],
                        "court_name": (record.get("court") or "")[:256],
                        "case_type": (record.get("type") or "")[:64],
                        "cause_of_action": (record.get("domain") or "")[:256],
                        "decision_date": str(record.get("year") or "")[:32],
                        "parties": "",
                        "summary": (record.get("summary") or "")[:8000],
                        "full_text": (record.get("facts") or "") + "\n\n" + (record.get("judgment") or ""),
                        "key_points": json.dumps(record.get("keywords", []), ensure_ascii=False)[:8000],
                        "referenced_laws": "",
                        "judgment_result": (record.get("judgment") or "")[:8000],
                        "tags": (record.get("subarea") or "")[:512],
                    }

                    batch.append(case_data)

                    # 批次插入
                    if len(batch) >= BATCH_SIZE:
                        if not dry_run:
                            stmt = pg_insert(text("court_cases")).values(batch)
                            stmt = stmt.on_conflict_do_nothing(index_elements=["case_number"])
                            result = await session.execute(stmt)
                            await session.commit()
                            stats["inserted"] += result.rowcount
                            stats["skipped"] += len(batch) - result.rowcount
                        else:
                            stats["inserted"] += len(batch)

                        batch = []

                        # 进度报告
                        if stats["total_lines"] % 100000 == 0:
                            elapsed = time.time() - start_time
                            rate = stats["total_lines"] / elapsed if elapsed > 0 else 0
                            logger.info(
                                f"进度: {stats['total_lines']:,} 条 | "
                                f"已插入: {stats['inserted']:,} | "
                                f"速度: {rate:.0f} 条/秒"
                            )

                except json.JSONDecodeError as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        logger.warning(f"JSON解析错误 (行 {line_num}): {e}")
                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        logger.error(f"处理错误 (行 {line_num}): {e}")

        # 处理剩余数据
        if batch and not dry_run:
            stmt = pg_insert(text("court_cases")).values(batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["case_number"])
            result = await session.execute(stmt)
            await session.commit()
            stats["inserted"] += result.rowcount
            stats["skipped"] += len(batch) - result.rowcount
        elif batch and dry_run:
            stats["inserted"] += len(batch)

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("案例导入完成")
    logger.info(f"总行数: {stats['total_lines']:,}")
    logger.info(f"插入: {stats['inserted']:,}")
    logger.info(f"跳过: {stats['skipped']:,}")
    logger.info(f"错误: {stats['errors']:,}")
    logger.info(f"耗时: {elapsed:.1f}秒 ({elapsed/60:.1f}分钟)")
    if elapsed > 0:
        logger.info(f"平均速度: {stats['total_lines']/elapsed:.0f} 条/秒")
    logger.info("=" * 60)

    return stats


async def main(data_type: str = "all", dry_run: bool = False):
    """主函数"""
    logger.info("=" * 60)
    logger.info("合成数据导入工具")
    logger.info(f"数据类型: {data_type}")
    logger.info(f"Dry run: {dry_run}")
    logger.info("=" * 60)

    start_time = time.time()
    results = {}

    if data_type in ("qa", "all"):
        results["qa"] = await import_qa_pairs(dry_run)

    if data_type in ("cases", "all"):
        results["cases"] = await import_cases(dry_run)

    elapsed = time.time() - start_time

    logger.info("\n" + "=" * 60)
    logger.info("导入完成汇总")
    logger.info("=" * 60)

    if "qa" in results:
        qa = results["qa"]
        logger.info(f"QA对: {qa.get('inserted', 0):,} 条插入, {qa.get('errors', 0):,} 错误")

    if "cases" in results:
        cases = results["cases"]
        logger.info(f"案例: {cases.get('inserted', 0):,} 条插入, {cases.get('errors', 0):,} 错误")

    logger.info(f"总耗时: {elapsed:.1f}秒 ({elapsed/3600:.1f}小时)")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="合成数据导入工具")
    parser.add_argument(
        "--type",
        choices=["qa", "cases", "all"],
        default="all",
        help="导入的数据类型 (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计，不实际写入数据库",
    )

    args = parser.parse_args()
    asyncio.run(main(args.type, args.dry_run))
