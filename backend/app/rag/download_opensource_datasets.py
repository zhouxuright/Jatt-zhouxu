"""开源法律数据集下载与导入脚本。

自动从 GitHub / HuggingFace 下载公开的中国法律数据集，
解析、标准化后导入 PostgreSQL 和 Milvus。

数据来源：
1. twang2218/law-datasets (GitHub) — 22,556 条法律法规
2. Dusker/chinese-laws-pretrain (HuggingFace) — 117 部法律全文
3. Aiiluo/Chinese-Law-SFT-Dataset (HuggingFace) — 民商法/刑法 SFT 数据

运行方式：
    cd backend
    python -m app.rag.download_opensource_datasets
    python -m app.rag.download_opensource_datasets --only-download   # 仅下载不导入
    python -m app.rag.download_opensource_datasets --only-import      # 仅导入不下载
    python -m app.rag.download_opensource_datasets --skip-milvus      # 跳过 Milvus
    python -m app.rag.download_opensource_datasets --datasets github  # 只下载特定数据源
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "datasets"
DOWNLOAD_TIMEOUT = 300.0  # 5 分钟超时（大文件）


# =========================================================================
# 数据集定义
# =========================================================================

DATASETS = {
    "github_laws": {
        "name": "twang2218/law-datasets",
        "description": "22,556 条中国法律法规（从国家法律法规数据库获取）",
        "url": "https://github.com/twang2218/law-datasets/raw/main/law-and-regulations/data/laws.json.zip",
        "format": "json_zip",
        "estimated_records": 22556,
    },
    "hf_chinese_laws": {
        "name": "Dusker/chinese-laws-pretrain",
        "description": "117 部中国法律全文（刑法/民法典/行政法等）",
        "urls": [
            # 核心法律（较大的文件优先下载）
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%88%91%E6%B3%95.json", "刑法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%90%88%E5%90%8C%E7%BC%96.json", "合同编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%A9%9A%E5%A7%BB%E5%AE%B6%E5%BA%AD%E7%BC%96.json", "婚姻家庭编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E4%BE%B5%E6%9D%83%E8%B4%A3%E4%BB%BB%E7%BC%96.json", "侵权责任编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E7%89%A9%E6%9D%83%E7%BC%96.json", "物权编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E7%BB%A7%E6%89%BF%E7%BC%96.json", "继承编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E6%80%BB%E5%88%99%E7%BC%96.json", "总则编.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%85%AC%E5%8F%B8%E6%B3%95.json", "公司法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%8A%B3%E5%8A%A8%E6%B3%95.json", "劳动法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%8A%B3%E5%8A%A8%E5%90%88%E5%90%8C%E6%B3%95.json", "劳动合同法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E6%B6%88%E8%B4%B9%E8%80%85%E6%9D%83%E7%9B%8A%E4%BF%9D%E6%8A%A4%E6%B3%95.json", "消费者权益保护法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E8%A1%8C%E6%94%BF%E5%A4%84%E7%BD%9A%E6%B3%95.json", "行政处罚法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E6%B0%91%E4%BA%8B%E8%AF%89%E8%AE%BC%E6%B3%95.json", "民事诉讼法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%88%91%E4%BA%8B%E8%AF%89%E8%AE%BC%E6%B3%95.json", "刑事诉讼法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E8%A1%8C%E6%94%BF%E8%AF%89%E8%AE%BC%E6%B3%95.json", "行政诉讼法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E5%AE%AA%E6%B3%95.json", "宪法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E4%B8%AA%E4%BA%BA%E4%BF%A1%E6%81%AF%E4%BF%9D%E6%8A%A4%E6%B3%95.json", "个人信息保护法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E7%BD%91%E7%BB%9C%E5%AE%89%E5%85%A8%E6%B3%95.json", "网络安全法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E6%95%B0%E6%8D%AE%E5%AE%89%E5%85%A8%E6%B3%95.json", "数据安全法.json"),
            ("https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main/%E7%94%B5%E5%AD%90%E5%95%86%E5%8A%A1%E6%B3%95.json", "电子商务法.json"),
        ],
        "format": "json_files",
        "estimated_records": 5000,
    },
    "hf_sft_data": {
        "name": "Aiiluo/Chinese-Law-SFT-Dataset",
        "description": "民商法/刑法 SFT 训练数据（法律问答对）",
        "urls": [
            ("https://huggingface.co/datasets/Aiiluo/Chinese-Law-SFT-Dataset/resolve/main/Chinese_Civil_and_Commercial%20Law_Law_SFT_Dataset_Basic%20(November%202024).json", "civil_basic.json"),
            ("https://huggingface.co/datasets/Aiiluo/Chinese-Law-SFT-Dataset/resolve/main/Chinese_Criminal_Law_SFT_Dataset_Basic%20(November%202024).json", "criminal_basic.json"),
        ],
        "format": "json_sft",
        "estimated_records": 3000,
    },
}


# =========================================================================
# 下载器
# =========================================================================

async def download_file(url: str, output_path: Path, description: str = "") -> bool:
    """下载文件，支持断点续传和大文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 如果文件已存在且大于 0 字节，跳过下载
    if output_path.exists() and output_path.stat().st_size > 0:
        logger.info("File already exists, skipping: %s (%.1f KB)", output_path.name, output_path.stat().st_size / 1024)
        return True

    logger.info("Downloading %s: %s → %s", description, url[:80], output_path.name)

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)

                        # 每 10MB 打印一次进度
                        if total_size > 0 and downloaded % (10 * 1024 * 1024) < 65536:
                            pct = downloaded / total_size * 100
                            logger.info("  Progress: %.1f MB / %.1f MB (%.1f%%)", downloaded / 1024 / 1024, total_size / 1024 / 1024, pct)

        logger.info("Downloaded: %s (%.1f KB)", output_path.name, output_path.stat().st_size / 1024)
        return True

    except Exception as exc:
        logger.error("Download failed for %s: %s", url, exc)
        # 删除不完整的文件
        if output_path.exists():
            output_path.unlink()
        return False


