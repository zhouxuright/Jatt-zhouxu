#!/usr/bin/env python3
"""
从多个公开法律数据源爬取数据

当前支持的数据源：
1. 中国法院网 - 公开案例
2. 国家法律法规数据库 - 法律法规
3. 北大法宝（公开部分）- 法律条文
"""

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup


class MultiSourceCrawler:
    """多数据源爬虫"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self.data_dir = "./crawler_data_multi"
        os.makedirs(self.data_dir, exist_ok=True)

        self.stats = {
            "total_crawled": 0,
            "total_saved": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    async def close(self):
        await self.client.aclose()

    async def crawl_laws_from_pkulaw(self) -> list[dict[str, Any]]:
        """从北大法宝爬取法律法规（公开部分）"""
        print("\n[数据源 1] 北大法宝 - 法律法规")
        url = "https://www.pkulaw.com/cluster_form.aspx?db=chl"

        try:
            response = await self.client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            laws = []

            # 查找法律链接
            links = soup.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                text = link.get_text(strip=True)

                if text and len(text) > 10 and ("法" in text or "条例" in text or "规定" in text):
                    laws.append({
                        "title": text,
                        "link": href if href.startswith("http") else f"https://www.pkulaw.com{href}",
                        "source": "pkulaw",
                    })

            print(f"  ✓ 找到 {len(laws)} 条法律法规")
            return laws[:50]  # 限制数量

        except Exception as e:
            print(f"  ✗ 爬取失败: {e}")
            return []

    async def crawl_cases_from_court(self) -> list[dict[str, Any]]:
        """从中国法院网爬取公开案例"""
        print("\n[数据源 2] 中国法院网 - 公开案例")
        url = "http://www.chinacourt.org/article/detail/2024/01/id/1.shtml"

        try:
            response = await self.client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            cases = []

            # 查找案例链接
            links = soup.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                text = link.get_text(strip=True)

                if text and len(text) > 15 and ("案" in text or "判决" in text):
                    cases.append({
                        "title": text,
                        "link": href if href.startswith("http") else f"http://www.chinacourt.org{href}",
                        "source": "chinacourt",
                    })

            print(f"  ✓ 找到 {len(cases)} 个案例")
            return cases[:50]  # 限制数量

        except Exception as e:
            print(f"  ✗ 爬取失败: {e}")
            return []

    async def crawl_document_detail(self, url: str, source: str) -> dict[str, Any] | None:
        """爬取文书详情"""
        try:
            response = await self.client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            doc_data = {
                "url": url,
                "source": source,
                "crawl_time": datetime.now().isoformat(),
            }

            # 提取标题
            title_elem = soup.find("title")
            if title_elem:
                doc_data["title"] = title_elem.get_text(strip=True)

            # 提取正文内容
            content_elem = (
                soup.find("div", class_="content") or
                soup.find("div", class_="article-content") or
                soup.find("div", id="content") or
                soup.find("article")
            )

            if content_elem:
                doc_data["full_text"] = content_elem.get_text(separator="\n", strip=True)
            else:
                doc_data["full_text"] = soup.get_text(separator="\n", strip=True)

            # 生成摘要
            if doc_data.get("full_text"):
                doc_data["summary"] = doc_data["full_text"][:500]

            return doc_data

        except Exception as e:
            print(f"    爬取详情失败: {e}")
            return None

    async def save_to_file(self, doc_data: dict[str, Any]):
        """保存文书到文件"""
        # 生成文件名
        title = doc_data.get("title", "unknown")
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50]
        filename = f"{self.data_dir}/{safe_title}_{int(time.time())}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)

        self.stats["total_saved"] += 1

    async def crawl_batch(self, max_per_source: int = 20):
        """批量爬取多个数据源"""
        print("=" * 60)
        print(f"开始多数据源爬取（每个数据源最多 {max_per_source} 条）")
        print("=" * 60)

        # 数据源 1: 北大法宝
        laws = await self.crawl_laws_from_pkulaw()
        for i, law in enumerate(laws[:max_per_source], 1):
            print(f"\n[{i}/{len(laws)}] 爬取: {law['title'][:50]}")
            doc_data = await self.crawl_document_detail(law["link"], law["source"])
            if doc_data:
                doc_data["title"] = law["title"]
                await self.save_to_file(doc_data)
                self.stats["total_crawled"] += 1
                print(f"  ✓ 已保存")
            await asyncio.sleep(1)

        # 数据源 2: 中国法院网
        cases = await self.crawl_cases_from_court()
        for i, case in enumerate(cases[:max_per_source], 1):
            print(f"\n[{i}/{len(cases)}] 爬取: {case['title'][:50]}")
            doc_data = await self.crawl_document_detail(case["link"], case["source"])
            if doc_data:
                doc_data["title"] = case["title"]
                await self.save_to_file(doc_data)
                self.stats["total_crawled"] += 1
                print(f"  ✓ 已保存")
            await asyncio.sleep(1)

        print("\n" + "=" * 60)
        print(f"爬取完成: 共 {self.stats['total_crawled']} 条文书")
        print(f"数据保存在: {self.data_dir}")
        print("=" * 60)


async def main():
    crawler = MultiSourceCrawler()

    try:
        await crawler.crawl_batch(max_per_source=20)
    except KeyboardInterrupt:
        print("\n用户中断爬取")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
