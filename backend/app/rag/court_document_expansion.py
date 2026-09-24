"""裁判文书批量采集与导入系统。

数据源：
1. 中国裁判文书网 (wenshu.court.gov.cn) — ~1.67 亿篇
2. 人民法院案例库 (rmfyalk.court.gov.cn) — 权威案例
3. CAIL2018 开源数据集 — 260 万刑事案例
4. 开源法律 SFT 数据集 — 数百万 Q&A 对

数据规模路径：
  Level 1: 法律法规 66 万条文（✅ 已完成）
  Level 2: CAIL + SFT 数据 ~500 万条（✅ 本模块支持）
  Level 3: 裁判文书网采集 ~1 亿+条（⚡ 需配合代理池）
  Level 4: 全量数据 + 持续增量 → 上亿

使用方法：
    # 列出可下载的数据集
    python -m app.rag.court_document_expansion --list

    # 下载所有开源数据集
    python -m app.rag.court_document_expansion --download-all

    # 下载特定数据集
    python -m app.rag.court_document_expansion --dataset cail2018

    # 查看统计
    python -m app.rag.court_document_expansion --stats

    # 导入到数据库
    python -m app.rag.court_document_expansion --import

    # 启动裁判文书网采集（需要代理池）
    python -m app.rag.court_document_expansion --crawl --pages 1000
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import httpx

from app.services.proxy_service import get_proxy_service

logger = logging.getLogger(__name__)

# 数据目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXPANDED_DATA_DIR = PROJECT_ROOT / "data" / "expanded_datasets"
EXPANDED_DATA_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================================
# 数据集注册表
# =============================================================================

DATASET_REGISTRY = {
    # --- 案例数据集 ---
    "cail2018": {
        "name": "CAIL2018 中国法研杯",
        "description": "260万+ 刑事案例，用于判决预测",
        "url": "https://huggingface.co/datasets/coastalcph/fairlex",
        "estimated_records": 2_600_000,
        "data_type": "cases",
        "download_method": "huggingface_api",
        "hf_repo": "coastalcph/fairlex",
        "hf_config": "cail",
    },
    "cail_long": {
        "name": "CAIL-Long 长文本案例",
        "description": "来自中国裁判文书网的长文本案例",
        "url": "https://github.com/china-ai-law-challenge/CAIL",
        "estimated_records": 200_000,
        "data_type": "cases",
        "download_method": "github",
        "github_repo": "china-ai-law-challenge/CAIL",
    },
    "leven": {
        "name": "Leven 刑事案例数据集",
        "description": "8,116 个刑事案例，含结构化标签",
        "url": "https://huggingface.co/datasets/FudanDISC/Leven",
        "estimated_records": 8_116,
        "data_type": "cases",
        "download_method": "huggingface_api",
        "hf_repo": "FudanDISC/Leven",
    },
    # --- SFT 训练数据（法律 Q&A）---
    "disc_lawllm_sft": {
        "name": "DISC-LawLLM SFT 数据",
        "description": "复旦大学法律 SFT 训练数据，~50万条法律 Q&A",
        "url": "https://huggingface.co/datasets/ShengbinYue/DISC-LawLLM",
        "estimated_records": 500_000,
        "data_type": "qa_pairs",
        "download_method": "huggingface_api",
        "hf_repo": "ShengbinYue/DISC-LawLLM",
    },
    "chinese_law_sft": {
        "name": "Chinese-Law-SFT-Dataset",
        "description": "民商法/刑法 SFT 训练数据",
        "url": "https://huggingface.co/datasets/Aiiluo/Chinese-Law-SFT-Dataset",
        "estimated_records": 100_000,
        "data_type": "qa_pairs",
        "download_method": "huggingface_api",
        "hf_repo": "Aiiluo/Chinese-Law-SFT-Dataset",
    },
    "refined_legal_dataset": {
        "name": "Refined Chinese Legal Dataset",
        "description": "基于 CAIL2018 的精炼法律数据集",
        "url": "https://huggingface.co/datasets/SunSpace0923/Refined-Chinese-Legal-Dataset",
        "estimated_records": 50_000,
        "data_type": "qa_pairs",
        "download_method": "huggingface_api",
        "hf_repo": "SunSpace0923/Refined-Chinese-Legal-Dataset",
    },
    # --- 法律知识数据 ---
    "lawgpt_knowledge": {
        "name": "LaWGPT 法律知识数据",
        "description": "基于中文法律知识的大模型训练数据",
        "url": "https://github.com/pengxiao-song/LaWGPT",
        "estimated_records": 200_000,
        "data_type": "mixed",
        "download_method": "github",
        "github_repo": "pengxiao-song/LaWGPT",
    },
    "crime_kg_assistant": {
        "name": "CrimeKgAssistant 刑事法律问答",
        "description": "刑事法律知识图谱问答数据",
        "url": "https://github.com/LCS-0207/CrimeKgAssitant",
        "estimated_records": 50_000,
        "data_type": "qa_pairs",
        "download_method": "github",
        "github_repo": "LCS-0207/CrimeKgAssitant",
    },
    # --- 综合法律数据 ---
    "chinese_laws_pretrain": {
        "name": "Chinese Laws Pretrain",
        "description": "中国法律法规预训练数据（已在 data/datasets 中）",
        "url": "https://huggingface.co/datasets/Dusker/chinese-laws-pretrain",
        "estimated_records": 5_000,
        "data_type": "laws",
        "download_method": "local",  # Already downloaded
    },
    "judicial_exam": {
        "name": "司法考试题目数据",
        "description": "历年司法/法律职业资格考试题目与解析",
        "url": "https://huggingface.co/datasets/xlang-ai/Chinese-Law",
        "estimated_records": 30_000,
        "data_type": "qa_pairs",
        "download_method": "huggingface_api",
        "hf_repo": "xlang-ai/Chinese-Law",
    },
}


# =============================================================================
# 下载器
# =============================================================================

class DatasetDownloader:
    """法律数据集下载管理器。"""

    def __init__(self, data_dir: Path = EXPANDED_DATA_DIR):
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def list_datasets(self) -> list[dict[str, Any]]:
        """列出所有可下载的数据集。"""
        result = []
        for key, meta in DATASET_REGISTRY.items():
            local_path = self._data_dir / f"{key}.jsonl"
            info = {
                "key": key,
                "name": meta["name"],
                "description": meta["description"],
                "estimated_records": meta["estimated_records"],
                "data_type": meta["data_type"],
                "downloaded": local_path.exists(),
                "local_path": str(local_path) if local_path.exists() else None,
                "local_records": 0,
            }
            if local_path.exists():
                info["local_records"] = sum(1 for _ in open(local_path, encoding="utf-8"))
            result.append(info)
        return result

    async def download(self, dataset_key: str) -> Path | None:
        """下载指定数据集。"""
        meta = DATASET_REGISTRY.get(dataset_key)
        if not meta:
            logger.error("Unknown dataset: %s", dataset_key)
            return None

        output_path = self._data_dir / f"{dataset_key}.jsonl"
        if output_path.exists():
            logger.info("Dataset '%s' already downloaded at %s", dataset_key, output_path)
            return output_path

        method = meta["download_method"]
        logger.info("Downloading '%s' via %s...", meta["name"], method)

        try:
            if method == "huggingface_api":
                await self._download_huggingface(meta, output_path)
            elif method == "github":
                await self._download_github(meta, output_path)
            elif method == "local":
                logger.info("Dataset '%s' is local, skipping download", dataset_key)
                return None
            elif method == "modelscope":
                await self._download_modelscope(meta, output_path)
            else:
                logger.error("Unknown download method: %s", method)
                return None
        except Exception as exc:
            logger.error("Failed to download '%s': %s", dataset_key, exc)
            # Clean up partial download
            if output_path.exists():
                output_path.unlink()
            return None

        return output_path

    async def download_all(self) -> dict[str, Path | None]:
        """下载所有数据集。"""
        results = {}
        for key in DATASET_REGISTRY:
            path = await self.download(key)
            results[key] = path
        return results

    async def _download_huggingface(self, meta: dict, output_path: Path) -> None:
        """通过 HuggingFace datasets-server API 下载。"""
        repo = meta["hf_repo"]
        config = meta.get("hf_config", "default")
        data_type = meta["data_type"]

        # Try using datasets library first
        try:
            from datasets import load_dataset

            logger.info("  Using datasets library for %s...", repo)
            try:
                ds = load_dataset(repo, config, split="train", streaming=True)
            except Exception:
                ds = load_dataset(repo, split="train", streaming=True)

            count = 0
            with open(output_path, "w", encoding="utf-8") as f:
                for item in ds:
                    normalized = self._normalize_record(dict(item), data_type, dataset_key=meta["name"])
                    if normalized:
                        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                        count += 1
                        if count % 100_000 == 0:
                            logger.info("  Downloaded %d records...", count)

            logger.info("  Downloaded %d records from HuggingFace", count)
            return

        except ImportError:
            logger.info("  datasets library not available, using HTTP API...")

        # Fallback: HuggingFace Datasets Server API
        base_url = f"https://datasets-server.huggingface.co/rows"
        params = {"dataset": repo, "config": config, "split": "train"}

        count = 0
        offset = 0
        batch_size = 100

        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            while True:
                params["offset"] = offset
                params["length"] = batch_size

                try:
                    resp = await client.get(base_url, params=params)
                    if resp.status_code != 200:
                        logger.warning("  API returned %d at offset %d", resp.status_code, offset)
                        break

                    data = resp.json()
                    rows = data.get("rows", [])
                    if not rows:
                        break

                    with open(output_path, "a", encoding="utf-8") as f:
                        for row in rows:
                            item = row.get("row", row)
                            normalized = self._normalize_record(item, data_type, dataset_key=meta["name"])
                            if normalized:
                                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                                count += 1

                    offset += len(rows)
                    if count % 10_000 == 0:
                        logger.info("  Downloaded %d records...", count)

                    # Check if we've reached the end
                    total = data.get("num_rows_total")
                    if total and offset >= total:
                        break

                except Exception as exc:
                    logger.error("  API error at offset %d: %s", offset, exc)
                    break

        logger.info("  Downloaded %d records via API", count)

    async def _download_github(self, meta: dict, output_path: Path) -> None:
        """从 GitHub 仓库下载数据文件。"""
        repo = meta["github_repo"]
        data_type = meta["data_type"]

        # Common data file paths in legal AI repos
        possible_files = [
            "data/train.jsonl", "data/train.json",
            "data/dataset.jsonl", "data/sft_data.jsonl",
            "data/legal_data.json", "data/qa_data.json",
            "dataset/data.jsonl", "dataset/train.json",
            "data/cail2018/train.json", "data/raw/cail2018.json",
            "resources/data.json", "corpus/data.jsonl",
        ]

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            for file_path in possible_files:
                url = f"https://raw.githubusercontent.com/{repo}/main/{file_path}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200 and len(resp.text) > 100:
                        count = self._parse_and_save(resp.text, data_type, output_path, meta["name"])
                        if count > 0:
                            logger.info("  Downloaded %d records from %s", count, url)
                            return
                except Exception:
                    continue

        logger.warning("  Could not find data files in %s", repo)

    async def _download_modelscope(self, meta: dict, output_path: Path) -> None:
        """从 ModelScope 下载。"""
        try:
            from modelscope.msdatasets import MsDataset
            ds = MsDataset.load(meta.get("ms_repo", meta["name"]), split="train")

            count = 0
            with open(output_path, "w", encoding="utf-8") as f:
                for item in ds:
                    normalized = self._normalize_record(dict(item), meta["data_type"], dataset_key=meta["name"])
                    if normalized:
                        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                        count += 1

            logger.info("  Downloaded %d records from ModelScope", count)
        except ImportError:
            logger.error("  modelscope not installed. Install: pip install modelscope")

    def _parse_and_save(self, content: str, data_type: str, output_path: Path, dataset_name: str) -> int:
        """Parse content and save normalized records."""
        count = 0

        with open(output_path, "w", encoding="utf-8") as f:
            # Try JSON array
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    for item in data:
                        normalized = self._normalize_record(item, data_type, dataset_key=dataset_name)
                        if normalized:
                            f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                            count += 1
                    return count
            except json.JSONDecodeError:
                pass

            # Try JSONL
            for line in content.strip().split("\n"):
                if line.strip():
                    try:
                        item = json.loads(line)
                        normalized = self._normalize_record(item, data_type, dataset_key=dataset_name)
                        if normalized:
                            f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                            count += 1
                    except json.JSONDecodeError:
                        continue

        return count

    def _normalize_record(self, item: dict, data_type: str, dataset_key: str = "") -> dict | None:
        """将任意格式的法律数据标准化。"""
        if not isinstance(item, dict):
            return None

        record = {
            "source_dataset": dataset_key,
            "data_type": data_type,
            "content": "",
            "metadata": {},
        }

        if data_type == "cases":
            text = ""
            for key in ["text", "case_text", "facts", "content", "body",
                        "case_content", "judgment_text", "doc"]:
                val = item.get(key, "")
                if val and len(str(val)) > len(text):
                    text = str(val)
            if not text:
                return None
            record["content"] = text[:8000]
            record["metadata"] = {
                k: str(v)[:500] for k, v in item.items()
                if k in ("case_type", "type", "cause", "cause_of_action",
                         "judgment", "result", "articles", "relevant_articles",
                         "criminal_code", "charges")
            }

        elif data_type == "qa_pairs":
            question = ""
            for key in ["instruction", "input", "question", "query", "prompt"]:
                val = item.get(key, "")
                if val:
                    question = str(val)
                    break

            answer = ""
            for key in ["output", "answer", "response", "content", "text"]:
                val = item.get(key, "")
                if val and len(str(val)) > len(answer):
                    answer = str(val)

            if not answer:
                return None

            record["content"] = f"Q: {question}\nA: {answer}"[:8000]
            record["metadata"] = {
                "question": question[:2000],
                "answer_length": len(answer),
            }

        elif data_type == "mixed" or data_type == "laws":
            content = ""
            for key in ["text", "content", "body", "instruction", "output", "input"]:
                val = item.get(key, "")
                if val and len(str(val)) > len(content):
                    content = str(val)
            if not content:
                return None
            record["content"] = content[:8000]
            record["metadata"] = {
                k: str(v)[:500] for k, v in item.items()
                if k not in ("content", "text", "body") and isinstance(v, (str, int, float, bool))
            }

        return record

    def get_stats(self) -> dict[str, Any]:
        """获取所有数据集的统计信息。"""
        stats = {
            "total_datasets": len(DATASET_REGISTRY),
            "total_estimated_records": sum(m["estimated_records"] for m in DATASET_REGISTRY.values()),
            "total_downloaded_records": 0,
            "total_downloaded_files": 0,
            "datasets": {},
        }

        for key, meta in DATASET_REGISTRY.items():
            path = self._data_dir / f"{key}.jsonl"
            local_count = 0
            file_size = 0
            if path.exists():
                file_size = path.stat().st_size
                with open(path, "r", encoding="utf-8") as f:
                    local_count = sum(1 for _ in f)

            stats["datasets"][key] = {
                "name": meta["name"],
                "estimated": meta["estimated_records"],
                "downloaded": local_count,
                "file_size_mb": round(file_size / 1024 / 1024, 1),
            }
            stats["total_downloaded_records"] += local_count
            if path.exists():
                stats["total_downloaded_files"] += 1

        return stats

    def iter_dataset(self, dataset_key: str) -> Iterator[dict]:
        """迭代读取已下载数据集的记录。"""
        path = self._data_dir / f"{dataset_key}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"Dataset '{dataset_key}' not downloaded")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


# =============================================================================
# 裁判文书网增强采集器
# =============================================================================

class CourtDocumentCrawler:
    """裁判文书网增强采集器。

    注意：
    1. 裁判文书网有严格的反爬措施，需要代理池支持
    2. 近年公开文书数量大幅减少
    3. 请遵守 robots.txt 和网站使用条款
    4. 仅供法律研究和学术研究使用
    """

    BASE_URL = "https://wenshu.court.gov.cn"
    DATA_DIR = PROJECT_ROOT / "data" / "court_documents"

    def __init__(self, proxy_url: str | None = None):
        self._proxy_url = proxy_url or os.environ.get("PROXY_URL")
        self._proxy_service = get_proxy_service()
        self._client: httpx.AsyncClient | None = None
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._proxy_service.enabled:
                self._client = await self._proxy_service.get_crawl_client(timeout=60)
                logger.info("Using proxy pool for court document crawling")
            else:
                proxy = self._proxy_url
                self._client = httpx.AsyncClient(
                    timeout=60,
                    follow_redirects=True,
                    proxy=proxy if proxy else None,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                    },
                )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    async def crawl_by_case_type(
        self,
        case_type: str = "民事案件",
        max_pages: int = 100,
        output_file: Path | None = None,
    ) -> int:
        """按案件类型采集裁判文书。

        案件类型：民事案件, 刑事案件, 行政案件, 赔偿案件, 执行案件
        """
        if output_file is None:
            output_file = self.DATA_DIR / f"wenshu_{case_type}.jsonl"

        client = await self._get_client()
        count = 0

        for page in range(1, max_pages + 1):
            try:
                # 搜索列表
                search_url = f"{self.BASE_URL}/website/parse/rest.q4w"
                payload = {
                    "pageNum": page,
                    "pageSize": 15,
                    "sortFields": "s50:desc",
                    "ciphertext": self._generate_ciphertext(),
                    "pageId": self._generate_page_id(),
                    "queryCondition": json.dumps([{"key": "s8", "value": case_type}]),
                }

                resp = await client.post(search_url, data=payload)
                if resp.status_code != 200:
                    logger.warning("Page %d returned %d", page, resp.status_code)
                    break

                data = resp.json()
                items = data.get("queryResult", {}).get("resultList", [])
                if not items:
                    logger.info("No more results at page %d", page)
                    break

                for item in items:
                    record = self._parse_wenshu_item(item)
                    if record:
                        with open(output_file, "a", encoding="utf-8") as f:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        count += 1

                logger.info("Crawled page %d: %d items (total: %d)", page, len(items), count)

                # 礼貌延迟
                await asyncio.sleep(3 + 2 * (1 if page % 10 == 0 else 0))

            except Exception as exc:
                logger.error("Error at page %d: %s", page, exc)
                await asyncio.sleep(10)
                continue

        logger.info("Crawled %d documents for '%s'", count, case_type)
        return count

    def _parse_wenshu_item(self, item: dict) -> dict | None:
        """解析文书列表中的单条记录。"""
        title = item.get("rowkey", "")
        if not title:
            return None

        return {
            "source_dataset": "wenshu_court_gov_cn",
            "data_type": "cases",
            "content": json.dumps(item, ensure_ascii=False)[:8000],
            "metadata": {
                "title": item.get("s1", ""),
                "case_number": item.get("s7", ""),
                "court": item.get("s2", ""),
                "case_type": item.get("s8", ""),
                "date": item.get("s31", ""),
                "cause": item.get("s9", ""),
            },
        }

    @staticmethod
    def _generate_ciphertext() -> str:
        return hashlib.md5(str(int(time.time())).encode()).hexdigest()

    @staticmethod
    def _generate_page_id() -> str:
        return hashlib.md5(os.urandom(16)).hexdigest()


# =============================================================================
# 数据导入器 — 将扩展数据导入现有管道
# =============================================================================

class ExpandedDataImporter:
    """将下载的扩展数据导入 PostgreSQL 和 Milvus。"""

    def __init__(self, session_factory: Any):
        self._session_factory = session_factory

    async def import_qa_pairs_as_articles(self, dataset_key: str) -> dict[str, int]:
        """将 Q&A 对作为法律知识条目导入。

        每个 Q&A 对被视为一个法律知识条目，存入 legal_articles 表。
        """
        downloader = DatasetDownloader()
        count = 0
        skipped = 0

        async with self._session_factory() as session:
            from app.models.legal_knowledge import LegalArticle, Law
            from sqlalchemy import select
            import uuid

            # Find or create a "knowledge base" law entry
            result = await session.execute(
                select(Law).where(Law.name == f"法律知识库-{dataset_key}")
            )
            law = result.scalar_one_or_none()
            if not law:
                law = Law(
                    id=str(uuid.uuid4()),
                    name=f"法律知识库-{dataset_key}",
                    short_name=dataset_key,
                    law_type="其他",
                    category="法律知识",
                    status="active",
                )
                session.add(law)
                await session.flush()

            for record in downloader.iter_dataset(dataset_key):
                content = record.get("content", "")
                if not content or len(content) < 20:
                    skipped += 1
                    continue

                # Generate a unique article number from content hash
                content_hash = hashlib.md5(content[:200].encode()).hexdigest()[:12]
                article_number = f"QA-{content_hash}"

                # Check existence
                result = await session.execute(
                    select(LegalArticle).where(
                        LegalArticle.law_id == law.id,
                        LegalArticle.article_number == article_number,
                    )
                )
                if result.scalar_one_or_none():
                    skipped += 1
                    continue

                article = LegalArticle(
                    id=str(uuid.uuid4()),
                    law_id=law.id,
                    article_number=article_number,
                    content=content[:8000],
                    tags=record.get("metadata", {}).get("question", "")[:512],
                    effective_status="active",
                )
                session.add(article)
                count += 1

                if count % 200 == 0:
                    await session.commit()
                    logger.info("  Imported %d QA pairs...", count)

            await session.commit()

        logger.info("Imported %d QA pairs from '%s' (skipped %d)", count, dataset_key, skipped)
        return {"imported": count, "skipped": skipped}

    async def import_cases(self, dataset_key: str) -> dict[str, int]:
        """将案例数据导入 court_cases 表。"""
        downloader = DatasetDownloader()
        count = 0
        skipped = 0

        async with self._session_factory() as session:
            from app.models.legal_knowledge import CourtCase
            from sqlalchemy import select
            import uuid

            for record in downloader.iter_dataset(dataset_key):
                content = record.get("content", "")
                if not content or len(content) < 50:
                    skipped += 1
                    continue

                # Generate unique case number
                content_hash = hashlib.md5(content[:200].encode()).hexdigest()[:16]
                case_number = f"{dataset_key.upper()}-{content_hash}"

                # Check existence
                result = await session.execute(
                    select(CourtCase).where(CourtCase.case_number == case_number)
                )
                if result.scalar_one_or_none():
                    skipped += 1
                    continue

                metadata = record.get("metadata", {})
                case = CourtCase(
                    id=str(uuid.uuid4()),
                    case_number=case_number,
                    title=metadata.get("title", content[:100])[:512],
                    court_name=metadata.get("court", "")[:256],
                    case_type=metadata.get("case_type", "")[:64],
                    cause_of_action=metadata.get("cause", metadata.get("cause_of_action", ""))[:256],
                    summary=content[:2000],
                    full_text=content[:8000],
                    tags=f"{dataset_key},{metadata.get('case_type', '')}",
                )
                session.add(case)
                count += 1

                if count % 200 == 0:
                    await session.commit()
                    logger.info("  Imported %d cases...", count)

            await session.commit()

        logger.info("Imported %d cases from '%s' (skipped %d)", count, dataset_key, skipped)
        return {"imported": count, "skipped": skipped}


# =============================================================================
# CLI 入口
# =============================================================================

async def main_async():
    parser = argparse.ArgumentParser(
        description="法律数据扩展系统 — 下载和导入大规模开源法律数据集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="列出所有可用数据集")
    parser.add_argument("--download-all", action="store_true", help="下载所有数据集")
    parser.add_argument("--dataset", type=str, help="下载指定数据集")
    parser.add_argument("--import", dest="do_import", action="store_true", help="导入已下载数据到数据库")
    parser.add_argument("--import-dataset", type=str, help="导入指定数据集到数据库")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--crawl", action="store_true", help="启动裁判文书网采集")
    parser.add_argument("--pages", type=int, default=100, help="采集页数")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    downloader = DatasetDownloader()

    if args.list or args.stats:
        datasets = downloader.list_datasets()
        print(f"\n{'='*80}")
        print(f"  法律数据集注册表 ({len(datasets)} 个数据集)")
        print(f"{'='*80}\n")
        total_est = 0
        total_dl = 0
        for ds in datasets:
            status = "✅" if ds["downloaded"] else "⬜"
            print(f"  {status} {ds['key']:25s} | {ds['name']:30s} | "
                  f"预估: {ds['estimated_records']:>12,} | "
                  f"已下载: {ds['local_records']:>10,}")
            total_est += ds["estimated_records"]
            total_dl += ds["local_records"]
        print(f"\n  总计: 预估 {total_est:,} 条 | 已下载 {total_dl:,} 条")
        print(f"{'='*80}\n")

    if args.download_all:
        results = await downloader.download_all()
        for key, path in results.items():
            status = "✅" if path else "❌"
            print(f"  {status} {key}: {path}")

    if args.dataset:
        path = await downloader.download(args.dataset)
        if path:
            print(f"Downloaded to: {path}")
        else:
            print(f"Failed to download: {args.dataset}")

    if args.do_import:
        from app.core.database import async_session_factory
        importer = ExpandedDataImporter(async_session_factory)

        for ds in downloader.list_datasets():
            if ds["downloaded"] and ds["local_records"] > 0:
                print(f"\nImporting {ds['key']} ({ds['local_records']:,} records)...")
                if ds.get("data_type") == "cases" or "case" in ds["key"]:
                    # Check actual data type from metadata
                    pass
                try:
                    if "qa" in ds["key"] or "sft" in ds["key"] or "law" in ds["key"]:
                        result = await importer.import_qa_pairs_as_articles(ds["key"])
                    else:
                        result = await importer.import_cases(ds["key"])
                    print(f"  Result: {result}")
                except Exception as exc:
                    print(f"  Error: {exc}")

    if args.import_dataset:
        from app.core.database import async_session_factory
        importer = ExpandedDataImporter(async_session_factory)
        result = await importer.import_qa_pairs_as_articles(args.import_dataset)
        print(f"Result: {result}")

    if args.crawl:
        crawler = CourtDocumentCrawler()
        try:
            for case_type in ["民事案件", "刑事案件", "行政案件"]:
                count = await crawler.crawl_by_case_type(case_type, max_pages=args.pages)
                print(f"Crawled {count} documents for '{case_type}'")
        finally:
            await crawler.close()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