async def download_github_laws() -> list[dict[str, Any]]:
    """下载并解析 twang2218/law-datasets (22,556 条法律法规)。"""
    ds_info = DATASETS["github_laws"]
    zip_path = DATASET_DIR / "github_laws.json.zip"
    json_path = DATASET_DIR / "github_laws.json"

    # 下载
    if not json_path.exists() or json_path.stat().st_size == 0:
        success = await download_file(ds_info["url"], zip_path, ds_info["name"])
        if not success:
            return []

        # 解压
        if zip_path.exists():
            logger.info("Extracting %s...", zip_path.name)
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    # 找到 JSON 文件
                    json_files = [f for f in zf.namelist() if f.endswith(".json")]
                    if json_files:
                        zf.extract(json_files[0], DATASET_DIR)
                        # 重命名为标准名称
                        extracted = DATASET_DIR / json_files[0]
                        if extracted.name != "github_laws.json":
                            extracted.rename(json_path)
                        logger.info("Extracted: %s → %s", json_files[0], json_path.name)
                    else:
                        logger.error("No JSON files found in zip")
                        return []
            except Exception as exc:
                logger.error("Failed to extract zip: %s", exc)
                return []

    # 解析
    if not json_path.exists():
        return []

    articles: list[dict[str, Any]] = []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            for item in data:
                parsed = _parse_github_law_item(item)
                if parsed:
                    articles.extend(parsed)
        elif isinstance(data, dict):
            # 可能是嵌套结构
            for key in ("laws", "data", "items"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        parsed = _parse_github_law_item(item)
                        if parsed:
                            articles.extend(parsed)
                    break

        logger.info("Parsed %d articles from %s", len(articles), ds_info["name"])
    except Exception as exc:
        logger.error("Failed to parse %s: %s", json_path.name, exc)

    return articles


def _parse_github_law_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """解析 twang2218/law-datasets 中的单条法律数据。

    该数据集的结构通常是：
    {
        "title": "中华人民共和国xxx法",
        "content": "（xxxx年x月x日通过）\n目录\n第一条 ...\n第二条 ...",
        ...
    }
    需要按条文拆分为多条。
    """
    results: list[dict[str, Any]] = []

    title = item.get("title", item.get("name", ""))
    content = item.get("content", item.get("text", ""))

    if not title or not content:
        return results

    # 按"第X条"拆分法条
    import re
    # 匹配 "第一条"、"第二条"... "第一百条"... "第一千条" 等
    article_pattern = re.compile(r'(第[一二三四五六七八九十百零\d]+条[ \s])')

    parts = article_pattern.split(content)

    if len(parts) <= 1:
        # 无法拆分，作为整体存入
        results.append({
            "law": title,
            "num": "",
            "title": title,
            "content": content.strip(),
            "tags": "",
            "category": _guess_category(title),
        })
        return results

    # 第一部分是前言/目录，跳过
    # 后续部分是成对的 [条号, 内容]
    for i in range(1, len(parts) - 1, 2):
        article_num = parts[i].strip()
        article_content = parts[i + 1].strip() if i + 1 < len(parts) else ""

        if article_content:
            # 清理内容（去除多余换行和空白）
            article_content = re.sub(r'\s+', '', article_content)
            if len(article_content) > 10:  # 过滤太短的内容
                results.append({
                    "law": title,
                    "num": article_num,
                    "title": "",
                    "content": article_content,
                    "tags": _guess_tags(title),
                    "category": _guess_category(title),
                })

    return results


async def download_hf_chinese_laws() -> list[dict[str, Any]]:
    """下载并解析 Dusker/chinese-laws-pretrain (117 部法律全文)。"""
    ds_info = DATASETS["hf_chinese_laws"]
    articles: list[dict[str, Any]] = []

    for url, filename in ds_info["urls"]:
        file_path = DATASET_DIR / f"hf_laws_{filename}"

        success = await download_file(url, file_path, f"{ds_info['name']}/{filename}")
        if not success:
            continue

        # 解析
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            law_name = filename.replace(".json", "")
            parsed = _parse_hf_law_data(data, law_name)
            articles.extend(parsed)
            logger.info("Parsed %d articles from %s", len(parsed), filename)

        except Exception as exc:
            logger.error("Failed to parse %s: %s", filename, exc)

    logger.info("Total articles from %s: %d", ds_info["name"], len(articles))
    return articles


def _parse_hf_law_data(data: Any, law_name: str) -> list[dict[str, Any]]:
    """解析 HuggingFace 法律全文数据。"""
    results: list[dict[str, Any]] = []
    import re

    if isinstance(data, str):
        # 纯文本格式
        content = data
    elif isinstance(data, dict):
        content = data.get("content", data.get("text", json.dumps(data, ensure_ascii=False)))
    elif isinstance(data, list):
        # 列表格式 — 每个元素可能是一条法条
        for item in data:
            if isinstance(item, dict):
                article = {
                    "law": law_name,
                    "num": item.get("article_number", item.get("num", item.get("条号", ""))),
                    "content": item.get("content", item.get("text", item.get("内容", ""))),
                    "title": item.get("title", item.get("标题", "")),
                    "tags": _guess_tags(law_name),
                    "category": _guess_category(law_name),
                }
                if article["content"]:
                    results.append(article)
            elif isinstance(item, str):
                # 每个元素是法条文本
                match = re.match(r'(第[一二三四五六七八九十百零\d]+条[ \s])', item)
                if match:
                    results.append({
                        "law": law_name,
                        "num": match.group(1).strip(),
                        "content": re.sub(r'\s+', '', item[match.end():]),
                        "tags": _guess_tags(law_name),
                        "category": _guess_category(law_name),
                    })
        return results
    else:
        return results

    # 按"第X条"拆分
    article_pattern = re.compile(r'(第[一二三四五六七八九十百零\d]+条[ \s])')
    parts = article_pattern.split(content)

    if len(parts) <= 1:
        if content.strip():
            results.append({
                "law": law_name,
                "num": "",
                "content": re.sub(r'\s+', '', content.strip()),
                "tags": _guess_tags(law_name),
                "category": _guess_category(law_name),
            })
    else:
        for i in range(1, len(parts) - 1, 2):
            article_num = parts[i].strip()
            article_content = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if article_content:
                article_content = re.sub(r'\s+', '', article_content)
                if len(article_content) > 10:
                    results.append({
                        "law": law_name,
                        "num": article_num,
                        "content": article_content,
                        "tags": _guess_tags(law_name),
                        "category": _guess_category(law_name),
                    })

    return results


async def download_hf_sft_data() -> list[dict[str, Any]]:
    """下载并解析 Aiiluo/Chinese-Law-SFT-Dataset (法律问答对)。"""
    ds_info = DATASETS["hf_sft_data"]
    articles: list[dict[str, Any]] = []

    for url, filename in ds_info["urls"]:
        file_path = DATASET_DIR / f"hf_sft_{filename}"

        success = await download_file(url, file_path, f"{ds_info['name']}/{filename}")
        if not success:
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            parsed = _parse_sft_data(data, filename)
            articles.extend(parsed)
            logger.info("Parsed %d entries from %s", len(parsed), filename)

        except Exception as exc:
            logger.error("Failed to parse %s: %s", filename, exc)

    logger.info("Total entries from %s: %d", ds_info["name"], len(articles))
    return articles


def _parse_sft_data(data: Any, source_name: str) -> list[dict[str, Any]]:
    """解析 SFT 训练数据为法律知识条目。"""
    results: list[dict[str, Any]] = []

    if not isinstance(data, list):
        return results

    for item in data:
        if not isinstance(item, dict):
            continue

        # SFT 数据通常是 instruction/input/output 格式
        instruction = item.get("instruction", item.get("prompt", item.get("question", "")))
        input_text = item.get("input", item.get("input_text", ""))
        output = item.get("output", item.get("response", item.get("answer", "")))

        # 组合为知识条目
        if output:
            content = f"{instruction}"
            if input_text:
                content += f"\n{input_text}"

            results.append({
                "law": source_name.replace(".json", "").replace("_", " "),
                "num": "",
                "title": instruction[:50] if instruction else "",
                "content": output.strip(),
                "tags": "",
                "category": "法律知识",
            })

        # 如果有 input，也单独存为一条（通常是具体的法律问题）
        if input_text and len(input_text) > 20:
            results.append({
                "law": source_name.replace(".json", "").replace("_", " "),
                "num": "",
                "title": input_text[:50] if input_text else "",
                "content": f"问题：{input_text}\n回答：{output}",
                "tags": "",
                "category": "法律问答",
            })

    return results


# =========================================================================
# 辅助函数
# =========================================================================

def _guess_category(law_name: str) -> str:
    """根据法律名称猜测分类。"""
    category_keywords = {
        "宪法": ["宪法"],
        "民法": ["民法典", "民法", "物权", "合同", "婚姻家庭", "继承", "人格权"],
        "刑法": ["刑法", "刑事"],
        "行政法": ["行政处罚", "行政许可", "行政法", "个人信息", "网络安全", "数据安全"],
        "经济法": ["消费者权益", "反不正当竞争", "反垄断", "电子商务", "土地管理"],
        "社会法": ["劳动法", "劳动合同", "社会保险"],
        "商法": ["公司法", "合伙企业", "破产"],
        "诉讼法": ["民事诉讼法", "刑事诉讼法", "行政诉讼法"],
        "知识产权": ["著作权", "专利", "商标", "知识产权"],
    }

    for category, keywords in category_keywords.items():
        for kw in keywords:
            if kw in law_name:
                return category
    return "其他"


def _guess_tags(law_name: str) -> str:
    """根据法律名称生成标签。"""
    category = _guess_category(law_name)
    # 取法律的简称
    short_name = law_name.replace("中华人民共和国", "").replace("法", "")
    return f"{category},{short_name}"


# =========================================================================
# 主流程
# =========================================================================

async def download_all(
    dataset_filter: str | None = None,
    skip_milvus: bool = False,
    only_download: bool = False,
) -> dict[str, Any]:
    """下载并导入所有开源数据集。"""
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {
        "downloaded": {},
        "imported": {},
        "total_articles": 0,
    }

    all_articles: list[dict[str, Any]] = []

    # ---- 下载阶段 ----
    downloaders = {
        "github": ("github_laws", download_github_laws),
        "hf_laws": ("hf_chinese_laws", download_hf_chinese_laws),
        "hf_sft": ("hf_sft_data", download_hf_sft_data),
    }

    for key, (ds_key, downloader) in downloaders.items():
        if dataset_filter and dataset_filter != key:
            continue

        logger.info("=" * 60)
        logger.info("Downloading: %s", DATASETS[ds_key]["name"])
        logger.info("=" * 60)

        try:
            articles = await downloader()
            results["downloaded"][ds_key] = len(articles)
            all_articles.extend(articles)
            logger.info("Downloaded %d articles from %s", len(articles), DATASETS[ds_key]["name"])
        except Exception as exc:
            logger.error("Failed to download %s: %s", DATASETS[ds_key]["name"], exc)
            results["downloaded"][ds_key] = 0

    results["total_articles"] = len(all_articles)
    logger.info("Total articles downloaded: %d", len(all_articles))

    if only_download:
        # 保存到文件
        output_path = DATASET_DIR / "all_opensource_articles.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_articles, f, ensure_ascii=False, indent=2)
        logger.info("Saved all articles to: %s", output_path)
        return results

    if not all_articles:
        logger.warning("No articles downloaded. Nothing to import.")
        return results

    # ---- 导入阶段 ----
    if not skip_milvus:
        try:
            from app.rag.batch_import import MilvusVectorImporter
            from app.rag.batch_import import _build_default_category_map

            milvus_importer = MilvusVectorImporter()
            category_map = _build_default_category_map()
            milvus_stats = milvus_importer.import_articles(all_articles, category_map)
            results["imported"]["milvus"] = milvus_stats
        except Exception as exc:
            logger.error("Milvus import failed: %s", exc)
            results["imported"]["milvus"] = {"error": str(exc)}

    # 导入 PostgreSQL
    try:
        from app.rag.batch_import import PostgreSQLImporter
        from app.core.database import async_session_factory

        pg_importer = PostgreSQLImporter(async_session_factory)

        # 先导入法律列表（从文章中提取唯一的法律名称）
        unique_laws = {}
        for art in all_articles:
            law_name = art.get("law", "")
            if law_name and law_name not in unique_laws:
                unique_laws[law_name] = {
                    "name": law_name,
                    "law_type": _guess_category(law_name),
                    "short_name": law_name.replace("中华人民共和国", "").replace("法", ""),
                    "status": "active",
                }
        if unique_laws:
            await pg_importer.import_laws(list(unique_laws.values()))

        # 导入法条
        await pg_importer.import_articles(all_articles)

        results["imported"]["postgres"] = pg_importer.get_stats()
    except Exception as exc:
        logger.error("PostgreSQL import failed: %s", exc)
        results["imported"]["postgres"] = {"error": str(exc)}

    return results


def print_download_summary(results: dict[str, Any]) -> None:
    """打印下载和导入结果摘要。"""
    print("\n" + "=" * 60)
    print("  开源法律数据集下载与导入 — 结果摘要")
    print("=" * 60)

    print("\n[下载统计]")
    for ds_key, count in results.get("downloaded", {}).items():
        ds_info = DATASETS.get(ds_key, {})
        name = ds_info.get("name", ds_key)
        estimated = ds_info.get("estimated_records", "?")
        print(f"  {name:40s} : {count:>8,} 条 (预估 {estimated:,})")

    print(f"\n  {'总计':40s} : {results.get('total_articles', 0):>8,} 条")

    imported = results.get("imported", {})
    if imported:
        print("\n[导入统计]")
        if "postgres" in imported:
            pg = imported["postgres"]
            if isinstance(pg, dict) and "error" not in pg:
                print(f"  PostgreSQL: {pg.get('laws_created', 0)} 法律新增, {pg.get('articles_created', 0)} 法条新增")
            else:
                print(f"  PostgreSQL: 失败 — {pg.get('error', 'unknown')}")

        if "milvus" in imported:
            mv = imported["milvus"]
            if isinstance(mv, dict) and "error" not in mv:
                print(f"  Milvus: {mv.get('vectors_inserted', 0)} 向量插入")
            else:
                print(f"  Milvus: 失败 — {mv.get('error', 'unknown')}")

    print("\n" + "=" * 60)


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="开源法律数据集下载与导入")
    parser.add_argument(
        "--datasets",
        choices=["github", "hf_laws", "hf_sft"],
        default=None,
        help="只下载特定数据源",
    )
    parser.add_argument("--only-download", action="store_true", help="仅下载不导入")
    parser.add_argument("--only-import", action="store_true", help="仅导入已下载的数据")
    parser.add_argument("--skip-milvus", action="store_true", help="跳过 Milvus 导入")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.only_import:
        # 从已下载的文件导入
        logger.info("Importing from previously downloaded files...")
        articles = []
        for f in DATASET_DIR.glob("*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    articles.extend(data)
            except Exception:
                continue
        logger.info("Loaded %d articles from downloaded files", len(articles))
        # TODO: 实现仅导入逻辑
        return

    results = asyncio.run(download_all(
        dataset_filter=args.datasets,
        skip_milvus=args.skip_milvus,
        only_download=args.only_download,
    ))

    print_download_summary(results)


if __name__ == "__main__":
    main()
