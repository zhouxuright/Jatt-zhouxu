"""
MCP (Model Context Protocol) Tool Calling Framework

Provides a pluggable tool registry for external tool integration.
Agents can discover and invoke tools registered in the MCP registry,
enabling the legal AI system to call external services like:
- Court case lookup APIs
- Enterprise registration lookup (天眼查/企查查)
- Government regulatory databases
- Knowledge base search
- Custom user-defined tools

Architecture:
    ToolRegistry (singleton)
      └── MCPTool (base class)
            ├── WenshuSearchTool
            ├── EnterpriseLookupTool
            ├── GovernmentRegulationTool
            ├── KnowledgeBaseSearchTool
            └── CurrencyConverterTool
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Result & Schema Types
# =============================================================================

@dataclass
class ToolResult:
    """Result returned by an MCP tool execution."""

    success: bool
    data: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "elapsed_seconds": self.elapsed_seconds,
            "metadata": self.metadata,
        }


@dataclass
class ToolParameter:
    """Schema definition for a single tool parameter."""

    name: str
    type: str  # "string" | "integer" | "number" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    default: Any = None
    enum: list[str] | None = None


# =============================================================================
# Base MCP Tool
# =============================================================================

class MCPTool(ABC):
    """Abstract base class for all MCP tools.

    Every tool must define:
    - name: unique identifier
    - description: what the tool does
    - parameters: input parameter schema
    - execute(): the actual tool logic
    """

    name: str = ""
    description: str = ""
    category: str = "general"  # legal_research / enterprise / government / utility
    version: str = "1.0.0"

    @abstractmethod
    def get_parameters(self) -> list[ToolParameter]:
        """Return the parameter schema for this tool."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given parameters."""
        ...

    def get_schema(self) -> dict[str, Any]:
        """Return the JSON Schema-like description of this tool."""
        properties = {}
        required = []
        for param in self.get_parameters():
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                prop["default"] = param.default
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


# =============================================================================
# Built-in Legal Research Tools
# =============================================================================

