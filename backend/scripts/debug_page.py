#!/usr/bin/env python3
"""
调试脚本 - 查看裁判文书网页面结构
"""

import asyncio
from playwright.async_api import async_playwright


async def debug_page():
    """调试页面结构"""
    print("=" * 60)
    print("调试裁判文书网页面结构")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # 访问搜索页
        url = "https://wenshu.court.gov.cn/website/wenshu/181029CR4M5A62CH/index.html?s8=合同纠纷&page=1"
        print(f"\n访问: {url}")

        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(5)

        # 保存页面截图
        await page.screenshot(path="debug_screenshot.png")
        print("✓ 截图已保存: debug_screenshot.png")

        # 保存页面 HTML
        html = await page.content()
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("✓ HTML 已保存: debug_page.html")

        # 查找所有可能的列表元素
        selectors = [
            ".result-list",
            ".search-result-list",
            ".wenshu-list",
            "[class*='result']",
            "[class*='list']",
            "table",
            ".data-list",
            ".item-list",
        ]

        print("\n查找列表元素:")
        for selector in selectors:
            elements = await page.query_selector_all(selector)
            if elements:
                print(f"  ✓ {selector}: 找到 {len(elements)} 个元素")

                # 查看第一个元素的内容
                if elements:
                    first_html = await elements[0].inner_html()
                    print(f"    第一个元素 HTML 前 200 字符: {first_html[:200]}")

        # 查找所有链接
        print("\n查找链接:")
        links = await page.query_selector_all("a[href]")
        print(f"  找到 {len(links)} 个链接")

        # 查看前 10 个链接
        for i, link in enumerate(links[:10]):
            href = await link.get_attribute("href")
            text = await link.inner_text()
            if text.strip():
                print(f"  [{i}] {text.strip()[:50]} -> {href[:80]}")

        # 查找包含 docId 的链接
        print("\n查找包含 docId 的链接:")
        doc_links = []
        for link in links:
            href = await link.get_attribute("href") or ""
            if "docId" in href or "181107ANFZ0BXSK4" in href:
                doc_links.append(link)

        print(f"  找到 {len(doc_links)} 个文书链接")
        for i, link in enumerate(doc_links[:5]):
            href = await link.get_attribute("href")
            text = await link.inner_text()
            print(f"  [{i}] {text.strip()[:50]} -> {href[:80]}")

        await browser.close()

    print("\n" + "=" * 60)
    print("调试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(debug_page())
