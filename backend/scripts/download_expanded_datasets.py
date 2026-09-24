"""
法律数据大规模扩展 — 下载命令行工具。

下载并处理多个大规模开源法律数据集，保存到本地 JSONL 文件，
供 mass_import 管道导入 PostgreSQL 和 Milvus。

使用方法:
    cd backend
    python scripts/download_expanded_datasets.py --list        # 列出可用数据集
    python scripts/download_expanded_datasets.py --all         # 下载全部数据集
    python scripts/download_expanded_datasets.py --dataset cail2018  # 下载指定数据集
    python scripts/download_expanded_datasets.py --stats       # 查看统计信息
    python scripts/download_expanded_datasets.py --download-and-import  # 下载后导入
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from app.rag.data_expansion import (
    DATASETS,
    DataExpansionManager,
    print_expansion_summary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =========================================================================
# CLI 命令
# =========================================================================

def cmd_list(manager: DataExpansionManager) -> None:
    """列出所有可用数据集。"""
    datasets = manager.list_datasets()

    print("\n" + "=" * 80)
    print("  可用法律数据集")
    print("=" * 80)
    print()
    print(f"  {'#':<3s} {'Key':<24s} {'名称':<36s} {'预估':>10s}  {'已下载':>10s}  {'来源':10s}")
    print("  " + "-" * 100)

    for i, (key, meta) in enumerate(datasets.items(), 1):
        name = meta["name"][:34]
        estimated = f"{meta['estimated_records']:,}"
        local = f"{meta['local_records']:,}" if meta["downloaded"] else "—"
        source = meta["source"]
        status = "✓" if meta["downloaded"] else " "
        print(
            f"  {status}{i:<2d} {key:<24s} {name:<36s} {estimated:>10s}  {local:>10s}  {source:10s}"
        )

    total_est = sum(m["estimated_records"] for m in datasets.values())
    total_dl = sum(m["local_records"] for m in datasets.values())
    print("  " + "-" * 100)
    print(f"  {'合计':<64s} {total_est:>10,}  {total_dl:>10,}")
    print()


def cmd_stats(manager: DataExpansionManager) -> None:
    """显示统计信息。"""
    stats = manager.get_total_stats()
    print_expansion_summary(stats)


async def cmd_download(
    manager: DataExpansionManager,
    dataset_key: str | None = None,
) -> None:
    """下载数据集。"""
    if dataset_key:
        if dataset_key not in DATASETS:
            logger.error("未知数据集: %s", dataset_key)
            logger.info("可用数据集: %s", ", ".join(DATASETS.keys()))
            return

        logger.info("正在下载数据集: %s", DATASETS[dataset_key]["name"])
        start = time.time()
        path = await manager.download_dataset(dataset_key)
        elapsed = time.time() - start
        logger.info("下载完成: %s (%.1f 秒)", path, elapsed)
    else:
        logger.info("正在下载全部数据集...")
        start = time.time()
        results = await manager.download_all()
        elapsed = time.time() - start
        logger.info("下载完成: %d 个数据集 (%.1f 秒)", len(results), elapsed)

    # 打印最新统计
    stats = manager.get_total_stats()
    print_expansion_summary(stats)


async def cmd_download_and_import(
    manager: DataExpansionManager,
    dataset_key: str | None = None,
    skip_milvus: bool = False,
) -> None:
    """下载数据集后通过 mass_import 管道导入。"""
    # 第一步：下载
    await cmd_download(manager, dataset_key)

    # 第二步：提示用户运行 mass_import
    logger.info("=" * 60)
    logger.info("下载完成。现在可以通过 mass_import 管道导入:")
    logger.info("  python -m app.rag.mass_import --phase parse")
    logger.info("=" * 60)


# =========================================================================
# 主入口
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="法律数据大规模扩展 — 下载开源法律数据集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/download_expanded_datasets.py --list               # 列出数据集
  python scripts/download_expanded_datasets.py --stats              # 查看统计
  python scripts/download_expanded_datasets.py --all                # 下载全部
  python scripts/download_expanded_datasets.py --dataset cail2018   # 下载指定
  python scripts/download_expanded_datasets.py --download-and-import  # 下载后导入
        """,
    )

    # 操作模式（互斥）
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list", action="store_true",
        help="列出所有可用数据集",
    )
    group.add_argument(
        "--stats", action="store_true",
        help="显示统计信息",
    )
    group.add_argument(
        "--all", action="store_true",
        help="下载全部数据集",
    )
    group.add_argument(
        "--dataset", type=str, default=None,
        help="下载指定数据集（如 cail2018, disc_lawllm 等）",
    )
    group.add_argument(
        "--download-and-import", action="store_true",
        help="下载数据集并准备导入",
    )

    parser.add_argument(
        "--skip-milvus", action="store_true",
        help="跳过 Milvus 导入（仅用于 --download-and-import）",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    manager = DataExpansionManager()

    if args.list:
        cmd_list(manager)
    elif args.stats:
        cmd_stats(manager)
    elif args.all:
        asyncio.run(cmd_download(manager, dataset_key=None))
    elif args.dataset:
        asyncio.run(cmd_download(manager, dataset_key=args.dataset))
    elif args.download_and_import:
        asyncio.run(cmd_download_and_import(
            manager,
            dataset_key=None,
            skip_milvus=args.skip_milvus,
        ))


if __name__ == "__main__":
    main()
