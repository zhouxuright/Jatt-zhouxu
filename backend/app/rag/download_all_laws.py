"""批量下载 HuggingFace 上 117 部中国法律全文。

数据集：Dusker/chinese-laws-pretrain
URL: https://huggingface.co/datasets/Dusker/chinese-laws-pretrain

先列出所有文件，然后批量下载。

运行方式：
    python -m app.rag.download_all_laws
    python -m app.rag.download_all_laws --list-only   # 仅列出文件不下载
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data" / "datasets"
BASE_URL = "https://huggingface.co/datasets/Dusker/chinese-laws-pretrain/resolve/main"
TREE_URL = "https://huggingface.co/api/datasets/Dusker/chinese-laws-pretrain/tree/main"


async def list_all_files() -> list[dict]:
    """通过 HuggingFace API 列出数据集中所有文件。"""
    logger.info("Fetching file list from HuggingFace API...")

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(TREE_URL)
        resp.raise_for_status()
        files = resp.json()

    # 只保留 .json 文件
    json_files = [
        f for f in files
        if f.get("type") == "file" and f.get("path", "").endswith(".json")
    ]

    logger.info("Found %d JSON files in dataset", len(json_files))
    return json_files


async def download_all(
    list_only: bool = False,
    max_files: int = 0,
) -> None:
    """下载所有法律全文。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    files = await list_all_files()

    if list_only:
        print(f"\n{'='*60}")
        print(f"  HuggingFace 法律全文数据集 — 文件列表")
        print(f"{'='*60}\n")
        for f in files:
            size_kb = f.get("size", 0) / 1024
            print(f"  {f['path']:50s}  {size_kb:>8.1f} KB")
        print(f"\n  Total: {len(files)} files")
        return

    if max_files > 0:
        files = files[:max_files]
        logger.info("Limited to %d files", max_files)

    # 下载
    downloaded = 0
    skipped = 0
    failed = 0

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        for i, file_info in enumerate(files):
            filename = file_info["path"]
            url = f"{BASE_URL}/{quote(filename)}"
            output_path = DATA_DIR / f"hf_laws_{filename}"

            # 已存在则跳过
            if output_path.exists() and output_path.stat().st_size > 0:
                skipped += 1
                continue

            try:
                resp = await client.get(url)
                resp.raise_for_status()

                with open(output_path, "wb") as f:
                    f.write(resp.content)

                downloaded += 1
                if downloaded % 10 == 0:
                    logger.info(
                        "  Progress: %d downloaded, %d skipped, %d failed (total: %d)",
                        downloaded, skipped, failed, len(files),
                    )

            except Exception as exc:
                failed += 1
                logger.warning("Failed to download %s: %s", filename, exc)

    logger.info(
        "\nDownload complete: %d new, %d skipped, %d failed (total: %d)",
        downloaded, skipped, failed, len(files),
    )

    # 统计解析后的条文数
    total_articles = 0
    for f in sorted(DATA_DIR.glob("hf_laws_*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                total_articles += len(data)
            elif isinstance(data, str):
                import re
                total_articles += len(re.findall(r'第[一二三四五六七八九十百零\d]+条', data))
        except Exception:
            pass

    print(f"\n{'='*60}")
    print(f"  下载完成")
    print(f"{'='*60}")
    print(f"  新增下载   : {downloaded}")
    print(f"  已存在跳过 : {skipped}")
    print(f"  下载失败   : {failed}")
    print(f"  法律全文总条目: ~{total_articles:,}")
    print(f"{'='*60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 HuggingFace 中国法律全文数据集")
    parser.add_argument("--list-only", action="store_true", help="仅列出文件不下载")
    parser.add_argument("--max-files", type=int, default=0, help="最多下载文件数（0=全部）")
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    asyncio.run(download_all(
        list_only=args.list_only,
        max_files=args.max_files,
    ))


if __name__ == "__main__":
    main()
