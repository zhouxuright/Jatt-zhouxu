"""
MCP (Model Context Protocol 2026) Server -- Standard FastAPI Endpoints

Exposes a standard MCP-compatible tool server over plain HTTP so that any
LLM client (Claude, OpenAI, local models) can discover and invoke tools
without the ``mcp`` Python package.  The endpoints follow the MCP 2026
surface:

    GET  /api/v1/mcp/tools        -- list tools with JSON-Schema inputs
    POST /api/v1/mcp/execute      -- execute a single tool by name
    POST /api/v1/mcp/agent-tools  -- discover + run tools in one call

Tool catalogue
--------------
1. ``search_laws``              -- hybrid law search via advanced_retriever
2. ``search_cases``             -- semantic case search via milvus_service
3. ``enterprise_lookup``        -- enterprise credit / registration lookup
4. ``legal_calculator``         -- damages / fees / interest calculator
5. ``web_search``               -- online legal search (wraps web_search.py)
6. ``knowledge_graph_query``    -- query the legal knowledge graph
7. ``document_analyze``         -- analyse an uploaded legal document
8. ``compensation_calculator``  -- labour dispute compensation calculator

Compatibility
-------------
The module reuses the existing :class:`ToolRegistry` from ``app.mcp`` so
that the ``/api/v1/mcp/tools/execute`` endpoint defined in
``app.api.v1.mcp_tools`` and the new ``/api/v1/mcp/*`` endpoints see the
same tool set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.mcp import (
    MCPTool,
    ToolParameter,
    ToolResult,
    get_tool_registry,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ===========================================================================
# Request / response schemas
# ===========================================================================

class ToolInputSchemaProperty(BaseModel):
    """A single property inside a tool's ``input_schema``."""
    type: str
    description: str
    default: Any | None = None
    enum: list[str] | None = None


class ToolInputSchema(BaseModel):
    """JSON-Schema description of a tool's parameters."""
    type: str = "object"
    properties: dict[str, ToolInputSchemaProperty] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    """Public description of one MCP tool."""
    name: str
    description: str
    category: str
    version: str = "1.0.0"
    input_schema: ToolInputSchema


class ToolListResponse(BaseModel):
    """``GET /tools`` response."""
    tools: list[ToolDefinition]
    categories: list[str]
    total: int
    protocol: str = "mcp-2026"


class ExecuteRequest(BaseModel):
    """``POST /execute`` body."""
    tool_name: str = Field(..., description="Tool to execute")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments -- keys must match ``input_schema.properties``",
    )


class ExecuteResponse(BaseModel):
    """``POST /execute`` response."""
    tool_name: str
    success: bool
    data: Any | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentToolCall(BaseModel):
    """A single tool call inside an ``agent-tools`` request."""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentToolsRequest(BaseModel):
    """``POST /agent-tools`` body -- an agent's planned action list."""
    task: str = Field(
        default="",
        description="High-level description of what the agent is trying to achieve",
    )
    tool_calls: list[AgentToolCall] = Field(
        ...,
        description="Ordered list of tool invocations to execute",
    )
    parallel: bool = Field(
        default=True,
        description="Whether independent tool calls may run in parallel",
    )


class AgentToolsResponse(BaseModel):
    """``POST /agent-tools`` response."""
    task: str
    results: list[ExecuteResponse]
    total: int
    successful: int
    failed: int


# ===========================================================================
# 1. search_laws -- wraps AdvancedRAGPipeline + law article DB fallback
# ===========================================================================

