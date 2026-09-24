"""条文去重模块。

两级去重：
1. 精确内容哈希去重：MD5(content[:200])
2. 同源去重：(law_name, article_number) 组合键
跨源冲突时按优先级保留：laws.json > hf_laws > crawler_data > seed
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.rag.mass_import.progress import ImportProgress

logger = logging.getLogger(__name__)

# 数据源优先级（数字越小优先级越高）
SOURCE_PRIORITY = {
    "laws_json": 0,
    "hf_laws": 1,
    "crawler_data": 2,
    "seed": 3,
}


class ArticleDeduplicator:
    """条文去重器。"""

    def deduplicate(
        self,
        articles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """执行两级去重。

        Args:
            articles: 所有来源的条文列表，每条需含
                      law_name, article_number, content, 可选 _source 字段

        Returns:
            去重后的条文列表
        """
        total_before = len(articles)
        progress = ImportProgress(total_before, "dedup")

        # Level 1: 精确内容哈希去重
        seen_hashes: dict[str, int] = {}  # hash → index of best version
        unique_by_hash: list[dict[str, Any]] = []

        for art in articles:
            content = art.get("content", "")
            if not content:
                continue

            h = hashlib.md5(content[:200].encode()).hexdigest()

            if h in seen_hashes:
                # 已存在，按优先级决定是否替换
                existing_idx = seen_hashes[h]
                existing = unique_by_hash[existing_idx]
                if self._should_replace(existing, art):
                    unique_by_hash[existing_idx] = art
            else:
                seen_hashes[h] = len(unique_by_hash)
                unique_by_hash.append(art)

            progress.update()

        after_hash = len(unique_by_hash)
        progress.finish()
        logger.info("Level 1 (content hash): %d → %d (removed %d)",
                     total_before, after_hash, total_before - after_hash)

        # Level 2: (law_name, article_number) 去重
        seen_keys: dict[str, int] = {}
        unique: list[dict[str, Any]] = []

        for art in unique_by_hash:
            key = f"{art.get('law_name', '')}|{art.get('article_number', '')}"

            if key in seen_keys:
                existing_idx = seen_keys[key]
                existing = unique[existing_idx]
                if self._should_replace(existing, art):
                    unique[existing_idx] = art
            else:
                seen_keys[key] = len(unique)
                unique.append(art)

        after_key = len(unique)
        logger.info("Level 2 (law+article key): %d → %d (removed %d)",
                     after_hash, after_key, after_hash - after_key)
        logger.info("Total dedup: %d → %d (removed %d, %.1f%%)",
                     total_before, after_key,
                     total_before - after_key,
                     (1 - after_key / max(total_before, 1)) * 100)

        # 清理内部字段
        for art in unique:
            art.pop("_source", None)

        return unique

    @staticmethod
    def _should_replace(existing: dict[str, Any], new: dict[str, Any]) -> bool:
        """判断是否应该用 new 替换 existing。

        优先级：
        1. 数据源优先级（laws_json > hf_laws > crawler_data > seed）
        2. 内容更长的版本（通常更完整）
        """
        existing_source = SOURCE_PRIORITY.get(existing.get("_source", ""), 99)
        new_source = SOURCE_PRIORITY.get(new.get("_source", ""), 99)

        if new_source < existing_source:
            return True
        if new_source > existing_source:
            return False

        # 同优先级时，保留内容更长的
        return len(new.get("content", "")) > len(existing.get("content", ""))