class WenshuSearchTool(MCPTool):
    """Search China Judgments Online (裁判文书网) for court decisions."""

    name = "wenshu_search"
    description = "在中国裁判文书网中搜索裁判文书，支持按案号、案由、法院、当事人等条件检索"
    category = "legal_research"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("keyword", "string", "搜索关键词（案由、当事人名称等）"),
            ToolParameter("case_type", "string", "案件类型", required=False,
                          enum=["民事", "刑事", "行政", "执行", "赔偿",
                                "劳动争议", "婚姻家庭", "知识产权"]),
            ToolParameter("court_name", "string", "法院名称", required=False),
            ToolParameter("year_from", "integer", "起始年份", required=False),
            ToolParameter("year_to", "integer", "截止年份", required=False),
            ToolParameter("page_size", "integer", "返回条数", required=False, default=10),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute wenshu search via local PostgreSQL database (cached mirror)."""
        start = time.time()
        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text

            keyword = kwargs.get("keyword", "")
            case_type = kwargs.get("case_type", "")
            court_name = kwargs.get("court_name", "")
            year_from = kwargs.get("year_from")
            year_to = kwargs.get("year_to")
            page_size = min(kwargs.get("page_size", 10), 50)

            # Build SQL query against court_cases table
            conditions = []
            params: dict[str, Any] = {}

            if keyword:
                conditions.append(
                    "(title ILIKE :kw OR cause_of_action ILIKE :kw OR summary ILIKE :kw)"
                )
                params["kw"] = f"%{keyword}%"
            if case_type:
                # The corpus labels civil sub-domains as their own case_type
                # (劳动争议 / 婚姻家庭 / 知识产权), so an exact match on "民事"
                # silently hides them — the single most common civil query type
                # was invisible to the tool. Treat 民事 as the superset.
                if case_type == "民事":
                    conditions.append("case_type = ANY(:case_types)")
                    params["case_types"] = ["民事", "劳动争议", "婚姻家庭", "知识产权"]
                else:
                    conditions.append("case_type = :case_type")
                    params["case_type"] = case_type
            if court_name:
                conditions.append("court_name ILIKE :court")
                params["court"] = f"%{court_name}%"
            if year_from:
                conditions.append("EXTRACT(YEAR FROM decision_date) >= :year_from")
                params["year_from"] = year_from
            if year_to:
                conditions.append("EXTRACT(YEAR FROM decision_date) <= :year_to")
                params["year_to"] = year_to

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = text(f"""
                SELECT id, case_number, title, court_name, case_type,
                       cause_of_action, decision_date, summary, judgment_result
                FROM court_cases
                WHERE {where_clause}
                ORDER BY decision_date DESC NULLS LAST
                LIMIT :limit
            """)
            params["limit"] = page_size

            async with async_session_factory() as session:
                result = await session.execute(sql, params)
                rows = result.fetchall()

            cases = []
            for row in rows:
                cases.append({
                    "id": str(row[0]),
                    "case_number": row[1],
                    "title": row[2],
                    "court_name": row[3],
                    "case_type": row[4],
                    "cause_of_action": row[5],
                    "decision_date": str(row[6]) if row[6] else None,
                    "summary": (row[7] or "")[:500],
                    "judgment_result": row[8],
                })

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"cases": cases, "total": len(cases)},
                elapsed_seconds=elapsed,
                metadata={"source": "court_cases_db", "keyword": keyword},
            )
        except Exception as exc:
            elapsed = time.time() - start
            logger.error("WenshuSearchTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)


class LawArticleSearchTool(MCPTool):
    """Search legal articles from the laws database."""

    name = "law_article_search"
    description = "在法律法规数据库中搜索法条，支持按法律名称、条文内容、法律类别等条件检索"
    category = "legal_research"
    version = "1.1.0"

    # Article numbers are stored with Chinese numerals ("第四十七条"), but callers
    # and users write them either way ("第47条"). Matching on raw equality means
    # the Arabic form silently returns zero rows, so both forms are normalised to
    # the stored Chinese form before querying.
    _CN_DIGITS = "零一二三四五六七八九"

    @classmethod
    def _int_to_cn(cls, n: int) -> str:
        """Render an integer as a Chinese numeral the way statutes are numbered.

        Covers 1-9999, which spans the whole corpus (the Civil Code runs to
        article 1260, stored as 第一千二百六十条). Note the thousands form is
        positional with 零 fillers — "第一千零七十九条", not a digit-by-digit
        "第一零七九条" — so 4-digit articles need real place-value handling.
        """
        if n <= 0:
            return str(n)

        d = cls._CN_DIGITS
        parts: list[str] = []

        thousands, rem = divmod(n, 1000)
        hundreds, rem = divmod(rem, 100)
        tens, ones = divmod(rem, 10)

        if thousands:
            parts.append(d[thousands] + "千")
        if hundreds:
            parts.append(d[hundreds] + "百")
        elif thousands and (tens or ones):
            # 1079 -> 一千零七十九 (zero filler for the empty hundreds place)
            parts.append("零")

        if tens:
            # 10-19 read as "十X" only when nothing precedes them
            if tens == 1 and not (thousands or hundreds):
                parts.append("十")
            else:
                parts.append(d[tens] + "十")
        elif ones and (thousands or hundreds) and hundreds:
            # 1103 -> 一千一百零三 (zero filler for the empty tens place)
            parts.append("零")

        if ones:
            parts.append(d[ones])

        return "".join(parts)

    @classmethod
    def _normalise_article_number(cls, raw: str) -> list[str]:
        """Return candidate stored forms for a user-supplied article number.

        "第47条" / "47" / "第四十七条" all yield ["第四十七条", "第47条"], so the
        query can match whichever form the row actually uses.
        """
        if not raw:
            return []
        s = str(raw).strip()
        candidates: list[str] = [s]

        m = re.search(r"(\d+)", s)
        if m:
            n = int(m.group(1))
            cn = f"第{cls._int_to_cn(n)}条"
            arabic = f"第{n}条"
            candidates.extend([cn, arabic])
        else:
            # Already Chinese numerals — make sure it is wrapped in 第...条
            core = s.strip("第条")
            if core:
                candidates.append(f"第{core}条")

        seen: set[str] = set()
        out: list[str] = []
        for c in candidates:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("keyword", "string", "搜索关键词（在条文内容中模糊匹配）", required=False),
            ToolParameter("law_name", "string", "法律名称（部分匹配）", required=False),
            ToolParameter("category", "string", "法律类别", required=False,
                          enum=["constitutional", "civil", "criminal", "administrative",
                                "economic", "social", "litigation", "commercial"]),
            ToolParameter("article_number", "string",
                          "条文编号，中文或阿拉伯数字均可（如'第四十七条'或'第47条'）",
                          required=False),
            ToolParameter("page_size", "integer", "返回条数", required=False, default=10),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text

            keyword = kwargs.get("keyword", "")
            law_name = kwargs.get("law_name", "")
            category = kwargs.get("category", "")
            article_number = kwargs.get("article_number", "")
            page_size = min(kwargs.get("page_size", 10), 50)

            conditions = []
            params: dict[str, Any] = {}

            # When an exact article is named, that is the user's intent — do not
            # also AND a content keyword, which would drop the row whenever the
            # keyword happens not to appear in that article's text.
            if article_number:
                variants = self._normalise_article_number(article_number)
                or_parts = []
                for i, v in enumerate(variants):
                    key = f"art{i}"
                    or_parts.append(f"la.article_number = :{key}")
                    params[key] = v
                if or_parts:
                    conditions.append("(" + " OR ".join(or_parts) + ")")
            elif keyword:
                conditions.append("la.content ILIKE :kw")
                params["kw"] = f"%{keyword}%"

            if law_name:
                conditions.append("l.name ILIKE :law_name")
                params["law_name"] = f"%{law_name}%"
            if category:
                conditions.append("l.law_type = :category")
                params["category"] = category

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            sql = text(f"""
                SELECT la.id, l.name as law_name, la.article_number,
                       la.title, la.content, la.chapter, l.law_type
                FROM legal_articles la
                JOIN laws l ON la.law_id = l.id
                WHERE {where_clause}
                ORDER BY l.name, la.article_number
                LIMIT :limit
            """)
            params["limit"] = page_size

            async with async_session_factory() as session:
                result = await session.execute(sql, params)
                rows = result.fetchall()

            articles = []
            seen_content: set[tuple[str, str]] = set()
            for row in rows:
                # The corpus contains duplicate rows for some articles (same law,
                # same number) from overlapping imports — collapse them so the
                # model is not shown the same text twice.
                dedup_key = (row[1], row[2])
                if dedup_key in seen_content:
                    continue
                seen_content.add(dedup_key)
                articles.append({
                    "id": str(row[0]),
                    "law_name": row[1],
                    "article_number": row[2],
                    "title": row[3],
                    "content": row[4],
                    "chapter": row[5],
                    "law_type": row[6],
                })

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"articles": articles, "total": len(articles)},
                elapsed_seconds=elapsed,
                metadata={
                    "source": "laws_db",
                    "keyword": keyword,
                    "article_number_variants": self._normalise_article_number(article_number),
                },
            )
        except Exception as exc:
            elapsed = time.time() - start
            logger.error("LawArticleSearchTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)


class EnterpriseLookupTool(MCPTool):
    """Look up enterprise registration information (placeholder for 天眼查/企查查 API)."""

    name = "enterprise_lookup"
    description = "查询企业工商登记信息（企业名称、统一社会信用代码、法定代表人、注册资本、经营状态等）"
    category = "enterprise"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("company_name", "string", "企业名称（全称或关键词）"),
            ToolParameter("credit_code", "string", "统一社会信用代码", required=False),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        company_name = kwargs.get("company_name", "")
        credit_code = kwargs.get("credit_code", "")

        if not company_name and not credit_code:
            return ToolResult(success=False, error="必须提供企业名称或统一社会信用代码")

        try:
            from app.core.config import settings
            # Use proxy service for external API call if configured
            # For now, return a structured placeholder demonstrating the integration pattern
            api_base = getattr(settings, "ENTERPRISE_API_BASE", "")
            api_key = getattr(settings, "ENTERPRISE_API_KEY", "")

            if api_base and api_key:
                # Real API integration (天眼查/企查查/国家企业信用信息公示系统)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    headers = {"Authorization": f"Bearer {api_key}"}
                    resp = await client.get(
                        f"{api_base}/enterprise/search",
                        params={"name": company_name, "code": credit_code},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    elapsed = time.time() - start
                    return ToolResult(
                        success=True, data=data,
                        elapsed_seconds=elapsed,
                        metadata={"source": "enterprise_api"},
                    )
            else:
                # Demo mode: return sample data structure
                elapsed = time.time() - start
                return ToolResult(
                    success=True,
                    data={
                        "company_name": company_name or "未知企业",
                        "credit_code": credit_code or "待接入真实API",
                        "legal_representative": "（需配置ENTERPRISE_API_KEY）",
                        "registered_capital": "—",
                        "establishment_date": "—",
                        "business_status": "—",
                        "registered_address": "—",
                        "business_scope": "—",
                        "note": "企业工商查询API尚未配置，请在.env中设置ENTERPRISE_API_BASE和ENTERPRISE_API_KEY",
                    },
                    elapsed_seconds=elapsed,
                    metadata={"source": "demo_mode", "configured": False},
                )
        except Exception as exc:
            elapsed = time.time() - start
            logger.error("EnterpriseLookupTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)


class GovernmentRegulationTool(MCPTool):
    """Search government regulations and policies."""

    name = "government_regulation"
    description = "检索国务院及各部委发布的行政法规、部门规章、规范性文件"
    category = "government"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("keyword", "string", "搜索关键词"),
            ToolParameter("issuing_authority", "string", "发布机关（如'国务院'、'最高人民法院'）", required=False),
            ToolParameter("regulation_type", "string", "文件类型", required=False,
                          enum=["行政法规", "部门规章", "规范性文件", "司法解释"]),
            ToolParameter("effective_status", "string", "效力状态", required=False,
                          enum=["现行有效", "已修改", "已废止"]),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text

            keyword = kwargs.get("keyword", "")
            issuing_authority = kwargs.get("issuing_authority", "")
            regulation_type = kwargs.get("regulation_type", "")
            effective_status = kwargs.get("effective_status", "")

            conditions = []
            params: dict[str, Any] = {}

            if keyword:
                # `laws` carries the same three text fields; the previous version
                # queried `judicial_interpretations`, which has no `abstract`
                # column at all — every keyword call died on UndefinedColumnError.
                conditions.append(
                    "(name ILIKE :kw OR COALESCE(abstract, '') ILIKE :kw "
                    "OR law_type ILIKE :kw)"
                )
                params["kw"] = f"%{keyword}%"
            if issuing_authority:
                conditions.append("issuing_authority ILIKE :authority")
                params["authority"] = f"%{issuing_authority}%"
            if regulation_type == "司法解释":
                conditions.append("law_type IN ('司法解释', '司法解', '法律解释')")
            elif regulation_type:
                conditions.append("law_type = :reg_type")
                params["reg_type"] = regulation_type

            # `effective_status` has no column in `judicial_interpretations`, so
            # it is applied to the `laws` branch only, in the UNION below.
            statuses: list[str] | None = None
            if effective_status:
                # Tool exposes Chinese labels; the table stores English enum values.
                status_map = {
                    "现行有效": ["active"],
                    "已修改": ["amended"],
                    "已废止": ["repealed"],
                }
                statuses = status_map.get(effective_status)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # Two sources, one ranked list:
            #  - `judicial_interpretations` carries the assembled full text plus
            #    文号 and the construed statutes, but only covers 司法解释.
            #  - `laws` covers every regulation type (行政法规/部门规章/地方性法规…)
            #    but its `abstract` is empty for all but 131 rows, so it yields
            #    titles with little body text.
            # Preferring the interpretation table for 司法解释 rows means a caller
            # gets the actual text rather than a bare title.
            if statuses:
                laws_status_clause = "AND l.status = ANY(:statuses)"
                params["statuses"] = statuses
                # Interpretations are all 现行有效; skip them when the caller
                # asked for any other status.
                ji_status_clause = "" if "active" in statuses else "AND FALSE"
            else:
                laws_status_clause = ""
                ji_status_clause = ""
            # Skip `laws` rows already represented in the richer table, so a
            # 司法解释 is not returned twice (once with text, once as a title).
            dedupe_clause = (
                "AND NOT EXISTS (SELECT 1 FROM judicial_interpretations ji2 "
                "WHERE ji2.name = l.name)"
                if not statuses
                else "AND NOT EXISTS (SELECT 1 FROM judicial_interpretations ji2 "
                     "WHERE ji2.name = l.name AND ji2.content IS NOT NULL)"
            )

            # Bound as plain LIKE patterns; NULL means "no filter" on each branch.
            params["kw"] = f"%{keyword}%" if keyword else None
            params["authority"] = f"%{issuing_authority}%" if issuing_authority else None

            sql = text(f"""
                WITH ji AS (
                    SELECT ji.id, ji.name, ji.issuing_court AS issuing_authority,
                           ji.effective_date, ji.content, '司法解释' AS law_type,
                           '现行有效' AS status, ji.doc_number
                    FROM judicial_interpretations ji
                    WHERE (
                        CAST(:kw AS text) IS NULL
                        OR ji.name ILIKE CAST(:kw AS text)
                        OR COALESCE(ji.content, '') ILIKE CAST(:kw AS text)
                    )
                      AND (
                        CAST(:authority AS text) IS NULL
                        OR COALESCE(ji.issuing_court, '') ILIKE CAST(:authority AS text)
                      )
                      {ji_status_clause}
                ),
                ls AS (
                    SELECT l.id, l.name, l.issuing_authority,
                           l.effective_date,
                           COALESCE(NULLIF(l.abstract, ''), '') AS content,
                           l.law_type, l.status, NULL AS doc_number
                    FROM laws l
                    WHERE {where_clause}
                      {laws_status_clause}
                      {dedupe_clause}
                )
                SELECT id, name, issuing_authority, effective_date, content,
                       law_type, status, doc_number,
                       length(COALESCE(content, '')) AS body_len
                FROM (SELECT * FROM ji UNION ALL SELECT * FROM ls) u
                ORDER BY body_len DESC, effective_date DESC NULLS LAST
                LIMIT 20
            """)

            async with async_session_factory() as session:
                result = await session.execute(sql, params)
                rows = result.fetchall()

            regulations = []
            for row in rows:
                regulations.append({
                    "id": str(row[0]),
                    "name": row[1],
                    "issuing_authority": row[2],
                    "effective_date": str(row[3]) if row[3] else None,
                    "content": (row[4] or "")[:1000],
                    "law_type": row[5],
                    "status": row[6],
                    "doc_number": row[7],
                })

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"regulations": regulations, "total": len(regulations)},
                elapsed_seconds=elapsed,
                metadata={"source": "laws_db", "regulation_type": regulation_type or None},
            )
        except Exception as exc:
            elapsed = time.time() - start
            logger.error("GovernmentRegulationTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)


class KnowledgeBaseSearchTool(MCPTool):
    """Search the user's private knowledge base (enterprise knowledge)."""

    name = "knowledge_base_search"
    description = "在企业私有知识库中搜索相关文档和知识，支持语义检索"
    category = "knowledge"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("query", "string", "搜索问题或关键词"),
            ToolParameter("namespace", "string", "知识库命名空间（企业隔离）", required=False, default="default"),
            ToolParameter("top_k", "integer", "返回条数", required=False, default=5),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        try:
            query = kwargs.get("query", "")
            namespace = kwargs.get("namespace", "default")
            top_k = min(kwargs.get("top_k", 5), 20)

            # Use Milvus vector search on knowledge_base collection
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()

            # Search in knowledge_base collection
            collection_name = f"knowledge_base_{namespace}"
            try:
                results = milvus.search(
                    collection_name=collection_name,
                    query_text=query,
                    top_k=top_k,
                )
            except Exception:
                # Fallback: try the default collection
                results = milvus.search(
                    collection_name=settings.MILVUS_COLLECTION_NAME,
                    query_text=query,
                    top_k=top_k,
                )

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"results": results, "total": len(results)},
                elapsed_seconds=elapsed,
                metadata={"source": "milvus_knowledge_base", "namespace": namespace},
            )
        except Exception as exc:
            elapsed = time.time() - start
            logger.error("KnowledgeBaseSearchTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)


class LegalCalculatorTool(MCPTool):
    """Legal calculation utilities (statute of limitations, interest, damages)."""

    name = "legal_calculator"
    description = "法律计算工具：诉讼时效计算、利息计算、赔偿金计算、诉讼费用计算等"
    category = "utility"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("calc_type", "string", "计算类型",
                          enum=["statute_of_limitations", "interest", "litigation_fee",
                                "late_payment_penalty", "economic_compensation"]),
            ToolParameter("params", "object", "计算参数（JSON对象），具体字段取决于计算类型"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        calc_type = kwargs.get("calc_type", "")
        params = kwargs.get("params", {})

        try:
            from datetime import datetime, timedelta
            from dateutil.relativedelta import relativedelta

            if calc_type == "statute_of_limitations":
                # 诉讼时效计算
                start_date_str = params.get("start_date", "")
                limitation_years = params.get("limitation_years", 3)  # 默认3年普通诉讼时效

                if not start_date_str:
                    return ToolResult(success=False, error="请提供start_date（起算日期，格式YYYY-MM-DD）")

                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                expire_date = start_date + relativedelta(years=limitation_years)
                remaining_days = (expire_date - datetime.now()).days

                return ToolResult(
                    success=True,
                    data={
                        "calc_type": "statute_of_limitations",
                        "start_date": start_date_str,
                        "limitation_years": limitation_years,
                        "expire_date": expire_date.strftime("%Y-%m-%d"),
                        "remaining_days": remaining_days,
                        "is_expired": remaining_days < 0,
                        "legal_basis": "《中华人民共和国民法典》第一百八十八条：向人民法院请求保护民事权利的诉讼时效期间为三年",
                    },
                    elapsed_seconds=time.time() - start,
                )

            elif calc_type == "interest":
                # 利息计算
                principal = float(params.get("principal", 0))
                annual_rate = float(params.get("annual_rate", 0))
                days = int(params.get("days", 0))
                interest = principal * annual_rate / 365 * days

                return ToolResult(
                    success=True,
                    data={
                        "calc_type": "interest",
                        "principal": principal,
                        "annual_rate": annual_rate,
                        "days": days,
                        "interest": round(interest, 2),
                        "total": round(principal + interest, 2),
                    },
                    elapsed_seconds=time.time() - start,
                )

            elif calc_type == "litigation_fee":
                # 诉讼费用计算（根据《诉讼费用交纳办法》）
                amount = float(params.get("amount", 0))
                if amount <= 0:
                    return ToolResult(success=False, error="请提供正数金额")

                # 分段累进计算
                if amount <= 10000:
                    fee = 50
                elif amount <= 100000:
                    fee = (amount - 10000) * 0.025 + 50
                elif amount <= 200000:
                    fee = (amount - 100000) * 0.02 + 2250 + 50
                elif amount <= 500000:
                    fee = (amount - 200000) * 0.015 + 2250 + 2000 + 50
                elif amount <= 1000000:
                    fee = (amount - 500000) * 0.01 + 4500 + 2250 + 2000 + 50
                elif amount <= 2000000:
                    fee = (amount - 1000000) * 0.009 + 5000 + 4500 + 2250 + 2000 + 50
                elif amount <= 5000000:
                    fee = (amount - 2000000) * 0.008 + 9000 + 5000 + 4500 + 2250 + 2000 + 50
                elif amount <= 10000000:
                    fee = (amount - 5000000) * 0.007 + 24000 + 9000 + 5000 + 4500 + 2250 + 2000 + 50
                else:
                    fee = (amount - 10000000) * 0.006 + 34000 + 24000 + 9000 + 5000 + 4500 + 2250 + 2000 + 50

                return ToolResult(
                    success=True,
                    data={
                        "calc_type": "litigation_fee",
                        "claim_amount": amount,
                        "litigation_fee": round(fee, 2),
                        "legal_basis": "《诉讼费用交纳办法》第十三条",
                    },
                    elapsed_seconds=time.time() - start,
                )

            elif calc_type == "economic_compensation":
                # 经济补偿金计算（劳动合同法N+1）
                monthly_salary = float(params.get("monthly_salary", 0))
                years_of_service = float(params.get("years_of_service", 0))
                # 每满一年支付一个月工资，六个月以上不满一年按一年算
                months = int(years_of_service * 12)
                n_months = max(1, (months + 11) // 12)  # 向上取整到年
                # 不满6个月支付半个月
                remaining_months = months % 12
                if remaining_months < 6:
                    compensation = n_months * monthly_salary + 0.5 * monthly_salary
                else:
                    compensation = (n_months + 1) * monthly_salary

                return ToolResult(
                    success=True,
                    data={
                        "calc_type": "economic_compensation",
                        "monthly_salary": monthly_salary,
                        "years_of_service": years_of_service,
                        "compensation_months": n_months,
                        "compensation_amount": round(compensation, 2),
                        "legal_basis": "《中华人民共和国劳动合同法》第四十七条",
                    },
                    elapsed_seconds=time.time() - start,
                )

            else:
                return ToolResult(
                    success=False,
                    error=f"不支持的计算类型: {calc_type}。支持: statute_of_limitations, interest, litigation_fee, economic_compensation",
                )

        except Exception as exc:
            logger.error("LegalCalculatorTool failed: %s", exc)
            return ToolResult(success=False, error=str(exc), elapsed_seconds=time.time() - start)


# =============================================================================
# New MCP Tools (Phase 2 expansion)
# =============================================================================

class LimitationCalculatorTool(MCPTool):
    """诉讼时效计算工具 — 根据《民法典》计算各类请求权的诉讼时效届满日。"""

    name = "limitation_calculator"
    description = (
        "诉讼时效计算器：根据《民法典》计算各类请求权的诉讼时效期间及届满日期。"
        "支持普通诉讼时效(3年)、国际货物买卖合同(4年)、人身损害(3年)等。"
        "输入起始日期和时效类型，返回届满日期、是否已过时效、剩余天数。"
    )
    category = "utility"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("start_date", "string", "时效起算日期，格式 YYYY-MM-DD"),
            ToolParameter("case_type", "string",
                          "案件类型: general(普通3年), international(国际货物买卖4年), "
                          "personal_injury(人身损害3年), product_liability(产品责任2年), "
                          "labor_dispute(劳动争议1年), environmental(环境污染3年)"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        from datetime import datetime, timedelta
        start = kwargs.get("start_date", "")
        case_type = kwargs.get("case_type", "general")
        periods = {
            "general": (3, "普通诉讼时效（《民法典》第188条）"),
            "international": (4, "国际货物买卖合同时效"),
            "personal_injury": (3, "人身损害赔偿时效"),
            "product_liability": (2, "产品责任时效"),
            "labor_dispute": (1, "劳动争议仲裁时效（《劳动争议调解仲裁法》第27条）"),
            "environmental": (3, "环境污染损害赔偿时效"),
        }
        years, rule = periods.get(case_type, periods["general"])
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
        except ValueError:
            return ToolResult(success=False, error="日期格式错误，请使用 YYYY-MM-DD")
        end_dt = start_dt.replace(year=start_dt.year + years)
        today = datetime.now()
        remaining = (end_dt - today).days
        return ToolResult(success=True, data={
            "start_date": start,
            "limitation_years": years,
            "limitation_rule": rule,
            "expiration_date": end_dt.strftime("%Y-%m-%d"),
            "is_expired": remaining < 0,
            "remaining_days": max(remaining, 0),
            "suspension_note": "注意：存在时效中止/中断情形时，实际届满日可能不同",
        })


class LitigationCostCalculatorTool(MCPTool):
    """诉讼费用计算器 — 根据《诉讼费用交纳办法》计算案件受理费。"""

    name = "litigation_cost_calculator"
    description = (
        "诉讼费用计算器：根据《诉讼费用交纳办法》计算案件受理费。"
        "输入争议标的金额，自动按阶梯费率计算，支持财产案件、非财产案件、"
        "知识产权案件等类型。返回受理费、减半金额（简易程序）、保全费参考。"
    )
    category = "utility"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("amount", "number", "争议标的金额（人民币元）"),
            ToolParameter("case_type", "string",
                          "案件类型: property(财产案件), non_property(非财产案件), "
                          "ip(知识产权案件), labor(劳动争议10元/件)", required=False,
                          default="property"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        amount = float(kwargs.get("amount", 0))
        case_type = kwargs.get("case_type", "property")
        if amount < 0:
            return ToolResult(success=False, error="标的金额不能为负数")
        if case_type == "labor":
            return ToolResult(success=True, data={
                "case_type": "劳动争议", "amount": amount,
                "filing_fee": 10, "half_fee": 5,
                "note": "劳动争议案件每件交纳10元",
            })
        if case_type == "non_property":
            return ToolResult(success=True, data={
                "case_type": "非财产案件", "amount": amount,
                "filing_fee": 300, "half_fee": 150,
                "note": "非财产案件定额交纳300元",
            })
        # 财产案件阶梯费率（《诉讼费用交纳办法》第13条）
        tiers = [
            (10000, 50, 0),          # ≤1万: 50元
            (100000, 0.025, -150),    # 1-10万: 2.5% - 150
            (200000, 0.02, -100),     # 10-20万: 2% - 100 (actually 50+2250=2300)
            (500000, 0.015, 0),       # 20-50万: 1.5%
            (1000000, 0.01, 1000),    # 50-100万: 1% + 1000
            (2000000, 0.009, 1900),   # 100-200万: 0.9% + 1900
            (5000000, 0.008, 3900),   # 200-500万: 0.8% + 3900
            (10000000, 0.007, 8900),  # 500-1000万: 0.7% + 8900
            (20000000, 0.006, 18900), # 1000-2000万: 0.6% + 18900
            (float("inf"), 0.005, 38900),  # >2000万: 0.5% + 38900
        ]
        fee = 50.0
        for upper, rate, adjust in tiers:
            if amount <= upper:
                fee = amount * rate + adjust
                break
        fee = max(fee, 50)
        return ToolResult(success=True, data={
            "case_type": "财产案件", "amount": amount,
            "filing_fee": round(fee, 2),
            "half_fee": round(fee / 2, 2),
            "preservation_fee": round(min(amount * 0.005, 5000), 2),
            "note": "适用简易程序减半交纳；保全费不超过5000元",
        })


class ContractRiskCheckerTool(MCPTool):
    """合同风险速查工具 — 快速检查合同文本中的常见高风险条款。"""

    name = "contract_risk_checker"
    description = (
        "合同风险速查：快速扫描合同文本中的常见高风险条款模式，"
        "包括：违约金过高、管辖权不利、自动续约陷阱、免责条款过宽、"
        "知识产权归属不明等。返回风险清单及修改建议。"
    )
    category = "contract"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("contract_text", "string", "合同文本内容（前5000字）"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("contract_text", "")[:5000]
        if not text.strip():
            return ToolResult(success=False, error="合同文本不能为空")
        risks = []
        patterns = [
            ("自动续约", ["自动续约", "自动续期", "自动延长"],
             "合同包含自动续约条款，注意续约条件和退出机制"),
            ("违约金过高", ["违约金.*每日.*%", "违约金.*合同总价"],
             "违约金条款可能过高，根据《民法典》第585条，超过实际损失30%可请求法院适当减少"),
            ("单方解除权", ["甲方有权.*单方解除", "有权.*随时解除"],
             "一方享有单方解除权，可能导致合同不稳定"),
            ("免责过宽", ["不承担任何责任", "概不负责", "免除.*全部责任"],
             "免责条款过宽，根据《民法典》第506条，造成对方人身损害和故意/重大过失造成财产损失的免责条款无效"),
            ("知识产权归属", ["知识产权.*归.*所有", "著作权.*归属"],
             "知识产权归属条款需要明确约定，避免纠纷"),
            ("竞业限制", ["竞业限制", "竞业禁止", "不得从事"],
             "竞业限制条款需注意期限不超过2年（《劳动合同法》第24条），且需支付经济补偿"),
            ("管辖权", ["仲裁", "管辖法院", "争议解决"],
             "注意管辖权/仲裁条款，选择对己方有利的争议解决方式"),
            ("保证金", ["保证金", "押金", "定金"],
             "保证金/定金条款需明确退还条件和期限"),
        ]
        for name, keywords, suggestion in patterns:
            found = False
            for kw in keywords:
                if re.search(kw, text):
                    found = True
                    break
            if found:
                risks.append({
                    "risk_category": name,
                    "risk_level": "high" if name in ("违约金过高", "免责过宽") else "medium",
                    "suggestion": suggestion,
                })
        return ToolResult(success=True, data={
            "total_risks_found": len(risks),
            "risks": risks,
            "scanned_chars": len(text),
        })


class EvidenceChecklistTool(MCPTool):
    """证据清单生成工具 — 根据案件类型生成所需证据清单。"""

    name = "evidence_checklist"
    description = (
        "证据清单生成器：根据案件类型自动生成所需的证据清单模板，"
        "包括书证、物证、证人证言、电子数据等。覆盖劳动争议、合同纠纷、"
        "侵权责任、婚姻家事、知识产权等常见案件类型。"
    )
    category = "litigation"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("case_type", "string",
                          "案件类型: labor_dispute(劳动争议), contract(合同纠纷), "
                          "tort(侵权), marriage(婚姻家事), ip(知识产权), "
                          "consumer(消费维权)"),
            ToolParameter("description", "string", "案件简要描述（可选）",
                          required=False, default=""),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        case_type = kwargs.get("case_type", "contract")
        checklists = {
            "labor_dispute": {
                "case_type": "劳动争议",
                "items": [
                    {"category": "劳动关系证明", "evidence": "劳动合同/聘用协议", "priority": "必须"},
                    {"category": "劳动关系证明", "evidence": "工资条/银行流水", "priority": "必须"},
                    {"category": "劳动关系证明", "evidence": "社保缴纳记录", "priority": "必须"},
                    {"category": "劳动关系证明", "evidence": "工牌/工作证", "priority": "建议"},
                    {"category": "考勤记录", "evidence": "打卡记录/考勤表", "priority": "必须"},
                    {"category": "考勤记录", "evidence": "加班审批单", "priority": "如有"},
                    {"category": "解除关系", "evidence": "解除/终止劳动合同通知书", "priority": "必须"},
                    {"category": "解除关系", "evidence": "辞退邮件/微信记录", "priority": "如有"},
                    {"category": "工资争议", "evidence": "工资发放记录（至少12个月）", "priority": "必须"},
                    {"category": "工资争议", "evidence": "年终奖/绩效约定", "priority": "如有"},
                ],
            },
            "contract": {
                "case_type": "合同纠纷",
                "items": [
                    {"category": "合同文本", "evidence": "合同原件/复印件", "priority": "必须"},
                    {"category": "合同文本", "evidence": "补充协议/变更协议", "priority": "如有"},
                    {"category": "履行证据", "evidence": "付款凭证/发票", "priority": "必须"},
                    {"category": "履行证据", "evidence": "交货/验收记录", "priority": "必须"},
                    {"category": "违约证据", "evidence": "催告函/律师函", "priority": "建议"},
                    {"category": "违约证据", "evidence": "往来邮件/微信聊天记录", "priority": "必须"},
                    {"category": "损失证明", "evidence": "损失计算依据", "priority": "必须"},
                    {"category": "损失证明", "evidence": "第三方评估报告", "priority": "建议"},
                ],
            },
            "tort": {
                "case_type": "侵权责任",
                "items": [
                    {"category": "侵权事实", "evidence": "现场照片/视频", "priority": "必须"},
                    {"category": "侵权事实", "evidence": "报警记录/出警记录", "priority": "如有"},
                    {"category": "损害后果", "evidence": "医院诊断证明/病历", "priority": "必须"},
                    {"category": "损害后果", "evidence": "伤残鉴定报告", "priority": "如有"},
                    {"category": "损害后果", "evidence": "医疗费/误工费票据", "priority": "必须"},
                    {"category": "因果关系", "evidence": "司法鉴定意见", "priority": "建议"},
                    {"category": "身份信息", "evidence": "侵权人身份信息", "priority": "必须"},
                    {"category": "证人证言", "evidence": "目击者联系方式及证言", "priority": "建议"},
                ],
            },
        }
        checklist = checklists.get(case_type, checklists["contract"])
        return ToolResult(success=True, data=checklist)


class LegalTermDictionaryTool(MCPTool):
    """法律术语词典 — 解释常见法律术语的含义和适用场景。"""

    name = "legal_term_dictionary"
    description = (
        "法律术语词典：查询常见法律术语的含义、法律依据和适用场景。"
        "覆盖民法、刑法、行政法、诉讼法等领域的核心术语。"
        "帮助用户理解法律文书中的专业用语。"
    )
    category = "knowledge"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("term", "string", "要查询的法律术语"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        term = kwargs.get("term", "").strip()
        if not term:
            return ToolResult(success=False, error="请输入要查询的法律术语")
        dictionary = {
            "不可抗力": {
                "definition": "不能预见、不能避免且不能克服的客观情况",
                "legal_basis": "《民法典》第180条、第590条",
                "examples": "自然灾害（地震、洪水）、战争、政府行为、疫情等",
                "effect": "因不可抗力不能履行民事义务的，不承担民事责任。部分或全部免责。",
            },
            "善意取得": {
                "definition": "无权处分人将不动产或动产转让给受让人，受让人善意且以合理价格取得，可取得所有权",
                "legal_basis": "《民法典》第311条",
                "examples": "甲将借用乙的电脑卖给不知情的丙，丙以市场价购买",
                "effect": "受让人取得所有权，原所有权人有权向无权处分人请求损害赔偿",
            },
            "诉讼时效": {
                "definition": "权利人在法定期间内不行使权利，义务人获得抗辩权的法律制度",
                "legal_basis": "《民法典》第188条（普通3年）、第189-199条",
                "examples": "借款到期后3年内未主张还款，债务人可主张时效抗辩",
                "effect": "时效届满后，权利人丧失胜诉权，但实体权利不消灭",
            },
            "定金": {
                "definition": "当事人为确保合同履行，一方预先支付给对方的一定数额的金钱",
                "legal_basis": "《民法典》第586-588条",
                "examples": "购房定金、订金（注意：订金≠定金）",
                "effect": "给付方违约无权请求返还；收受方违约应双倍返还。定金不超过主合同标的额20%",
            },
            "保全": {
                "definition": "法院为保证判决执行或避免当事人合法权益受损，对财产或行为采取的强制措施",
                "legal_basis": "《民事诉讼法》第100-108条",
                "examples": "诉前财产保全、诉中财产保全、行为保全",
                "effect": "查封、扣押、冻结被申请人财产，保全费由败诉方承担",
            },
            "管辖权异议": {
                "definition": "当事人认为受诉法院对案件无管辖权，在答辩期内提出的异议",
                "legal_basis": "《民事诉讼法》第127条",
                "examples": "被告在收到起诉状副本15日内提出",
                "effect": "法院审查后裁定异议成立则移送，不成立则驳回，可上诉",
            },
        }
        # 精确匹配
        if term in dictionary:
            return ToolResult(success=True, data={"term": term, **dictionary[term]})
        # 模糊匹配
        matches = {k: v for k, v in dictionary.items() if term in k or k in term}
        if matches:
            return ToolResult(success=True, data={
                "term": term, "matched_terms": list(matches.keys()),
                "results": list(matches.values()),
            })
        return ToolResult(success=True, data={
            "term": term,
            "message": f"未找到'{term}'的词条，建议尝试：{', '.join(dictionary.keys())}",
        })


class CourtJurisdictionTool(MCPTool):
    """法院管辖权查询工具 — 根据案件类型和地点确定管辖法院。"""

    name = "court_jurisdiction"
    description = (
        "法院管辖权查询：根据案件类型（民事、刑事、行政、劳动）和当事人所在地，"
        "确定有管辖权的法院。覆盖级别管辖、地域管辖、专属管辖规则。"
        "帮助确定应向哪个法院起诉。"
    )
    category = "litigation"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("case_type", "string",
                          "案件类型: civil(民事), contract(合同纠纷), labor(劳动争议), "
                          "tort(侵权), marriage(婚姻家事), real_estate(不动产)"),
            ToolParameter("plaintiff_location", "string", "原告所在地（市/区）", required=False, default=""),
            ToolParameter("defendant_location", "string", "被告所在地（市/区）", required=False, default=""),
            ToolParameter("amount", "number", "争议标的金额（元）", required=False, default=0),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        case_type = kwargs.get("case_type", "civil")
        plaintiff = kwargs.get("plaintiff_location", "")
        defendant = kwargs.get("defendant_location", "")
        amount = float(kwargs.get("amount", 0))
        rules = {
            "contract": {
                "rule": "合同纠纷：被告住所地或合同履行地法院管辖",
                "legal_basis": "《民事诉讼法》第24条",
                "options": [
                    f"被告住所地基层人民法院（{defendant or '需填写'}）" if not defendant else f"{defendant}基层人民法院",
                    "合同履行地人民法院",
                ],
                "level": "基层人民法院" if amount < 50000000 else "中级及以上人民法院",
            },
            "labor": {
                "rule": "劳动争议：用人单位所在地或劳动合同履行地",
                "legal_basis": "《劳动争议调解仲裁法》第21条、《民事诉讼法》相关司法解释",
                "options": [
                    "劳动合同履行地劳动争议仲裁委员会",
                    f"用人单位所在地劳动争议仲裁委员会" if defendant else "用人单位所在地劳动争议仲裁委员会",
                ],
                "level": "劳动仲裁前置，仲裁不服可向基层人民法院起诉",
            },
            "tort": {
                "rule": "侵权纠纷：侵权行为地或被告住所地",
                "legal_basis": "《民事诉讼法》第29条",
                "options": [
                    "侵权行为实施地人民法院",
                    "侵权结果发生地人民法院",
                    f"被告住所地人民法院（{defendant or '需填写'}）" if not defendant else f"{defendant}基层人民法院",
                ],
                "level": "基层人民法院",
            },
            "marriage": {
                "rule": "婚姻家事：被告住所地（离婚案件有特殊规则）",
                "legal_basis": "《民事诉讼法》第22条",
                "options": [
                    f"被告住所地基层人民法院（{defendant or '需填写'}）" if not defendant else f"{defendant}基层人民法院",
                ],
                "level": "基层人民法院",
                "note": "被告离开住所地超过一年的，由原告住所地法院管辖",
            },
            "real_estate": {
                "rule": "不动产纠纷：不动产所在地法院专属管辖",
                "legal_basis": "《民事诉讼法》第34条",
                "options": ["不动产所在地人民法院（专属管辖，不可协议变更）"],
                "level": "基层人民法院",
            },
        }
        info = rules.get(case_type, rules.get("contract"))
        return ToolResult(success=True, data={
            **info,
            "plaintiff_location": plaintiff or "未提供",
            "defendant_location": defendant or "未提供",
            "amount": amount,
            "reminder": "起诉前请确认被告身份信息和住所地，以法院立案庭要求为准",
        })


# =============================================================================
# Tool Registry (Singleton)
# =============================================================================

class ToolRegistry:
    """Central registry for all MCP tools.

    Provides tool discovery, schema inspection, and execution dispatch.
    Thread-safe singleton pattern.
    """

    _instance: Optional[ToolRegistry] = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        self._tools: dict[str, MCPTool] = {}
        self._execution_log: list[dict[str, Any]] = []

    @classmethod
    async def get_instance(cls) -> ToolRegistry:
        """Get or create the singleton ToolRegistry instance."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    registry = cls()
                    registry.register_builtin_tools()
                    cls._instance = registry
        return cls._instance

    @classmethod
    def get_instance_sync(cls) -> ToolRegistry:
        """Synchronous access to the singleton (assumes already initialized)."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.register_builtin_tools()
        return cls._instance

    def register_builtin_tools(self) -> None:
        """Register all built-in MCP tools."""
        builtins: list[MCPTool] = [
            WenshuSearchTool(),
            LawArticleSearchTool(),
            EnterpriseLookupTool(),
            GovernmentRegulationTool(),
            KnowledgeBaseSearchTool(),
            LegalCalculatorTool(),
            # New tools (Phase 2 expansion)
            LimitationCalculatorTool(),
            LitigationCostCalculatorTool(),
            ContractRiskCheckerTool(),
            EvidenceChecklistTool(),
            LegalTermDictionaryTool(),
            CourtJurisdictionTool(),
        ]
        for tool in builtins:
            self.register_tool(tool)
        logger.info("Registered %d built-in MCP tools", len(builtins))

    def register_tool(self, tool: MCPTool) -> None:
        """Register a tool in the registry."""
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool: %s", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered MCP tool: %s (%s)", tool.name, tool.category)

    def unregister_tool(self, name: str) -> bool:
        """Remove a tool from the registry. Returns True if removed."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> MCPTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self, category: str | None = None) -> list[dict[str, Any]]:
        """List all registered tools, optionally filtered by category."""
        tools = self._tools.values()
        if category:
            tools = [t for t in tools if t.category == category]
        return [t.get_schema() for t in tools]

    def list_categories(self) -> list[str]:
        """List all tool categories."""
        return sorted(set(t.category for t in self._tools.values()))

    async def execute_tool(self, name: str, **kwargs: Any) -> ToolResult:
        """Execute a tool by name with the given parameters.

        Records execution in the log for auditing.
        """
        tool = self.get_tool(name)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' not found. Available tools: {list(self._tools.keys())}",
            )

        start = time.time()
        try:
            result = await tool.execute(**kwargs)
            elapsed = time.time() - start

            # Log execution
            self._execution_log.append({
                "tool": name,
                "timestamp": time.time(),
                "elapsed": elapsed,
                "success": result.success,
                "params": {k: v for k, v in kwargs.items() if not k.startswith("_")},
            })

            # Keep log bounded
            if len(self._execution_log) > 1000:
                self._execution_log = self._execution_log[-500:]

            return result

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("Tool execution failed: %s - %s", name, exc)
            self._execution_log.append({
                "tool": name,
                "timestamp": time.time(),
                "elapsed": elapsed,
                "success": False,
                "error": str(exc),
            })
            return ToolResult(success=False, error=str(exc), elapsed_seconds=elapsed)

    async def execute_tools_parallel(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[ToolResult]:
        """Execute multiple tools in parallel.

        Args:
            tool_calls: List of dicts with "name" and parameter keys.
                       e.g. [{"name": "wenshu_search", "keyword": "合同纠纷"}]

        Returns:
            List of ToolResults in the same order as tool_calls.
        """
        tasks = []
        for call in tool_calls:
            name = call.pop("name", "")
            tasks.append(self.execute_tool(name, **call))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Convert exceptions to ToolResults
        final_results = []
        for r in results:
            if isinstance(r, ToolResult):
                final_results.append(r)
            elif isinstance(r, Exception):
                final_results.append(ToolResult(success=False, error=str(r)))
            else:
                final_results.append(ToolResult(success=False, error="Unknown result type"))

        return final_results

    def get_execution_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent tool execution log entries."""
        return self._execution_log[-limit:]

    def get_stats(self) -> dict[str, Any]:
        """Return registry statistics."""
        total_executions = len(self._execution_log)
        successful = sum(1 for e in self._execution_log if e.get("success"))
        return {
            "total_tools": len(self._tools),
            "categories": self.list_categories(),
            "total_executions": total_executions,
            "successful_executions": successful,
            "failed_executions": total_executions - successful,
            "tools": {name: {"category": t.category, "description": t.description}
                      for name, t in self._tools.items()},
        }


# =============================================================================
# Convenience functions
# =============================================================================

async def get_tool_registry() -> ToolRegistry:
    """Get the singleton tool registry."""
    return await ToolRegistry.get_instance()


def get_tool_registry_sync() -> ToolRegistry:
    """Synchronous access to the tool registry."""
    return ToolRegistry.get_instance_sync()


# =============================================================================
# LLM Tool-Calling Integration
# =============================================================================

TOOL_CALLING_SYSTEM_PROMPT = """你是一位法律智能助手，可以使用外部工具来辅助回答法律问题。

## 可用工具
当用户的问题需要查询外部数据时，你可以调用以下工具。请在需要工具辅助时，以JSON格式输出工具调用请求：

{tool_schemas}

## 工具调用格式
当需要使用工具时，请在回答中输出以下格式的JSON块：
```tool_call
{{"tool": "工具名称", "parameters": {{参数名: 参数值}}}}
```

## 规则
1. 仅在需要查询外部数据时调用工具，不要为一般性法律问答调用工具
2. 可以同时调用多个工具（输出多个tool_call块）
3. 工具返回结果后，基于结果生成最终回答
4. 如果工具调用失败，使用已有知识回答并说明限制
"""


async def build_tool_calling_prompt() -> str:
    """Build the system prompt for tool-calling mode."""
    registry = await get_tool_registry()
    tools = registry.list_tools()

    tool_descriptions = []
    for tool in tools:
        params_desc = []
        props = tool.get("parameters", {}).get("properties", {})
        required = tool.get("parameters", {}).get("required", [])
        for pname, pinfo in props.items():
            req_mark = "（必填）" if pname in required else "（可选）"
            params_desc.append(f"    - {pname}: {pinfo.get('description', '')}{req_mark}")

        tool_desc = f"- **{tool['name']}** ({tool.get('category', '')}): {tool['description']}\n  参数:\n" + "\n".join(params_desc)
        tool_descriptions.append(tool_desc)

    return TOOL_CALLING_SYSTEM_PROMPT.format(
        tool_schemas="\n".join(tool_descriptions)
    )


def parse_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    """Extract tool call JSON blocks from LLM output text.

    Looks for ```tool_call ... ``` blocks.
    """
    import re
    pattern = r'```tool_call\s*\n?(.*?)\n?\s*```'
    matches = re.findall(pattern, text, re.DOTALL)

    tool_calls = []
    for match in matches:
        try:
            call = json.loads(match.strip())
            if "tool" in call:
                tool_calls.append(call)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool call: %s", match[:100])
            continue

    return tool_calls
