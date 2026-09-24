"""
Compliance Risk Management Agent

Provides compliance management capabilities:
1. Regulation tracking — monitor regulatory updates in specific domains
2. Risk assessment — assess compliance risks for business operations
3. Compliance checklist — generate industry-specific checklists
4. Gap analysis — identify gaps between current practices and requirements
5. Compliance report — generate compliance audit reports
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.llm_service import get_raw_llm_service

logger = logging.getLogger(__name__)

# Backtick fence for JSON blocks in prompts (avoids Python 3.11 f-string issues)
_FENCE = "```"


# Industry-specific compliance domains
COMPLIANCE_DOMAINS = {
    "data_privacy": {
        "name": "数据隐私与个人信息保护",
        "regulations": ["《个人信息保护法》", "《数据安全法》", "《网络安全法》", "GDPR"],
        "keywords": ["个人信息", "数据", "隐私", "收集", "存储", "传输", "跨境"],
    },
    "labor": {
        "name": "劳动用工合规",
        "regulations": ["《劳动法》", "《劳动合同法》", "《社会保险法》", "《工伤保险条例》"],
        "keywords": ["劳动合同", "社保", "工资", "工时", "加班", "休假", "解雇"],
    },
    "anti_corruption": {
        "name": "反腐败合规",
        "regulations": ["《反不正当竞争法》", "《刑法》相关条款"],
        "keywords": ["贿赂", "回扣", "利益输送", "关联交易", "商业道德"],
    },
    "ip": {
        "name": "知识产权合规",
        "regulations": ["《著作权法》", "《专利法》", "《商标法》", "《反不正当竞争法》"],
        "keywords": ["著作权", "专利", "商标", "商业秘密", "开源", "许可"],
    },
    "environmental": {
        "name": "环境保护合规",
        "regulations": ["《环境保护法》", "《大气污染防治法》", "《水污染防治法》"],
        "keywords": ["排放", "环评", "污染", "废物", "节能", "碳排放"],
    },
    "financial": {
        "name": "金融合规",
        "regulations": ["《证券法》", "《银行业监督管理法》", "《反洗钱法》"],
        "keywords": ["反洗钱", "KYC", "适当性", "信息披露", "风控"],
    },
    "consumer": {
        "name": "消费者权益保护",
        "regulations": ["《消费者权益保护法》", "《产品质量法》", "《广告法》"],
        "keywords": ["消费者", "产品质量", "广告", "退货", "赔偿", "虚假宣传"],
    },
    "ecommerce": {
        "name": "电子商务合规",
        "regulations": ["《电子商务法》", "《网络交易监督管理办法》"],
        "keywords": ["电商平台", "经营者义务", "评价", "刷单", "搭售"],
    },
}


class ComplianceRiskAgent:
    """Manages compliance risk assessment and monitoring."""

    async def risk_assessment(
        self,
        business_description: str,
        industry: str = "",
        compliance_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Assess compliance risks for a business operation.

        Args:
            business_description: Description of the business/operation.
            industry: Industry sector.
            compliance_domains: Specific compliance domains to check.

        Returns:
            Structured risk assessment with scores and recommendations.
        """
        llm = get_raw_llm_service()

        # Build domain-specific context
        domain_context = ""
        if compliance_domains:
            for domain_id in compliance_domains:
                domain = COMPLIANCE_DOMAINS.get(domain_id)
                if domain:
                    domain_context += f"\n\n### {domain['name']}\n适用法规: {', '.join(domain['regulations'])}"

        prompt = f"""请对以下业务进行合规风险评估：

## 业务描述
{business_description}

## 行业
{industry or '未指定'}

## 重点合规领域
{domain_context or '全面评估'}

请从以下维度进行评估：

1. **合规风险识别**：列出所有潜在合规风险点
2. **风险等级评定**：每个风险点的严重程度和发生概率
3. **法规映射**：每个风险点对应的法律法规
4. **合规差距**：当前做法与法规要求的差距
5. **整改建议**：具体的整改措施和优先级

以JSON格式返回：
{_FENCE}json
{{
    "overall_risk_level": "高/中/低",
    "overall_score": 0-100,
    "risk_areas": [
        {{
            "domain": "合规领域",
            "risks": [
                {{
                    "description": "风险描述",
                    "severity": "高/中/低",
                    "probability": "高/中/低",
                    "regulation": "相关法规",
                    "current_status": "当前状态",
                    "gap": "合规差距",
                    "recommendation": "整改建议",
                    "priority": "紧急/重要/一般",
                    "deadline": "建议整改期限"
                }}
            ]
        }}
    ],
    "positive_compliance": ["已合规项目"],
    "action_plan": ["优先行动计划"],
    "summary": "评估总结"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是企业合规风险管理专家，精通中国法律法规体系和企业合规管理实务。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"summary": content, "overall_score": 50}

        except Exception as exc:
            return {"error": str(exc), "overall_score": 0}

    async def generate_checklist(
        self,
        industry: str,
        compliance_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a compliance checklist for an industry.

        Returns a structured checklist that can be used for
        regular compliance self-assessment.
        """
        llm = get_raw_llm_service()

        # Build domain list
        domains_text = ""
        if compliance_domains:
            for domain_id in compliance_domains:
                domain = COMPLIANCE_DOMAINS.get(domain_id)
                if domain:
                    domains_text += f"\n- {domain['name']}: {', '.join(domain['regulations'])}"
        else:
            domains_text = "所有适用领域"

        prompt = f"""为{industry}行业生成合规检查清单。

适用领域：{domains_text}

请生成结构化的检查清单，包含：
1. 检查项目分类
2. 具体检查项
3. 检查标准
4. 合规依据（法规条款）
5. 检查频率

以JSON格式返回：
{_FENCE}json
{{
    "industry": "{industry}",
    "checklist_categories": [
        {{
            "category": "检查类别",
            "items": [
                {{
                    "item": "检查项目",
                    "standard": "检查标准",
                    "legal_basis": "法律依据",
                    "frequency": "检查频率（每日/每月/每季度/每年）",
                    "responsible": "负责部门",
                    "evidence": "所需证据/记录"
                }}
            ]
        }}
    ],
    "total_items": 0,
    "last_updated": "更新依据"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是企业合规管理专家。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"summary": content}

        except Exception as exc:
            return {"error": str(exc)}

    async def track_regulation_updates(
        self,
        topics: list[str],
        days_back: int = 30,
    ) -> dict[str, Any]:
        """Track recent regulation updates for specified topics.

        Uses web search to find the latest regulatory changes.
        """
        from app.services.web_search import get_web_search_engine

        engine = get_web_search_engine()
        all_results = []

        for topic in topics:
            query = f"{topic} 最新法规 政策变化 规范性文件"
            result = await engine.search(
                query=query,
                num_results=5,
                legal_only=True,
                time_range="month" if days_back <= 30 else "year",
            )
            if result.results:
                all_results.append({
                    "topic": topic,
                    "results": [r.to_dict() for r in result.results],
                    "count": len(result.results),
                })

        # Summarize with LLM
        if all_results:
            llm = get_raw_llm_service()
            results_text = json.dumps(all_results, ensure_ascii=False)[:3000]

            try:
                summary_result = await llm.chat(
                    messages=[
                        {"role": "system", "content": "你是法规动态追踪专家。请总结最新的法规变化。"},
                        {"role": "user", "content": f"以下是搜索结果，请总结重要变化：\n{results_text}"},
                    ],
                    temperature=0.2,
                    max_tokens=2000,
                )
                summary = summary_result.get("content", "")
            except Exception:
                summary = ""

            return {
                "topics": topics,
                "days_back": days_back,
                "results_by_topic": all_results,
                "summary": summary,
                "total_results": sum(r["count"] for r in all_results),
            }

        return {
            "topics": topics,
            "days_back": days_back,
            "results_by_topic": [],
            "summary": "未找到相关法规更新",
            "total_results": 0,
        }

    async def generate_compliance_report(
        self,
        company_name: str,
        assessment_result: dict[str, Any],
        checklist_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a comprehensive compliance audit report.

        Combines risk assessment and checklist results into
        a professional compliance report.
        """
        llm = get_raw_llm_service()

        prompt = f"""请基于以下合规评估结果，生成一份专业的合规审计报告。

公司名称：{company_name}

风险评估结果：
{json.dumps(assessment_result, ensure_ascii=False)[:3000]}

{f'检查清单结果：{json.dumps(checklist_result, ensure_ascii=False)[:2000]}' if checklist_result else ''}

请生成正式合规报告，包含：

1. 报告摘要
2. 评估范围和方法
3. 合规现状概述
4. 风险发现（按严重程度排序）
5. 整改建议（按优先级排序）
6. 合规行动计划
7. 结论

报告要求专业、客观、可操作。"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是合规审计专家，请出具专业的合规审计报告。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            return {
                "company": company_name,
                "report": result.get("content", ""),
                "generated_at": datetime.now().isoformat(),
            }
        except Exception as exc:
            return {"error": str(exc)}


# =============================================================================
# Singleton
# =============================================================================

_agent: ComplianceRiskAgent | None = None


def get_compliance_risk_agent() -> ComplianceRiskAgent:
    global _agent
    if _agent is None:
        _agent = ComplianceRiskAgent()
    return _agent
