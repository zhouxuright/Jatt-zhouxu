"""基于 Playwright 的裁判文书网爬虫

使用浏览器模拟方式绕过反爬虫机制。
"""

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)


# =============================================================================
# 配置
# =============================================================================

class PlaywrightConfig:
    """Playwright 爬虫配置"""
    BASE_URL = "https://wenshu.court.gov.cn"
    DATA_DIR = "./crawler_data_playwright"
    CHECKPOINT_FILE = "./crawler_data_playwright/checkpoint.json"

    # 浏览器配置
    HEADLESS = True  # 无头模式
    SLOW_MO = 500  # 慢动作（毫秒），防止过快
    VIEWPORT = {"width": 1920, "height": 1080}

    # 爬取配置
    PAGES_TO_CRAWL = 10  # 每次爬取的页数
    DELAY_BETWEEN_PAGES = 3  # 页面间延迟（秒）
    DELAY_BETWEEN_ITEMS = 1  # 条目间延迟（秒）


# =============================================================================
# Playwright 爬虫
# =============================================================================

class PlaywrightCrawler:
    """使用 Playwright 爬取裁判文书网"""

    def __init__(self):
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.playwright = None
        self.checkpoint = self._load_checkpoint()
        self.stats = {
            "total_crawled": 0,
            "total_saved": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    async def start(self):
        """启动浏览器"""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=PlaywrightConfig.HEADLESS,
            slow_mo=PlaywrightConfig.SLOW_MO,
        )
        self.page = await self.browser.new_page(
            viewport=PlaywrightConfig.VIEWPORT,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        logger.info("浏览器已启动")

    async def close(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self._save_checkpoint()
        logger.info("浏览器已关闭")

    def _load_checkpoint(self) -> dict[str, Any]:
        """加载断点数据"""
        if os.path.exists(PlaywrightConfig.CHECKPOINT_FILE):
            try:
                with open(PlaywrightConfig.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载断点文件失败: {e}")
        return {
            "last_page": 0,
            "crawled_ids": [],
            "start_time": datetime.now().isoformat(),
        }

    def _save_checkpoint(self):
        """保存断点数据"""
        os.makedirs(os.path.dirname(PlaywrightConfig.CHECKPOINT_FILE), exist_ok=True)
        with open(PlaywrightConfig.CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(self.checkpoint, f, ensure_ascii=False, indent=2)

    def _is_crawled(self, doc_id: str) -> bool:
        """检查是否已爬取"""
        return doc_id in self.checkpoint["crawled_ids"]

    def _mark_crawled(self, doc_id: str, page: int):
        """标记为已爬取"""
        self.checkpoint["crawled_ids"].append(doc_id)
        self.checkpoint["last_page"] = page
        # 只保留最近 10000 个 ID
        if len(self.checkpoint["crawled_ids"]) > 10000:
            self.checkpoint["crawled_ids"] = self.checkpoint["crawled_ids"][-10000:]

    async def crawl_search_page(self, keyword: str = "", page_num: int = 1) -> list[dict[str, Any]]:
        """爬取搜索结果页面"""
        if not self.page:
            raise RuntimeError("浏览器未启动，请先调用 start()")

        try:
            # 先访问主页
            logger.info("访问裁判文书网首页")
            await self.page.goto(PlaywrightConfig.BASE_URL, wait_until="networkidle")
            await asyncio.sleep(3)

            # 构建搜索 URL
            search_url = f"{PlaywrightConfig.BASE_URL}/website/wenshu/181029CR4M5A62CH/index.html"
            if keyword:
                search_url += f"?s8={keyword}"
            search_url += f"&page={page_num}"

            logger.info(f"访问搜索页: {search_url}")

            await self.page.goto(search_url, wait_until="networkidle")
            await asyncio.sleep(5)  # 等待页面完全加载（增加等待时间）

            # 保存调试页面（始终保存，便于分析 DOM 结构）
            page_content = await self.page.content()
            debug_dir = f"{PlaywrightConfig.DATA_DIR}/debug"
            os.makedirs(debug_dir, exist_ok=True)
            debug_file = f"{debug_dir}/page_{page_num}.html"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(page_content)
            logger.info(f"调试页面已保存: {debug_file}")

            # 说明：
            # 裁判文书网当前搜索结果区域常由前端脚本动态渲染，直接对 HTML 用固定 CSS 选择器
            # 容易“命中不到”。因此这里改为：
            # 1) 先等待页面关键区域出现（搜索框/列表容器）
            # 2) 通过 JS 在“真实 DOM”中提取疑似文书条目（优先从列表容器中抽取，再降级到全局扫描）
            # 3) 将提取逻辑集中在一个 evaluate() 中，方便后续根据实际 DOM 做增量调整

            # 等待“裁判文书网搜索框”出现（真实 DOM 结构：input.searchKey.search-inp[data-val='s21']）
            # 注意：页面上存在大量其它 input（例如“意见建议”弹窗的 suggestSource），因此禁止使用 input[type=text] 这类宽泛选择器
            await self.page.wait_for_selector(".search-con input.searchKey.search-inp, input.searchKey.search-inp[data-val='s21']", timeout=15000)

            items_data = await self.page.evaluate(
                """() => {
                    const results = [];
                    const seen = new Set();

                    const push = (obj) => {
                      const key = obj.doc_id || obj.link || obj.title;
                      if (!key || seen.has(key)) return;
                      seen.add(key);
                      results.push(obj);
                    };

                    // 1) 优先在可能的列表容器内找（根据历史页面：存在 .list-box/.ws_con 等类名）
                    const containers = [
                      '.list-box',
                      '.ws_con',
                      '.container',
                      '[class*=\"list\"]',
                      '[class*=\"ws_\"]',
                    ];
                    let scope = null;
                    for (const sel of containers) {
                      const el = document.querySelector(sel);
                      if (el && (el.innerText || '').trim().length > 50) { scope = el; break; }
                    }
                    const root = scope || document;

                    // 2) 提取所有链接，优先带 docId 的
                    const links = root.querySelectorAll('a[href]');
                    for (const a of links) {
                      const href = a.getAttribute('href') || '';
                      const text = (a.innerText || '').trim();
                      if (!text) continue;

                      // 文书详情页常见特征：包含 docId 或特定详情页面路径
                      const m = href.match(/docId=([^&]+)/i);
                      const docId = m ? m[1] : '';
                      const looksLikeDetail = href.includes('docId=') || href.includes('181107ANFZ0BXSK4') || href.includes('wenshu/');

                      if (looksLikeDetail && text.length >= 4) {
                        push({ doc_id: docId, title: text, link: href });
                      }
                    }

                    // 3) 若仍然为空：从列表项文本特征抓取（包含“人民法院/判决书/裁定书/年份”）
                    if (results.length === 0) {
                      const candidates = root.querySelectorAll('div, li, tr');
                      for (const el of candidates) {
                        const text = (el.innerText || '').trim();
                        if (text.length < 30 || text.length > 1000) continue;
                        if (!(text.includes('人民法院') || text.includes('判决书') || text.includes('裁定书') || /\\d{4}年/.test(text))) continue;
                        const a = el.querySelector('a[href]');
                        const href = a ? (a.getAttribute('href') || '') : '';
                        const m = href.match(/docId=([^&]+)/i);
                        push({
                          doc_id: m ? m[1] : '',
                          title: text.split('\\n')[0] || text.slice(0, 80),
                          link: href,
                          full_text: text,
                        });
                      }
                    }

                    // 4) 诊断：仍为空则返回前 1500 字文本，便于排查 DOM/登录/验证码等阻断
                    if (results.length === 0) {
                      const body = document.body?.innerText || '';
                      push({ doc_id: '', title: '__DIAGNOSTIC__', link: '', full_text: body.slice(0, 1500) });
                    }

                    return results;
                }"""
            )

            if items_data and items_data[0].get("title") == "__DIAGNOSTIC__":
                logger.warning(f"页面诊断信息: {items_data[0].get('full_text', '')[:500]}")
                return []

            results: list[dict[str, Any]] = []
            for item_data in items_data:
                title = item_data.get("title", "")
                link = item_data.get("link", "")
                doc_id = item_data.get("doc_id", "") or self._extract_doc_id(link or "")
                if doc_id and self._is_crawled(doc_id):
                    continue
                results.append(
                    {
                        "doc_id": doc_id,
                        "title": title,
                        "case_number": "",
                        "court_name": "",
                        "decision_date": "",
                        "link": link,
                    }
                )

            logger.info(f"找到 {len(results)} 条文书")
            return results

        except Exception as e:
            logger.error(f"爬取搜索页失败: {e}")
            return []

    async def crawl_document_detail(self, doc_id: str, link: str) -> dict[str, Any] | None:
        """爬取文书详情页"""
        if not self.page:
            raise RuntimeError("浏览器未启动")

        if self._is_crawled(doc_id):
            logger.debug(f"文书已爬取，跳过: {doc_id}")
            return None

        # 构建详情 URL
        if link and not link.startswith("http"):
            detail_url = f"{PlaywrightConfig.BASE_URL}{link}"
        else:
            detail_url = link or f"{PlaywrightConfig.BASE_URL}/website/wenshu/181107ANFZ0BXSK4/index.html?docId={doc_id}"

        logger.info(f"访问详情页: {detail_url}")

        try:
            await self.page.goto(detail_url, wait_until="networkidle")
            await asyncio.sleep(2)

            # 等待内容加载
            await self.page.wait_for_selector(".doc-content, .wenshu-content", timeout=10000)

            # 提取文书内容
            content_elem = await self.page.query_selector(".doc-content, .wenshu-content")
            full_text = await content_elem.inner_text() if content_elem else ""

            # 提取标题
            title_elem = await self.page.query_selector(".doc-title, .wenshu-title")
            title = await title_elem.inner_text() if title_elem else ""

            # 提取案号
            case_num_elem = await self.page.query_selector(".case-num, .ah")
            case_number = await case_num_elem.inner_text() if case_num_elem else ""

            # 提取法院
            court_elem = await self.page.query_selector(".court, .fy")
            court = await court_elem.inner_text() if court_elem else ""

            # 提取案由
            cause_elem = await self.page.query_selector(".cause, .ay")
            cause = await cause_elem.inner_text() if cause_elem else ""

            # 提取日期
            date_elem = await self.page.query_selector(".date, .rq")
            date = await date_elem.inner_text() if date_elem else ""

            doc_data = {
                "doc_id": doc_id,
                "title": title,
                "case_number": case_number,
                "court_name": court,
                "cause_of_action": cause,
                "decision_date": date,
                "full_text": full_text,
                "crawl_time": datetime.now().isoformat(),
            }

            self.stats["total_crawled"] += 1
            return doc_data

        except Exception as e:
            logger.error(f"爬取详情页失败 (doc_id={doc_id}): {e}")
            self.stats["errors"] += 1
            return None

    def _extract_doc_id(self, link: str) -> str:
        """从链接中提取文书 ID"""
        if "docId=" in link:
            return link.split("docId=")[1].split("&")[0]
        return ""

    async def save_to_file(self, doc_data: dict[str, Any]):
        """保存文书到文件"""
        os.makedirs(PlaywrightConfig.DATA_DIR, exist_ok=True)
        doc_id = doc_data.get("doc_id", "unknown")
        filename = f"{PlaywrightConfig.DATA_DIR}/{doc_id}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)

        self.stats["total_saved"] += 1

    async def crawl_batch(self, keyword: str = "", pages: int = None):
        """批量爬取"""
        pages = pages or PlaywrightConfig.PAGES_TO_CRAWL
        start_page = self.checkpoint["last_page"] + 1

        logger.info(f"开始批量爬取: page {start_page} - {start_page + pages - 1}")

        for page_num in range(start_page, start_page + pages):
            logger.info(f"爬取第 {page_num} 页")

            # 爬取搜索页
            results = await self.crawl_search_page(keyword, page_num)

            if not results:
                logger.warning(f"第 {page_num} 页没有结果，可能已到达最后一页")
                break

            logger.info(f"第 {page_num} 页找到 {len(results)} 条新文书")

            # 爬取每个文书的详情
            for item in results:
                doc_id = item["doc_id"]
                link = item.get("link", "")

                # 爬取详情
                doc_data = await self.crawl_document_detail(doc_id, link)

                if doc_data:
                    # 合并列表页和详情页的数据
                    doc_data.update({
                        "title": item.get("title", doc_data.get("title", "")),
                        "case_number": item.get("case_number", doc_data.get("case_number", "")),
                    })

                    # 保存到文件
                    await self.save_to_file(doc_data)

                    # 标记为已爬取
                    self._mark_crawled(doc_id, page_num)

                # 延迟
                await asyncio.sleep(PlaywrightConfig.DELAY_BETWEEN_ITEMS)

            # 页面间延迟
            await asyncio.sleep(PlaywrightConfig.DELAY_BETWEEN_PAGES)

            # 打印进度
            elapsed = time.time() - self.stats["start_time"]
            speed = self.stats["total_crawled"] / elapsed if elapsed > 0 else 0
            logger.info(
                f"进度: {self.stats['total_crawled']} 条 | "
                f"速度: {speed:.2f} 条/秒 | "
                f"错误: {self.stats['errors']}"
            )

        logger.info(f"批量爬取完成: 共 {self.stats['total_crawled']} 条文书")


# =============================================================================
# 主函数
# =============================================================================

async def main():
    """主函数"""
    crawler = PlaywrightCrawler()

    try:
        await crawler.start()

        # 爬取合同纠纷案例（示例）
        await crawler.crawl_batch(keyword="合同纠纷", pages=10)

    except KeyboardInterrupt:
        logger.info("用户中断爬取")
    finally:
        await crawler.close()
        logger.info(f"爬取统计: {crawler.stats}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
