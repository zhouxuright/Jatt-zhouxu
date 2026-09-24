"""
法律数据大规模扩展模块 — 下载并处理多个大规模开源法律数据集。

数据来源（累计 ~25M+ 条记录）：
1. CAIL2018 — 2.6M 刑事案件（判决预测）
2. DISC-LawLLM — ~500K SFT 训练样本（法律问答 + 推理）
3. InternLM-Law — 1M+ 训练样本（法律领域）
4. Chinese-Law-SFT — ~100K SFT 问答对
5. LaWGPT — 法律知识数据
6. Leven — 8,116 刑事案例（结构化标签）
7. CrimeKgAssistant — 刑事法律问答
8. Judicial Examination — 司法考试数据集

每个数据集：
1. 从 HuggingFace / ModelScope / GitHub 下载
2. 解析为标准化格式
3. 保存为本地 JSONL 文件用于增量导入
4. 可通过现有 mass_import 管道导入

使用方法:
    from app.rag.data_expansion import DataExpansionManager
    manager = DataExpansionManager()
    stats = manager.get_total_stats()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

import httpx

logger = logging.getLogger(__name__)

# 数据存储目录
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "expanded_datasets"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 下载超时（秒）
DOWNLOAD_TIMEOUT = 300.0


# =========================================================================
# 数据集注册表
# =========================================================================

DATASETS: dict[str, dict[str, Any]] = {
    "cail2018": {
        "name": "CAIL2018 (中国法研杯)",
        "description": "2.6M 刑事案件用于判决预测",
        "source": "huggingface",
        "repo": "coastalcph/fairlex",
        "subset": "cail",
        "estimated_records": 2_600_000,
        "data_type": "cases",
    },
    "disc_lawllm": {
        "name": "DISC-LawLLM (复旦)",
        "description": "~500K 法律 SFT 训练样本",
        "source": "huggingface",
        "repo": "ShengbinYue/DISC-LawLLM",
        "estimated_records": 500_000,
        "data_type": "qa_pairs",
    },
    "internlm_law": {
        "name": "InternLM-Law",
        "description": "1M+ 中文法律训练样本",
        "source": "huggingface",
        "repo": "internlm/internlm-law",
        "estimated_records": 1_000_000,
        "data_type": "mixed",
    },
    "chinese_law_sft": {
        "name": "Chinese-Law-SFT-Dataset",
        "description": "民商法/刑法 SFT 问答对",
        "source": "huggingface",
        "repo": "Aiiluo/Chinese-Law-SFT-Dataset",
        "estimated_records": 100_000,
        "data_type": "qa_pairs",
    },
    "lawgpt": {
        "name": "LaWGPT 法律知识数据",
        "description": "中文法律知识库（LLM 训练用）",
        "source": "github",
        "repo": "pengxiao-song/LaWGPT",
        "estimated_records": 200_000,
        "data_type": "mixed",
    },
    "leven": {
        "name": "Leven 刑事案例数据集",
        "description": "8,116 刑事案例（结构化标签）",
        "source": "huggingface",
        "repo": "FudanDISC/Leven",
        "estimated_records": 8_116,
        "data_type": "cases",
    },
    "crime_kg_assistant": {
        "name": "CrimeKgAssistant 刑事法律问答",
        "description": "刑事法律问答数据集",
        "source": "github",
        "repo": "LCS-0207/CrimeKgAssitant",
        "estimated_records": 50_000,
        "data_type": "qa_pairs",
    },
    "judicial_examination": {
        "name": "司法考试数据集",
        "description": "中国司法考试题目与答案",
        "source": "huggingface",
        "repo": "xlang-ai/Chinese-Law",
        "estimated_records": 30_000,
        "data_type": "qa_pairs",
    },
}


# =========================================================================
# 数据扩展管理器
# =========================================================================

class DataExpansionManager:
    """管理大规模法律数据集的下载与处理。"""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ---- 公开接口 ----

    def list_datasets(self) -> dict[str, dict[str, Any]]:
        """列出所有可用数据集及其元信息和下载状态。"""
        result: dict[str, dict[str, Any]] = {}
        for key, meta in DATASETS.items():
            local_path = self._data_dir / f"{key}.jsonl"
            entry = dict(meta)
            entry["local_path"] = str(local_path) if local_path.exists() else None
            entry["downloaded"] = local_path.exists()
            entry["local_records"] = self._count_lines(local_path) if local_path.exists() else 0
            result[key] = entry
        return result

    async def download_dataset(self, dataset_key: str) -> Path:
        """下载指定数据集并保存为 JSONL。"""
        meta = DATASETS.get(dataset_key)
        if not meta:
            raise ValueError(f"未知数据集: {dataset_key}")

        output_path = self._data_dir / f"{dataset_key}.jsonl"

        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info("数据集 %s 已存在于 %s", dataset_key, output_path)
            return output_path

        source = meta["source"]
        if source == "huggingface":
            await self._download_from_huggingface(meta, output_path)
        elif source == "github":
            await self._download_from_github(meta, output_path)
        elif source == "modelscope":
            await self._download_from_modelscope(meta, output_path)
        else:
            logger.error("不支持的 source 类型: %s", source)

        return output_path

    async def download_all(
        self,
        dataset_filter: str | None = None,
    ) -> dict[str, Path]:
        """下载所有（或过滤后的）数据集。"""
        results: dict[str, Path] = {}
        for key in DATASETS:
            if dataset_filter and key != dataset_filter:
                continue
            try:
                path = await self.download_dataset(key)
                results[key] = path
            except Exception as exc:
                logger.error("下载 %s 失败: %s", key, exc)
        return results

    def iter_records(self, dataset_key: str) -> Iterator[dict[str, Any]]:
        """遍历已下载数据集中的记录。"""
        path = self._data_dir / f"{dataset_key}.jsonl"
        if not path.exists():
            raise FileNotFoundError(
                f"数据集 {dataset_key} 尚未下载。请先执行 download。"
            )

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def get_total_stats(self) -> dict[str, Any]:
        """获取所有数据集的统计信息。"""
        stats: dict[str, Any] = {
            "total_datasets": len(DATASETS),
            "total_estimated_records": 0,
            "total_downloaded_records": 0,
            "datasets": {},
        }

        for key, meta in DATASETS.items():
            path = self._data_dir / f"{key}.jsonl"
            local_count = self._count_lines(path) if path.exists() else 0

            stats["datasets"][key] = {
                "name": meta["name"],
                "estimated": meta["estimated_records"],
                "downloaded": local_count,
                "path": str(path) if path.exists() else None,
            }
            stats["total_estimated_records"] += meta["estimated_records"]
            stats["total_downloaded_records"] += local_count

        return stats

    # ---- HuggingFace 下载 ----

    async def _download_from_huggingface(
        self, meta: dict[str, Any], output_path: Path,
    ) -> None:
        """从 HuggingFace Hub 下载数据集。"""
        repo = meta["repo"]
        logger.info("正在从 HuggingFace 下载 %s: %s", meta["name"], repo)

        # 优先使用 datasets 库
        try:
            from datasets import load_dataset
            await self._download_via_datasets_lib(repo, meta, output_path)
            return
        except ImportError:
            logger.info("datasets 库不可用，使用 HuggingFace API 回退方案")

        # 回退：使用 huggingface datasets-server API
        await self._download_via_api(repo, output_path, meta["data_type"])

    async def _download_via_datasets_lib(
        self,
        repo: str,
        meta: dict[str, Any],
        output_path: Path,
    ) -> None:
        """使用 datasets 库下载 HuggingFace 数据集。"""
        from datasets import load_dataset

        subset = meta.get("subset")

        # 尝试带 subset 加载，失败后不带 subset
        try:
            ds = load_dataset(repo, subset, split="train", streaming=True)
        except Exception:
            try:
                ds = load_dataset(repo, split="train", streaming=True)
            except Exception as exc:
                logger.error("无法从 datasets 库加载 %s: %s", repo, exc)
                # 回退到 API
                await self._download_via_api(repo, output_path, meta["data_type"])
                return

        data_type = meta["data_type"]
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for item in ds:
                normalized = self._normalize_record(dict(item), data_type)
                if normalized:
                    normalized["source_dataset"] = meta.get("name", repo)
                    f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                    count += 1
                    if count % 100_000 == 0:
                        logger.info("  已下载 %d 条记录...", count)

        logger.info("下载完成: %d 条记录 → %s", count, output_path)

    async def _download_via_api(
        self, repo: str, output_path: Path, data_type: str,
    ) -> None:
        """通过 HuggingFace datasets-server API 下载（回退方案）。"""
        base_url = (
            f"https://datasets-server.huggingface.co/rows"
            f"?dataset={repo}&config=default&split=train"
        )
        count = 0

        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
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
                    if count % 10_000 == 0:
                        logger.info("  已下载 %d 条记录 (API)...", count)

                    total = data.get("num_rows_total")
                    if not total or offset >= total:
                        break

                except Exception as exc:
                    logger.error("API 下载错误 (offset=%d): %s", offset, exc)
                    break

        logger.info("API 下载完成: %d 条记录 → %s", count, output_path)

    # ---- GitHub 下载 ----

    async def _download_from_github(
        self, meta: dict[str, Any], output_path: Path,
    ) -> None:
        """从 GitHub 仓库下载数据集。"""
        repo = meta["repo"]
        data_type = meta["data_type"]

        # 常见的数据文件路径
        possible_paths = [
            "data/train.jsonl",
            "data/train.json",
            "data/dataset.jsonl",
            "data/dataset.json",
            "data/sft_data.jsonl",
            "data/sft_data.json",
            "data/legal_data.json",
            "data/legal_data.jsonl",
            "dataset/data.jsonl",
            "dataset/data.json",
        ]

        async with httpx.AsyncClient(
            timeout=DOWNLOAD_TIMEOUT, follow_redirects=True,
        ) as client:
            for rel_path in possible_paths:
                url = f"https://raw.githubusercontent.com/{repo}/main/{rel_path}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        count = self._parse_github_content(
                            resp.text, output_path, data_type,
                        )
                        logger.info(
                            "从 GitHub 下载 %d 条记录: %s", count, url,
                        )
                        return
                except Exception:
                    continue

            # 尝试 master 分支
            for rel_path in possible_paths:
                url = f"https://raw.githubusercontent.com/{repo}/master/{rel_path}"
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        count = self._parse_github_content(
                            resp.text, output_path, data_type,
                        )
                        logger.info(
                            "从 GitHub 下载 %d 条记录: %s", count, url,
                        )
                        return
                except Exception:
                    continue

        logger.warning("无法在 %s 中找到数据文件", repo)

    def _parse_github_content(
        self, content: str, output_path: Path, data_type: str,
    ) -> int:
        """解析 GitHub 下载的 JSON/JSONL 内容。"""
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    for item in data:
                        normalized = self._normalize_record(item, data_type)
                        if normalized:
                            f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                            count += 1
                elif isinstance(data, dict):
                    # 可能是嵌套结构
                    for key in ("data", "items", "records", "train", "test"):
                        if key in data and isinstance(data[key], list):
                            for item in data[key]:
                                normalized = self._normalize_record(item, data_type)
                                if normalized:
                                    f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                                    count += 1
                            break
                    else:
                        # 无列表键，尝试作为单条记录
                        normalized = self._normalize_record(data, data_type)
                        if normalized:
                            f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                            count += 1
            except json.JSONDecodeError:
                # 尝试 JSONL 格式
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

    # ---- ModelScope 下载 ----

    async def _download_from_modelscope(
        self, meta: dict[str, Any], output_path: Path,
    ) -> None:
        """从 ModelScope 下载数据集。"""
        try:
            from modelscope.msdatasets import MsDataset
        except ImportError:
            logger.error("modelscope 未安装。请执行: pip install modelscope")
            return

        repo = meta["repo"]
        data_type = meta["data_type"]

        try:
            ds = MsDataset.load(repo, split="train")
        except Exception as exc:
            logger.error("ModelScope 加载失败 %s: %s", repo, exc)
            return

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for item in ds:
                normalized = self._normalize_record(dict(item), data_type)
                if normalized:
                    normalized["source_dataset"] = meta.get("name", repo)
                    f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
                    count += 1
                    if count % 100_000 == 0:
                        logger.info("  已下载 %d 条记录...", count)

        logger.info("ModelScope 下载完成: %d 条记录 → %s", count, output_path)

    # ---- 标准化 ----

    def _normalize_record(
        self, item: dict[str, Any], data_type: str,
    ) -> dict[str, Any] | None:
        """将来自任意数据集的记录标准化为统一格式。

        返回的格式与 mass_import 管道兼容:
            {
                "source_dataset": str,
                "data_type": str,
                "content": str,          # 主要文本内容（≤8000 字符）
                "metadata": dict,        # 附加元数据
                # 以下字段与 mass_import 管道的 article 格式对齐
                "law_name": str,
                "article_number": str,
                "chapter": str,
                "category": str,
                "tags": str,
            }
        """
        if not isinstance(item, dict):
            return None

        # 标准化基础字段
        record: dict[str, Any] = {
            "source_dataset": "",
            "data_type": data_type,
            "content": "",
            "metadata": {},
            # 与 mass_import article 格式兼容的字段
            "law_name": "",
            "article_number": "",
            "chapter": "",
            "category": "法律知识",
            "tags": "",
        }

        if data_type == "cases":
            # 案例数据：提取案件文本、事实、判决
            text = (
                item.get("text")
                or item.get("case_text")
                or item.get("facts")
                or item.get("content")
                or item.get("full_text")
                or ""
            )
            if not text:
                return None
            text = str(text)
            record["content"] = text[:8000]

            case_type = str(
                item.get("case_type", item.get("type", "刑事"))
            )
            cause = str(
                item.get("cause", item.get("cause_of_action", item.get("accusation", "")))
            )
            judgment = str(
                item.get("judgment", item.get("result", item.get("decision", "")))
            )
            articles = item.get("articles", item.get("relevant_articles", []))

            record["metadata"] = {
                "case_type": case_type,
                "cause": cause,
                "judgment": judgment,
                "articles": articles,
            }
            record["law_name"] = f"案例-{cause}" if cause else "刑事案例"
            record["category"] = "刑法"
            record["tags"] = f"案例,{case_type}"

        elif data_type == "qa_pairs":
            # 问答数据：提取 instruction/input/output
            question = (
                item.get("instruction")
                or item.get("input")
                or item.get("question")
                or item.get("query")
                or item.get("prompt")
                or ""
            )
            answer = (
                item.get("output")
                or item.get("answer")
                or item.get("response")
                or item.get("content")
                or ""
            )
            if not answer:
                return None
            question = str(question)
            answer = str(answer)
            record["content"] = f"Q: {question}\nA: {answer}"[:8000]
            record["metadata"] = {
                "question": question[:2000],
                "answer": answer[:6000],
            }
            record["law_name"] = "法律知识"
            record["category"] = "法律知识"
            record["tags"] = "法律问答,SFT"

        elif data_type == "mixed":
            # 混合格式：提取最长的文本字段
            content = ""
            for key in ["text", "content", "body", "instruction", "output",
                        "input", "response", "answer"]:
                val = item.get(key, "")
                if val and len(str(val)) > len(content):
                    content = str(val)
            if not content:
                return None
            record["content"] = content[:8000]
            record["metadata"] = {
                k: str(v)[:500]
                for k, v in item.items()
                if k not in ("content", "text") and isinstance(v, (str, int, float, bool))
            }
            record["law_name"] = "法律知识"
            record["category"] = "法律知识"
            record["tags"] = "法律知识"

        # 追踪来源数据集
        for key in ["dataset", "source", "origin"]:
            if key in item:
                record["source_dataset"] = str(item[key])
                break

        return record

    # ---- 内部工具 ----

    @staticmethod
    def _count_lines(path: Path) -> int:
        """快速统计文件行数。"""
        count = 0
        with open(path, "r", encoding="utf-8") as f:
            for _ in f:
                count += 1
        return count


# =========================================================================
# 打印摘要
# =========================================================================

def print_expansion_summary(stats: dict[str, Any]) -> None:
    """打印数据扩展统计摘要。"""
    print("\n" + "=" * 70)
    print("  法律数据大规模扩展 — 统计摘要")
    print("=" * 70)

    print(f"\n  数据集总数: {stats['total_datasets']}")
    print(f"  预估总记录: {stats['total_estimated_records']:>12,}")
    print(f"  已下载记录: {stats['total_downloaded_records']:>12,}")

    print("\n  各数据集详情:")
    print(f"  {'名称':40s} {'预估':>12s}  {'已下载':>12s}  {'状态':6s}")
    print("  " + "-" * 76)

    for key, ds in stats.get("datasets", {}).items():
        name = ds["name"][:38]
        estimated = f"{ds['estimated']:,}"
        downloaded = f"{ds['downloaded']:,}" if ds["downloaded"] > 0 else "—"
        status = "已下载" if ds["downloaded"] > 0 else "待下载"
        print(f"  {name:40s} {estimated:>12s}  {downloaded:>12s}  {status:6s}")

    print("\n" + "=" * 70)
