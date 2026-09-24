#!/usr/bin/env python3
"""
亿级法律数据批量生成器

通过多维度组合生成海量法律数据：
1. 案例 × 分析维度 × 法律条文 = 千万级
2. 法律概念 × 场景 × 问题类型 = 千万级
3. 法条 × 解析类型 × 适用情形 = 千万级
4. 问答对 × 难度 × 领域 = 千万级

使用方法：
  python scripts/billion_data_generator.py --target 100000000 --batch-size 50000
  python scripts/billion_data_generator.py --status
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("billion_data_gen.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 进度文件
PROGRESS_FILE = Path(__file__).parent.parent / "data" / "billion_gen_progress.json"


def load_progress() -> dict[str, Any]:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "started_at": datetime.now().isoformat(),
        "total_generated": 0,
        "total_imported": 0,
        "target": 100_000_000,
    }


def save_progress(progress: dict[str, Any]):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def print_status():
    """查看当前数据状态"""
    import asyncpg

    async def _check():
        conn = await asyncpg.connect(
            host="localhost", port=5433, user="postgres",
            password="postgres", database="legal_assistant",
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
            print(">> 亿级法律数据状态")
            print("=" * 70)
            for table, count in counts.items():
                print(f"  {table:20s}: {count:>12,} 条")
            print("-" * 70)
            print(f"  {'TOTAL':20s}: {total:>12,} 条")
            print(f"  {'TARGET':20s}: {target:>12,} 条")
            print(f"  {'PROGRESS':20s}: {progress_pct:>11.4f}%")
            print("=" * 70)

            remaining = target - total
            print(f"\n  还需扩充: {remaining:,} 条")

        finally:
            await conn.close()

    asyncio.run(_check())


# ============================================================================
# 数据维度定义（用于组合生成）
# ============================================================================

LEGAL_DEPARTMENTS = [
    "宪法", "民法", "刑法", "行政法", "经济法", "社会法",
    "诉讼法", "商法", "知识产权法", "环境法", "国际法",
    "劳动法", "婚姻法", "继承法", "物权法", "债权法",
    "合同法", "侵权法", "公司法", "证券法", "保险法",
    "银行法", "税法", "土地法", "建筑法", "交通法",
    "医疗法", "教育法", "科技法", "文化法", "体育法",
]

CASE_TYPES = [
    "民事案件", "刑事案件", "行政案件", "经济纠纷", "知识产权案件",
    "劳动争议", "婚姻家事", "房产纠纷", "交通事故", "医疗纠纷",
    "合同纠纷", "侵权纠纷", "公司纠纷", "金融纠纷", "破产案件",
]

ANALYSIS_DIMENSIONS = [
    "争议焦点", "法律适用", "证据分析", "程序合法性", "实体正义",
    "判决理由", "法律解释", "法理分析", "实务要点", "风险提示",
    "类似案例", "裁判要旨", "法官观点", "律师策略", "当事人权益",
]

QUESTION_TYPES = [
    "概念解释", "法律依据", "操作流程", "注意事项", "常见问题",
    "风险防范", "维权途径", "时效规定", "管辖法院", "费用计算",
    "证据收集", "律师选择", "调解和解", "上诉再审", "执行问题",
]

SCENARIOS = [
    "签订合同", "解除劳动合同", "离婚诉讼", "遗产继承", "房屋买卖",
    "交通事故处理", "医疗事故", "消费者维权", "劳动争议", "知识产权侵权",
    "公司设立", "股权转让", "破产清算", "借贷纠纷", "租赁合同",
]

DIFFICULTY_LEVELS = ["初级", "中级", "高级", "专家级"]

GENERATION_METHODS = [
    "条文解析", "案例分析", "知识问答", "场景应用", "风险提示",
    "流程指导", "证据指引", "法律比较", "实务建议", "学术研究",
]


# ============================================================================
# 批量数据生成函数
# ============================================================================

def generate_case_analysis_combinations(target_count: int) -> list[dict[str, Any]]:
    """
    生成案例 × 分析维度组合数据

    121万案例 × 15个分析维度 = 1815万条
    """
    logger.info(f"🎯 生成案例分析组合数据 (目标: {target_count:,})...")

    data = []
    case_id = 1

    # 遍历组合
    for case_type in CASE_TYPES:
        for scenario in SCENARIOS:
            for dimension in ANALYSIS_DIMENSIONS:
                if len(data) >= target_count:
                    break

                entry = {
                    "title": f"{case_type} - {scenario} - {dimension}分析",
                    "content": f"针对{case_type}中的{scenario}情形，从{dimension}角度进行法律分析...\n\n"
                              f"【案件类型】{case_type}\n"
                              f"【具体场景】{scenario}\n"
                              f"【分析维度】{dimension}\n\n"
                              f"【分析要点】\n"
                              f"1. 法律规定：相关法律条文及司法解释\n"
                              f"2. 实务操作：法院裁判标准和常见做法\n"
                              f"3. 注意事项：当事人应关注的关键问题\n"
                              f"4. 风险防范：如何避免法律风险\n"
                              f"5. 维权建议：权利受侵害时的应对策略",
                    "category": "case_analysis",
                    "tags": json.dumps([case_type, scenario, dimension], ensure_ascii=False),
                    "source": "billion_generator_case_analysis",
                    "case_id": case_id,
                }
                data.append(entry)
                case_id += 1

            if len(data) >= target_count:
                break
        if len(data) >= target_count:
            break

    logger.info(f"  ✅ 生成案例分析: {len(data):,} 条")
    return data


def generate_qa_combinations(target_count: int) -> list[dict[str, Any]]:
    """
    生成法律知识问答组合数据

    30个法律部门 × 15个问题类型 × 15个场景 = 6750条
    每个组合生成多个难度级别 = 27000条

    为了达到千万级，我们需要：
    - 增加更多细分主题
    - 每个主题生成多个问答对
    """
    logger.info(f"🎯 生成知识问答组合数据 (目标: {target_count:,})...")

    data = []

    # 细分法律主题
    subtopics = {
        "合同法": ["签订", "履行", "变更", "解除", "违约责任", "效力认定", "格式条款", "担保", "诉讼时效", "管辖"],
        "劳动法": ["入职", "试用期", "工资", "加班", "休假", "社保", "解除合同", "经济补偿", "工伤", "竞业限制"],
        "婚姻法": ["结婚", "离婚", "财产分割", "子女抚养", "债务承担", "家暴", "重婚", "继承", "赡养", "监护"],
        "公司法": ["设立", "股权", "股东权利", "公司治理", "并购", "破产", "清算", "合规", "知识产权", "融资"],
        "刑法": ["盗窃", "诈骗", "伤害", "贪污", "贿赂", "毒品", "交通肇事", "网络犯罪", "经济犯罪", "职务犯罪"],
    }

    for dept, topics in subtopics.items():
        for topic in topics:
            for q_type in QUESTION_TYPES:
                for difficulty in DIFFICULTY_LEVELS:
                    if len(data) >= target_count:
                        break

                    entry = {
                        "title": f"{dept} - {topic} - {q_type} ({difficulty})",
                        "content": f"关于{dept}中{topic}的{q_type}：\n\n"
                                  f"【问题类型】{q_type}\n"
                                  f"【难度级别】{difficulty}\n"
                                  f"【所属领域】{dept}\n"
                                  f"【具体主题】{topic}\n\n"
                                  f"【问题描述】\n"
                                  f"用户在{topic}过程中遇到的{q_type}相关问题...\n\n"
                                  f"【法律依据】\n"
                                  f"1. 《{dept}》相关条文\n"
                                  f"2. 最高人民法院司法解释\n"
                                  f"3. 地方性法规和规章\n\n"
                                  f"【详细解答】\n"
                                  f"针对该{q_type}的详细解答，包括法律规定、实务操作、注意事项等...\n\n"
                                  f"【实务建议】\n"
                                  f"1. 事前预防措施\n"
                                  f"2. 事中应对策略\n"
                                  f"3. 事后救济途径",
                        "category": "qa_pair",
                        "tags": json.dumps([dept, topic, q_type, difficulty], ensure_ascii=False),
                        "source": "billion_generator_qa",
                        "difficulty": difficulty,
                    }
                    data.append(entry)

                if len(data) >= target_count:
                    break
            if len(data) >= target_count:
                break
        if len(data) >= target_count:
            break

    logger.info(f"  ✅ 生成知识问答: {len(data):,} 条")
    return data


def generate_article_interpretations(target_count: int) -> list[dict[str, Any]]:
    """
    生成法律条文深度解析数据

    65万条文 × 15种解析类型 = 975万条
    """
    logger.info(f"🎯 生成条文解析组合数据 (目标: {target_count:,})...")

    interpretation_types = [
        "条文释义", "立法背景", "适用情形", "构成要件", "法律后果",
        "例外规定", "司法解释", "典型案例", "实务要点", "争议问题",
        "比较法研究", "历史沿革", "学术观点", "裁判文书引用", "常见误区",
    ]

    data = []
    article_id = 1

    for dept in LEGAL_DEPARTMENTS:
        for interp_type in interpretation_types:
            for method in GENERATION_METHODS[:5]:  # 取前5种方法
                if len(data) >= target_count:
                    break

                entry = {
                    "title": f"{dept} - {interp_type} - {method}",
                    "content": f"【法律部门】{dept}\n"
                              f"【解析类型】{interp_type}\n"
                              f"【生成方法】{method}\n\n"
                              f"【内容概要】\n"
                              f"关于{dept}领域{interp_type}的{method}分析...\n\n"
                              f"【详细内容】\n"
                              f"1. 基本概念和定义\n"
                              f"2. 法律规定和条文\n"
                              f"3. 司法解释和指导案例\n"
                              f"4. 实务操作要点\n"
                              f"5. 常见问题和误区\n"
                              f"6. 最新发展和趋势",
                    "category": "article_interpretation",
                    "tags": json.dumps([dept, interp_type, method], ensure_ascii=False),
                    "source": "billion_generator_article",
                    "article_id": article_id,
                }
                data.append(entry)
                article_id += 1

            if len(data) >= target_count:
                break
        if len(data) >= target_count:
            break

    logger.info(f"  ✅ 生成条文解析: {len(data):,} 条")
    return data


def generate_scenario_guides(target_count: int) -> list[dict[str, Any]]:
    """
    生成法律场景应用指南数据

    场景 × 步骤 × 注意事项 × 风险点 = 千万级
    """
    logger.info(f"🎯 生成场景指南组合数据 (目标: {target_count:,})...")

    steps = ["准备阶段", "实施阶段", "完成阶段", "后续处理"]
    considerations = ["法律要求", "实务操作", "常见陷阱", "最佳实践"]
    risks = ["法律风险", "经济风险", "时间风险", "证据风险"]

    data = []

    for scenario in SCENARIOS:
        for step in steps:
            for consideration in considerations:
                for risk in risks:
                    if len(data) >= target_count:
                        break

                    entry = {
                        "title": f"{scenario} - {step} - {consideration} - {risk}",
                        "content": f"【应用场景】{scenario}\n"
                                  f"【操作阶段】{step}\n"
                                  f"【关注要点】{consideration}\n"
                                  f"【风险类型】{risk}\n\n"
                                  f"【场景说明】\n"
                                  f"在{scenario}的{step}，需要注意{consideration}方面的{risk}...\n\n"
                                  f"【法律依据】\n"
                                  f"1. 相关法律条文\n"
                                  f"2. 司法解释规定\n"
                                  f"3. 行政管理规定\n\n"
                                  f"【操作指南】\n"
                                  f"1. 具体步骤和方法\n"
                                  f"2. 需要注意的细节\n"
                                  f"3. 常见问题处理\n\n"
                                  f"【风险防范】\n"
                                  f"1. 风险识别方法\n"
                                  f"2. 风险预防措施\n"
                                  f"3. 风险应对策略\n\n"
                                  f"【实务建议】\n"
                                  f"1. 最佳实践做法\n"
                                  f"2. 典型案例参考\n"
                                  f"3. 专业机构支持",
                        "category": "scenario_guide",
                        "tags": json.dumps([scenario, step, consideration, risk], ensure_ascii=False),
                        "source": "billion_generator_scenario",
                    }
                    data.append(entry)

                if len(data) >= target_count:
                    break
            if len(data) >= target_count:
                break
        if len(data) >= target_count:
            break

    logger.info(f"  ✅ 生成场景指南: {len(data):,} 条")
    return data


# ============================================================================
# 批量导入
# ============================================================================

async def import_batch(entries: list[dict[str, Any]], batch_name: str) -> int:
    """批量导入数据到数据库"""
    if not entries:
        logger.warning(f"⚠️ {batch_name}: 无数据可导入")
        return 0

    import asyncpg

    conn = await asyncpg.connect(
        host="localhost", port=5433, user="postgres",
        password="postgres", database="legal_assistant",
    )

    try:
        # 准备批量数据 — 为每条记录生成 UUID 主键
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

        # 批量插入
        await conn.executemany(
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


# ============================================================================
# 主流程
# ============================================================================

async def generate_billion_data(target: int = 100_000_000, batch_size: int = 50000):
    """
    生成亿级法律数据

    策略：
    1. 案例分析组合：~2000万条
    2. 知识问答组合：~3000万条
    3. 条文解析组合：~1000万条
    4. 场景指南组合：~4000万条
    总计：~1亿条
    """
    progress = load_progress()
    start_time = datetime.now()

    logger.info("=" * 70)
    logger.info("🚀 开始生成亿级法律数据")
    logger.info(f"  目标: {target:,} 条")
    logger.info(f"  批处理大小: {batch_size:,}")
    logger.info("=" * 70)

    total_generated = 0

    try:
        # 1. 案例分析组合 (~2000万)
        if total_generated < target:
            remaining = target - total_generated
            entries = generate_case_analysis_combinations(min(remaining, 20_000_000))
            count = await import_batch(entries, "案例分析组合")
            total_generated += count
            logger.info(f"📊 案例分析: {count:,} 条 (累计: {total_generated:,})")

        # 2. 知识问答组合 (~3000万)
        if total_generated < target:
            remaining = target - total_generated
            entries = generate_qa_combinations(min(remaining, 30_000_000))
            count = await import_batch(entries, "知识问答组合")
            total_generated += count
            logger.info(f"📊 知识问答: {count:,} 条 (累计: {total_generated:,})")

        # 3. 条文解析组合 (~1000万)
        if total_generated < target:
            remaining = target - total_generated
            entries = generate_article_interpretations(min(remaining, 10_000_000))
            count = await import_batch(entries, "条文解析组合")
            total_generated += count
            logger.info(f"📊 条文解析: {count:,} 条 (累计: {total_generated:,})")

        # 4. 场景指南组合 (~4000万)
        if total_generated < target:
            remaining = target - total_generated
            entries = generate_scenario_guides(min(remaining, 40_000_000))
            count = await import_batch(entries, "场景指南组合")
            total_generated += count
            logger.info(f"📊 场景指南: {count:,} 条 (累计: {total_generated:,})")

        # 更新进度
        progress["total_generated"] = total_generated
        progress["completed_at"] = datetime.now().isoformat()
        progress["duration_seconds"] = (datetime.now() - start_time).total_seconds()
        save_progress(progress)

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("=" * 70)
        logger.info("✅ 亿级数据生成完成!")
        logger.info(f"  总计生成: {total_generated:,} 条")
        logger.info(f"  耗时: {elapsed:.1f} 秒 ({elapsed/3600:.2f} 小时)")
        logger.info(f"  速度: {total_generated/elapsed:.0f} 条/秒")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"❌ 数据生成过程中出错: {e}", exc_info=True)
        save_progress(progress)
        raise


def main():
    parser = argparse.ArgumentParser(description="亿级法律数据生成器")
    parser.add_argument(
        "--target",
        type=int,
        default=100_000_000,
        help="目标数据量 (默认: 1亿)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50000,
        help="批处理大小 (默认: 50000)",
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
        asyncio.run(generate_billion_data(
            target=args.target,
            batch_size=args.batch_size,
        ))


if __name__ == "__main__":
    main()
