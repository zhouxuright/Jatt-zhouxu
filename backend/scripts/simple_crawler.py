#!/usr/bin/env python3
"""
简化版裁判文书网爬虫 - 直接获取页面数据

这个版本专注于获取真实的裁判文书数据，使用直接访问模式（不使用代理）。
"""

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup


class SimpleWenshuCrawler:
    """简化版裁判文书网爬虫"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.base_url = "https://wenshu.court.gov.cn"
        self.data_dir = "./crawler_data_simple"
        os.makedirs(self.data_dir, exist_ok=True)

        self.stats = {
            "total_crawled": 0,
            "total_saved": 0,
            "errors": 0,
            "start_time": time.time(),
        }

    async def close(self):
        await self.client.aclose()

    async def crawl_homepage(self) -> list[dict[str, Any]]:
        """爬取首页，获取最新文书列表"""
        try:
            print(f"访问首页: {self.base_url}")
            response = await self.client.get(self.base_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # 尝试找到文书列表
            results = []

            # 查找文书链接
            links = soup.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                text = link.get_text(strip=True)

                # 过滤出文书链接
                if "181107ANFZ0BXSK4" in href or "docId=" in href:
                    doc_id = ""
                    if "docId=" in href:
                        doc_id = href.split("docId=")[1].split("&")[0]

                    if doc_id and text:
                        results.append({
                            "doc_id": doc_id,
                            "title": text,
                            "link": href if href.startswith("http") else f"{self.base_url}{href}",
                        })

            print(f"找到 {len(results)} 条文书链接")
            return results

        except Exception as e:
            print(f"爬取首页失败: {e}")
            return []

    async def crawl_document_detail(self, url: str) -> dict[str, Any] | None:
        """爬取单个文书详情"""
        try:
            print(f"访问文书: {url}")
            response = await self.client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # 提取文书内容
            doc_data = {
                "url": url,
                "crawl_time": datetime.now().isoformat(),
            }

            # 提取标题
            title_elem = soup.find("title")
            if title_elem:
                doc_data["title"] = title_elem.get_text(strip=True)

            # 提取文书正文
            content_elem = soup.find("div", class_="doc-content") or soup.find("div", class_="wenshu-content")
            if content_elem:
                doc_data["full_text"] = content_elem.get_text(separator="\n", strip=True)
            else:
                # 尝试获取页面所有文本
                doc_data["full_text"] = soup.get_text(separator="\n", strip=True)

            # 提取案号、法院等信息（从文本中解析）
            text = doc_data.get("full_text", "")

            # 提取案号
            case_number_patterns = [
                r"[（(]\d{4}[）)][^\s]+字第\d+号",
                r"[（(]\d{4}[）)][^\s]+第\d+号",
            ]
            import re
            for pattern in case_number_patterns:
                match = re.search(pattern, text)
                if match:
                    doc_data["case_number"] = match.group(0)
                    break

            # 提取法院名称
            court_patterns = [
                r"([一-龥]+人民法院)",
                r"([一-龥]+中级人民法院)",
                r"([一-龥]+高级人民法院)",
            ]
            for pattern in court_patterns:
                match = re.search(pattern, text[:500])
                if match:
                    doc_data["court_name"] = match.group(1)
                    break

            # 提取裁判日期
            date_patterns = [
                r"(\d{4}年\d{1,2}月\d{1,2}日)",
                r"(\d{4}-\d{2}-\d{2})",
            ]
            for pattern in date_patterns:
                match = re.search(pattern, text)
                if match:
                    doc_data["decision_date"] = match.group(1)
                    break

            # 生成摘要（前 500 字）
            if doc_data.get("full_text"):
                doc_data["summary"] = doc_data["full_text"][:500]

            return doc_data

        except Exception as e:
            print(f"爬取文书详情失败: {e}")
            return None

    async def save_to_file(self, doc_data: dict[str, Any]):
        """保存文书到文件"""
        doc_id = doc_data.get("doc_id", "unknown")
        filename = f"{self.data_dir}/{doc_id}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)

        self.stats["total_saved"] += 1

    async def crawl_batch(self, max_docs: int = 10):
        """批量爬取"""
        print("=" * 60)
        print(f"开始批量爬取（目标: {max_docs} 条文书）")
        print("=" * 60)

        # 爬取首页获取文书列表
        doc_list = await self.crawl_homepage()

        if not doc_list:
            print("未找到文书，尝试直接访问搜索页面")
            # 可以尝试访问搜索页面
            return

        # 限制爬取数量
        doc_list = doc_list[:max_docs]

        print(f"\n准备爬取 {len(doc_list)} 条文书")

        for i, doc_info in enumerate(doc_list, 1):
            print(f"\n[{i}/{len(doc_list)}] 爬取: {doc_info.get('title', 'N/A')}")

            doc_data = await self.crawl_document_detail(doc_info["link"])

            if doc_data:
                doc_data["doc_id"] = doc_info["doc_id"]
                doc_data["title"] = doc_info.get("title", doc_data.get("title", ""))

                await self.save_to_file(doc_data)
                self.stats["total_crawled"] += 1

                print(f"  ✓ 已保存: {doc_data.get('case_number', 'N/A')}")

                # 显示进度
                elapsed = time.time() - self.stats["start_time"]
                speed = self.stats["total_crawled"] / elapsed if elapsed > 0 else 0
                print(f"  进度: {self.stats['total_crawled']}/{len(doc_list)} | 速度: {speed:.2f} 条/秒")

            # 延迟，避免请求过快
            await asyncio.sleep(2)

        print("\n" + "=" * 60)
        print(f"爬取完成: 共 {self.stats['total_crawled']} 条文书")
        print(f"数据保存在: {self.data_dir}")
        print("=" * 60)


async def main():
    crawler = SimpleWenshuCrawler()

    try:
        # 爬取 10 条文书
        await crawler.crawl_batch(max_docs=10)
    except KeyboardInterrupt:
        print("\n用户中断爬取")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