class SearchLawsTool(MCPTool):
    """Hybrid law search: BM25 + Milvus vector + knowledge-graph enhanced."""

    name = "search_laws"
    description = (
        "在法律法规库中进行语义检索，支持自然语言问题。"
        "结合 BM25 关键词、Milvus 向量相似度与知识图谱进行混合检索并 rerank。"
    )
    category = "legal_research"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("query", "string", "检索问题或关键词"),
            ToolParameter("top_k", "integer", "返回条数", required=False, default=5),
            ToolParameter("use_reranker", "boolean", "是否使用 cross-encoder reranker",
                          required=False, default=True),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        query = kwargs.get("query", "")
        top_k = min(int(kwargs.get("top_k", 5)), 20)
        use_reranker = bool(kwargs.get("use_reranker", True))

        if not query:
            return ToolResult(success=False, error="必须提供 query 参数")

        try:
            from app.rag.advanced_retriever import AdvancedRAGPipeline

            pipeline = AdvancedRAGPipeline()
            try:
                pipeline.initialize()
            except Exception as init_exc:
                logger.warning("AdvancedRAGPipeline init failed: %s -- falling back to DB", init_exc)
                return await self._fallback_db_search(query, top_k, start)

            results = pipeline.retrieve(
                query=query,
                top_k=top_k,
                use_bm25=True,
                use_vector=True,
                use_graph=True,
                use_reranker=use_reranker,
            )

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"results": results, "total": len(results)},
                elapsed_seconds=elapsed,
                metadata={"source": "advanced_retriever", "query": query},
            )
        except Exception as exc:
            logger.error("search_laws failed: %s", exc)
            return await self._fallback_db_search(query, top_k, start, error=str(exc))

    async def _fallback_db_search(
        self, query: str, top_k: int, start: float, *, error: str | None = None,
    ) -> ToolResult:
        """Graceful fallback to SQL ILIKE search when the retriever is unavailable."""
        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text

            sql = text("""
                SELECT la.id, l.name AS law_name, la.article_number,
                       la.title, la.content, la.chapter, l.law_type
                FROM legal_articles la
                JOIN laws l ON la.law_id = l.id
                WHERE la.content ILIKE :kw
                ORDER BY l.name, la.article_number
                LIMIT :limit
            """)
            async with async_session_factory() as session:
                rows = (await session.execute(sql, {"kw": f"%{query}%", "limit": top_k})).fetchall()

            articles = [
                {
                    "id": str(r[0]), "law_name": r[1], "article_number": r[2],
                    "title": r[3], "content": r[4], "chapter": r[5], "law_type": r[6],
                }
                for r in rows
            ]
            return ToolResult(
                success=True,
                data={"results": articles, "total": len(articles), "fallback": True},
                elapsed_seconds=time.time() - start,
                metadata={
                    "source": "laws_db_fallback",
                    "query": query,
                    "retriever_error": error,
                },
            )
        except Exception as fb_exc:
            return ToolResult(
                success=False,
                error=f"Primary error: {error}; fallback error: {fb_exc}",
                elapsed_seconds=time.time() - start,
            )


# ===========================================================================
# 2. search_cases -- wraps MilvusService.search_cases + DB fallback
# ===========================================================================

class SearchCasesTool(MCPTool):
    """Semantic search over embedded court cases via Milvus."""

    name = "search_cases"
    description = "在裁判文书库中按语义相似度搜索案例，支持按案件类型、法院过滤。"
    category = "legal_research"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("query", "string", "搜索问题或案情描述"),
            ToolParameter("top_k", "integer", "返回条数", required=False, default=10),
            ToolParameter("case_type", "string", "案件类型（民事/刑事/行政/执行/赔偿）",
                          required=False),
            ToolParameter("court_name", "string", "法院名称", required=False),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        query = kwargs.get("query", "")
        top_k = min(int(kwargs.get("top_k", 10)), 50)
        case_type = kwargs.get("case_type", "")
        court_name = kwargs.get("court_name", "")

        if not query:
            return ToolResult(success=False, error="必须提供 query 参数")

        try:
            from app.rag.milvus_service import get_milvus_service
            milvus = get_milvus_service()
            results = milvus.search_cases(
                query=query, top_k=top_k,
                case_type=case_type or "", court_name=court_name or "",
            )
            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"cases": results, "total": len(results)},
                elapsed_seconds=elapsed,
                metadata={"source": "milvus_cases", "query": query},
            )
        except Exception as exc:
            logger.warning("Milvus case search failed: %s -- falling back to DB", exc)
            return await self._fallback_db_search(
                query, top_k, case_type, court_name, start, error=str(exc),
            )

    async def _fallback_db_search(
        self, query: str, top_k: int, case_type: str, court_name: str,
        start: float, *, error: str | None = None,
    ) -> ToolResult:
        try:
            from app.core.database import async_session_factory
            from sqlalchemy import text

            conditions: list[str] = []
            params: dict[str, Any] = {"kw": f"%{query}%", "limit": top_k}

            conditions.append(
                "(title ILIKE :kw OR cause_of_action ILIKE :kw OR summary ILIKE :kw)"
            )
            if case_type:
                conditions.append("case_type = :case_type")
                params["case_type"] = case_type
            if court_name:
                conditions.append("court_name ILIKE :court")
                params["court"] = f"%{court_name}%"

            where = " AND ".join(conditions)
            sql = text(f"""
                SELECT id, case_number, title, court_name, case_type,
                       cause_of_action, decision_date, summary, judgment_result
                FROM court_cases
                WHERE {where}
                ORDER BY decision_date DESC NULLS LAST
                LIMIT :limit
            """)

            async with async_session_factory() as session:
                rows = (await session.execute(sql, params)).fetchall()

            cases = [
                {
                    "id": str(r[0]), "case_number": r[1], "title": r[2],
                    "court_name": r[3], "case_type": r[4], "cause_of_action": r[5],
                    "decision_date": str(r[6]) if r[6] else None,
                    "summary": (r[7] or "")[:500], "judgment_result": r[8],
                }
                for r in rows
            ]
            return ToolResult(
                success=True,
                data={"cases": cases, "total": len(cases), "fallback": True},
                elapsed_seconds=time.time() - start,
                metadata={"source": "court_cases_db_fallback", "query": query,
                          "milvus_error": error},
            )
        except Exception as fb_exc:
            return ToolResult(
                success=False,
                error=f"Primary error: {error}; fallback error: {fb_exc}",
                elapsed_seconds=time.time() - start,
            )


