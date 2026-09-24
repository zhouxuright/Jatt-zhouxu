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
