#!/usr/bin/env python3
"""
北大法宝爬虫 - 带手动登录 + 正确搜索交互

流程：
  1. 打开浏览器 -> 北大法宝首页
  2. 你手动输入手机号/密码登录
  3. 回到终端按 Enter
  4. 爬虫自动搜索并爬取法律法规
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.pkulaw.com"
DATA_DIR = "./crawler_data_pkulaw"


def load_checkpoint() -> dict[str, Any]:
    path = os.path.join(DATA_DIR, "checkpoint.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_page": 0, "crawled_ids": [], "start_time": datetime.now().isoformat()}


def save_checkpoint(checkpoint: dict[str, Any]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "checkpoint.json"), "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)


async def wait_for_login(page):
    """等待用户手动登录"""
    # 先进入首页
    logger.info("打开北大法宝首页")
    await page.goto(BASE_URL, wait_until="networkidle")
    await asyncio.sleep(2)

    # 查找登录入口
    login_link = await page.evaluate("""() => {
        const links = document.querySelectorAll('a[href]');
        for (const a of links) {
            const text = a.innerText?.trim() || '';
            const href = a.getAttribute('href') || '';
            if ((text.includes('登录') || text.includes('注册')) && href) {
                return { text, href };
            }
        }
        return null;
    }""")

    if login_link:
        logger.info(f"找到登录入口: {login_link}")
        login_href = login_link["href"]
        if not login_href.startswith("http"):
            login_href = f"{BASE_URL}{login_href}"
        await page.goto(login_href, wait_until="networkidle")
    else:
        # 尝试常见登录 URL
        for url in [f"{BASE_URL}/account/login", f"{BASE_URL}/login", f"{BASE_URL}/user/login"]:
            try:
                await page.goto(url, wait_until="networkidle", timeout=5000)
                break
            except:
                continue

    await asyncio.sleep(2)

    logger.info("=" * 60)
    logger.info("请在浏览器窗口中手动登录北大法宝")
    logger.info("登录成功后回到终端按 Enter 键继续...")
    logger.info("=" * 60)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, ">>> 登录完成后按 Enter 继续爬取...")

    # 验证登录
    await page.goto(BASE_URL, wait_until="networkidle")
    await asyncio.sleep(2)
    logger.info("✅ 继续爬取流程")


async def search_and_get_results(page, keyword: str, page_num: int) -> list[dict[str, Any]]:
    """通过搜索框搜索法律法规"""

    # 回到首页搜索
    logger.info(f"访问北大法宝首页")
    await page.goto(BASE_URL, wait_until="networkidle")
    await asyncio.sleep(2)

    # 查找搜索框
    search_selectors = [
        "input[type='text']",
        "input[name='keyword']",
        "input[name='searchKey']",
        "input.search-input",
        "input[placeholder*='搜索']",
        "input[placeholder*='检索']",
        "input[placeholder*='关键词']",
        "#searchkey",
        "#keyword",
        ".search-input input",
    ]

    search_input = None
    for sel in search_selectors:
        try:
            elem = await page.wait_for_selector(sel, timeout=3000)
            if elem:
                is_visible = await elem.is_visible()
                if is_visible:
                    search_input = elem
                    logger.info(f"找到搜索框: {sel}")
                    break
        except:
            continue

    if not search_input:
        # 截图调试
        debug_dir = os.path.join(DATA_DIR, "debug")
        os.makedirs(debug_dir, exist_ok=True)
        screenshot_path = os.path.join(debug_dir, f"no_search_box_{page_num}.png")
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.warning(f"未找到搜索框，已保存截图: {screenshot_path}")

        # 保存 HTML
        html = await page.content()
        with open(os.path.join(debug_dir, f"home_{page_num}.html"), "w", encoding="utf-8") as f:
            f.write(html)
        return []

    # 输入关键词
    await search_input.click()
    await search_input.fill("")
    await search_input.type(keyword, delay=80)
    await asyncio.sleep(1)

    # 查找搜索按钮或按 Enter
    search_btn_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        ".search-btn",
        ".search-btn-click",
        "button.search",
        ".search_click",
        "a.search-btn",
    ]

    clicked = False
    for sel in search_btn_selectors:
        try:
            btn = await page.wait_for_selector(sel, timeout=2000)
            if btn and await btn.is_visible():
                await btn.click()
                clicked = True
                logger.info(f"点击搜索按钮: {sel}")
                break
        except:
            continue

    if not clicked:
        # 按 Enter 搜索
        await search_input.press("Enter")
        logger.info("按 Enter 搜索")

    # 等待结果加载
    logger.info("等待搜索结果...")
    await asyncio.sleep(5)

    # 保存调试页面
    debug_dir = os.path.join(DATA_DIR, "debug")
    os.makedirs(debug_dir, exist_ok=True)
    html = await page.content()
    with open(os.path.join(debug_dir, f"search_result_{page_num}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    current_url = page.url
    logger.info(f"当前 URL: {current_url}")

    # 截图
    await page.screenshot(path=os.path.join(debug_dir, f"search_result_{page_num}.png"), full_page=True)

    # 提取结果
    items_data = await page.evaluate(r"""() => {
        const results = [];
        const seen = new Set();

        // 查找结果列表
        const resultSelectors = [
            '.result-item', '.search-result-item', '.list-item',
            '.search_list li', '.result-list > div', '.result-list > li',
            'table.result-table tr', '.law-item', '.chl-item'
        ];

        for (const sel of resultSelectors) {
            const items = document.querySelectorAll(sel);
            if (items.length > 0) {
                for (const item of items) {
                    const text = item.innerText?.trim() || '';
                    const links = item.querySelectorAll('a[href]');
                    let title = '', link = '';
                    for (const a of links) {
                        const h = a.getAttribute('href') || '';
                        const t = a.innerText?.trim() || '';
                        if (t.length > 5 && (h.includes('chl') || h.includes('law') || h.includes('detail'))) {
                            title = t;
                            link = h;
                            break;
                        }
                    }
                    if (!title && text.length > 5) {
                        title = text.split('\n')[0]?.trim() || text.substring(0, 80);
                    }
                    if (link && !seen.has(link)) {
                        seen.add(link);
                        results.push({ title, link, full_text: text });
                    }
                }
                if (results.length > 0) break;
            }
        }

        // 如果还没找到，查找所有包含法律关键词的链接
        if (results.length === 0) {
            const allLinks = document.querySelectorAll('a[href]');
            for (const a of allLinks) {
                const href = a.getAttribute('href') || '';
                const text = a.innerText?.trim() || '';
                if (text.length > 8 && (href.includes('chl') || href.includes('/law/') || href.includes('detail'))) {
                    if (!seen.has(href)) {
                        seen.add(href);
                        results.push({ title: text, link: href });
                    }
                }
            }
        }

        // 诊断
        if (results.length === 0) {
            const body = document.body?.innerText || '';
            results.push({ title: '__DIAGNOSTIC__', link: '', full_text: body.substring(0, 3000) });
        }

        return results;
    }""")

    if items_data and items_data[0].get("title") == "__DIAGNOSTIC__":
        diag = items_data[0].get("full_text", "")
        logger.warning(f"页面诊断信息:\n{diag[:500]}")
        return []

    logger.info(f"第 {page_num} 页提取到 {len(items_data)} 条法律法规")
    return items_data


async def crawl_document_detail(page, link: str, title: str) -> dict[str, Any] | None:
    """爬取法律法规详情"""
    if link.startswith("http"):
        detail_url = link
    else:
        detail_url = f"{BASE_URL}{link}" if link.startswith("/") else f"{BASE_URL}/{link}"

    logger.info(f"  访问详情: {title[:50]}")

    try:
        await page.goto(detail_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        content = await page.evaluate("""() => {
            const selectors = [
                '.article-content', '.law-content', '.content',
                '.main-content', '#content', '.law-text',
                '.full-text', '.doc-content', '.detail-content',
                '.chl-content', '.formalLaw-content'
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText?.trim().length > 50) {
                    return el.innerText.trim();
                }
            }
            return document.body?.innerText || '';
        }""")

        page_title = await page.title()

        return {
            "title": title or page_title,
            "full_text": content,
            "url": detail_url,
            "crawl_time": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"  爬取详情失败: {e}")
        return None


async def run_crawler(keyword: str, pages: int):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False, slow_mo=300)
    page = await browser.new_page(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    checkpoint = load_checkpoint()
    crawled_ids = set(checkpoint.get("crawled_ids", []))
    stats = {"total_crawled": 0, "total_saved": 0, "errors": 0, "start_time": time.time()}

    try:
        logger.info("=" * 60)
        logger.info("北大法宝爬虫 - 手动登录模式")
        logger.info(f"关键词: {keyword} | 页数: {pages}")
        logger.info("=" * 60)

        await wait_for_login(page)
        os.makedirs(DATA_DIR, exist_ok=True)

        for page_num in range(1, pages + 1):
            logger.info(f"\n--- 第 {page_num}/{pages} 页 ---")

            results = await search_and_get_results(page, keyword, page_num)

            if not results:
                logger.warning(f"第 {page_num} 页无结果")
                break

            new_results = [r for r in results if r.get("link") not in crawled_ids]
            logger.info(f"其中新条目: {len(new_results)} 条")

            for item in new_results:
                link = item.get("link", "")
                title = item.get("title", "")

                if not link or link in crawled_ids:
                    continue

                doc_data = await crawl_document_detail(page, link, title)
                if doc_data:
                    safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50]
                    filename = os.path.join(DATA_DIR, f"{safe_title}_{int(time.time())}.json")
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(doc_data, f, ensure_ascii=False, indent=2)

                    stats["total_saved"] += 1
                    stats["total_crawled"] += 1
                    crawled_ids.add(link)
                    logger.info(f"  ✅ 已保存: {title[:50]}")

                await asyncio.sleep(2)

            checkpoint["last_page"] = page_num
            checkpoint["crawled_ids"] = list(crawled_ids)[-10000:]
            save_checkpoint(checkpoint)

            elapsed = time.time() - stats["start_time"]
            speed = stats["total_crawled"] / elapsed if elapsed > 0 else 0
            logger.info(f"进度: {stats['total_crawled']} 条 | 速度: {speed:.2f} 条/秒")
            await asyncio.sleep(3)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"爬取完成: 共 {stats['total_crawled']} 条法律法规")
        logger.info(f"数据保存在: {DATA_DIR}")
        logger.info(f"{'=' * 60}")

    except KeyboardInterrupt:
        logger.info("\n用户中断")
    finally:
        checkpoint["crawled_ids"] = list(crawled_ids)[-10000:]
        save_checkpoint(checkpoint)
        await browser.close()
        await pw.stop()


def main():
    parser = argparse.ArgumentParser(description="北大法宝爬虫（手动登录）")
    parser.add_argument("--keyword", type=str, default="合同法", help="搜索关键词")
    parser.add_argument("--pages", type=int, default=5, help="爬取页数")
    args = parser.parse_args()
    asyncio.run(run_crawler(args.keyword, args.pages))


if __name__ == "__main__":
    main()
