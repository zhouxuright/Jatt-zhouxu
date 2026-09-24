#!/usr/bin/env python3
"""
从公开 API 和数据集爬取法律数据

数据源：
1. 国家法律法规数据库 API（模拟）
2. 公开法律数据集
3. 政府公开数据
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any

import httpx


class PublicAPICrawler:
    """公开 API 爬虫"""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.data_dir = "./crawler_data_public"
        os.makedirs(self.data_dir, exist_ok=True)

        self.stats = {
            "total_crawled": 0,
            "total_saved": 0,
            "start_time": time.time(),
        }

    async def close(self):
        await self.client.aclose()

    async def crawl_law_articles(self) -> list[dict[str, Any]]:
        """爬取法律法规条文（从公开数据）"""
        print("\n[数据源 1] 法律法规条文")

        # 这里我们使用已有的种子数据作为基础
        # 实际生产环境中可以从公开 API 获取
        from app.rag.knowledge_seed import ARTICLES_DATA

        laws = []
        for article in ARTICLES_DATA:
            laws.append({
                "title": f"{article['law']} {article['num']}",
                "law_name": article["law"],
                "article_number": article["num"],
                "content": article["content"],
                "tags": article.get("tags", ""),
                "source": "seed_data",
            })

        print(f"  ✓ 获取 {len(laws)} 条法律条文")
        return laws

    async def crawl_court_cases(self) -> list[dict[str, Any]]:
        """爬取典型案例（从公开数据）"""
        print("\n[数据源 2] 典型案例")

        from app.rag.knowledge_seed import COURT_CASES_DATA

        cases = []
        for case in COURT_CASES_DATA:
            cases.append({
                "title": case["title"],
                "case_number": case.get("case_number", ""),
                "court_name": case.get("court_name", ""),
                "case_type": case.get("case_type", ""),
                "cause_of_action": case.get("cause_of_action", ""),
                "decision_date": case.get("decision_date", ""),
                "summary": case.get("summary", ""),
                "key_points": case.get("key_points", ""),
                "referenced_laws": case.get("referenced_laws", ""),
                "tags": case.get("tags", ""),
                "source": "seed_data",
            })

        print(f"  ✓ 获取 {len(cases)} 个案例")
        return cases

    async def crawl_legal_concepts(self) -> list[dict[str, Any]]:
        """爬取法律概念"""
        print("\n[数据源 3] 法律概念")

        from app.rag.knowledge_seed import LEGAL_CONCEPTS_DATA

        concepts = []
        for concept in LEGAL_CONCEPTS_DATA:
            concepts.append({
                "title": concept["name"],
                "name": concept["name"],
                "definition": concept["definition"],
                "category": concept.get("category", ""),
                "source": "seed_data",
            })

        print(f"  ✓ 获取 {len(concepts)} 个法律概念")
        return concepts

    async def save_to_file(self, doc_data: dict[str, Any], doc_type: str):
        """保存文书到文件"""
        title = doc_data.get("title", "unknown")
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_")[:50]
        filename = f"{self.data_dir}/{doc_type}_{safe_title}_{int(time.time())}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(doc_data, f, ensure_ascii=False, indent=2)

        self.stats["total_saved"] += 1

    async def crawl_batch(self):
        """批量爬取"""
        print("=" * 60)
        print("开始爬取公开法律数据")
        print("=" * 60)

        # 1. 法律条文
        laws = await self.crawl_law_articles()
        for law in laws:
            await self.save_to_file(law, "law")
            self.stats["total_crawled"] += 1
        print(f"  ✓ 已保存 {len(laws)} 条法律条文")

        # 2. 案例
        cases = await self.crawl_court_cases()
        for case in cases:
            await self.save_to_file(case, "case")
            self.stats["total_crawled"] += 1
        print(f"  ✓ 已保存 {len(cases)} 个案例")

        # 3. 法律概念
        concepts = await self.crawl_legal_concepts()
        for concept in concepts:
            await self.save_to_file(concept, "concept")
            self.stats["total_crawled"] += 1
        print(f"  ✓ 已保存 {len(concepts)} 个法律概念")

        print("\n" + "=" * 60)
        print(f"爬取完成: 共 {self.stats['total_crawled']} 条数据")
        print(f"数据保存在: {self.data_dir}")
        print("=" * 60)

        # 统计
        files = os.listdir(self.data_dir)
        print(f"\n文件统计:")
        print(f"  法律条文: {len([f for f in files if f.startswith('law_')])}")
        print(f"  案例: {len([f for f in files if f.startswith('case_')])}")
        print(f"  概念: {len([f for f in files if f.startswith('concept_')])}")


async def main():
    crawler = PublicAPICrawler()

    try:
        await crawler.crawl_batch()
    except KeyboardInterrupt:
        print("\n用户中断爬取")
    finally:
        await crawler.close()


if __name__ == "__main__":
    asyncio.run(main())
