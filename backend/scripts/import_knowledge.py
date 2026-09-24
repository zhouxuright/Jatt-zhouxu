"""
法律智能辅助系统 - 法律知识库数据导入工具

功能:
1. 从 knowledge_seed.py 加载种子数据
2. 批量导入到 PostgreSQL (法律条文、案例、概念)
3. 批量导入到 Milvus 向量数据库 (用于 RAG 检索)
4. 支持增量导入（已存在的跳过）

使用方法:
    python scripts/import_knowledge.py
"""

import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.core.database import async_session_factory, engine, Base
from app.models.legal_knowledge import (
    Law, LegalArticle, CourtCase, JudicialInterpretation, LegalConcept
)
from app.rag.knowledge_seed import (
    LAWS_DATA, ARTICLES_DATA, COURT_CASES_DATA, LEGAL_CONCEPTS_DATA
)
from app.rag.milvus_service import MilvusRAGService


async def import_laws():
    """导入法律基本信息"""
    print("=" * 60)
    print("导入法律基本信息...")
    print("=" * 60)

    async with async_session_factory() as session:
        imported = 0
        skipped = 0

        for law_data in LAWS_DATA:
            # 检查是否已存在
            result = await session.execute(
                select(Law).where(Law.name == law_data["name"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            law = Law(
                name=law_data["name"],
                short_name=law_data.get("short_name"),
                law_type=law_data.get("law_type"),
                category=law_data.get("category"),
                effective_date=law_data.get("effective_date"),
                status=law_data.get("status", "active"),
                issuing_authority=law_data.get("issuing_authority"),
            )
            session.add(law)
            imported += 1

        await session.commit()
        print(f"✓ 导入完成: {imported} 条法律, 跳过 {skipped} 条已存在的")
        return imported


async def import_articles():
    """导入法律条文"""
    print("\n" + "=" * 60)
    print("导入法律条文...")
    print("=" * 60)

    async with async_session_factory() as session:
        # 先加载所有法律，建立 name -> id 映射
        result = await session.execute(select(Law))
        laws = {law.name: law.id for law in result.scalars().all()}

        imported = 0
        skipped = 0
        errors = 0

        for article_data in ARTICLES_DATA:
            law_name = article_data["law"]
            law_id = laws.get(law_name)

            if not law_id:
                print(f"  ✗ 找不到法律: {law_name}")
                errors += 1
                continue

            # 检查是否已存在
            result = await session.execute(
                select(LegalArticle).where(
                    LegalArticle.law_id == law_id,
                    LegalArticle.article_number == article_data["num"]
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            article = LegalArticle(
                law_id=law_id,
                article_number=article_data["num"],
                title=article_data.get("title"),
                content=article_data["content"],
                chapter=article_data.get("chapter"),
                section=article_data.get("section"),
                effective_status=article_data.get("effective_status", "active"),
                tags=article_data.get("tags"),
            )
            session.add(article)
            imported += 1

            if imported % 50 == 0:
                print(f"  已导入 {imported} 条...")

        await session.commit()
        print(f"✓ 导入完成: {imported} 条条文, 跳过 {skipped} 条已存在的, {errors} 条错误")
        return imported


async def import_court_cases():
    """导入典型案例"""
    print("\n" + "=" * 60)
    print("导入典型案例...")
    print("=" * 60)

    async with async_session_factory() as session:
        imported = 0
        skipped = 0

        for case_data in COURT_CASES_DATA:
            # 检查是否已存在
            result = await session.execute(
                select(CourtCase).where(CourtCase.case_number == case_data["case_number"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            case = CourtCase(
                case_number=case_data["case_number"],
                title=case_data["title"],
                court_name=case_data.get("court_name"),
                case_type=case_data.get("case_type"),
                cause_of_action=case_data.get("cause_of_action"),
                decision_date=case_data.get("decision_date"),
                parties=case_data.get("parties"),
                summary=case_data.get("summary"),
                full_text=case_data.get("full_text"),
                key_points=case_data.get("key_points"),
                referenced_laws=case_data.get("referenced_laws"),
                judgment_result=case_data.get("judgment_result"),
                tags=case_data.get("tags"),
            )
            session.add(case)
            imported += 1

        await session.commit()
        print(f"✓ 导入完成: {imported} 条案例, 跳过 {skipped} 条已存在的")
        return imported


async def import_legal_concepts():
    """导入法律概念"""
    print("\n" + "=" * 60)
    print("导入法律概念...")
    print("=" * 60)

    async with async_session_factory() as session:
        imported = 0
        skipped = 0

        for concept_data in LEGAL_CONCEPTS_DATA:
            # 检查是否已存在
            result = await session.execute(
                select(LegalConcept).where(LegalConcept.name == concept_data["name"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                skipped += 1
                continue

            concept = LegalConcept(
                name=concept_data["name"],
                definition=concept_data["definition"],
                category=concept_data.get("category"),
                related_articles=concept_data.get("related_articles"),
                related_concepts=concept_data.get("related_concepts"),
            )
            session.add(concept)
            imported += 1

        await session.commit()
        print(f"✓ 导入完成: {imported} 条概念, 跳过 {skipped} 条已存在的")
        return imported


async def import_to_milvus():
    """导入到 Milvus 向量数据库"""
    print("\n" + "=" * 60)
    print("导入到 Milvus 向量数据库...")
    print("=" * 60)

    # 从数据库读取所有条文
    async with async_session_factory() as session:
        result = await session.execute(
            select(LegalArticle).join(Law).where(Law.status == "active")
        )
        articles = result.scalars().all()

        print(f"从数据库读取 {len(articles)} 条有效法律条文")

        if not articles:
            print("✗ 没有文档需要导入")
            return 0

        # 准备导入数据
        ids = []
        law_names = []
        article_numbers = []
        contents = []
        tags_list = []
        categories = []

        for article in articles:
            # 获取法律名称
            law_result = await session.execute(
                select(Law).where(Law.id == article.law_id)
            )
            law = law_result.scalar_one_or_none()

            if not law:
                continue

            ids.append(str(article.id))
            law_names.append(law.name)
            article_numbers.append(article.article_number)
            contents.append(article.content)
            tags_list.append(article.tags or "")
            categories.append(law.category or "")

    # 初始化 Milvus 并导入
    milvus = MilvusRAGService()

    # 先创建集合（如果不存在）
    if not milvus.create_collection():
        print("✗ 无法创建 Milvus 集合")
        return 0

    # 生成嵌入向量
    print(f"正在生成 {len(ids)} 条文档的嵌入向量...")
    from app.rag.milvus_service import _get_embeddings_sync
    embeddings = _get_embeddings_sync(contents)

    # 插入数据
    try:
        from pymilvus import Collection
        from app.rag.milvus_service import LEGAL_ARTICLES_COLLECTION

        collection = Collection(LEGAL_ARTICLES_COLLECTION)
        collection.load()

        data = [ids, law_names, article_numbers, contents, tags_list, categories, embeddings]
        collection.insert(data)
        collection.flush()
        collection.load()

        count = collection.num_entities
        print(f"✓ 导入完成: {count} 条文档到 Milvus")
        return count
    except Exception as exc:
        print(f"✗ Milvus 导入失败: {exc}")
        import traceback
        traceback.print_exc()
        return 0


async def print_statistics():
    """打印统计信息"""
    print("\n" + "=" * 60)
    print("知识库统计信息")
    print("=" * 60)

    async with async_session_factory() as session:
        # 法律数量
        result = await session.execute(select(Law))
        laws_count = len(result.scalars().all())

        # 条文数量
        result = await session.execute(select(LegalArticle))
        articles_count = len(result.scalars().all())

        # 案例数量
        result = await session.execute(select(CourtCase))
        cases_count = len(result.scalars().all())

        # 概念数量
        result = await session.execute(select(LegalConcept))
        concepts_count = len(result.scalars().all())

        print(f"法律: {laws_count} 部")
        print(f"条文: {articles_count} 条")
        print(f"案例: {cases_count} 个")
        print(f"概念: {concepts_count} 个")

    # Milvus 统计
    print("\nMilvus 向量数据库:")
    milvus = MilvusRAGService()
    stats = milvus.get_stats()
    print(f"  集合: {stats.get('collection', 'N/A')}")
    print(f"  实体数: {stats.get('count', 0)}")
    print(f"  状态: {stats.get('status', 'unknown')}")


async def main():
    """主函数"""
    print("法律智能辅助系统 - 知识库数据导入工具")
    print("版本: 1.0.0")
    print()

    try:
        # 先创建数据库表（如果不存在）
        print("检查并创建数据库表...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✓ 数据库表已就绪\n")

        # 1. 导入到 PostgreSQL
        laws_count = await import_laws()
        articles_count = await import_articles()
        cases_count = await import_court_cases()
        concepts_count = await import_legal_concepts()

        # 2. 导入到 Milvus
        milvus_count = await import_to_milvus()

        # 3. 打印统计
        await print_statistics()

        print("\n" + "=" * 60)
        print("✓ 所有数据导入完成!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 导入过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
