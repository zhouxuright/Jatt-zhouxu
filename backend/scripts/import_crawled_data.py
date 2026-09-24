#!/usr/bin/env python3
"""
将爬取的法律数据导入数据库
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import os
from typing import Any

from sqlalchemy import select
from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle, CourtCase, LegalConcept


async def import_laws_from_files(data_dir: str):
    """从文件导入法律条文"""
    print("\n[导入 1] 法律条文")

    async with async_session_factory() as session:
        files = [f for f in os.listdir(data_dir) if f.startswith("law_")]
        imported = 0

        for filename in files:
            filepath = os.path.join(data_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 检查是否已存在
            result = await session.execute(
                select(LegalArticle).where(
                    LegalArticle.article_number == data.get("article_number", ""),
                )
            )
            if result.scalars().first():
                continue

            # 查找或创建法律
            law_name = data.get("law_name", "")
            result = await session.execute(
                select(Law).where(Law.name == law_name)
            )
            law = result.scalars().first()

            if not law:
                law = Law(
                    name=law_name,
                    law_type="法律",
                    status="active",
                )
                session.add(law)
                await session.flush()

            # 创建法条
            article = LegalArticle(
                law_id=law.id,
                article_number=data.get("article_number", ""),
                content=data.get("content", ""),
                tags=data.get("tags", ""),
            )
            session.add(article)
            imported += 1

        await session.commit()
        print(f"  ✓ 导入 {imported} 条法律条文")


async def import_cases_from_files(data_dir: str):
    """从文件导入案例"""
    print("\n[导入 2] 案例")

    async with async_session_factory() as session:
        files = [f for f in os.listdir(data_dir) if f.startswith("case_")]
        imported = 0

        for filename in files:
            filepath = os.path.join(data_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 检查是否已存在
            case_number = data.get("case_number", "")
            if case_number:
                result = await session.execute(
                    select(CourtCase).where(CourtCase.case_number == case_number)
                )
                if result.scalars().first():
                    continue

            # 创建案例
            case = CourtCase(
                case_number=case_number or filename,
                title=data.get("title", ""),
                court_name=data.get("court_name", ""),
                case_type=data.get("case_type", ""),
                cause_of_action=data.get("cause_of_action", ""),
                decision_date=data.get("decision_date", ""),
                summary=data.get("summary", ""),
                full_text=data.get("summary", ""),  # 使用摘要作为全文
                key_points=data.get("key_points", ""),
                referenced_laws=data.get("referenced_laws", ""),
                tags=data.get("tags", ""),
            )
            session.add(case)
            imported += 1

        await session.commit()
        print(f"  ✓ 导入 {imported} 个案例")


async def import_concepts_from_files(data_dir: str):
    """从文件导入法律概念"""
    print("\n[导入 3] 法律概念")

    async with async_session_factory() as session:
        files = [f for f in os.listdir(data_dir) if f.startswith("concept_")]
        imported = 0

        for filename in files:
            filepath = os.path.join(data_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 检查是否已存在
            name = data.get("name", "")
            result = await session.execute(
                select(LegalConcept).where(LegalConcept.name == name)
            )
            if result.scalars().first():
                continue

            # 创建概念
            concept = LegalConcept(
                name=name,
                definition=data.get("definition", ""),
                category=data.get("category", ""),
            )
            session.add(concept)
            imported += 1

        await session.commit()
        print(f"  ✓ 导入 {imported} 个法律概念")


async def main():
    data_dir = "./crawler_data_public"

    if not os.path.exists(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        return

    print("=" * 60)
    print("开始导入法律数据到数据库")
    print("=" * 60)

    await import_laws_from_files(data_dir)
    await import_cases_from_files(data_dir)
    await import_concepts_from_files(data_dir)

    # 统计
    async with async_session_factory() as session:
        laws_count = (await session.execute(select(Law))).scalars().all()
        articles_count = (await session.execute(select(LegalArticle))).scalars().all()
        cases_count = (await session.execute(select(CourtCase))).scalars().all()
        concepts_count = (await session.execute(select(LegalConcept))).scalars().all()

    print("\n" + "=" * 60)
    print("导入完成！数据库统计:")
    print(f"  法律: {len(laws_count)} 部")
    print(f"  法条: {len(articles_count)} 条")
    print(f"  案例: {len(cases_count)} 个")
    print(f"  概念: {len(concepts_count)} 个")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
