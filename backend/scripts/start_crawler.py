#!/usr/bin/env python3
"""
法律知识库爬虫启动脚本

使用方法：
1. 启动裁判文书网爬虫（持续爬取）
   python start_crawler.py --source wenshu --continuous

2. 启动单次爬取（测试用）
   python start_crawler.py --source wenshu --pages 10

3. 处理已爬取的数据
   python start_crawler.py --process ./crawler_data

4. 批量导入到数据库
   python start_crawler.py --import ./crawler_data_processed

注意：
- 爬虫需要配置代理 IP 池（见 CrawlerConfig）
- 请遵守各网站的 robots.txt
- 数据仅用于法律研究和公共服务
"""

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crawlers.wenshu_crawler import WenshuCrawler
from app.crawlers.document_processor import BatchProcessor
from scripts.batch_import import PostgreSQLBatchImporter, MilvusBatchImporter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def crawl_wenshu(pages: int = None, continuous: bool = False):
    """爬取裁判文书网"""
    logger.info("=" * 60)
    logger.info("启动裁判文书网爬虫")
    logger.info("=" * 60)

    crawler = WenshuCrawler()

    try:
        if continuous:
            logger.info("模式: 持续爬取")
            # 持续爬取，直到手动停止
            while True:
                async for doc_data in crawler.crawl_batch():
                    await crawler.save_to_file(doc_data)

                    if crawler.stats["total_crawled"] % 10 == 0:
                        logger.info(
                            f"进度: {crawler.stats['total_crawled']} 条 | "
                            f"错误: {crawler.stats['errors']}"
                        )
        else:
            logger.info(f"模式: 单次爬取 {pages or 10} 页")
            async for doc_data in crawler.crawl_batch(end_page=pages):
                await crawler.save_to_file(doc_data)

    except KeyboardInterrupt:
        logger.info("用户中断爬取")
    finally:
        await crawler.close()
        logger.info(f"爬取统计: {crawler.stats}")


async def process_data(input_dir: str):
    """处理已爬取的数据"""
    logger.info("=" * 60)
    logger.info(f"处理数据: {input_dir}")
    logger.info("=" * 60)

    if not os.path.exists(input_dir):
        logger.error(f"目录不存在: {input_dir}")
        return

    processor = BatchProcessor()
    stats = await processor.process_directory(input_dir)
    logger.info(f"处理统计: {stats}")


async def import_data(input_dir: str):
    """批量导入数据到数据库"""
    logger.info("=" * 60)
    logger.info(f"导入数据: {input_dir}")
    logger.info("=" * 60)

    if not os.path.exists(input_dir):
        logger.error(f"目录不存在: {input_dir}")
        return

    # 1. 导入到 PostgreSQL
    logger.info("步骤 1: 导入到 PostgreSQL")
    pg_importer = PostgreSQLBatchImporter(batch_size=100)
    pg_stats = await pg_importer.import_from_directory(input_dir)
    logger.info(f"PostgreSQL 导入统计: {pg_stats}")

    # 2. 导入到 Milvus
    logger.info("步骤 2: 导入到 Milvus (向量数据库)")
    milvus_importer = MilvusBatchImporter(batch_size=100)
    milvus_stats = await milvus_importer.import_from_database()
    logger.info(f"Milvus 导入统计: {milvus_stats}")


def main():
    parser = argparse.ArgumentParser(description="法律知识库爬虫启动脚本")

    parser.add_argument(
        "--source",
        type=str,
        choices=["wenshu"],
        help="数据源（目前仅支持裁判文书网）"
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="爬取页数（默认 10 页）"
    )

    parser.add_argument(
        "--continuous",
        action="store_true",
        help="持续爬取模式"
    )

    parser.add_argument(
        "--process",
        type=str,
        metavar="DIR",
        help="处理已爬取的数据目录"
    )

    parser.add_argument(
        "--import",
        type=str,
        metavar="DIR",
        dest="import_dir",
        help="批量导入数据到数据库"
    )

    args = parser.parse_args()

    # 执行对应操作
    if args.process:
        asyncio.run(process_data(args.process))
    elif args.import_dir:
        asyncio.run(import_data(args.import_dir))
    elif args.source == "wenshu":
        asyncio.run(crawl_wenshu(pages=args.pages, continuous=args.continuous))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
