"""
批量导入工具 - 将裁判文书数据导入到 PostgreSQL 和 Milvus

支持：
1. 增量导入（跳过已存在的记录）
2. 并行导入（使用多线程加速）
3. 向量嵌入（使用 BGE-M3 模型）
4. 批量写入 Milvus（优化性能）
"""

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory
from app.models.legal_knowledge import CourtCase
from app.services.model_registry import ModelRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# PostgreSQL 批量导入
# =============================================================================

class PostgreSQLBatchImporter:
    """PostgreSQL 批量导入器"""

    def __init__(self, batch_size: int = 100):
        self.batch_size = batch_size
        self.stats = {
            "total_processed": 0,
            "total_inserted": 0,
            "total_skipped": 0,
            "errors": 0,
        }

    async def import_from_directory(self, input_dir: str):
        """从目录批量导入"""
        json_files = [f for f in os.listdir(input_dir) if f.endswith(".json")]
        logger.info(f"找到 {len(json_files)} 个文件待导入")

        batch = []
        for i, filename in enumerate(json_files, 1):
            filepath = os.path.join(input_dir, filename)

            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                batch.append(data)
                self.stats["total_processed"] += 1

                # 达到批次大小时执行导入
                if len(batch) >= self.batch_size:
                    await self._import_batch(batch)
                    batch = []

                # 打印进度
                if i % 100 == 0:
                    logger.info(f"处理进度: {i}/{len(json_files)} | "
                              f"已插入: {self.stats['total_inserted']} | "
                              f"已跳过: {self.stats['total_skipped']}")

            except Exception as e:
                logger.error(f"处理文件失败 ({filename}): {e}")
                self.stats["errors"] += 1

        # 导入剩余的数据
        if batch:
            await self._import_batch(batch)

        logger.info(f"批量导入完成: {self.stats}")
        return self.stats

    async def _import_batch(self, batch: list[dict[str, Any]]):
        """导入一批数据"""
        try:
            async with async_session_factory() as session:
                # 获取所有案号，检查已存在的记录
                case_numbers = [d.get("case_number") for d in batch if d.get("case_number")]
                if case_numbers:
                    result = await session.execute(
                        select(CourtCase.case_number).where(
                            CourtCase.case_number.in_(case_numbers)
                        )
                    )
                    existing_numbers = set(result.scalars().all())
                else:
                    existing_numbers = set()

                # 准备插入的数据
                insert_data = []
                for data in batch:
                    case_number = data.get("case_number")
                    if not case_number:
                        continue

                    if case_number in existing_numbers:
                        self.stats["total_skipped"] += 1
                        continue

                    insert_data.append({
                        "case_number": case_number,
                        "title": data.get("title", ""),
                        "court_name": data.get("court_name", ""),
                        "case_type": data.get("case_type", ""),
                        "cause_of_action": data.get("cause_of_action", ""),
                        "decision_date": data.get("decision_date", ""),
                        "parties": data.get("parties", ""),
                        "summary": data.get("summary", ""),
                        "full_text": data.get("full_text", ""),
                        "key_points": data.get("key_points", ""),
                        "referenced_laws": data.get("referenced_laws", ""),
                        "judgment_result": data.get("judgment_result", ""),
                        "tags": data.get("tags", ""),
                    })

                # 批量插入
                if insert_data:
                    stmt = insert(CourtCase).values(insert_data)
                    await session.execute(stmt)
                    await session.commit()
                    self.stats["total_inserted"] += len(insert_data)
                    logger.debug(f"批量插入 {len(insert_data)} 条记录")

        except Exception as e:
            logger.error(f"批量导入失败: {e}")
            self.stats["errors"] += len(batch)


# =============================================================================
# Milvus 向量导入
# =============================================================================

