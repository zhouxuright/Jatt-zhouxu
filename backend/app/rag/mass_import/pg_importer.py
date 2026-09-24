"""优化的 PostgreSQL 批量导入器。

使用 INSERT ... ON CONFLICT DO NOTHING 替代逐行 SELECT+INSERT，
大幅提升百万级数据导入速度。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.rag.mass_import.progress import ImportProgress

logger = logging.getLogger(__name__)

# 批次大小
PG_BATCH_SIZE = 200


class OptimizedPGImporter:
    """高性能 PostgreSQL 批量导入器。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._stats = {
            "laws_inserted": 0,
            "laws_skipped": 0,
            "articles_inserted": 0,
            "articles_skipped": 0,
        }

    async def import_laws_bulk(self, laws: list[dict[str, Any]]) -> dict[str, int]:
        """批量导入法律元数据到 laws 表。

        使用 INSERT ... ON CONFLICT DO NOTHING 实现幂等导入。
        需要先确保 laws 表有 (name, effective_date) 的唯一约束。

        Args:
            laws: 法律元数据列表

        Returns:
            导入统计
        """
        from app.models.legal_knowledge import Law
        from sqlalchemy import text

        async with self._session_factory() as session:
            # 先确保唯一约束存在
            try:
                await session.execute(text(
                    "ALTER TABLE laws ADD CONSTRAINT uq_law_name_date "
                    "UNIQUE (name, effective_date)"
                ))
                await session.commit()
                logger.info("Added unique constraint uq_law_name_date to laws table")
            except Exception:
                # 约束可能已存在
                await session.rollback()

            progress = ImportProgress(len(laws), "pg_import_laws")

            for batch_start in range(0, len(laws), PG_BATCH_SIZE):
                batch = laws[batch_start:batch_start + PG_BATCH_SIZE]

                values = []
                for law in batch:
                    values.append({
                        "id": str(uuid.uuid4()),
                        "name": law.get("name", "")[:512],
                        "short_name": law.get("short_name", "")[:256],
                        "law_type": law.get("law_type", "其他")[:64],
                        "category": law.get("category", "")[:128],
                        "effective_date": law.get("effective_date", "")[:32],
                        "status": law.get("status", "active")[:32],
                        "issuing_authority": law.get("issuing_authority", "")[:256],
                        "abstract": law.get("abstract", "") or "",
                    })

                # 批量插入（ON CONFLICT 跳过已存在的）
                stmt = text("""
                    INSERT INTO laws (id, name, short_name, law_type, category,
                                     effective_date, status, issuing_authority, abstract)
                    VALUES (:id, :name, :short_name, :law_type, :category,
                            :effective_date, :status, :issuing_authority, :abstract)
                    ON CONFLICT ON CONSTRAINT uq_law_name_date DO NOTHING
                """)

                try:
                    result = await session.execute(stmt, values)
                    inserted = result.rowcount
                    self._stats["laws_inserted"] += inserted
                    self._stats["laws_skipped"] += len(batch) - inserted
                    await session.commit()
                except Exception as exc:
                    logger.error("Failed to insert laws batch: %s", exc)
                    await session.rollback()

                progress.update(len(batch))

            progress.finish()
            logger.info("Laws import: %d inserted, %d skipped",
                         self._stats["laws_inserted"], self._stats["laws_skipped"])

        return dict(self._stats)

    async def import_articles_bulk(
        self,
        articles: list[dict[str, Any]],
        laws_map: dict[str, str] | None = None,
    ) -> dict[str, int]:
        """批量导入条文到 legal_articles 表。

        Args:
            articles: 条文列表
            laws_map: law_name → law_id 映射（如为 None 则自动构建）

        Returns:
            导入统计
        """
        from app.models.legal_knowledge import Law
        from sqlalchemy import text, select

        if laws_map is None:
            laws_map = await self._build_laws_map()

        async with self._session_factory() as session:
            # 确保唯一约束存在
            try:
                await session.execute(text(
                    "ALTER TABLE legal_articles ADD CONSTRAINT uq_law_article "
                    "UNIQUE (law_id, article_number)"
                ))
                await session.commit()
                logger.info("Added unique constraint uq_law_article to legal_articles")
            except Exception:
                await session.rollback()

            # 过滤掉找不到对应法律的条文
            valid_articles = []
            for art in articles:
                law_name = art.get("law_name", "")
                law_id = laws_map.get(law_name)
                if law_id:
                    art["_law_id"] = law_id
                    valid_articles.append(art)
                else:
                    self._stats["articles_skipped"] += 1

            logger.info("Valid articles for import: %d / %d (missing law_id: %d)",
                         len(valid_articles), len(articles),
                         len(articles) - len(valid_articles))

            progress = ImportProgress(len(valid_articles), "pg_import_articles")

            for batch_start in range(0, len(valid_articles), PG_BATCH_SIZE):
                batch = valid_articles[batch_start:batch_start + PG_BATCH_SIZE]

                values = []
                for art in batch:
                    article_num = art.get("article_number", "")
                    # 对于没有条号的条文，使用内容的哈希作为唯一标识
                    if not article_num:
                        import hashlib
                        content_hash = hashlib.md5(
                            art.get("content", "")[:100].encode()
                        ).hexdigest()[:16]
                        article_num = f"__{content_hash}"

                    values.append({
                        "id": str(uuid.uuid4()),
                        "law_id": art["_law_id"],
                        "article_number": article_num[:64],
                        "title": art.get("title", "")[:256] if art.get("title") else "",
                        "content": art.get("content", ""),
                        "chapter": art.get("chapter", "")[:128],
                        "section": "",
                        "effective_status": art.get("effective_status", "active")[:32],
                        "tags": art.get("tags", "")[:512],
                    })

                stmt = text("""
                    INSERT INTO legal_articles (id, law_id, article_number, title, content,
                                               chapter, section, effective_status, tags)
                    VALUES (:id, :law_id, :article_number, :title, :content,
                            :chapter, :section, :effective_status, :tags)
                    ON CONFLICT ON CONSTRAINT uq_law_article DO NOTHING
                """)

                try:
                    result = await session.execute(stmt, values)
                    inserted = result.rowcount
                    self._stats["articles_inserted"] += inserted
                    self._stats["articles_skipped"] += len(batch) - inserted
                    await session.commit()
                except Exception as exc:
                    logger.error("Failed to insert articles batch at %d: %s", batch_start, exc)
                    await session.rollback()

                progress.update(len(batch))

            progress.finish()

            # 清理内部字段
            for art in valid_articles:
                art.pop("_law_id", None)

            logger.info("Articles import: %d inserted, %d skipped",
                         self._stats["articles_inserted"], self._stats["articles_skipped"])

        return dict(self._stats)

    async def _build_laws_map(self) -> dict[str, str]:
        """构建 law_name → law_id 映射。"""
        from app.models.legal_knowledge import Law
        from sqlalchemy import select

        async with self._session_factory() as session:
            result = await session.execute(select(Law.id, Law.name))
            return {row[1]: row[0] for row in result.all()}

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)
