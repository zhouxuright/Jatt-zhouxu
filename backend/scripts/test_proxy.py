#!/usr/bin/env python3
"""
测试代理 IP 集成

验证 Bright Data 代理是否正常工作
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crawlers.wenshu_crawler import WenshuCrawler, CrawlerConfig


async def test_proxy():
    """测试代理连接"""
    print("=" * 60)
    print("代理 IP 集成测试")
    print("=" * 60)

    print(f"\n代理配置:")
    print(f"  代理 URL: {CrawlerConfig.BRIGHTDATA_PROXY[:50]}...")
    print(f"  使用代理: {CrawlerConfig.USE_PROXY}")

    crawler = WenshuCrawler()

    try:
        # 测试 1: 直接请求（不使用代理）
        print("\n[测试 1] 直接请求裁判文书网（不使用代理）")
        CrawlerConfig.USE_PROXY = False

        url = "https://wenshu.court.gov.cn/"
        try:
            response = await crawler.client.get(url, timeout=10.0)
            print(f"  状态码: {response.status_code}")
            print(f"  响应长度: {len(response.text)} 字符")
            if response.status_code == 200:
                print("  ✓ 直接请求成功")
            else:
                print(f"  ✗ 直接请求失败: {response.status_code}")
        except Exception as e:
            print(f"  ✗ 直接请求异常: {e}")

        # 测试 2: 通过代理请求
        print("\n[测试 2] 通过 Bright Data 代理请求")
        CrawlerConfig.USE_PROXY = True

        try:
            response = await crawler.client.get(url, timeout=30.0)
            print(f"  状态码: {response.status_code}")
            print(f"  响应长度: {len(response.text)} 字符")

            if response.status_code == 200:
                print("  ✓ 代理请求成功")
                print(f"  响应前 200 字符: {response.text[:200]}")
            else:
                print(f"  ✗ 代理请求失败: {response.status_code}")
                print(f"  响应内容: {response.text[:500]}")
        except Exception as e:
            print(f"  ✗ 代理请求异常: {e}")
            import traceback
            traceback.print_exc()

        # 测试 3: 爬取列表页
        print("\n[测试 3] 爬取列表页（通过代理）")
        try:
            results = await crawler.crawl_list_page(page=1)
            print(f"  找到 {len(results)} 条文书")

            if len(results) > 0:
                print("  ✓ 列表页爬取成功")
                print(f"  第一条文书 ID: {results[0].get('rowkey', 'N/A')}")
            else:
                print("  ✗ 列表页爬取失败：未找到文书")
        except Exception as e:
            print(f"  ✗ 列表页爬取异常: {e}")
            import traceback
            traceback.print_exc()

    finally:
        await crawler.close()

    print("\n" + "=" * 60)
    print("代理 IP 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_proxy())
