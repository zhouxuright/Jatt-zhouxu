#!/usr/bin/env python3
"""
Playwright 爬虫启动脚本

使用方法：
1. 爬取默认关键词（合同纠纷）
   python scripts/start_playwright_crawler.py

2. 爬取指定关键词
   python scripts/start_playwright_crawler.py --keyword 劳动合同

3. 爬取指定页数
   python scripts/start_playwright_crawler.py --pages 20

4. 有头模式（显示浏览器）
   python scripts/start_playwright_crawler.py --headed
"""

import argparse
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crawlers.playwright_crawler import PlaywrightCrawler, PlaywrightConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_crawler(keyword: str, pages: int, headed: bool):
    """运行爬虫"""
    # 更新配置
    PlaywrightConfig.HEADLESS = not headed

    crawler = PlaywrightCrawler()

    try:
        logger.info("=" * 60)
        logger.info(f"启动 Playwright 爬虫")
        logger.info(f"关键词: {keyword}")
        logger.info(f"爬取页数: {pages}")
        logger.info(f"浏览器模式: {'有头' if headed else '无头'}")
        logger.info("=" * 60)

        await crawler.start()
        await crawler.crawl_batch(keyword=keyword, pages=pages)

    except KeyboardInterrupt:
        logger.info("用户中断爬取")
    except Exception as e:
        logger.error(f"爬取失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()
        logger.info(f"爬取统计: {crawler.stats}")


def main():
    parser = argparse.ArgumentParser(description="Playwright 裁判文书网爬虫")

    parser.add_argument(
        "--keyword",
        type=str,
        default="合同纠纷",
        help="搜索关键词（默认: 合同纠纷）"
    )

    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="爬取页数（默认: 10）"
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="使用有头模式（显示浏览器窗口）"
    )

    args = parser.parse_args()

    asyncio.run(run_crawler(
        keyword=args.keyword,
        pages=args.pages,
        headed=args.headed,
    ))


if __name__ == "__main__":
    main()
