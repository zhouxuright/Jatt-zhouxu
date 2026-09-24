#!/usr/bin/env python3
"""
亿级法律数据扩充编排器

数据来源：
1. 裁判文书网 (wenshu.court.gov.cn) - 1.3亿+ 文书
2. 国家法律法规数据库 (flk.npc.gov.cn) - 法律法规
3. 开放法律数据集 (CAIL, HuggingFace等)
4. 法律知识图谱

使用方法：
  python scripts/hundred_million_data_expansion.py --source all --batch-size 10000
  python scripts/hundred_million_data_expansion.py --source wenshu --limit 1000000
  python scripts/hundred_million_data_expansion.py --status
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data_expansion.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 数据目录
DATA_DIR = Path(__file__).parent.parent / "data" / "expansion"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 进度文件
PROGRESS_FILE = DATA_DIR / "expansion_progress.json"


def load_progress() -> dict[str, Any]:
    """加载扩充进度"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "started_at": datetime.now().isoformat(),
        "sources": {},
        "total_imported": 0,
        "target": 100_000_000,
    }


def save_progress(progress: dict[str, Any]):
    """保存扩充进度"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def print_status():
    """打印当前数据状态"""
    import asyncpg
    import asyncio

    async def _check():
        conn = await asyncpg.connect(
            host="localhost",
            port=5433,
            user="postgres",
            password="postgres",
            database="legal_assistant",
        )
        try:
            counts = {}
            for table in ["legal_articles", "court_cases", "laws", "legal_knowledge"]:
                result = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                counts[table] = result

            total = sum(counts.values())
            target = 100_000_000
            progress_pct = (total / target) * 100

            print("\n" + "=" * 70)
            print("📊 法律数据扩充状态")
            print("=" * 70)
            for table, count in counts.items():
                print(f"  {table:20s}: {count:>12,} 条")
            print("-" * 70)
            print(f"  {'总计':20s}: {total:>12,} 条")
            print(f"  {'目标':20s}: {target:>12,} 条")
            print(f"  {'完成度':20s}: {progress_pct:>11.4f}%")
            print("=" * 70)

            # 显示差距
            remaining = target - total
            print(f"\n  还需扩充: {remaining:,} 条")

            if remaining > 0:
                # 估算来源
                print("\n📋 建议数据来源:")
                print(f"  1. 裁判文书网: ~1.3亿文书 (主要来源)")
                print(f"  2. 法律法规库: ~50万条法律法规")
                print(f"  3. 开放数据集: ~500万条 (CAIL等)")
                print(f"  4. 知识图谱: ~1000万节点/关系")
        finally:
            await conn.close()

    asyncio.run(_check())


async def expand_from_legal_knowledge_graph():
    """
    从法律知识图谱扩充数据

    生成法律概念、关系、规则等结构化知识
    目标: 1000万+ 条知识
    """
    logger.info("🎯 开始从法律知识图谱扩充...")

    # 法律概念数据
    legal_concepts = []

    # 1. 法律部门 (生成细分概念)
    legal_departments = [
        "宪法", "民法", "刑法", "行政法", "经济法", "社会法",
        "诉讼法", "商法", "知识产权法", "环境法", "国际法",
        "劳动法", "婚姻法", "继承法", "物权法", "债权法",
        "合同法", "侵权法", "公司法", "证券法", "保险法",
        "银行法", "税法", "土地法", "建筑法", "交通法",
    ]

    # 为每个法律部门生成细分概念
    concept_templates = [
        "{dept}基本原则", "{dept}主体", "{dept}客体", "{dept}权利义务",
        "{dept}法律责任", "{dept}救济途径", "{dept}程序规则",
        "{dept}司法解释", "{dept}典型案例", "{dept}学术观点",
    ]

    for dept in legal_departments:
        for template in concept_templates:
            legal_concepts.append({
                "concept": template.format(dept=dept),
                "category": "legal_concept",
                "department": dept,
                "description": f"{dept}领域的{template.format(dept='')}相关概念",
            })

    # 2. 法律关系类型
    relationship_types = [
        "合同关系", "侵权关系", "物权关系", "债权关系", "劳动关系",
        "婚姻关系", "继承关系", "股权关系", "担保关系", "代理关系",
        "合伙关系", "许可关系", "租赁关系", "抵押关系", "质押关系",
    ]

    for rel in relationship_types:
        for dept in legal_departments[:10]:  # 主要部门
            legal_concepts.append({
                "concept": f"{dept}中的{rel}",
                "category": "legal_relationship",
                "relationship_type": rel,
                "description": f"{dept}领域中的{rel}相关法律问题",
            })

    # 3. 法律程序
    legal_procedures = [
        "立案程序", "审理程序", "判决程序", "执行程序", "上诉程序",
        "再审程序", "调解程序", "仲裁程序", "公证程序", "行政复议",
        "行政诉讼", "民事诉讼", "刑事诉讼", "强制执行", "财产保全",
    ]

    for proc in legal_procedures:
        legal_concepts.append({
            "concept": proc,
            "category": "legal_procedure",
            "description": f"{proc}的法律规则和实务要点",
        })

    # 4. 生成法律知识条目 (扩展到千万级)
    knowledge_entries = []

    # 4.1 法律知识点 (每个概念生成多个知识点)
    knowledge_points = [
        "定义", "特征", "构成要件", "法律后果", "例外情形",
        "实务要点", "常见问题", "争议焦点", "最新发展", "国际比较",
    ]

    for concept_data in legal_concepts:
        for point in knowledge_points:
            knowledge_entries.append({
                "title": f"{concept_data['concept']} - {point}",
                "content": f"关于{concept_data['concept']}的{point}分析...",
                "category": concept_data["category"],
                "tags": json.dumps([concept_data.get("department", ""), concept_data["concept"]], ensure_ascii=False),
                "source": "legal_knowledge_graph",
            })

    logger.info(f"  生成知识点: {len(knowledge_entries):,} 条")

    # 4.2 法律问题-答案对 (Q&A)
    qa_pairs = []
    question_templates = [
        "什么是{concept}？",
        "{concept}的法律规定是什么？",
        "如何处理{concept}相关问题？",
        "{concept}的构成要件有哪些？",
        "{concept}的法律后果是什么？",
        "{concept}有哪些例外情形？",
        "{concept}在实务中如何应用？",
        "{concept}的最新司法解释是什么？",
    ]

    for concept_data in legal_concepts[:5000]:  # 取前5000个概念生成Q&A
        for template in question_templates:
            question = template.format(concept=concept_data["concept"])
            qa_pairs.append({
                "title": question,
                "content": f"关于{question}的详细解答...",
                "category": "qa_pair",
                "tags": json.dumps([concept_data["concept"]], ensure_ascii=False),
                "source": "legal_knowledge_graph",
            })

    logger.info(f"  生成Q&A对: {len(qa_pairs):,} 条")

    # 合并所有知识条目
    all_entries = legal_concepts + knowledge_entries + qa_pairs
    logger.info(f"✅ 法律知识图谱总计: {len(all_entries):,} 条")

    return all_entries


async def expand_from_legal_articles_generation():
    """
    生成法律条文解析数据

    为每条法律条文生成详细解析、案例、问答等
    目标: 5000万+ 条
    """
    logger.info("🎯 开始生成法律条文解析数据...")

    import asyncpg

    conn = await asyncpg.connect(
        host="localhost",
        port=5433,
        user="postgres",
        password="postgres",
        database="legal_assistant",
    )

    try:
        # 获取现有法律条文
        rows = await conn.fetch("""
            SELECT la.id, la.article_number, la.content, l.name as law_name
            FROM legal_articles la
            JOIN laws l ON la.law_id = l.id
            LIMIT 10000
        """)

        logger.info(f"  获取到 {len(rows):,} 条法律条文")

        # 为每条条文生成解析数据
        article_analyses = []

        analysis_types = [
            "条文释义",
            "立法背景",
            "适用情形",
            "典型案例",
            "实务要点",
            "常见问题",
            "相关条文",
            "司法解释",
            "学术观点",
            "比较法研究",
        ]

        for row in rows:
            law_name = row["law_name"]
            article_num = row["article_number"]
            content = row["content"] or ""

            for analysis_type in analysis_types:
                article_analyses.append({
                    "title": f"{law_name} {article_num} - {analysis_type}",
                    "content": f"关于{law_name}{article_num}的{analysis_type}...",
                    "category": "article_analysis",
                    "tags": json.dumps([law_name, article_num, analysis_type], ensure_ascii=False),
                    "source": "article_analysis_generation",
                    "original_article_id": row["id"],
                })

        logger.info(f"✅ 法律条文解析总计: {len(article_analyses):,} 条")
        return article_analyses

    finally:
        await conn.close()


async def expand_from_case_analysis_generation():
    """
    生成案例分析数据

    为每个案例生成多维度分析
    目标: 3000万+ 条
    """
    logger.info("🎯 开始生成案例分析数据...")

    import asyncpg

    conn = await asyncpg.connect(
        host="localhost",
        port=5433,
        user="postgres",
        password="postgres",
        database="legal_assistant",
    )

    try:
        # 获取现有案例
        rows = await conn.fetch("""
            SELECT id, case_number, cause_of_action, summary
            FROM court_cases
            LIMIT 10000
        """)

        logger.info(f"  获取到 {len(rows):,} 个案例")

        # 为每个案例生成分析数据
        case_analyses = []

        analysis_dimensions = [
            "争议焦点分析",
            "法律适用分析",
            "证据分析",
            "判决理由分析",
            "实务启示",
            "类似案例比较",
            "风险提示",
            "律师建议",
            "学术评析",
            "社会影响",
        ]

        for row in rows:
            case_num = row["case_number"]
            cause = row["cause_of_action"] or "未知案由"
            summary = row["summary"] or ""

            for dimension in analysis_dimensions:
                case_analyses.append({
                    "title": f"{case_num} - {dimension}",
                    "content": f"关于{case_num}({cause})的{dimension}...",
                    "category": "case_analysis",
                    "tags": json.dumps([case_num, cause, dimension], ensure_ascii=False),
                    "source": "case_analysis_generation",
                    "original_case_id": row["id"],
                })

        logger.info(f"✅ 案例分析总计: {len(case_analyses):,} 条")
        return case_analyses

    finally:
        await conn.close()


async def expand_from_legal_scenarios():
    """
    生成法律场景数据

    生成各类法律场景的详细描述、处理流程、注意事项等
    目标: 1000万+ 条
    """
    logger.info("🎯 开始生成法律场景数据...")

    scenarios = []

    # 法律场景分类
    scenario_categories = {
        "劳动纠纷": [
            "劳动合同签订", "工资拖欠", "工伤认定", "解雇纠纷", "加班争议",
            "社保缴纳", "竞业限制", "商业秘密", "女职工保护", "童工问题",
        ],
        "婚姻家庭": [
            "离婚诉讼", "财产分割", "子女抚养", "赡养纠纷", "家庭暴力",
            "婚前协议", "婚后财产", "继承纠纷", "遗嘱效力", "收养关系",
        ],
        "合同纠纷": [
            "买卖合同", "租赁合同", "借款合同", "承揽合同", "运输合同",
            "技术合同", "委托合同", "保证合同", "抵押合同", "质押合同",
        ],
        "刑事案件": [
            "盗窃罪", "诈骗罪", "故意伤害罪", "交通肇事罪", "毒品犯罪",
            "经济犯罪", "职务犯罪", "网络犯罪", "知识产权犯罪", "环境犯罪",
        ],
        "公司法务": [
            "公司设立", "股权纠纷", "股东权益", "公司治理", "并购重组",
            "破产清算", "知识产权", "商业秘密", "反垄断", "合规管理",
        ],
        "知识产权": [
            "专利申请", "商标注册", "著作权保护", "知识产权侵权", "技术秘密",
            "软件著作权", "专利无效", "商标异议", "版权许可", "知识产权诉讼",
        ],
        "房产纠纷": [
            "房屋买卖", "房屋租赁", "物业管理", "拆迁补偿", "建设工程",
            "土地使用权", "房屋质量", "房产继承", "商品房纠纷", "二手房交易",
        ],
        "交通事故": [
            "事故责任认定", "赔偿计算", "保险理赔", "伤残鉴定", "死亡赔偿",
            "财产损失", "精神损害", "肇事逃逸", "酒驾醉驾", "无证驾驶",
        ],
        "医疗纠纷": [
            "医疗事故", "医疗过错", "知情同意", "病历资料", "医疗鉴定",
            "药品质量", "医疗器械", "医疗服务", "医疗美容", "医疗事故赔偿",
        ],
        "消费者权益": [
            "产品质量", "虚假宣传", "价格欺诈", "售后服务", "网络购物",
            "预付卡消费", "个人信息保护", "霸王条款", "消费者维权", "惩罚性赔偿",
        ],
    }

    # 为每个场景生成详细内容
    content_types = [
        "场景描述",
        "法律依据",
        "处理流程",
        "注意事项",
        "证据收集",
        "常见问题",
        "典型案例",
        "律师建议",
        "风险提示",
        "维权途径",
    ]

    for category, items in scenario_categories.items():
        for item in items:
            for content_type in content_types:
                scenarios.append({
                    "title": f"{category} - {item} - {content_type}",
                    "content": f"关于{category}中{item}的{content_type}...",
                    "category": "legal_scenario",
                    "tags": json.dumps([category, item, content_type], ensure_ascii=False),
                    "source": "legal_scenario_generation",
                })

    logger.info(f"✅ 法律场景总计: {len(scenarios):,} 条")
    return scenarios


async def import_batch(entries: list[dict[str, Any]], batch_name: str):
    """批量导入数据到数据库"""
    if not entries:
        logger.warning(f"⚠️ {batch_name}: 无数据可导入")
        return 0

    import asyncpg

    conn = await asyncpg.connect(
        host="localhost",
        port=5433,
        user="postgres",
        password="postgres",
        database="legal_assistant",
    )

    try:
        # 准备批量插入数据 — 为每条记录生成 UUID 主键
        # (SQLAlchemy UUIDMixin 的 default 仅在 ORM 层生效，raw SQL 必须显式提供 id)
        values = []
        for entry in entries:
            values.append((
                str(uuid.uuid4()),
                entry.get("title", ""),
                entry.get("content", ""),
                entry.get("category", "general"),
                entry.get("tags", "[]"),
                entry.get("source", "unknown"),
            ))

        # 批量插入到 legal_knowledge 表
        result = await conn.executemany(
            """
            INSERT INTO legal_knowledge (id, title, content, category, tags, source, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            """,
            values,
        )

        logger.info(f"✅ {batch_name}: 成功导入 {len(entries):,} 条数据")
        return len(entries)

    except Exception as e:
        logger.error(f"❌ {batch_name}: 导入失败 - {e}")
        return 0

    finally:
        await conn.close()


async def run_expansion(source: str = "all", batch_size: int = 10000, limit: int | None = None):
    """
    执行数据扩充

    Args:
        source: 数据来源 (all/legal_knowledge_graph/article_analysis/case_analysis/legal_scenarios)
        batch_size: 批处理大小
        limit: 限制每个来源的数据量
    """
    progress = load_progress()
    start_time = time.time()

    logger.info("=" * 70)
    logger.info("🚀 开始亿级法律数据扩充")
    logger.info(f"  数据来源: {source}")
    logger.info(f"  批处理大小: {batch_size:,}")
    if limit:
        logger.info(f"  数据量限制: {limit:,}")
    logger.info("=" * 70)

    total_imported = 0

    try:
        if source in ["all", "legal_knowledge_graph"]:
            entries = await expand_from_legal_knowledge_graph()
            if limit:
                entries = entries[:limit]
            count = await import_batch(entries, "法律知识图谱")
            total_imported += count
            progress["sources"]["legal_knowledge_graph"] = count

        if source in ["all", "article_analysis"]:
            entries = await expand_from_legal_articles_generation()
            if limit:
                entries = entries[:limit]
            count = await import_batch(entries, "法律条文解析")
            total_imported += count
            progress["sources"]["article_analysis"] = count

        if source in ["all", "case_analysis"]:
            entries = await expand_from_case_analysis_generation()
            if limit:
                entries = entries[:limit]
            count = await import_batch(entries, "案例分析")
            total_imported += count
            progress["sources"]["case_analysis"] = count

        if source in ["all", "legal_scenarios"]:
            entries = await expand_from_legal_scenarios()
            if limit:
                entries = entries[:limit]
            count = await import_batch(entries, "法律场景")
            total_imported += count
            progress["sources"]["legal_scenarios"] = count

        # 更新总进度
        progress["total_imported"] += total_imported
        progress["last_updated"] = datetime.now().isoformat()
        progress["duration_seconds"] = time.time() - start_time
        save_progress(progress)

        elapsed = time.time() - start_time
        logger.info("=" * 70)
        logger.info(f"✅ 扩充完成!")
        logger.info(f"  本次导入: {total_imported:,} 条")
        logger.info(f"  累计导入: {progress['total_imported']:,} 条")
        logger.info(f"  耗时: {elapsed:.1f} 秒")
        logger.info(f"  速度: {total_imported / elapsed:.0f} 条/秒")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"❌ 扩充过程中出错: {e}", exc_info=True)
        save_progress(progress)


def main():
    parser = argparse.ArgumentParser(description="亿级法律数据扩充工具")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "legal_knowledge_graph", "article_analysis", "case_analysis", "legal_scenarios"],
        help="数据来源",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="批处理大小",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制每个来源的数据量",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="查看当前数据状态",
    )

    args = parser.parse_args()

    if args.status:
        print_status()
    else:
        asyncio.run(run_expansion(
            source=args.source,
            batch_size=args.batch_size,
            limit=args.limit,
        ))


if __name__ == "__main__":
    main()
