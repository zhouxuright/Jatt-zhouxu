"""
国家法律法规数据库（flk.npc.gov.cn）爬虫

特点：
- 优先使用站点公开的 JSON 接口（比解析 DOM 更稳定）
- 通过 /law-search/index/aggregateData 获取首页公开数据（热门查询/新法速递等）
- 通过 /law-search/search/flfgDetails 获取法规详情元数据（含文件路径、目录树等）
- 通过 /law-search/amazonFile/ofdGenerateLink 获取文件访问链接信息（注意：返回的 download_url 可能指向内网 OSS 域名，
  在不同网络环境下可达性不同；我们仍然把该链接与 filePath 一并保存，便于后续在可达环境下载）
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FlkCrawlerConfig:
    BASE_URL = "https://flk.npc.gov.cn"
    DATA_DIR = "./crawler_data_flk"
    TIMEOUT = 60.0


class FlkCrawler:
    def __init__(self):
        self.client = httpx.Client(
            timeout=httpx.Timeout(FlkCrawlerConfig.TIMEOUT, connect=10.0, read=FlkCrawlerConfig.TIMEOUT),
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
            },
            follow_redirects=True,
        )
        os.makedirs(FlkCrawlerConfig.DATA_DIR, exist_ok=True)
        self.stats = {"total_crawled": 0, "total_saved": 0, "errors": 0, "start_time": time.time()}

    def close(self) -> None:
        self.client.close()

    def aggregate_data(self) -> dict[str, Any]:
        url = f"{FlkCrawlerConfig.BASE_URL}/law-search/index/aggregateData"
        r = self.client.get(url)
        r.raise_for_status()
        return r.json()

    def flfg_details(self, bbbs: str) -> dict[str, Any]:
        url = f"{FlkCrawlerConfig.BASE_URL}/law-search/search/flfgDetails"
        r = self.client.get(url, params={"bbbs": bbbs})
        r.raise_for_status()
        return r.json()

    def ofd_generate_link(self, file_path: str) -> dict[str, Any]:
        url = f"{FlkCrawlerConfig.BASE_URL}/law-search/amazonFile/ofdGenerateLink"
        r = self.client.get(url, params={"filePath": file_path})
        r.raise_for_status()
        # 返回 content-type 可能是 text/plain;charset=UTF-8，但内容是 JSON
        return json.loads(r.text)

    def save_to_file(self, payload: dict[str, Any], filename: str) -> None:
        path = os.path.join(FlkCrawlerConfig.DATA_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.stats["total_saved"] += 1

    def crawl_home_samples(self, limit: int = 10) -> list[dict[str, Any]]:
        """
        爬取首页公开样例数据：
        - popularSearch（热门查询）
        - xfsd（新法速递）
        """
        agg = self.aggregate_data()
        data = agg.get("data", {}) if isinstance(agg, dict) else {}

        items: list[dict[str, Any]] = []
        for key in ("popularSearch", "xfsd"):
            raw_list = data.get(key) or []
            if isinstance(raw_list, list):
                items.extend(raw_list)

        # 去重（按 bbbs）
        dedup: dict[str, dict[str, Any]] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            bbbs = str(it.get("bbbs") or "").strip()
            if not bbbs:
                continue
            dedup[bbbs] = it

        return list(dedup.values())[: max(0, int(limit))]

    def crawl(self, limit: int = 10) -> None:
        logger.info("开始爬取国家法律法规数据库首页样例数据，limit=%s", limit)
        samples = self.crawl_home_samples(limit=limit)
        logger.info("获取到样例条目：%s", len(samples))

        for it in samples:
            bbbs = str(it.get("bbbs") or "").strip()
            title = str(it.get("title") or "").strip()
            if not bbbs:
                continue

            try:
                details = self.flfg_details(bbbs)
                d = details.get("data") if isinstance(details, dict) else None
                oss = (d or {}).get("ossFile") or {}

                file_links: dict[str, Any] = {}
                # 优先尝试 PDF（更通用）
                if isinstance(oss, dict):
                    pdf_path = oss.get("ossPdfPath")
                    if isinstance(pdf_path, str) and pdf_path.strip():
                        try:
                            file_links["pdf"] = self.ofd_generate_link(pdf_path.strip())
                        except Exception as exc:
                            file_links["pdf_error"] = str(exc)

                    word_path = oss.get("ossWordPath")
                    if isinstance(word_path, str) and word_path.strip():
                        try:
                            file_links["word"] = self.ofd_generate_link(word_path.strip())
                        except Exception as exc:
                            file_links["word_error"] = str(exc)

                record = {
                    "source": "flk.npc.gov.cn",
                    "bbbs": bbbs,
                    "title": title,
                    "home_item": it,
                    "details": details,
                    "file_links": file_links,
                    "crawl_time": datetime.now().isoformat(),
                }
                safe = "".join(c for c in title if c.isalnum() or c in " _-")[:40] or "law"
                self.save_to_file(record, f"law_{safe}_{bbbs}.json")
                self.stats["total_crawled"] += 1
                logger.info("✅ 已保存：%s", title or bbbs)
            except Exception as exc:
                self.stats["errors"] += 1
                logger.exception("爬取失败：%s (%s)", title or bbbs, exc)

        logger.info("爬取完成：%s", self.stats)

