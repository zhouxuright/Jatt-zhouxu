#!/usr/bin/env python3
"""
智能测试脚本 - 自动选择最佳爬取方式
"""

import asyncio
import httpx
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crawlers.wenshu_crawler import WenshuCrawler, CrawlerConfig


async def test_connection():
    """测试不同的连接方式"""
    print("=" * 60)
    print("智能连接测试")
    print("=" * 60)

    # 测试 1: 直接访问（无代理）
    print("\n[测试 1] 直接访问裁判文书网（无代理）")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://wenshu.court.gov.cn/")
            if response.status_code == 200:
                print(f"  ✓ 直接访问成功: {response.status_code}")
                print(f"  ✓ 响应长度: {len(response.text)} 字符")
                return "direct"  # 推荐直接访问
            else:
                print(f"  ✗ 直接访问失败: {response.status_code}")
    except Exception as e:
        print(f"  ✗ 直接访问异常: {e}")

    # 测试 2: 使用 Bright Data 代理
    print("\n[测试 2] 使用 Bright Data 代理访问")
    try:
        CrawlerConfig.USE_PROXY = True
        crawler = WenshuCrawler()
        try:
            response = await crawler.client.get("https://wenshu.court.gov.cn/", timeout=30.0)
            if response.status_code == 200:
                print(f"  ✓ 代理访问成功: {response.status_code}")
                print(f"  ✓ 响应长度: {len(response.text)} 字符")
                await crawler.close()
                return "proxy"  # 推荐使用代理
            else:
                print(f"  ✗ 代理访问失败: {response.status_code}")
        finally:
            await crawler.close()
    except Exception as e:
        print(f"  ✗ 代理访问异常: {e}")

    print("\n" + "=" * 60)
    print("所有测试失败")
    print("=" * 60)
    print("\n建议:")
    print("1. 检查 VPN 是否开启 - 如果开启，请关闭后重试")
    print("2. 检查网络连接是否正常")
    print("3. 检查 Bright Data 代理配置是否正确")
    print("4. 联系 Bright Data 客服确认账户状态")

    return None


async def main():
    result = await test_connection()

    if result == "direct":
        print("\n" + "=" * 60)
        print("✓ 推荐使用直接访问模式")
        print("=" * 60)
        print("\n启动爬虫命令:")
        print("  cd backend")
        print("  python scripts/start_crawler.py --source wenshu --pages 10")

    elif result == "proxy":
        print("\n" + "=" * 60)
        print("✓ 推荐使用 Bright Data 代理模式")
        print("=" * 60)
        print("\n启动爬虫命令:")
        print("  cd backend")
        print("  python scripts/start_crawler.py --source wenshu --pages 10")

    else:
        print("\n✗ 无法建立连接，请检查网络或代理配置")


if __name__ == "__main__":
    asyncio.run(main())
