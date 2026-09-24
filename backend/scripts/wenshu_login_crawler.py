#!/usr/bin/env python3
"""
裁判文书网爬虫 - 带手动登录 + 正确搜索交互

使用 Playwright 模拟真实用户操作：
  1. 打开浏览器 -> 登录页
  2. 你手动输入手机号/密码登录
  3. 回到终端按 Enter
  4. 爬虫自动：输入关键词 -> 点击搜索 -> 等待结果 -> 爬取文书
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

BASE_URL = "https://wenshu.court.gov.cn"
DATA_DIR = "./crawler_data_playwright"


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
    """等待用户在浏览器中手动登录"""
    login_url = f"{BASE_URL}/website/wenshu/181010CARHS5BS3C/index.html?open=login"
    logger.info(f"打开登录页面: {login_url}")
    await page.goto(login_url, wait_until="networkidle")
    await asyncio.sleep(2)

    logger.info("=" * 60)
    logger.info("请在浏览器窗口中手动登录裁判文书网")
    logger.info("登录成功后回到终端按 Enter 键继续...")
    logger.info("=" * 60)

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input, ">>> 登录完成后按 Enter 继续爬取...")

    # 验证登录状态
    await page.goto(BASE_URL, wait_until="networkidle")
    await asyncio.sleep(3)
    page_text = await page.evaluate("() => document.body?.innerText || ''")
    if "欢迎您" in page_text:
        logger.info("✅ 登录成功确认")
    else:
        logger.warning("⚠️ 未检测到登录成功标志，继续尝试...")


async def search_and_get_results(page, keyword: str, page_num: int) -> list[dict[str, Any]]:
    """通过搜索框输入关键词并获取结果"""

    # 先进入裁判文书首页（带搜索框的页面）
    home_url = f"{BASE_URL}/website/wenshu/181029CR4M5A62CH/index.html"
    logger.info(f"访问裁判文书检索页: {home_url}")
    await page.goto(home_url, wait_until="networkidle")
    await asyncio.sleep(3)

    # 在搜索框中输入关键词
    logger.info(f"输入搜索关键词: {keyword}")
    search_input = await page.wait_for_selector("input.searchKey, input[data-val='s21']", timeout=10000)
    await search_input.click()
    await search_input.fill("")
    await search_input.type(keyword, delay=100)
    await asyncio.sleep(1)

    # 点击搜索按钮
    logger.info("点击搜索按钮")
    search_btn = await page.wait_for_selector(".search-click, .search-middle + div", timeout=5000)
    await search_btn.click()

    # 等待搜索结果加载
    logger.info("等待搜索结果加载...")
    await asyncio.sleep(5)

    # 等待结果列表出现（尝试多种选择器）
    result_selectors = [
        ".result-list",
        ".search-result-list",
        ".list-content",
        ".result-item",
        ".search-result-item",
        "[class*='resultList']",
        ".complain_list",
        "table tbody tr",
    ]

    for sel in result_selectors:
        try:
            await page.wait_for_selector(sel, timeout=5000)
            logger.info(f"找到结果容器: {sel}")
            break
        except:
            continue

    # 保存搜索后的调试页面
    debug_dir = os.path.join(DATA_DIR, "debug")
    os.makedirs(debug_dir, exist_ok=True)
    page_content = await page.content()
    with open(os.path.join(debug_dir, f"search_after_{page_num}.html"), "w", encoding="utf-8") as f:
        f.write(page_content)
    logger.info(f"调试页面已保存: {debug_dir}/search_after_{page_num}.html")

    # 获取当前 URL（可能已跳转）
    current_url = page.url
    logger.info(f"当前页面 URL: {current_url}")

    # 用 JS 提取结果
    items_data = await page.evaluate(r"""() => {
        const results = [];
        const seen = new Set();

        // 方法1: 查找所有含 docId 的链接
        const allLinks = document.querySelectorAll('a[href*="docId"]');
        for (const a of allLinks) {
            const href = a.getAttribute('href') || '';
            const text = a.innerText?.trim() || '';
            const m = href.match(/docId=([^&]+)/);
            if (m && !seen.has(m[1])) {
                seen.add(m[1]);
                results.push({ doc_id: m[1], title: text, link: href });
            }
        }

        // 方法2: 查找 181107ANFZ0BXSK4 链接（文书详情页）
        if (results.length === 0) {
            const detailLinks = document.querySelectorAll('a[href*="181107ANFZ0BXSK4"]');
            for (const a of detailLinks) {
                const href = a.getAttribute('href') || '';
                const text = a.innerText?.trim() || '';
                if (text.length > 3 && !seen.has(href)) {
                    seen.add(href);
                    const m = href.match(/docId=([^&]+)/);
                    results.push({ doc_id: m ? m[1] : '', title: text, link: href });
                }
            }
        }

        // 方法3: 查找结果列表中的 div（裁判文书网的列表项通常有特定结构）
        if (results.length === 0) {
            // 查找带有 onclick 或特定 class 的列表项
            const allEls = document.querySelectorAll('.result-list > div, .result-list > li, .search_list > div, .search_list > li, .complain_list > div, .complain_list > li');
            for (const el of allEls) {
                const text = el.innerText?.trim() || '';
                if (text.length > 20 && text.length < 800) {
                    const links = el.querySelectorAll('a[href]');
                    let docId = '', link = '';
                    for (const l of links) {
                        const h = l.getAttribute('href') || '';
                        const dm = h.match(/docId=([^&]+)/);
                        if (dm) { docId = dm[1]; link = h; break; }
                        if (h.includes('181107')) { link = h; break; }
                    }
                    const title = text.split('\n')[0]?.trim() || text.substring(0, 80);
                    const key = docId || title;
                    if (!seen.has(key)) {
                        seen.add(key);
                        results.push({ doc_id: docId, title: title, link: link, full_text: text });
                    }
                }
            }
        }

        // 方法4: 暴力搜索 - 遍历所有 a 标签
        if (results.length === 0) {
            const allA = document.querySelectorAll('a[href]');
            for (const a of allA) {
                const href = a.getAttribute('href') || '';
                const text = a.innerText?.trim() || '';
                if (text.length > 8 &&
                    (href.includes('181107') || href.includes('181217') ||
                     href.includes('docId') || href.includes('ANFZ'))) {
                    if (!seen.has(href)) {
                        seen.add(href);
                        results.push({ doc_id: '', title: text, link: href });
                    }
                }
            }
        }

        // 方法5: 诊断 - 获取页面关键结构
        if (results.length === 0) {
            const body = document.body?.innerText || '';
            // 截取前2000字符作为诊断信息
            results.push({
                doc_id: '',
                title: '__DIAGNOSTIC__',
                link: '',
                full_text: body.substring(0, 3000),
            });
        }

        return results;
    }""")

    # 处理诊断信息
    if items_data and items_data[0].get("title") == "__DIAGNOSTIC__":
        diag = items_data[0].get("full_text", "")
        logger.warning(f"页面诊断信息（前500字）:\n{diag[:500]}")
        return []

    logger.info(f"第 {page_num} 页提取到 {len(items_data)} 条结果")
    return items_data


async def crawl_document_detail(page, doc_id: str, link: str) -> dict[str, Any] | None:
    """爬取文书详情"""
    if link and link.startswith("http"):
        detail_url = link
    elif link:
        detail_url = f"{BASE_URL}{link}" if not link.startswith("/") else f"{BASE_URL}{link}"
    elif doc_id:
        detail_url = f"{BASE_URL}/website/wenshu/181107ANFZ0BXSK4/index.html?docId={doc_id}"
    else:
        return None

    logger.info(f"  访问文书详情: {doc_id or link[:60]}")

    try:
        await page.goto(detail_url, wait_until="networkidle")
        await asyncio.sleep(3)

        content = await page.evaluate("""() => {
            const selectors = [
                '.doc-content', '.wenshu-content', '#DocContent',
                '.article-content', '.content', '.main-content',
                '.cpws_content', '.wenshu_main', '#content'
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && el.innerText?.trim().length > 50) {
                    return el.innerText.trim();
                }
            }
            return document.body?.innerText || '';
        }""")

        title = await page.title()

        return {
            "doc_id": doc_id,
            "title": title,
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
        logger.info("裁判文书网爬虫 - 手动登录模式")
        logger.info(f"关键词: {keyword} | 页数: {pages}")
        logger.info("=" * 60)

        await wait_for_login(page)
        os.makedirs(DATA_DIR, exist_ok=True)

        for page_num in range(1, pages + 1):
            logger.info(f"\n--- 第 {page_num}/{pages} 页 ---")

            results = await search_and_get_results(page, keyword, page_num)

            if not results:
                logger.warning(f"第 {page_num} 页无结果")
                # 保存截图用于调试
                screenshot_path = os.path.join(DATA_DIR, "debug", f"no_result_{page_num}.png")
                await page.screenshot(path=screenshot_path)
                logger.info(f"已保存截图: {screenshot_path}")
                break

            # 过滤掉已爬取的
            new_results = [r for r in results if not r.get("doc_id") or r["doc_id"] not in crawled_ids]
            logger.info(f"其中新文书: {len(new_results)} 条")

            for item in new_results:
                doc_id = item.get("doc_id", "")
                link = item.get("link", "")
                title = item.get("title", "")

                if not doc_id and not link:
                    continue

                doc_data = await crawl_document_detail(page, doc_id, link)
                if doc_data:
                    doc_data["title"] = title or doc_data.get("title", "")
                    doc_data["keyword"] = keyword

                    save_id = doc_id or str(int(time.time() * 1000))
                    filename = os.path.join(DATA_DIR, f"{save_id}.json")
                    with open(filename, "w", encoding="utf-8") as f:
                        json.dump(doc_data, f, ensure_ascii=False, indent=2)

                    stats["total_saved"] += 1
                    stats["total_crawled"] += 1
                    if doc_id:
                        crawled_ids.add(doc_id)
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
        logger.info(f"爬取完成: 共 {stats['total_crawled']} 条文书")
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
    parser = argparse.ArgumentParser(description="裁判文书网爬虫（手动登录）")
    parser.add_argument("--keyword", type=str, default="合同纠纷", help="搜索关键词")
    parser.add_argument("--pages", type=int, default=5, help="爬取页数")
    args = parser.parse_args()
    asyncio.run(run_crawler(args.keyword, args.pages))


if __name__ == "__main__":
    main()