class MilvusBatchImporter:
    """Milvus 批量向量导入器"""

    def __init__(self, batch_size: int = 100):
        self.batch_size = batch_size
        self.model = ModelRegistry.get_embedding_model()
        self.stats = {
            "total_processed": 0,
            "total_inserted": 0,
            "errors": 0,
        }

    async def import_from_database(self):
        """从数据库导入到 Milvus"""
        from app.rag.milvus_service import MilvusRAGService

        milvus = MilvusRAGService()
        milvus.create_collection()

        async with async_session_factory() as session:
            # 获取所有案例
            result = await session.execute(select(CourtCase))
            cases = result.scalars().all()

            logger.info(f"从数据库读取 {len(cases)} 条案例")

            batch_ids = []
            batch_texts = []
            batch_embeddings = []
            batch_metadata = []

            for i, case in enumerate(cases, 1):
                # 构建文本
                text = self._build_text(case)

                # 生成嵌入
                embedding = self._generate_embedding(text)

                batch_ids.append(str(case.id))
                batch_texts.append(text)
                batch_embeddings.append(embedding)
                batch_metadata.append({
                    "case_number": case.case_number,
                    "title": case.title,
                    "court_name": case.court_name,
                    "case_type": case.case_type,
                    "cause_of_action": case.cause_of_action,
                    "tags": case.tags,
                })

                self.stats["total_processed"] += 1

                # 达到批次大小时写入 Milvus
                if len(batch_ids) >= self.batch_size:
                    await self._insert_batch(
                        milvus, batch_ids, batch_texts, batch_embeddings, batch_metadata
                    )
                    batch_ids = []
                    batch_texts = []
                    batch_embeddings = []
                    batch_metadata = []

                # 打印进度
                if i % 100 == 0:
                    logger.info(f"向量导入进度: {i}/{len(cases)} | "
                              f"已插入: {self.stats['total_inserted']}")

            # 插入剩余数据
            if batch_ids:
                await self._insert_batch(
                    milvus, batch_ids, batch_texts, batch_embeddings, batch_metadata
                )

        logger.info(f"Milvus 向量导入完成: {self.stats}")
        return self.stats

    def _build_text(self, case: CourtCase) -> str:
        """构建用于向量化的文本"""
        parts = []
        if case.title:
            parts.append(case.title)
        if case.cause_of_action:
            parts.append(f"案由：{case.cause_of_action}")
        if case.summary:
            parts.append(case.summary[:500])
        if case.key_points:
            parts.append(f"裁判要旨：{case.key_points}")

        return "\n".join(parts)

    def _generate_embedding(self, text: str) -> list[float]:
        """生成文本嵌入"""
        # 截取前 2000 字
        text = text[:2000] if len(text) > 2000 else text
        output = self.model.encode([text], return_dense=True)
        return output["dense_vecs"][0].tolist()

    async def _insert_batch(
        self,
        milvus,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ):
        """批量插入到 Milvus"""
        try:
            from pymilvus import Collection
            from app.rag.milvus_service import LEGAL_ARTICLES_COLLECTION

            collection = Collection(LEGAL_ARTICLES_COLLECTION)

            # 构建插入数据
            data = [
                ids,
                [m.get("title", "") for m in metadatas],  # law_name -> title
                [m.get("case_number", "") for m in metadatas],  # article_number -> case_number
                texts,  # content
                [m.get("tags", "") for m in metadatas],  # tags
                [m.get("case_type", "") for m in metadatas],  # category -> case_type
                embeddings,
            ]

            collection.insert(data)
            collection.flush()

            self.stats["total_inserted"] += len(ids)
            logger.debug(f"批量插入 {len(ids)} 条向量到 Milvus")

        except Exception as e:
            logger.error(f"Milvus 批量插入失败: {e}")
            self.stats["errors"] += len(ids)


# =============================================================================
# 主函数
# =============================================================================

async def main():
    """主函数"""
    # 1. 从 JSON 文件导入到 PostgreSQL
    logger.info("=" * 60)
    logger.info("步骤 1: 导入到 PostgreSQL")
    logger.info("=" * 60)

    pg_importer = PostgreSQLBatchImporter(batch_size=100)
    input_dir = "./crawler_data_processed"

    if os.path.exists(input_dir):
        pg_stats = await pg_importer.import_from_directory(input_dir)
        logger.info(f"PostgreSQL 导入统计: {pg_stats}")
    else:
        logger.warning(f"数据目录不存在: {input_dir}")
        logger.info("跳过 PostgreSQL 导入")

    # 2. 从 PostgreSQL 导入到 Milvus
    logger.info("=" * 60)
    logger.info("步骤 2: 导入到 Milvus (向量数据库)")
    logger.info("=" * 60)

    milvus_importer = MilvusBatchImporter(batch_size=100)
    milvus_stats = await milvus_importer.import_from_database()
    logger.info(f"Milvus 导入统计: {milvus_stats}")

    logger.info("=" * 60)
    logger.info("批量导入完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
