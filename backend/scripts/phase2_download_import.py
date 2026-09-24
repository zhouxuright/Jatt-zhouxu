# -*- coding: utf-8 -*-
"""阶段2：大规模开源法律数据集下载与导入。

从 HuggingFace / ModelScope / GitHub 下载更多开源法律数据集，
并导入 PostgreSQL 数据库。

新增数据源（目标 +20M 条）：
  1. CAIL2018  — 2.6M 刑事案件判决预测
  2. DISC-LawLLM — 500K 法律 SFT 训练样本
  3. InternLM-Law — 1M+ 法律训练数据
  4. ChineseLawLLM — 3M+ 法律多任务数据
  5. LEVEN — 法律事件抽取数据集
  6. CrimeKgAssistant — 刑事法律问答
  7. 司法考试数据集
  8. LaWGPT 法律知识数据
  9. chinese-wenshu — 5M 裁判文书（HuggingFace）
  10. OpenLAW — 综合法律数据

使用方法:
    cd backend
    python scripts/phase2_download_import.py
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

# 项目根目录
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase2")

DATA_DIR = BACKEND_ROOT / "data" / "phase2_datasets"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CHECKPOINT_DIR = BACKEND_ROOT / "data" / "import_checkpoints"
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

BATCH_SIZE = 500

# =========================================================================
# 新增数据源注册表
# =========================================================================

DATASETS: dict[str, dict[str, Any]] = {
    # --- 大规模案例数据 ---
    "cail2018_big": {
        "name": "CAIL2018 刑事案件 (2.6M)",
        "source": "huggingface",
        "repo": "coastalcph/fairlex",
        "subset": "cail",
        "estimated_records": 2_600_000,
        "data_type": "cases",
        "priority": 0,
    },
    "chinese_wenshu": {
        "name": "Chinese-Wenshu 裁判文书 (5M)",
        "source": "huggingface",
        "repo": "SUSTechLaw/ChineseWenshu",
        "estimated_records": 5_000_000,
        "data_type": "cases",
        "priority": 0,
    },
    # --- 法律 SFT / 训练数据 ---
    "disc_lawllm": {
        "name": "DISC-LawLLM (500K SFT)",
        "source": "huggingface",
        "repo": "ShengbinYue/DISC-LawLLM",
        "estimated_records": 500_000,
        "data_type": "qa_pairs",
        "priority": 1,
    },
    "internlm_law": {
        "name": "InternLM-Law (1M+)",
        "source": "huggingface",
        "repo": "internlm/internlm-law",
        "estimated_records": 1_000_000,
        "data_type": "mixed",
        "priority": 1,
    },
    "chinese_law_sft_big": {
        "name": "Chinese-Law-SFT 大型问答集",
        "source": "huggingface",
        "repo": "SUSTechLaw/ChineseLawLLM",
        "estimated_records": 3_000_000,
        "data_type": "qa_pairs",
        "priority": 1,
    },
    # --- 法律知识 / 事件抽取 ---
    "leven": {
        "name": "LEVEN 法律事件抽取",
        "source": "huggingface",
        "repo": "thu-coai/LEVEN",
        "estimated_records": 500_000,
        "data_type": "mixed",
        "priority": 2,
    },
    "crime_kg": {
        "name": "CrimeKgAssistant 刑事法律问答",
        "source": "github",
        "repo": "LCS-0207/CrimeKgAssitant",
        "estimated_records": 50_000,
        "data_type": "qa_pairs",
        "priority": 2,
    },
    "judicial_exam": {
        "name": "司法考试数据集",
        "source": "huggingface",
        "repo": "xlang-ai/Chinese-Law",
        "estimated_records": 30_000,
        "data_type": "qa_pairs",
        "priority": 2,
    },
    # --- 综合法律数据 ---
    "lawgpt": {
        "name": "LaWGPT 法律知识",
        "source": "github",
        "repo": "pengxiao-song/LaWGPT",
        "estimated_records": 200_000,
        "data_type": "mixed",
        "priority": 2,
    },
    "open_law": {
        "name": "OpenLAW 法律开放数据",
        "source": "huggingface",
        "repo": "SUSTechLaw/OpenLAW",
        "estimated_records": 1_000_000,
        "data_type": "mixed",
        "priority": 1,
    },
    # --- 民事/行政案例 ---
    "civil_cases": {
        "name": "民事案例数据集",
        "source": "huggingface",
        "repo": "SUSTechLaw/CivilCaseDataset",
        "estimated_records": 2_000_000,
        "data_type": "cases",
        "priority": 0,
    },
    "admin_cases": {
        "name": "行政案例数据集",
        "source": "huggingface",
        "repo": "SUSTechLaw/AdminCaseDataset",
        "estimated_records": 500_000,
        "data_type": "cases",
        "priority": 1,
    },
}


# =========================================================================
# 下载器
# =========================================================================

class DatasetDownloader:
    """从 HuggingFace/GitHub 下载法律数据集。"""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def download_one(self, key: str, meta: dict) -> Path | None:
        """下载单个数据集。"""
        output_path = self._data_dir / f"{key}.jsonl"

        # 跳过已下载的
        if output_path.exists() and output_path.stat().st_size > 1024:
            count = self._count_lines(output_path)
            logger.info("[SKIP] %s: 已存在 (%d 行)", meta["name"], count)
            return output_path

        source = meta["source"]
        try:
            if source == "huggingface":
                await self._download_huggingface(meta, output_path)
            elif source == "github":
                await self._download_github(meta, output_path)
            else:
                logger.warning("[SKIP] %s: 不支持的源 %s", meta["name"], source)
                return None
        except Exception as exc:
            logger.error("[FAIL] %s: %s", meta["name"], exc)
            return None

        if output_path.exists():
            count = self._count_lines(output_path)
            logger.info("[DONE] %s: %d 行 -> %s", meta["name"], count, output_path.name)
            return output_path
        return None

    async def download_all(self, max_priority: int = 2) -> dict[str, Path]:
        """按优先级下载所有数据集。"""
        results: dict[str, Path] = {}
        # 按优先级排序
        sorted_keys = sorted(
            DATASETS.keys(),
            key=lambda k: DATASETS[k].get("priority", 99),
        )

        for key in sorted_keys:
            meta = DATASETS[key]
            if meta.get("priority", 99) > max_priority:
                continue
            path = await self.download_one(key, meta)
            if path:
                results[key] = path

        return results

    async def _download_huggingface(self, meta: dict, output_path: Path):
        """从 HuggingFace 下载。"""
        repo = meta["repo"]
        subset = meta.get("subset")

        # 方法1: 使用 datasets 库
        try:
            from datasets import load_dataset
            logger.info("[HF] 使用 datasets 库加载 %s ...", repo)
            try:
                ds = load_dataset(repo, subset, split="train", streaming=True)
            except Exception:
                ds = load_dataset(repo, split="train", streaming=True)

            count = 0
            with open(output_path, "w", encoding="utf-8") as f:
                for item in ds:
                    normalized = self._normalize_record(dict(item), meta["data_type"])
                    if normalized:
                        normalized["_source"] = meta["name"]
                        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                        count += 1
                        if count % 100_000 == 0:
                            logger.info("  [HF] %s: %d 行 ...", meta["name"], count)
            logger.info("[HF] %s: 下载完成 %d 行", meta["name"], count)
            return
        except ImportError:
            logger.info("[HF] datasets 库未安装，尝试 huggingface_hub ...")
        except Exception as exc:
            logger.warning("[HF] datasets 加载失败: %s, 尝试备选方案", exc)

        # 方法2: 使用 huggingface_hub 下载 parquet 文件
        try:
            await self._download_hf_parquet(repo, subset, output_path)
            return
        except Exception as exc:
            logger.warning("[HF] parquet 下载失败: %s", exc)

        # 方法3: 使用 API 回退
        await self._download_hf_api(repo, output_path, meta["data_type"])

    async def _download_hf_parquet(self, repo: str, subset: str | None, output_path: Path):
        """通过 huggingface_hub 下载 parquet 文件。"""
        from huggingface_hub import HfApi, hf_hub_download
        import pyarrow.parquet as pq

        api = HfApi()
        repo_files = api.list_repo_files(repo, repo_type="dataset")

        # 找到 parquet 文件
        split_prefix = f"data/{subset}-" if subset else "data/"
        parquet_files = sorted(
            f for f in repo_files
            if f.startswith(split_prefix) and f.endswith(".parquet")
        )
        if not parquet_files:
            # 尝试其他路径模式
            parquet_files = sorted(f for f in repo_files if f.endswith(".parquet"))

        if not parquet_files:
            raise FileNotFoundError(f"在 {repo} 中未找到 parquet 文件")

        logger.info("[HF-Parquet] 下载 %d 个文件 from %s ...", len(parquet_files), repo)
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for pf in parquet_files:
                try:
                    local_path = hf_hub_download(
                        repo_id=repo, filename=pf, repo_type="dataset",
                        endpoint=os.getenv("HF_ENDPOINT") or None,
                    )
                    table = pq.ParquetFile(local_path)
                    for batch in table.iter_batches(batch_size=5000):
                        for rec in batch.to_pylist():
                            normalized = self._normalize_record(rec, "mixed")
                            if normalized:
                                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                                count += 1
                    if count % 100_000 == 0:
                        logger.info("  [HF-Parquet] %d 行 ...", count)
                except Exception as exc:
                    logger.warning("[HF-Parquet] 文件 %s 失败: %s", pf, exc)

        logger.info("[HF-Parquet] %s: 下载完成 %d 行", repo, count)

    async def _download_hf_api(self, repo: str, output_path: Path, data_type: str):
        """通过 HuggingFace API 回退下载。"""
        import httpx
        base_url = (
            f"https://datasets-server.huggingface.co/rows"
            f"?dataset={repo}&config=default&split=train"
        )
        count = 0
        async with httpx.AsyncClient(timeout=300.0) as client:
            offset = 0
            while True:
                url = f"{base_url}&offset={offset}&length=100"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    rows = data.get("rows", [])
                    if not rows:
                        break
                    with open(output_path, "a", encoding="utf-8") as f:
                        for row in rows:
                            item = row.get("row", row)
                            normalized = self._normalize_record(item, data_type)
                            if normalized:
                                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                                count += 1
                    offset += len(rows)
                    total = data.get("num_rows_total")
                    if total and offset >= total:
                        break
                    if count % 10_000 == 0:
                        logger.info("  [HF-API] %d 行 ...", count)
                except Exception as exc:
                    logger.error("[HF-API] 错误 (offset=%d): %s", offset, exc)
                    break
        logger.info("[HF-API] %s: 下载完成 %d 行", repo, count)

    async def _download_github(self, meta: dict, output_path: Path):
        """从 GitHub 下载数据集。"""
        import httpx
        repo = meta["repo"]
        data_type = meta["data_type"]

        possible_paths = [
            "data/train.jsonl", "data/train.json", "data/dataset.jsonl",
            "data/dataset.json", "data/sft_data.jsonl", "data/sft_data.json",
            "data/legal_data.json", "data/legal_data.jsonl",
            "dataset/data.jsonl", "dataset/data.json",
        ]

        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
            for branch in ("main", "master"):
                for rel_path in possible_paths:
                    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}"
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            count = self._parse_content(resp.text, output_path, data_type)
                            logger.info("[GitHub] %s: %d 行 from %s", repo, count, url)
                            return
                    except Exception:
                        continue
        logger.warning("[GitHub] %s: 未找到数据文件", repo)

    def _parse_content(self, content: str, output_path: Path, data_type: str) -> int:
        """解析 GitHub 下载的 JSON/JSONL。"""
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            try:
                data = json.loads(content)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    normalized = self._normalize_record(item, data_type)
                    if normalized:
                        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                        count += 1
            except json.JSONDecodeError:
                for line in content.strip().split("\n"):
                    if line.strip():
                        try:
                            item = json.loads(line)
                            normalized = self._normalize_record(item, data_type)
                            if normalized:
                                f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                                count += 1
                        except json.JSONDecodeError:
                            continue
        return count

    def _normalize_record(self, item: dict, data_type: str) -> dict | None:
        """标准化记录格式。"""
        if not isinstance(item, dict):
            return None

        record: dict[str, Any] = {
            "data_type": data_type,
            "content": "",
            "question": "",
            "answer": "",
            "metadata": {},
            "tags": "",
        }

        if data_type == "cases":
            text = ""
            for key in ("text", "case_text", "facts", "content", "full_text", "fact"):
                val = item.get(key)
                if val and len(str(val)) > len(text):
                    text = str(val)
            if not text:
                return None
            record["content"] = text[:8000]
            cause = str(item.get("cause", item.get("cause_of_action",
                       item.get("accusation", item.get("charge", "")))))
            record["metadata"] = {
                "cause": cause[:200],
                "case_type": str(item.get("case_type", item.get("type", "刑事")))[:64],
            }
            record["tags"] = f"案例,{cause}"

        elif data_type == "qa_pairs":
            question = ""
            for key in ("instruction", "input", "question", "query", "prompt"):
                val = item.get(key)
                if val and len(str(val)) > len(question):
                    question = str(val)
            answer = ""
            for key in ("output", "answer", "response"):
                val = item.get(key)
                if val and len(str(val)) > len(answer):
                    answer = str(val)
            if not question or not answer:
                return None
            record["question"] = question[:2000]
            record["answer"] = answer[:6000]
            record["content"] = f"Q: {question}\nA: {answer}"[:8000]
            record["tags"] = "法律问答"

        elif data_type == "mixed":
            content = ""
            for key in ("text", "content", "body", "instruction", "output",
                        "input", "response", "answer", "fact", "facts"):
                val = item.get(key)
                if val and len(str(val)) > len(content):
                    content = str(val)
            if not content:
                return None
            record["content"] = content[:8000]
            record["tags"] = "法律知识"

        # 保留原始元数据
        for key in ("source", "dataset", "origin", "category", "label"):
            if key in item:
                record["metadata"][key] = str(item[key])[:200]

        return record

    @staticmethod
    def _count_lines(path: Path) -> int:
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for _ in f:
                count += 1
        return count


# =========================================================================
# 数据库导入器
# =========================================================================

class Phase2Importer:
    """将下载的数据集导入 PostgreSQL。"""

    async def import_all_downloads(self) -> dict[str, int]:
        """导入 data/phase2_datasets/ 下所有已下载的 JSONL 文件。"""
        stats = {}
        total = 0

        for jsonl_file in sorted(DATA_DIR.glob("*.jsonl")):
            key = jsonl_file.stem
            checkpoint = CHECKPOINT_DIR / f"phase2_{key}.done"
            if checkpoint.exists():
                count = int(checkpoint.read_text(encoding="utf-8").strip() or "0")
                logger.info("[SKIP] %s: 已导入 (%d 行)", key, count)
                stats[key] = count
                total += count
                continue

            count = await self._import_jsonl_to_db(jsonl_file)
            checkpoint.write_text(str(count), encoding="utf-8")
            stats[key] = count
            total += count
            logger.info("[DONE] %s: 导入 %d 行", key, count)

        stats["_total"] = total
        return stats

    async def _import_jsonl_to_db(self, filepath: Path) -> int:
        """将 JSONL 文件导入到对应的数据库表。"""
        from app.core.database import async_session_factory
        from app.models.legal_knowledge import (
            LegalQAPair, LegalKnowledgeEntry, CourtCase,
        )

        inserted = 0
        batch_qa: list = []
        batch_case: list = []
        batch_knowledge: list = []

        async with async_session_factory() as session:
            with open(filepath, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    data_type = rec.get("data_type", "mixed")
                    source_tag = rec.get("_source", filepath.stem)[:128]

                    if data_type == "qa_pairs":
                        question = rec.get("question", "")
                        answer = rec.get("answer", "")
                        if question and answer:
                            batch_qa.append(LegalQAPair(
                                question=question,
                                answer=answer,
                                law_name="",
                                article_number="",
                                law_type="",
                                content=rec.get("content", "")[:4000],
                                category="法律知识",
                                source=source_tag,
                            ))
                            if len(batch_qa) >= BATCH_SIZE:
                                session.add_all(batch_qa)
                                await session.commit()
                                inserted += len(batch_qa)
                                batch_qa.clear()

                    elif data_type == "cases":
                        content = rec.get("content", "")
                        if content:
                            cause = rec.get("metadata", {}).get("cause", "")
                            fact_hash = uuid.uuid5(uuid.NAMESPACE_DNS, f"p2_{content[:500]}").hex
                            batch_case.append(CourtCase(
                                case_number="P2-" + fact_hash[:16],
                                title=f"案例-{cause}" if cause else "法律案例",
                                court_name="",
                                case_type=rec.get("metadata", {}).get("case_type", "其他")[:64],
                                cause_of_action=cause[:256],
                                decision_date="",
                                parties="",
                                summary=content[:500],
                                full_text=content[:60000],
                                key_points="",
                                referenced_laws="",
                                judgment_result="",
                                tags=f"phase2,{source_tag}",
                            ))
                            if len(batch_case) >= BATCH_SIZE:
                                await self._commit_cases(session, batch_case)
                                inserted += len(batch_case)
                                batch_case.clear()

                    else:  # mixed
                        content = rec.get("content", "")
                        if content:
                            batch_knowledge.append(LegalKnowledgeEntry(
                                entry_type="mixed_data",
                                content=content,
                                law_name="",
                                law_type="",
                                category="法律知识",
                                source=source_tag,
                                metadata_json=None,
                            ))
                            if len(batch_knowledge) >= BATCH_SIZE:
                                session.add_all(batch_knowledge)
                                await session.commit()
                                inserted += len(batch_knowledge)
                                batch_knowledge.clear()

                    if line_no % 100_000 == 0:
                        logger.info("  %s: %d 行处理中 ...", filepath.name, line_no)

            # 提交剩余数据
            if batch_qa:
                session.add_all(batch_qa)
                await session.commit()
                inserted += len(batch_qa)
            if batch_case:
                await self._commit_cases(session, batch_case)
                inserted += len(batch_case)
            if batch_knowledge:
                session.add_all(batch_knowledge)
                await session.commit()
                inserted += len(batch_knowledge)

        return inserted

    async def _commit_cases(self, session, batch):
        """提交案例批次，处理唯一键冲突。"""
        from sqlalchemy.exc import IntegrityError
        try:
            session.add_all(batch)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            for case in batch:
                try:
                    session.add(case)
                    await session.commit()
                except IntegrityError:
                    await session.rollback()


# =========================================================================
# 统计
# =========================================================================

async def print_phase2_stats():
    """打印阶段2统计信息。"""
    print("\n" + "=" * 60)
    print("阶段2 — 开源数据集下载统计")
    print("=" * 60)

    total_downloaded = 0
    total_estimated = 0

    print(f"  {'数据集':40s} {'预估':>10s}  {'实际':>10s}  {'状态':6s}")
    print("  " + "-" * 72)

    for key, meta in sorted(DATASETS.items(), key=lambda x: x[1].get("priority", 99)):
        filepath = DATA_DIR / f"{key}.jsonl"
        estimated = meta["estimated_records"]
        total_estimated += estimated

        if filepath.exists():
            count = DatasetDownloader._count_lines(filepath)
            total_downloaded += count
            status = "已下载"
            count_str = f"{count:,}"
        else:
            count_str = "—"
            status = "待下载"

        name = meta["name"][:38]
        print(f"  {name:40s} {estimated:>10,}  {count_str:>10s}  {status:6s}")

    print("-" * 72)
    print(f"  {'总计':40s} {total_estimated:>10,}  {total_downloaded:>10,}")
    print("=" * 60)
    return total_downloaded


# =========================================================================
# Main
# =========================================================================

async def main():
    """主函数。"""
    logger.info("=" * 60)
    logger.info("阶段2: 大规模开源法律数据集下载与导入")
    logger.info("=" * 60)

    t0 = time.time()

    # Step 1: 下载
    logger.info("Step 1: 下载开源数据集 ...")
    downloader = DatasetDownloader()
    downloads = await downloader.download_all(max_priority=2)
    logger.info("下载完成: %d 个数据集", len(downloads))

    # Step 2: 导入数据库
    logger.info("Step 2: 导入数据库 ...")
    importer = Phase2Importer()
    import_stats = await importer.import_all_downloads()
    logger.info("导入完成: %d 行", import_stats.get("_total", 0))

    # Step 3: 统计
    await print_phase2_stats()

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("阶段2 完成 (%.1f 分钟)", elapsed / 60)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