# ===========================================================================
# 3. enterprise_lookup -- delegates to existing EnterpriseLookupTool logic
# ===========================================================================

class EnterpriseLookupTool(MCPTool):
    """Enterprise credit / registration lookup."""

    name = "enterprise_lookup"
    description = "查询企业工商登记、信用信息（企业名称、统一社会信用代码、法定代表人、注册资本、经营状态等）"
    category = "enterprise"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("company_name", "string", "企业名称（全称或关键词）", required=False),
            ToolParameter("credit_code", "string", "统一社会信用代码", required=False),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        company_name = kwargs.get("company_name", "")
        credit_code = kwargs.get("credit_code", "")

        if not company_name and not credit_code:
            return ToolResult(success=False, error="必须提供 company_name 或 credit_code")

        # Delegate to the existing enterprise-lookup registry tool when available
        try:
            registry = await get_tool_registry()
            existing = registry.get_tool("enterprise_lookup")
            if existing and existing is not self:
                result = await existing.execute(
                    company_name=company_name, credit_code=credit_code,
                )
                result.elapsed_seconds = time.time() - start
                return result
        except Exception as exc:
            logger.debug("Registry delegation failed: %s", exc)

        # Built-in mock response
        elapsed = time.time() - start
        return ToolResult(
            success=True,
            data={
                "company_name": company_name or "未知企业",
                "credit_code": credit_code or "待接入真实API",
                "legal_representative": "（需配置 ENTERPRISE_API_KEY）",
                "registered_capital": "—",
                "establishment_date": "—",
                "business_status": "—",
                "registered_address": "—",
                "business_scope": "—",
                "risk_info": {"litigation_count": 0, "penalty_count": 0},
                "note": "企业工商查询 API 尚未配置；请在 .env 中设置 ENTERPRISE_API_BASE 和 ENTERPRISE_API_KEY",
            },
            elapsed_seconds=elapsed,
            metadata={"source": "demo_mode", "configured": False},
        )


# ===========================================================================
# 4. legal_calculator -- wraps existing LegalCalculatorTool
# ===========================================================================

class LegalCalculatorTool(MCPTool):
    """Calculate damages, litigation fees, interest, statute of limitations."""

    name = "legal_calculator"
    description = (
        "法律计算工具：诉讼时效、利息、诉讼费用、逾期违约金、经济补偿金等。"
        "根据 calc_type 决定 params 结构。"
    )
    category = "utility"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                "calc_type", "string", "计算类型",
                enum=["statute_of_limitations", "interest", "litigation_fee",
                      "late_payment_penalty", "economic_compensation"],
            ),
            ToolParameter("params", "object", "计算参数（JSON 对象），具体字段取决于 calc_type"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        try:
            registry = await get_tool_registry()
            existing = registry.get_tool("legal_calculator")
            if existing and existing is not self:
                result = await existing.execute(**kwargs)
                result.elapsed_seconds = time.time() - start
                return result
        except Exception as exc:
            logger.debug("Registry delegation failed: %s", exc)

        return ToolResult(
            success=False,
            error=f"legal_calculator unavailable: {exc if 'exc' in dir() else 'registry not initialised'}",
            elapsed_seconds=time.time() - start,
        )


# ===========================================================================
# 5. web_search -- wraps WebSearchEngine
# ===========================================================================

class WebSearchTool(MCPTool):
    """Real-time web search for legal information."""

    name = "web_search"
    description = "在线搜索法律资讯、法规更新、判例、新闻；支持指定搜索引擎和时间范围。"
    category = "web"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("query", "string", "搜索关键词或问题"),
            ToolParameter("num_results", "integer", "返回条数", required=False, default=10),
            ToolParameter("engine", "string", "搜索引擎", required=False,
                          enum=["bing", "baidu", "sogou"]),
            ToolParameter("legal_only", "boolean", "仅搜索权威法律来源",
                          required=False, default=False),
            ToolParameter("time_range", "string", "时间范围（day/week/month/year）",
                          required=False),
            ToolParameter("mode", "string", "搜索模式", required=False,
                          enum=["general", "legal_updates", "cases"],
                          default="general"),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="必须提供 query 参数")

        num_results = min(int(kwargs.get("num_results", 10)), 30)
        engine = kwargs.get("engine")
        legal_only = bool(kwargs.get("legal_only", False))
        time_range = kwargs.get("time_range")
        mode = kwargs.get("mode", "general")

        try:
            from app.services.web_search import get_web_search_engine
            engine_inst = get_web_search_engine()

            if mode == "legal_updates":
                resp = await engine_inst.search_legal_updates(
                    topic=query, days_back=30 if not time_range else 7,
                )
            elif mode == "cases":
                resp = await engine_inst.search_cases(
                    keywords=query, case_type=None,
                )
            else:
                resp = await engine_inst.search(
                    query=query,
                    num_results=num_results,
                    engine=engine,
                    legal_only=legal_only,
                    time_range=time_range,
                )

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data=resp.to_dict(),
                elapsed_seconds=elapsed,
                metadata={"source": "web_search", "mode": mode},
            )
        except Exception as exc:
            logger.error("web_search failed: %s", exc)
            return ToolResult(success=False, error=str(exc),
                              elapsed_seconds=time.time() - start)


# ===========================================================================
# 6. knowledge_graph_query -- wraps KnowledgeGraphManager
# ===========================================================================

class KnowledgeGraphQueryTool(MCPTool):
    """Query the legal knowledge graph (Neo4j / in-memory fallback)."""

    name = "knowledge_graph_query"
    description = (
        "查询法律知识图谱：按关键词/类型搜索实体，获取关联法律、法条、案例、犯罪罪名等。"
    )
    category = "knowledge"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("mode", "string", "查询模式",
                          enum=["search_entities", "related_laws", "related_cases",
                                "entity_detail", "traverse"]),
            ToolParameter("keyword", "string", "搜索关键词", required=False),
            ToolParameter("entity_type", "string", "实体类型（Law/Article/Case/Crime/Court）",
                          required=False),
            ToolParameter("entity_id", "string", "实体 ID（用于 entity_detail / traverse）",
                          required=False),
            ToolParameter("case_id", "string", "案例 ID（用于 related_laws）", required=False),
            ToolParameter("article_id", "string", "法条 ID（用于 related_laws/related_cases）",
                          required=False),
            ToolParameter("crime_name", "string", "罪名（用于 related_cases）", required=False),
            ToolParameter("rel_type", "string", "关系类型过滤（用于 traverse）", required=False),
            ToolParameter("limit", "integer", "返回条数", required=False, default=20),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        mode = kwargs.get("mode", "search_entities")

        try:
            from app.rag.knowledge_graph import KnowledgeGraphManager

            kg = KnowledgeGraphManager()

            if mode == "search_entities":
                results = await kg.search_entities(
                    entity_type=kwargs.get("entity_type"),
                    keyword=kwargs.get("keyword", ""),
                    limit=min(int(kwargs.get("limit", 20)), 50),
                )
            elif mode == "related_laws":
                results = await kg.query_related_laws(
                    case_id=kwargs.get("case_id"),
                    article_id=kwargs.get("article_id"),
                    limit=min(int(kwargs.get("limit", 20)), 50),
                )
            elif mode == "related_cases":
                results = await kg.query_related_cases(
                    law_id=kwargs.get("entity_id"),
                    article_id=kwargs.get("article_id"),
                    crime_name=kwargs.get("crime_name"),
                    limit=min(int(kwargs.get("limit", 20)), 50),
                )
            elif mode == "entity_detail":
                entity_id = kwargs.get("entity_id")
                if not entity_id:
                    return ToolResult(success=False,
                                      error="entity_detail 模式需要 entity_id 参数")
                entity = await kg.get_entity(entity_id)
                results = [entity] if entity else []
            elif mode == "traverse":
                entity_id = kwargs.get("entity_id")
                if not entity_id:
                    return ToolResult(success=False,
                                      error="traverse 模式需要 entity_id 参数")
                rels = await kg.get_relationships(entity_id, rel_type=kwargs.get("rel_type"))
                results = rels if isinstance(rels, list) else []
            else:
                return ToolResult(
                    success=False,
                    error=f"未知 mode: {mode}。支持: search_entities, related_laws, "
                          "related_cases, entity_detail, traverse",
                )

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={"results": results, "total": len(results), "mode": mode},
                elapsed_seconds=elapsed,
                metadata={"source": "knowledge_graph", "mode": mode},
            )
        except Exception as exc:
            logger.error("knowledge_graph_query failed: %s", exc)
            return ToolResult(success=False, error=str(exc),
                              elapsed_seconds=time.time() - start)


# ===========================================================================
# 7. document_analyze -- analyse uploaded legal documents
# ===========================================================================

class DocumentAnalyzeTool(MCPTool):
    """Analyse an uploaded legal document (text / file)."""

    name = "document_analyze"
    description = (
        "分析法律文档：提取关键信息（当事人、案由、金额、日期、请求事项）、"
        "识别风险点、生成摘要。支持直接传入文本或 base64 编码的文件内容。"
    )
    category = "document"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("content", "string", "文档正文（纯文本或 Markdown）", required=False),
            ToolParameter("document_type", "string", "文档类型", required=False,
                          enum=["contract", "judgment", "complaint", "arbitration",
                                "legal_opinion", "other"]),
            ToolParameter("tasks", "array",
                          "分析任务列表，如 ['summary','risk','key_entities']",
                          required=False),
            ToolParameter("file_base64", "string",
                          "文件 base64 编码（当 content 为空时从文件中提取文本）",
                          required=False),
            ToolParameter("file_name", "string", "文件名（协助判断文档类型）", required=False),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        content = kwargs.get("content", "")
        document_type = kwargs.get("document_type", "other")
        tasks = kwargs.get("tasks") or ["summary", "key_entities", "risk"]
        file_base64 = kwargs.get("file_base64", "")
        file_name = kwargs.get("file_name", "")

        # If no inline text was supplied, try to extract it from a base64 file.
        if not content and file_base64:
            try:
                from app.services.document_parser import DocumentParser
                parser = DocumentParser()
                content = await parser.parse_base64(file_base64, filename=file_name)
            except Exception as exc:
                logger.warning("document_parser failed: %s", exc)
                content = ""

        if not content:
            return ToolResult(
                success=False,
                error="必须提供 content（文档文本）或 file_base64（文件编码）",
            )

        # Cap content length to avoid overwhelming downstream LLM calls.
        max_chars = 16000
        truncated = len(content) > max_chars

        try:
            from app.services.llm import get_llm_service  # type: ignore[import]

            llm = get_llm_service()

            task_descriptions = {
                "summary": "请生成该文档的简明摘要（200字以内）。",
                "key_entities": (
                    "请提取文档中的关键实体信息，以 JSON 格式返回，包括："
                    "当事人（plaintiff/defendant）、案由、涉案金额、关键日期、诉讼请求。"
                ),
                "risk": "请识别该文档中的法律风险点，逐条列出并说明原因。",
            }

            analysis: dict[str, Any] = {}
            for task in tasks:
                instruction = task_descriptions.get(task)
                if not instruction:
                    analysis[task] = {"error": f"未知分析任务: {task}"}
                    continue
                prompt = (
                    f"你是一位资深法律专家。请对以下{document_type}类文档执行分析任务。\n\n"
                    f"【任务】{instruction}\n\n"
                    f"【文档内容】\n{content[:max_chars]}"
                )
                try:
                    answer = await llm.generate(prompt)  # type: ignore[attr-defined]
                    analysis[task] = answer
                except Exception as task_exc:
                    analysis[task] = {"error": str(task_exc)}

            elapsed = time.time() - start
            return ToolResult(
                success=True,
                data={
                    "document_type": document_type,
                    "analysis": analysis,
                    "truncated": truncated,
                    "content_length": len(content),
                },
                elapsed_seconds=elapsed,
                metadata={"source": "document_analyze", "tasks": tasks},
            )
        except ImportError:
            # LLM service not available -- return a structural extraction only.
            analysis = self._structural_fallback(content, document_type, tasks)
            return ToolResult(
                success=True,
                data={
                    "document_type": document_type,
                    "analysis": analysis,
                    "truncated": truncated,
                    "content_length": len(content),
                    "note": "LLM 服务不可用，仅返回结构化基础提取结果",
                },
                elapsed_seconds=time.time() - start,
                metadata={"source": "document_analyze_structural"},
            )
        except Exception as exc:
            logger.error("document_analyze failed: %s", exc)
            return ToolResult(success=False, error=str(exc),
                              elapsed_seconds=time.time() - start)

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _structural_fallback(
        content: str, document_type: str, tasks: list[str],
    ) -> dict[str, Any]:
        """Very lightweight extraction when the LLM service is unavailable."""
        import re

        analysis: dict[str, Any] = {}
        if "summary" in tasks:
            first_paragraph = content.strip().split("\n\n")[0] if content.strip() else ""
            analysis["summary"] = first_paragraph[:300]

        if "key_entities" in tasks:
            entities: dict[str, Any] = {"raw_length": len(content)}
            amount_patterns = re.findall(
                r"[人金诉合]?.*?(\d[\d,\.]+)\s*[万]?元", content[:4000],
            )
            if amount_patterns:
                entities["amounts_detected"] = amount_patterns[:10]
            date_patterns = re.findall(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?", content[:4000])
            if date_patterns:
                entities["dates_detected"] = date_patterns[:10]
            analysis["key_entities"] = entities

        if "risk" in tasks:
            analysis["risk"] = {"note": "需要 LLM 服务支持，当前不可用"}

        return analysis


# ===========================================================================
# 8. compensation_calculator -- labour dispute compensation
# ===========================================================================

class CompensationCalculatorTool(MCPTool):
    """Calculate labour dispute compensation per PRC Labour Contract Law."""

    name = "compensation_calculator"
    description = (
        "劳动争议赔偿金计算器：根据《劳动合同法》计算经济补偿金（N）、"
        "违法解除赔偿金（2N）、未签合同双倍工资、代通知金（+1）等。"
    )
    category = "utility"
    version = "1.0.0"

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter("calc_type", "string", "计算类型",
                          enum=["economic_compensation", "illegal_termination",
                                "unsigned_contract_double_wage", "notice_payment",
                                "overtime", "annual_leave"]),
            ToolParameter("monthly_salary", "number", "劳动者月工资（元）"),
            ToolParameter("years_of_service", "number", "工作年限（年，可小数）",
                          required=False, default=0),
            ToolParameter("months_unsigned", "integer",
                          "未签合同的月数（用于 unsigned_contract_double_wage）",
                          required=False),
            ToolParameter("overtime_hours", "number",
                          "加班小时数（用于 overtime）", required=False),
            ToolParameter("unused_leave_days", "number",
                          "未休年假天数（用于 annual_leave）", required=False),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        start = time.time()
        calc_type = kwargs.get("calc_type", "")
        salary = float(kwargs.get("monthly_salary", 0))
        years = float(kwargs.get("years_of_service", 0))

        if not calc_type:
            return ToolResult(success=False, error="必须提供 calc_type")
        if salary <= 0:
            return ToolResult(success=False, error="monthly_salary 必须为正数")

        try:
            data = self._calculate(calc_type, salary, years, kwargs)
            return ToolResult(
                success=True,
                data=data,
                elapsed_seconds=time.time() - start,
                metadata={"source": "compensation_calculator"},
            )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc),
                              elapsed_seconds=time.time() - start)
        except Exception as exc:
            logger.error("compensation_calculator failed: %s", exc)
            return ToolResult(success=False, error=str(exc),
                              elapsed_seconds=time.time() - start)

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _calculate(calc_type: str, salary: float, years: float,
                   kwargs: dict[str, Any]) -> dict[str, Any]:
        """Pure calculation logic."""
        # N = number of compensation months (Labour Contract Law art. 47)
        months_full = int(years * 12)
        n_months = max(1, (months_full + 11) // 12) if years > 0 else 0
        remaining_months = months_full % 12

        if calc_type == "economic_compensation":
            # 经济补偿金 N
            if remaining_months < 6:
                extra = 0.5
            else:
                extra = 1.0
            total_months = n_months + extra if years > 0 else 0.5
            amount = total_months * salary
            return {
                "calc_type": "economic_compensation",
                "monthly_salary": salary,
                "years_of_service": years,
                "n_months": total_months,
                "amount": round(amount, 2),
                "legal_basis": "《劳动合同法》第四十七条",
            }

        if calc_type == "illegal_termination":
            # 违法解除赔偿金 2N
            if remaining_months < 6:
                extra = 0.5
            else:
                extra = 1.0
            n_months_total = (n_months + extra) if years > 0 else 0.5
            amount = n_months_total * salary * 2
            return {
                "calc_type": "illegal_termination",
                "monthly_salary": salary,
                "years_of_service": years,
                "n_months": n_months_total,
                "amount": round(amount, 2),
                "legal_basis": "《劳动合同法》第八十七条",
            }

        if calc_type == "unsigned_contract_double_wage":
            months_unsigned = int(kwargs.get("months_unsigned", 0))
            if months_unsigned <= 0:
                raise ValueError("unsigned_contract_double_wage 需要 months_unsigned > 0")
            # Employer pays double wage from month 2 onwards (art. 82).
            extra_wage = months_unsigned * salary
            return {
                "calc_type": "unsigned_contract_double_wage",
                "monthly_salary": salary,
                "months_unsigned": months_unsigned,
                "additional_wage": round(extra_wage, 2),
                "legal_basis": "《劳动合同法》第八十二条",
            }

        if calc_type == "notice_payment":
            # 代通知金 (+1)
            return {
                "calc_type": "notice_payment",
                "monthly_salary": salary,
                "notice_payment": round(salary, 2),
                "legal_basis": "《劳动合同法》第四十条",
            }

        if calc_type == "overtime":
            hours = float(kwargs.get("overtime_hours", 0))
            if hours <= 0:
                raise ValueError("overtime 需要 overtime_hours > 0")
            # 21.75 working days per month; default 150% rate (workday overtime).
            hourly = salary / 21.75 / 8
            overtime_pay = hourly * hours * 1.5
            return {
                "calc_type": "overtime",
                "monthly_salary": salary,
                "overtime_hours": hours,
                "hourly_rate": round(hourly, 2),
                "overtime_pay": round(overtime_pay, 2),
                "rate_applied": 1.5,
                "legal_basis": "《劳动法》第四十四条",
            }

        if calc_type == "annual_leave":
            days = float(kwargs.get("unused_leave_days", 0))
            if days <= 0:
                raise ValueError("annual_leave 需要 unused_leave_days > 0")
            daily = salary / 21.75
            compensation = daily * days * 3  # 300% including normal wage
            return {
                "calc_type": "annual_leave",
                "monthly_salary": salary,
                "unused_leave_days": days,
                "daily_wage": round(daily, 2),
                "compensation": round(compensation, 2),
                "rate_applied": 3.0,
                "legal_basis": "《职工带薪年休假条例》第五条",
            }

        raise ValueError(
            f"不支持的计算类型: {calc_type}。支持: economic_compensation, "
            "illegal_termination, unsigned_contract_double_wage, notice_payment, "
            "overtime, annual_leave"
        )


# ===========================================================================
# Tool catalogue -- registered in the shared ToolRegistry at import time
# ===========================================================================

_MCP_SERVER_TOOLS: list[MCPTool] = [
    SearchLawsTool(),
    SearchCasesTool(),
    EnterpriseLookupTool(),
    LegalCalculatorTool(),
    WebSearchTool(),
    KnowledgeGraphQueryTool(),
    DocumentAnalyzeTool(),
    CompensationCalculatorTool(),
]

_TOOLS_BY_NAME: dict[str, MCPTool] = {t.name: t for t in _MCP_SERVER_TOOLS}


def _ensure_registered() -> None:
    """Make sure the shared ToolRegistry knows about our tools.

    Called lazily on every request so that the two registries (the one from
    ``app.mcp.__init__`` and this server module) never drift apart.
    """
    try:
        registry = ToolRegistry.get_instance_sync()
    except Exception:
        return
    for tool in _MCP_SERVER_TOOLS:
        if registry.get_tool(tool.name) is None:
            registry.register_tool(tool)


def _to_tool_definition(tool: MCPTool) -> ToolDefinition:
    schema = tool.get_schema()
    params = schema.get("parameters", {})
    properties: dict[str, ToolInputSchemaProperty] = {}
    for pname, pinfo in params.get("properties", {}).items():
        properties[pname] = ToolInputSchemaProperty(
            type=pinfo.get("type", "string"),
            description=pinfo.get("description", ""),
            default=pinfo.get("default"),
            enum=pinfo.get("enum"),
        )
    return ToolDefinition(
        name=schema.get("name", tool.name),
        description=schema.get("description", tool.description),
        category=schema.get("category", tool.category),
        version=schema.get("version", tool.version),
        input_schema=ToolInputSchema(
            properties=properties,
            required=params.get("required", []),
        ),
    )


# ===========================================================================
# Endpoints
# ===========================================================================

@router.get(
    "/tools",
    response_model=ToolListResponse,
    summary="List available MCP tools (Model Context Protocol 2026)",
    dependencies=[Depends(get_current_user)],
)
async def list_tools(
    category: str | None = Query(default=None, description="Filter by category"),
) -> ToolListResponse:
    """Return all tools exposed by this MCP server with JSON-Schema inputs.

    Compatible with ``GET /api/v1/mcp/tools`` -- the ``ToolDefinition``
    payload is a superset of the ``ToolInfo`` schema used by the legacy
    ``mcp_tools`` API, so existing clients keep working.
    """
    _ensure_registered()
    tools = [_to_tool_definition(t) for t in _MCP_SERVER_TOOLS]
    if category:
        tools = [t for t in tools if t.category == category]
    categories = sorted({t.category for t in _MCP_SERVER_TOOLS})
    return ToolListResponse(tools=tools, categories=categories, total=len(tools))


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    summary="Execute an MCP tool by name",
    dependencies=[Depends(get_current_user)],
)
async def execute_tool(request: ExecuteRequest) -> ExecuteResponse:
    """Dispatch ``request.tool_name`` with ``request.arguments``.

    The endpoint first checks the server-local catalogue, then falls back to
    the shared :class:`ToolRegistry` so tools registered by other modules
    (``WenshuSearchTool``, ``LawArticleSearchTool``, ...) remain callable.
    """
    _ensure_registered()

    tool = _TOOLS_BY_NAME.get(request.tool_name)
    if tool is None:
        # Try the shared registry as a fallback (legacy built-in tools).
        registry = await get_tool_registry()
        fallback = registry.get_tool(request.tool_name)
        if fallback is None:
            available = sorted(
                set(_TOOLS_BY_NAME.keys()) | set(registry._tools.keys())
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": f"Tool '{request.tool_name}' not found",
                    "available_tools": available,
                },
            )
        tool = fallback

    result = await tool.execute(**request.arguments)
    return ExecuteResponse(
        tool_name=request.tool_name,
        success=result.success,
        data=result.data,
        error=result.error,
        elapsed_seconds=result.elapsed_seconds,
        metadata=result.metadata,
    )


@router.post(
    "/agent-tools",
    response_model=AgentToolsResponse,
    summary="Let an agent discover and execute tools in a single call",
    dependencies=[Depends(get_current_user)],
)
async def agent_tools(request: AgentToolsRequest) -> AgentToolsResponse:
    """Accept a batch of tool calls from an agent, execute them (optionally
    in parallel), and return all results.

    This is the MCP-2026 "agent-tools" endpoint: a higher-level wrapper
    around ``/execute`` so an LLM can plan + act without multiple round
    trips.
    """
    _ensure_registered()

    results: list[ExecuteResponse] = []
    error_count = 0

    async def _run_one(call: AgentToolCall) -> ExecuteResponse:
        nonlocal error_count
        tool = _TOOLS_BY_NAME.get(call.name)
        if tool is None:
            registry = await get_tool_registry()
            tool = registry.get_tool(call.name)
        if tool is None:
            error_count += 1
            return ExecuteResponse(
                tool_name=call.name, success=False,
                error=f"Tool '{call.name}' not found",
            )
        r = await tool.execute(**call.arguments)
        if not r.success:
            error_count += 1
        return ExecuteResponse(
            tool_name=call.name,
            success=r.success,
            data=r.data,
            error=r.error,
            elapsed_seconds=r.elapsed_seconds,
            metadata=r.metadata,
        )

    if request.parallel and len(request.tool_calls) > 1:
        results = list(await asyncio.gather(*[_run_one(c) for c in request.tool_calls]))
    else:
        for call in request.tool_calls:
            results.append(await _run_one(call))

    success_count = sum(1 for r in results if r.success)
    return AgentToolsResponse(
        task=request.task,
        results=results,
        total=len(results),
        successful=success_count,
        failed=len(results) - success_count,
    )


# ===========================================================================
# Health / introspection
# ===========================================================================

@router.get("/health", summary="MCP server health check")
async def health() -> dict[str, Any]:
    """Lightweight liveness probe."""
    return {
        "status": "ok",
        "protocol": "mcp-2026",
        "tool_count": len(_MCP_SERVER_TOOLS),
        "tools": [t.name for t in _MCP_SERVER_TOOLS],
    }
