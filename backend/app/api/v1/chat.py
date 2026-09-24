"""Chat endpoints -- Intent recognition, Milvus RAG, multi-turn context, SSE streaming.

Architecture:
1. Intent Classification (LLM-based) → route to correct handler
2. Milvus vector search → retrieve relevant legal articles
3. Context assembly (history + RAG + system prompt)
4. LLM streaming response via SSE
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.core.constants import DEFAULT_TENANT_ID
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.models.user import User
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    MessageResponse,
    TTSRequest,
    VoiceChatResponse,
)
from app.services.llm_service import get_raw_llm_service
from app.services.content_watermark import get_content_watermark_service
from app.agents.colloquial_rewrite_agent import get_colloquial_rewrite_agent
from app.services.context_manager import ContextManager
from app.prompts.legal_prompts import (
    LEGAL_CONSULT_SYSTEM_PROMPT,
    CONTRACT_REVIEW_SYSTEM_PROMPT,
    DOCUMENT_GEN_SYSTEM_PROMPT,
    LAW_RETRIEVAL_SYSTEM_PROMPT,
    LEGAL_DISCLAIMER,
    DISCLAIMER_PREFIX,
)
from app.middleware.content_safety import get_content_safety_filter
from app.services.citation_verifier import (
    build_citation_gate,
    build_unverified_note,
    summarize_verification,
    verify_citations,
)

import uuid
import tempfile

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# POST /chat/upload  -- Upload & auto-parse document files
# ============================================================================

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/upload")
async def chat_upload_file(
    file: UploadFile = File(...),
    current_user: Annotated[User, Depends(get_current_user)] = None,
):
    """Upload a document file and auto-extract its text content.

    Supports: PDF, DOCX, DOC, TXT, Markdown.
    Returns: { file_id, filename, file_size, extracted_text, char_count, parse_success }
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate extension
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {suffix}。支持: PDF, DOCX, DOC, TXT, Markdown",
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件超过50MB大小限制")

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # Save to temp file for parsing
    file_id = str(uuid.uuid4())
    upload_dir = Path(os.environ.get("UPLOAD_DIR", "./uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = upload_dir / f"{file_id}{suffix}"

    try:
        saved_path.write_bytes(content)

        # Parse the document using DocumentProcessor
        from app.rag.document_processor import DocumentProcessor
        processor = DocumentProcessor(chunk_size=2000, chunk_overlap=200)
        result = await processor.parse_file(str(saved_path))
        extracted_text = result["text"]

        logger.info(
            "File uploaded & parsed: %s (%d chars, %d chunks)",
            file.filename, len(extracted_text), len(result.get("chunks", [])),
        )

        return {
            "file_id": file_id,
            "filename": file.filename,
            "file_size": len(content),
            "file_type": suffix,
            "extracted_text": extracted_text,
            "char_count": len(extracted_text),
            "chunk_count": len(result.get("chunks", [])),
            "parse_success": True,
        }

    except Exception as exc:
        logger.error("File parse failed for '%s': %s", file.filename, exc)
        # Return error but don't fail — frontend can still show the file
        return {
            "file_id": file_id,
            "filename": file.filename,
            "file_size": len(content),
            "file_type": suffix,
            "extracted_text": "",
            "char_count": 0,
            "chunk_count": 0,
            "parse_success": False,
            "parse_error": str(exc),
        }
    finally:
        # Clean up temp file
        if saved_path.exists():
            saved_path.unlink(missing_ok=True)


# ============================================================================
# Colloquial normalization (口语/方言 → 专业法律表述)
# ============================================================================

async def _normalize_message(message: str, auto_normalize: bool = True) -> tuple[str, dict[str, Any]]:
    """Convert colloquial/dialect phrasing into professional legal language.

    Returns (effective_message, normalization_metadata). Falls back to the
    original message whenever the rewriter is unavailable, so chat never
    breaks because of normalization.
    """
    if not auto_normalize:
        return message, {"enabled": False}
    try:
        rewrite = await get_colloquial_rewrite_agent().run_async({"text": message})
    except Exception as exc:
        logger.warning("Colloquial normalization failed (using original): %s", exc)
        return message, {"enabled": True, "error": str(exc)}

    normalized = rewrite.get("normalized_text") or message
    meta = {
        "enabled": True,
        "is_colloquial": rewrite.get("is_colloquial", False),
        "legal_domain": rewrite.get("legal_domain", ""),
        "key_issues": rewrite.get("key_issues", []),
        "legal_terms": rewrite.get("legal_terms", {}),
        "confidence": rewrite.get("confidence", 0.0),
    }
    if normalized != message:
        meta["original_message"] = message
    return normalized, meta


@router.post("/chat/normalize")
async def normalize_message(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Standalone endpoint: rewrite colloquial/dialect text into professional legal language.

    Body: {"text": "老板三个月没发工资了咋办", "dialect_hint": "北方口语"(optional)}
    """
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 5000:
        raise HTTPException(status_code=400, detail="text too long (max 5000 chars)")

    rewrite = await get_colloquial_rewrite_agent().run_async({"text": text})
    return {
        "original_text": text,
        "normalized_text": rewrite.get("normalized_text", text),
        "is_colloquial": rewrite.get("is_colloquial", False),
        "legal_domain": rewrite.get("legal_domain", ""),
        "key_issues": rewrite.get("key_issues", []),
        "legal_terms": rewrite.get("legal_terms", {}),
        "confidence": rewrite.get("confidence", 0.0),
    }


# ============================================================================
# Intent Classification
# ============================================================================

# Fast keyword-based intent detection (no LLM call needed for obvious cases)
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "contract_review": [
        "审查合同", "看一下这个合同", "帮我审查", "合同审查", "审查一下",
        "看看合同", "合同有没有问题", "合同风险", "分析合同",
    ],
    "document_generation": [
        "帮我写", "起草", "生成", "写一份", "帮我起草", "撰写",
        "起诉状", "答辩状", "律师函", "法律意见书", "仲裁申请书",
        "写个", "拟一份",
    ],
    "law_retrieval": [
        "查一下法律", "检索法律", "搜索法律", "有哪些法律", "法条",
        "法律规定", "相关法规", "法律条文", "哪条法律",
    ],
    "deep_think": [
        "深度分析", "深入分析", "详细分析", "帮我推理", "深度思考",
        "全面分析", "逐步分析", "仔细分析",
    ],
    "tool_call": [
        "查企业", "查公司", "工商信息", "企业信息", "查一下这家公司",
        "计算诉讼", "计算赔偿", "计算时效", "诉讼时效",
        "最新法规", "法规更新", "联网搜索", "网上搜",
        "查一下企业", "查一下公司", "企业信息查询", "公司背景",
        "统一社会信用代码", "查这个公司", "查这个企业",
    ],
}

# Fallback intent patterns for legal consultation
_LEGAL_KEYWORDS = [
    "劳动", "合同", "工资", "辞职", "开除", "赔偿", "补偿", "社保", "工伤",
    "离婚", "财产", "抚养", "继承", "遗产", "婚姻", "家暴",
    "借款", "欠款", "还款", "利息", "债务", "借条",
    "侵权", "损害", "赔偿", "过错", "责任",
    "盗窃", "诈骗", "故意", "过失", "犯罪", "判刑",
    "起诉", "仲裁", "诉讼", "管辖", "法院",
    "知识产权", "专利", "商标", "著作权",
    "租房", "租赁", "买卖", "转让", "过户",
    "公司", "股权", "股东", "注册", "注销",
    "消费", "退货", "维权", "投诉",
    "交通", "事故", "保险", "理赔",
]


def classify_intent(query: str) -> str:
    """Classify user intent using keyword matching.

    Returns one of: legal_consultation, contract_review, document_generation,
    law_retrieval, deep_think, tool_call, general
    """
    query_stripped = query.strip()

    # 0. Check for deep thinking intent
    for kw in _INTENT_KEYWORDS.get("deep_think", []):
        if kw in query_stripped:
            return "deep_think"

    # 0b. Check for tool calling intent (keywords or patterns)
    for kw in _INTENT_KEYWORDS.get("tool_call", []):
        if kw in query_stripped:
            return "tool_call"

    # 0c. Smart pattern: "查" + company/enterprise related words
    has_query_verb = any(v in query_stripped for v in ["查", "查询", "搜索"])
    has_entity = any(e in query_stripped for e in ["公司", "企业", "工厂", "集团", "工作室"])
    has_calc = any(c in query_stripped for c in ["计算", "算一下", "多少钱", "费用"])
    if has_query_verb and has_entity:
        return "tool_call"
    if has_calc and any(t in query_stripped for t in ["赔偿", "诉讼", "利息", "补偿", "时效"]):
        return "tool_call"

    # 1. Check for contract review intent
    for kw in _INTENT_KEYWORDS["contract_review"]:
        if kw in query_stripped:
            return "contract_review"

    # 2. Check for document generation intent
    for kw in _INTENT_KEYWORDS["document_generation"]:
        if kw in query_stripped:
            return "document_generation"

    # 3. Check for law retrieval intent
    for kw in _INTENT_KEYWORDS["law_retrieval"]:
        if kw in query_stripped:
            return "law_retrieval"

    # 4. Check if it's a legal question
    for kw in _LEGAL_KEYWORDS:
        if kw in query_stripped:
            return "legal_consultation"

    # 5. Check for question patterns
    question_patterns = ["吗", "呢", "？", "?", "怎么", "如何", "什么", "哪些", "为什么", "是否", "能不能", "可以吗"]
    for p in question_patterns:
        if p in query_stripped:
            return "legal_consultation"

    # 6. Default: if it looks like a general question
    if len(query_stripped) > 5:
        return "legal_consultation"  # Default to legal consultation

    return "general"


def build_system_prompt_for_intent(intent: str, rag_context: str = "") -> str:
    """Build the appropriate system prompt based on classified intent."""
    if intent == "contract_review":
        base = CONTRACT_REVIEW_SYSTEM_PROMPT
    elif intent == "document_generation":
        base = DOCUMENT_GEN_SYSTEM_PROMPT
    elif intent == "law_retrieval":
        base = LAW_RETRIEVAL_SYSTEM_PROMPT
    else:
        base = LEGAL_CONSULT_SYSTEM_PROMPT

    # Append RAG context if available
    if rag_context:
        base += (
            "\n\n## 法律知识库检索结果（来自Milvus向量数据库）\n"
            "以下是与用户问题最相关的法律条文。\n"
            "【重要】在回答中你必须明确引用这些法条，"
            '格式为"根据《法律名称》第X条规定：..."\n'
            "如果检索结果与问题无关，则说明未检索到直接相关的法律条文。\n\n"
            f"{rag_context}"
        )

    return base


# ============================================================================
# RAG: BM25 + Milvus vector + Cross-Encoder reranking (Advanced RAG)
# ============================================================================

# Common Chinese stopwords filtered out of fallback keyword extraction
_KEYWORD_STOPWORDS = {
    "的", "了", "吗", "呢", "啊", "是", "在", "我", "有", "和", "与", "及", "或",
    "可以", "能", "要", "被", "把", "对", "吧", "想", "请", "需要", "怎么办",
    "咋办", "怎么", "请问", "咨询", "一下", "什么", "哪些", "为啥", "为什么",
    "如果", "但是", "还是", "这个", "那个", "有没有", "自己", "他们", "我们",
}


def _extract_keywords(query: str) -> list[str]:
    """Extract candidate keywords from a Chinese query (n-gram based).

    Used by the in-memory fallback retriever; Chinese has no whitespace
    tokenization, so 2-4 character grams are generated per punctuation-free
    segment and deduplicated in order.
    """
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", query or "")
    keywords: list[str] = []
    for segment in text.split():
        if not segment:
            continue
        if re.fullmatch(r"[A-Za-z0-9]+", segment):
            if len(segment) >= 2:
                keywords.append(segment.lower())
            continue
        for n in (4, 3, 2):
            for i in range(len(segment) - n + 1):
                gram = segment[i:i + n]
                if gram not in _KEYWORD_STOPWORDS:
                    keywords.append(gram)
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:15]


def retrieve_legal_knowledge(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Advanced RAG retrieval: BM25 keyword + Milvus vector + Cross-Encoder reranking.

    Pipeline:
    1. BM25 keyword search (Chinese legal optimized)
    2. Milvus vector similarity search (text2vec-base-chinese)
    3. Reciprocal Rank Fusion (RRF) merge
    4. Cross-Encoder reranking (ms-marco-MiniLM-L-6-v2)

    Fallback to in-memory keyword search if Milvus is unavailable.
    """
    try:
        from app.rag.advanced_retriever import get_rag_pipeline
        pipeline = get_rag_pipeline()
        results = pipeline.retrieve(
            query=query,
            top_k=top_k,
            use_bm25=True,
            use_vector=True,
            use_reranker=True,
        )
        if results:
            logger.info("Advanced RAG: %d results for '%s'", len(results), query[:30])
            return results
    except Exception as exc:
        logger.warning("Advanced RAG pipeline failed: %s, falling back to keyword", exc)

    # Fallback: in-memory keyword search
    from app.rag.milvus_service import SEED_LEGAL_ARTICLES
    query_lower = query.lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for article in SEED_LEGAL_ARTICLES:
        score = 0.0
        tags = article.get("tags", "")
        content = article.get("content", "")
        for tag in tags.split(","):
            tag = tag.strip()
            if tag and tag in query_lower:
                score += 3.0
        for keyword in _extract_keywords(query):
            if keyword in content:
                score += 2.0
        if score > 0:
            scored.append((score, {**article, "score": score / 10.0, "source": "memory"}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def format_rag_context(retrieved: list[dict[str, Any]]) -> str:
    """Format retrieved articles into context string for LLM.

    Shows scores from all retrieval stages (BM25, vector, reranker).
    """
    if not retrieved:
        return ""
    parts = []
    for i, art in enumerate(retrieved, 1):
        law = art.get("law_name", "")
        num = art.get("article_number", "")
        content = art.get("content", "")
        # Show available scores
        score_info = []
        if "rerank_score" in art:
            score_info.append(f"重排序:{art['rerank_score']:.3f}")
        if "rrf_score" in art:
            score_info.append(f"RRF:{art['rrf_score']:.4f}")
        if "bm25_score" in art:
            score_info.append(f"BM25:{art['bm25_score']:.2f}")
        if "score" in art and "rerank_score" not in art:
            score_info.append(f"向量:{art['score']:.3f}")
        score_str = " | ".join(score_info) if score_info else "相关"
        parts.append(f"【法条{i}】《{law}》{num}（{score_str}）\n{content}")
    return "\n\n".join(parts)


# ============================================================================
# Conversation context helpers
# ============================================================================

async def _get_conversation_history(
    db: AsyncSession, conversation_id: str, max_turns: int = 10,
) -> list[dict[str, str]]:
    """Load recent conversation history for multi-turn context.

    DEPRECATED: Use ContextManager for production. This is kept for backward compatibility.
    """
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(max_turns * 2)
    )
    messages = list(reversed(result.scalars().all()))
    history = []
    for msg in messages:
        role = "user" if msg.role == MessageRole.USER else "assistant"
        content = msg.content[:2000] if len(msg.content) > 2000 else msg.content
        history.append({"role": role, "content": content})
    return history


async def _build_context_with_manager(
    db: AsyncSession,
    conversation_id: str,
    current_message: str,
    rag_context: str = "",
    user_memory_text: str = "",
) -> list[dict[str, str]]:
    """
    Build context using the new ContextManager (Phase 2).

    This replaces the simple _get_conversation_history with intelligent
    three-layer context management:
    1. Short-term: Recent 10 turns (sliding window)
    2. Long-term: Conversation summary (auto-compressed)
    3. Working: RAG-retrieved legal context

    Args:
        db: Database session
        conversation_id: Conversation UUID
        current_message: Current user message
        rag_context: RAG-retrieved legal context

    Returns:
        List of message dicts for LLM
    """
    from app.services.context_manager import build_chat_context

    # P1-5：注入跨会话长期画像（新会话首轮同样生效）
    if not user_memory_text:
        try:
            from app.services.long_term_memory import load_memory_prompt

            owner_id = (
                await db.execute(
                    select(Conversation.user_id).where(Conversation.id == conversation_id)
                )
            ).scalar_one_or_none()
            if owner_id:
                user_memory_text = await load_memory_prompt(db, owner_id)
        except Exception as exc:  # noqa: BLE001 - 记忆注入失败不影响主链路
            logger.debug("长期记忆注入跳过: %s", exc)

    try:
        messages = await build_chat_context(
            db=db,
            conversation_id=conversation_id,
            current_message=current_message,
            rag_context=rag_context,
            user_memory_text=user_memory_text,
        )
        logger.info(
            f"ContextManager: Built context for conv={conversation_id[:8]}, "
            f"messages={len(messages)}"
        )
        return messages
    except Exception as e:
        logger.error(f"ContextManager failed, falling back to simple history: {e}")
        # Fallback to old method
        history = await _get_conversation_history(db, conversation_id, max_turns=8)
        messages = [{"role": "system", "content": rag_context}] if rag_context else []
        messages.extend(history)
        messages.append({"role": "user", "content": current_message})
        return messages


# ============================================================================
# Follow-up suggestions (上下文关联的继续追问)
# ============================================================================

FOLLOW_UP_SYSTEM_PROMPT = (
    "你是一名专业的法律对话助手。请根据用户与法律AI助手的多轮对话，"
    "生成3个最可能让用户继续追问的问题，帮助用户深入理解其法律问题。\n"
    "要求：\n"
    "1. 每个问题必须与对话中的法律问题紧密关联，聚焦尚未充分展开的要点"
    "（如证据、程序、时效、风险、救济途径、赔偿计算等）。\n"
    "2. 问题需口语化、自然、可直接点击追问，每个不超过25个字。\n"
    "3. 严格只输出一个JSON数组，不要输出任何解释、编号或多余文字，格式："
    '["问题1","问题2","问题3"]'
)


def _parse_followups(content: str) -> list[str]:
    """Parse LLM follow-up output into a clean list of ≤3 questions."""
    if not content:
        return []
    text = content.strip()

    # 1. Try to parse as a JSON array (possibly fenced in ```json blocks)
    candidates = [text]
    stripped = text.lstrip("`")
    if stripped != text:
        once = stripped
        idx = once.lower().find("json")
        if idx != -1:
            once = once[idx + 4:]
        once = once.rstrip("`").strip()
        if once:
            candidates.append(once)
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, list):
                items = [str(x).strip() for x in data if str(x).strip()]
                return items[:3]
        except Exception:
            continue

    # 2. Fallback: split by line and strip numbering/bullets
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lstrip("0123456789.、-·*# ").strip()
        if not line:
            continue
        if line.startswith(("```", "对话历史", "请输出", "生成", "以下为")):
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out[:3]


async def _generate_follow_up_questions(history: list[dict[str, str]]) -> list[str]:
    """Generate 3 context-aware follow-up questions from recent conversation history."""
    if not history:
        return []
    conv_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}" for m in history
    )
    llm_service = get_raw_llm_service()
    try:
        result = await asyncio.wait_for(
            llm_service.chat(
                messages=[
                    {"role": "system", "content": FOLLOW_UP_SYSTEM_PROMPT},
                    {"role": "user", "content": f"对话历史：\n{conv_text}\n\n请生成3个继续追问的问题。"},
                ],
                temperature=0.5,
                max_tokens=300,
            ),
            timeout=20.0,
        )
        return _parse_followups(result.get("content", ""))
    except Exception as exc:
        logger.warning("Follow-up suggestion generation failed: %s", exc)
        return []


@router.post("/follow-ups")
async def chat_follow_ups(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Generate 3 context-aware follow-up questions for multi-turn conversations.

    Body: {"conversation_id": "..."}
    Returns: {"conversation_id": "...", "suggestions": ["问题1", "问题2", "问题3"]}
    """
    conversation_id = str(payload.get("conversation_id", "") or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history = await _get_conversation_history(db, conversation_id, max_turns=4)
    suggestions = await _generate_follow_up_questions(history)
    return {"conversation_id": conversation_id, "suggestions": suggestions}


# ============================================================================
# Background conversation summary / fact-sheet updater
# ============================================================================

async def _background_update_conversation_context(
    conversation_id: str,
    latest_user_message: str,
) -> None:
    """Background task: update conversation summary and fact sheet after a response.

    Uses its own DB session (independent of the request-scoped session) so it
    can safely run after the streaming response completes.  Failures are logged
    but never raised -- this is a best-effort enrichment.
    """
    from app.core.database import async_session_factory

    try:
        async with async_session_factory() as session:
            # Load the conversation in this fresh session
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if not conversation:
                logger.warning(
                    "Background context update: conversation %s not found",
                    conversation_id[:8],
                )
                return

            manager = ContextManager(session)

            # 1. Update fact sheet (extract structured legal facts)
            try:
                fact_sheet = await manager._get_or_update_fact_sheet(
                    conversation, latest_user_message
                )
                conversation.fact_sheet = fact_sheet.to_dict()
                logger.info(
                    "Background context: fact sheet updated for conv=%s "
                    "(parties=%d, disputes=%d, laws=%d)",
                    conversation_id[:8],
                    len(fact_sheet.parties),
                    len(fact_sheet.dispute_focus),
                    len(fact_sheet.cited_laws),
                )
            except Exception as exc:
                logger.warning(
                    "Background context: fact sheet update failed for conv=%s: %s",
                    conversation_id[:8], exc,
                )

            # 1.5 P1-5：把会话事实档案合并进跨会话长期记忆
            try:
                from app.services.long_term_memory import update_memory_from_conversation

                await update_memory_from_conversation(session, conversation)
            except Exception as exc:
                logger.warning(
                    "Background context: long-term memory update failed for conv=%s: %s",
                    conversation_id[:8], exc,
                )

            # 2. Update conversation summary (incremental if one exists)
            message_count = await manager._get_message_count(conversation_id)
            if message_count >= 2:
                try:
                    # Load recent messages for incremental summarization
                    recent = await manager._get_recent_messages(
                        conversation_id, max_turns=10
                    )
                    if conversation.summary:
                        # Incremental update: merge existing summary with new turns
                        new_summary = await manager._incremental_update(
                            conversation, recent
                        )
                    else:
                        # First-time summary: compress all messages
                        summary_result = await session.execute(
                            select(Message)
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.created_at.desc())
                            .limit(30)
                        )
                        old_messages = list(reversed(summary_result.scalars().all()))
                        if old_messages:
                            new_summary = await manager._generate_summary(old_messages)
                        else:
                            new_summary = None

                    if new_summary:
                        conversation.summary = new_summary
                        logger.info(
                            "Background context: summary updated for conv=%s (%d chars)",
                            conversation_id[:8], len(new_summary),
                        )
                except Exception as exc:
                    logger.warning(
                        "Background context: summary update failed for conv=%s: %s",
                        conversation_id[:8], exc,
                    )

            # 3. Detect topic shift
            try:
                await manager._detect_topic_shift(conversation, latest_user_message)
            except Exception as exc:
                logger.warning(
                    "Background context: topic shift detection failed for conv=%s: %s",
                    conversation_id[:8], exc,
                )

            await session.commit()
            logger.info(
                "Background context update completed for conv=%s",
                conversation_id[:8],
            )

    except Exception as exc:
        logger.exception(
            "Background context update failed for conv=%s: %s",
            conversation_id[:8] if conversation_id else "unknown", exc,
        )


# ============================================================================
# POST /chat/stream  -- SSE streaming with intent recognition + Milvus RAG
# ============================================================================

def _may_need_tools(query: str) -> bool:
    """Cheap pre-check gating whether to spend an LLM call on tool selection.

    Deliberately broad. The keyword classifier is too brittle to gate on alone:
    it lists "诉讼费用" but not "诉讼费", and its calc pattern needs
    计算/算一下/多少钱/费用 next to 赔偿/诉讼/利息 — so "标的额50万的民事诉讼，
    诉讼费要交多少？" scored as a plain consultation and the calculator was never
    offered. Anticipating every phrasing in a keyword list is the very problem
    LLM-driven selection exists to solve, so this gate only filters out queries
    with no sign of needing external facts, and lets the selection LLM make the
    real call (it is instructed to return an empty list when tools add nothing).
    """
    q = (query or "").strip()
    if not q:
        return False

    # The keyword classifier's own verdict still counts as a positive signal
    if classify_intent(q) == "tool_call":
        return True

    # Digits usually mean a concrete amount, duration, or count to compute over
    if any(ch.isdigit() for ch in q):
        return True

    # Lookup / retrieval verbs
    if any(v in q for v in ("查", "检索", "搜索", "查询")):
        return True

    # Quantity questions
    if any(v in q for v in ("多少", "几年", "几个月", "多长时间", "费用", "标准")):
        return True

    # Named-entity markers that suggest a specific organisation or document
    if any(v in q for v in ("公司", "企业", "案号", "判决书", "裁定书")):
        return True

    # An explicit article citation ("第四十七条", "第1079条"). Chinese numerals mean
    # the isdigit() check above misses these, and "原文是什么" has no lookup verb,
    # so without this the exact-citation case never reaches tool selection — and
    # semantic RAG alone tends to return neighbouring articles instead of the one
    # named, leaving the model to recite the text from memory.
    if re.search(r"第[0-9〇零一二三四五六七八九十百千]+条", q):
        return True

    return False


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Stream an AI response with intent recognition and Milvus RAG."""

    # Content safety check
    safety_filter = get_content_safety_filter()
    is_safe, safety_reason = safety_filter.check_input(payload.message)
    if not is_safe:
        # Return a polite refusal message
        refusal_content = f"抱歉，{safety_reason or '您的输入未通过内容安全检查'}。请重新表述您的问题。"

        async def refusal_generator():
            meta = {
                "conversation_id": None,
                "intent": "blocked",
                "citations": [],
                "content_filtered": True,
            }
            yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'token', 'content': refusal_content}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': refusal_content}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            refusal_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # Sanitize the prompt
    sanitized_message = safety_filter.sanitize_prompt(payload.message)

    # Colloquial → professional legal normalization (简历需求 #6)
    normalized_message, norm_meta = await _normalize_message(
        sanitized_message, payload.auto_normalize
    )
    if normalized_message:
        sanitized_message = normalized_message

    # ── Inject file content into the user message ──
    file_context = ""
    if payload.files:
        file_parts = []
        for f in payload.files:
            if f.content:
                file_parts.append(f"### 附件: {f.filename}\n{f.content[:8000]}")
            elif f.filename:
                file_parts.append(f"[附件: {f.filename} (内容无法提取)]")
        if file_parts:
            file_context = "\n\n---\n## 用户上传的附件内容\n" + "\n\n".join(file_parts)
            sanitized_message = sanitized_message + file_context

    # ── Override intent based on enabled features ──
    force_deep_think = bool(payload.enable_deep_think)
    force_web_search = bool(payload.enable_web_search)
    force_multi_agent = bool(payload.enable_multi_agent)

    # ── Web Search — inject real-time search results ──
    web_search_context = ""
    web_search_sources = []
    if force_web_search:
        try:
            from app.services.web_search import get_web_search_engine
            engine = get_web_search_engine()
            search_result = await engine.search(payload.message or sanitized_message, num_results=5)
            if search_result and search_result.results:
                search_parts = []
                for r in search_result.results[:5]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    url = r.get("url", "")
                    search_parts.append(f"- **{title}**: {snippet}\n  来源: {url}")
                    web_search_sources.append({
                        "law_name": title[:50] if title else "联网搜索",
                        "article_number": "",
                        "content": snippet[:200] if snippet else "",
                        "score": 0.5,
                        "url": url,
                        "source_type": "web_search",
                    })
                web_search_context = "\n\n## 联网搜索结果（以下是从互联网实时检索到的相关法律信息，请结合使用）\n" + "\n".join(search_parts)
                logger.info("Web search returned %d results for: '%s'", len(search_result.results), payload.message[:50])
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)

    # Gate on the ORIGINAL message, not `sanitized_message`. By this point the
    # colloquial-normalization agent has rewritten the text into formal legal
    # language, which strips the cues this check keys on: "请计算我应得的赔偿金"
    # becomes a formal phrasing with no "计算" trigger, so a query that plainly
    # needs the calculator scored as a plain consultation and no tool ran.
    intent_hint_wants_tools = _may_need_tools(payload.message or sanitized_message)

    # ── MCP Tools — LLM-driven selection + parallel execution (Phase 3) ──
    #
    # The LLM decides which tools to call and with what arguments, instead of
    # keyword-substring matching. When the user has explicitly picked tools in
    # the UI, selection is restricted to those; otherwise the whole catalogue is
    # offered and the model may also choose to call nothing.
    mcp_tool_context = ""
    mcp_tool_meta: dict[str, Any] = {}
    want_tools = bool(payload.mcp_tools) or intent_hint_wants_tools
    if want_tools:
        try:
            from app.services.tool_orchestrator import orchestrate
            orchestration = await orchestrate(
                query=payload.message or sanitized_message,
                allowed_tools=payload.mcp_tools or None,
            )
            mcp_tool_context = orchestration["context"]
            mcp_tool_meta = orchestration["metadata"]
            logger.info(
                "MCP orchestration: selected=%d succeeded=%d failed=%d (%.2fs)",
                mcp_tool_meta.get("tools_selected", 0),
                mcp_tool_meta.get("tools_succeeded", 0),
                mcp_tool_meta.get("tools_failed", 0),
                mcp_tool_meta.get("elapsed_seconds", 0),
            )
        except Exception as exc:
            logger.warning("MCP orchestration failed: %s", exc)

    # ── Skill pack — execute when the user selected one (Phase 3) ──
    #
    # `skill_id` was previously accepted by the request schema and silently
    # ignored; the frontend's skill picker had no backend effect.
    skill_context = ""
    skill_meta: dict[str, Any] = {}
    if payload.skill_id:
        try:
            from app.services.tool_orchestrator import run_skill_for_chat
            skill_outcome = await run_skill_for_chat(
                skill_id=payload.skill_id,
                query=payload.message or sanitized_message,
            )
            skill_context = skill_outcome["context"]
            skill_meta = skill_outcome["metadata"]
            logger.info("Skill '%s' for chat: %s", payload.skill_id, skill_meta)
        except Exception as exc:
            logger.warning("Skill execution failed: %s", exc)

    if payload.conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conversation = Conversation(
            user_id=current_user.id,
            tenant_id=getattr(current_user, "tenant_id", None) or DEFAULT_TENANT_ID,
            title=payload.message[:100],
            agent_type=payload.agent_type,
        )
        db.add(conversation)
        await db.flush()

    # 2. Save user message
    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=payload.message,  # Save original user message
    )
    db.add(user_message)
    await db.flush()

    # Commit conversation + user message before expensive operations (web search, deep think)
    # to avoid DB connection timeout during long-running LLM/search calls
    await db.commit()

    # 3. Classify intent (use sanitized message)
    intent = classify_intent(sanitized_message)

    # 4. Check semantic cache first (skip cache when deep_think/web_search/files are enabled)
    from app.services.semantic_cache import get_semantic_cache
    cache = get_semantic_cache()
    use_cache = not (force_deep_think or force_web_search or force_multi_agent or payload.files or payload.mcp_tools)
    cached_result = await cache.get(sanitized_message) if use_cache else None

    if cached_result:
        # Cache hit — stream cached response directly, no LLM call needed
        logger.info("Semantic cache HIT for: '%s' (sim=%.4f)",
                     payload.message[:30], cached_result.get("similarity", 0))

        # Snapshot identity as plain values — see note in event_generator below.
        cached_conv_id = str(conversation.id)
        cached_needs_title = conversation.title == "New Conversation"

        async def cached_event_generator():
            from app.core.database import async_session_factory

            cached_content = cached_result["content"]
            meta = {
                "conversation_id": cached_conv_id,
                "intent": intent,
                "citations": [],
                "cached": True,
                "ai_generated": True,
            }
            yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"
            # Stream cached content in chunks (simulates real streaming)
            chunk_size = 10
            for i in range(0, len(cached_content), chunk_size):
                chunk = cached_content[i:i+chunk_size]
                yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': cached_content}, ensure_ascii=False)}\n\n"

            # Save to DB using a fresh session that outlives the request scope
            try:
                async with async_session_factory() as write_session:
                    write_session.add(Message(
                        conversation_id=cached_conv_id, role=MessageRole.ASSISTANT,
                        content=cached_content, tokens_used=len(cached_content) // 2,
                        metadata_=json.dumps({"intent": intent, "cached": True}, ensure_ascii=False),
                    ))
                    if cached_needs_title:
                        conv_row = await write_session.execute(
                            select(Conversation).where(Conversation.id == cached_conv_id)
                        )
                        conv_obj = conv_row.scalar_one_or_none()
                        if conv_obj is not None and conv_obj.title == "New Conversation":
                            conv_obj.title = payload.message[:100]
                    await write_session.commit()
            except Exception as exc:
                logger.exception("Failed to persist cached assistant message: %s", exc)

        return StreamingResponse(
            cached_event_generator(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # 5. Cache miss — full RAG + LLM pipeline
    retrieved = retrieve_legal_knowledge(sanitized_message, top_k=5)
    rag_context = format_rag_context(retrieved)

    # 5a. MCP tool results were produced earlier by the LLM-driven orchestrator.

    # 5b. Deep Thinking — use IRAC reasoning when explicitly enabled or intent matches
    deep_think_context = ""
    deep_think_steps = []
    if force_deep_think or intent == "deep_think":
        try:
            from app.agents.deep_thinking_agent import create_deep_thinking_agent
            thinking_agent = create_deep_thinking_agent()
            thinking_result = await thinking_agent.think(
                query=sanitized_message, rag_context=rag_context,
            )
            deep_think_context = thinking_result.final_answer
            if hasattr(thinking_result, 'reasoning_steps'):
                deep_think_steps = [
                    {"step_type": s.step_type, "title": s.title, "content": s.content[:500]}
                    for s in thinking_result.reasoning_steps
                ] if thinking_result.reasoning_steps else []
            if deep_think_context:
                rag_context = rag_context + "\n\n## 深度推理分析（IRAC框架）\n" + deep_think_context
            logger.info("Deep thinking completed for: '%s' (%d steps)", payload.message[:50], len(deep_think_steps))
        except Exception as exc:
            logger.warning("Deep thinking agent failed (%s), falling back to IRAC-enhanced prompt", exc)
            # Fallback: inject IRAC framework instructions into the prompt
            deep_think_context = ""
            deep_think_steps = [
                {"step_type": "issue", "title": "争点识别", "content": f"本案核心法律问题：{sanitized_message[:200]}"},
                {"step_type": "rule", "title": "规则检索", "content": "已检索相关法律法规（见引用来源）"},
                {"step_type": "application", "title": "法律适用", "content": "请结合上述法条与案件事实进行分析"},
                {"step_type": "conclusion", "title": "结论", "content": "请给出明确的法律意见"},
            ]
            # Add IRAC instructions to system prompt
            irac_instruction = (
                "\n\n## 深度推理要求（IRAC框架）\n"
                "请按照以下IRAC法律推理框架逐步分析用户的问题：\n"
                "1. **Issue（争点）**：识别本案的核心法律争议焦点\n"
                "2. **Rule（规则）**：引用相关的法律法规条文\n"
                "3. **Application（适用）**：将法律规则适用于具体案件事实\n"
                "4. **Conclusion（结论）**：给出明确的法律意见和建议\n"
                "请确保每个步骤都有法律依据，推理过程清晰、逻辑严密。\n"
            )
            rag_context = rag_context + irac_instruction

    # 5c. Multi-Agent Collaboration (P1.6) — fan out to specialized agents,
    # then feed the synthesized report into the main streaming pipeline so
    # citations / watermark / safety checks all stay on the primary path.
    multi_agent_meta: dict[str, Any] = {}
    if force_multi_agent:
        try:
            from app.agents.collaboration import create_multi_agent_collaborator
            collaborator = create_multi_agent_collaborator()
            collab_result = await collaborator.run(
                query=sanitized_message, context={},
            )
            multi_agent_meta = {
                "pattern": collab_result.get("collaboration_pattern", ""),
                "agents": collab_result.get("agents_involved", []),
                "agent_details": collab_result.get("agent_details", []),
                "iterations": collab_result.get("iterations", 0),
            }
            report = collab_result.get("final_response", "")
            # The collaborator appends its own disclaimer; strip it to avoid
            # duplication — the chat pipeline adds the canonical one.
            disclaimer_pos = report.find("【法律声明】")
            if disclaimer_pos > 0:
                report = report[:disclaimer_pos].rstrip().rstrip("-*# \n")
            if report:
                rag_context = rag_context + "\n\n## 多智能体协作分析报告（已由多个专业代理并行分析并综合）\n" + report
            logger.info(
                "Multi-agent collaboration completed for: '%s' (pattern=%s agents=%s)",
                payload.message[:50],
                multi_agent_meta.get("pattern"),
                multi_agent_meta.get("agents"),
            )
        except Exception as exc:
            logger.warning("Multi-agent collaboration failed (%s), continuing with standard pipeline", exc)

    # ── Inject web search + MCP tool + skill pack context into RAG context ──
    if web_search_context:
        rag_context = rag_context + web_search_context
    if mcp_tool_context:
        rag_context = rag_context + mcp_tool_context
    if skill_context:
        rag_context = rag_context + skill_context

    # 5. Build system prompt based on intent
    system_prompt = build_system_prompt_for_intent(intent, rag_context)

    # 6. Build context using ContextManager (Phase 2) or fallback to simple history
    # Use ContextManager for intelligent multi-turn context management
    messages_for_llm = await _build_context_with_manager(
        db=db,
        conversation_id=conversation.id,
        current_message=sanitized_message,
        rag_context=system_prompt,
    )

    # 8. Stream response
    llm_service = get_raw_llm_service()
    watermark_service = get_content_watermark_service()

    # Build watermark metadata for this response
    watermark_meta = watermark_service.get_watermark_metadata(
        model="deepseek-chat",
        user_id=str(current_user.id),
        content_type="chat",
    )

    # Snapshot the conversation identity as plain values BEFORE the generator runs.
    # The `conversation` ORM object is bound to the request-scoped session, which
    # FastAPI closes once the StreamingResponse is returned; touching its
    # attributes inside the generator can raise DetachedInstanceError or trigger
    # a lazy refresh on a dead connection.
    conversation_id_str = str(conversation.id)
    conversation_user_id = str(conversation.user_id)
    conversation_tenant_id = getattr(conversation, "tenant_id", None) or DEFAULT_TENANT_ID
    conversation_needs_title = conversation.title == "New Conversation"

    async def _persist_assistant_message(
        content: str,
        was_filtered: bool = False,
        citation_result: dict | None = None,
    ) -> None:
        """Write the assistant message using a session of its own.

        Uses a fresh session rather than the request-scoped `db`, because FastAPI
        closes the `get_db` dependency as soon as the StreamingResponse is returned
        while this generator is still producing output. `was_filtered` and
        `citation_result` are passed in rather than closed over, since they are
        locals of the generator, not of this enclosing scope.
        """
        from app.core.database import async_session_factory

        saved_citations = []
        for r in retrieved[:5]:
            saved_citations.append({
                "law_name": r.get("law_name", ""),
                "article_number": r.get("article_number", ""),
                "content": r.get("content", ""),
                "relevance_score": r.get("score", 0),
                "source_type": "knowledge_base",
            })
        for ws in web_search_sources:
            saved_citations.append({
                "law_name": ws.get("law_name", ""),
                "article_number": ws.get("article_number", ""),
                "content": ws.get("content", ""),
                "relevance_score": ws.get("score", 0),
                "source_type": "web_search",
                "url": ws.get("url", ""),
            })

        async with async_session_factory() as write_session:
            write_session.add(Message(
                conversation_id=conversation_id_str,
                role=MessageRole.ASSISTANT,
                content=content,
                tokens_used=len(content) // 2,
                metadata_=json.dumps({
                    "intent": intent,
                    "colloquial_normalization": norm_meta,
                    "citations": saved_citations,
                    "content_filtered": was_filtered,
                    "citation_verification": citation_result,
                    "disclaimer": DISCLAIMER_PREFIX,
                    "ai_generated": True,
                    "generation_metadata": watermark_meta,
                    "features_used": {
                        "deep_think": force_deep_think,
                        "web_search": force_web_search,
                        "files": len(payload.files) if payload.files else 0,
                        "mcp_tools": len(payload.mcp_tools) if payload.mcp_tools else 0,
                    },
                    "tool_orchestration": mcp_tool_meta or None,
                    "skill_execution": skill_meta or None,
                }, ensure_ascii=False),
            ))

            if conversation_needs_title:
                conv_row = await write_session.execute(
                    select(Conversation).where(Conversation.id == conversation_id_str)
                )
                conv_obj = conv_row.scalar_one_or_none()
                if conv_obj is not None and conv_obj.title == "New Conversation":
                    conv_obj.title = payload.message[:100]

            # P0-4：把"引用校验结果"写入审计日志，形成客户可查的合规凭证。
            # 与消息落库同事务，保证"有回答即有留痕"。
            write_session.add(AuditLog(
                user_id=conversation_user_id,
                tenant_id=conversation_tenant_id,
                action="ai.citation_verify",
                resource_type="conversation",
                resource_id=conversation_id_str,
                status_code=200,
                request_summary=summarize_verification(citation_result),
            ))

            # P1-5：把本会话沉淀的事实档案合并进用户长期记忆（跨会话延续）
            try:
                from app.services.long_term_memory import update_memory_by_conversation_id

                await update_memory_by_conversation_id(write_session, conversation_id_str)
            except Exception as exc:  # noqa: BLE001 - 记忆失败不影响回答
                logger.warning("长期记忆更新跳过: %s", exc)

            await write_session.commit()
        logger.info("Persisted assistant message for conv=%s", conversation_id_str[:8])

    async def event_generator():
        full_content = ""
        content_filtered = False
        citation_check = None
        saved = False
        try:
            # Build combined citations: RAG + web search
            all_citations = []
            # RAG citations from legal knowledge base
            for r in retrieved[:5]:
                all_citations.append({
                    "law_name": r.get("law_name", ""),
                    "article_number": r.get("article_number", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                    "source_type": "knowledge_base",
                })
            # Web search citations
            for ws in web_search_sources:
                all_citations.append(ws)

            # Deep think steps (if available)
            deep_think_meta = None
            if deep_think_steps:
                deep_think_meta = [
                    {"step_type": s.get("step_type", ""), "title": s.get("title", ""), "content": s.get("content", "")}
                    for s in deep_think_steps
                ] if isinstance(deep_think_steps, list) else None

            # Send metadata first (include watermark info)
            meta = {
                "conversation_id": conversation_id_str,
                "intent": intent,
                "citations": all_citations,
                "content_filtered": False,
                "disclaimer": DISCLAIMER_PREFIX,
                "ai_generated": True,
                "watermark_info": watermark_meta,
                "generation_metadata": watermark_meta,
                "features_used": {
                    "deep_think": force_deep_think,
                    "web_search": force_web_search,
                    "multi_agent": force_multi_agent,
                    "files": len(payload.files) if payload.files else 0,
                    "mcp_tools": len(payload.mcp_tools) if payload.mcp_tools else 0,
                },
            }
            # Phase 3: report what the orchestrator actually did, so the UI can
            # show which tools ran rather than only which were offered.
            if mcp_tool_meta:
                meta["tool_orchestration"] = mcp_tool_meta
            if skill_meta:
                meta["skill_execution"] = skill_meta
            if deep_think_meta:
                meta["deep_think_steps"] = deep_think_meta
            if multi_agent_meta:
                meta["multi_agent"] = multi_agent_meta
            yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"

            # Stream LLM tokens
            async for chunk in llm_service.chat_stream(
                messages=messages_for_llm, temperature=0.7, max_tokens=4096,
            ):
                full_content += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"

            # Post-process: apply content safety filter to the output
            filtered_content = safety_filter.check_output(full_content)
            if filtered_content != full_content:
                content_filtered = True
                full_content = filtered_content

            # Citation existence verification (法条号存在性校验回路, P0-3)
            try:
                citation_check = await verify_citations(db, full_content)
                unverified = citation_check.get("unverified", [])
                if unverified:
                    full_content += build_unverified_note(unverified)
                    logger.warning(
                        "Unverified legal citations detected: %s",
                        [f"《{c.get('law_name')}》第{c.get('article_number_raw')}条" for c in unverified],
                    )
                # 引用闸门：一条都没通过校验时，强制降级、拒绝确定性结论。
                gate = build_citation_gate(citation_check)
                if not gate["allow_definitive"]:
                    full_content += gate["note"]
                    logger.warning(
                        "Citation gate DEGRADED (0 verified of %s) - definitive conclusion withheld",
                        citation_check.get("citation_count", 0),
                    )
            except Exception as exc:
                logger.warning("Citation verification skipped: %s", exc)

            # Apply AI content identification watermarks (人工智能生成合成内容标识办法)
            watermarked_content = watermark_service.add_explicit_watermark(full_content, content_type="chat")
            watermarked_content = watermark_service.add_implicit_watermark(watermarked_content, watermark_meta)

            # Persist BEFORE the final `done` yield.
            #
            # This ordering is load-bearing. BaseHTTPMiddleware wraps the response
            # in an anyio cancel scope and tears it down once the stream completes;
            # the teardown raises CancelledError inside this generator. Because
            # CancelledError derives from BaseException (not Exception), an
            # `except Exception` block never sees it and the write vanishes with no
            # log line. Writing while the generator is still being actively pumped
            # keeps the save inside the live task scope.
            await _persist_assistant_message(
                watermarked_content,
                was_filtered=content_filtered,
                citation_result=citation_check,
            )
            saved = True

            yield f"data: {json.dumps({'type': 'done', 'content': watermarked_content}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            logger.exception("Streaming error: %s", exc)
            fallback = f"抱歉，处理您的问题时出现了错误：{type(exc).__name__}。请稍后重试。"
            full_content = fallback
            watermarked_content = fallback
            yield f"data: {json.dumps({'type': 'token', 'content': fallback}, ensure_ascii=False)}\n\n"
            if not saved:
                try:
                    await _persist_assistant_message(watermarked_content)
                    saved = True
                except Exception:
                    logger.exception("Failed to persist fallback assistant message")
            yield f"data: {json.dumps({'type': 'done', 'content': full_content}, ensure_ascii=False)}\n\n"

        # Write to semantic cache (fire-and-forget, don't block response)
        if full_content and len(full_content) > 50:
            try:
                await cache.set(sanitized_message, full_content)
            except Exception:
                pass

        # Fire-and-forget: update conversation summary + fact sheet in background.
        # Uses asyncio.create_task so it runs after the SSE stream completes,
        # with its own DB session (see _background_update_conversation_context).
        if full_content and len(full_content) > 30:
            asyncio.create_task(
                _background_update_conversation_context(
                    conversation_id=conversation_id_str,
                    latest_user_message=payload.message,
                ),
                name=f"ctx_update_{conversation_id_str[:8]}",
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# POST /chat  -- Non-streaming (backward compatible)
# ============================================================================

@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatResponse:
    """Non-streaming chat endpoint with intent recognition and Milvus RAG."""

    # Content safety check
    safety_filter = get_content_safety_filter()
    is_safe, safety_reason = safety_filter.check_input(payload.message)
    content_filtered = False

    if not is_safe:
        # Return a polite refusal without creating conversation/message
        refusal_content = f"抱歉，{safety_reason or '您的输入未通过内容安全检查'}。请重新表述您的问题。"
        return ChatResponse(
            conversation_id=payload.conversation_id or "",
            message_id="",
            role="assistant",
            content=refusal_content,
            tokens_used=0,
            metadata={"intent": "blocked", "content_filtered": True},
            created_at=datetime.now(timezone.utc),
            content_filtered=True,
        )

    # Sanitize the prompt
    sanitized_message = safety_filter.sanitize_prompt(payload.message)

    # Colloquial → professional legal normalization (简历需求 #6)
    normalized_message, norm_meta = await _normalize_message(
        sanitized_message, payload.auto_normalize
    )
    if normalized_message:
        sanitized_message = normalized_message

    if payload.conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conversation = Conversation(
            user_id=current_user.id,
            tenant_id=getattr(current_user, "tenant_id", None) or DEFAULT_TENANT_ID,
            title=payload.message[:100],
            agent_type=payload.agent_type,
        )
        db.add(conversation)
        await db.flush()

    user_message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=payload.message)
    db.add(user_message)
    await db.flush()

    # Intent + RAG + Context (use sanitized message)
    intent = classify_intent(sanitized_message)
    retrieved = retrieve_legal_knowledge(sanitized_message, top_k=5)
    rag_context = format_rag_context(retrieved)
    system_prompt = build_system_prompt_for_intent(intent, rag_context)

    # Build context using ContextManager (Phase 2) or fallback
    messages_for_llm = await _build_context_with_manager(
        db=db,
        conversation_id=conversation.id,
        current_message=sanitized_message,
        rag_context=system_prompt,
    )

    llm_service = get_raw_llm_service()
    watermark_service = get_content_watermark_service()

    # Build watermark metadata
    watermark_meta = watermark_service.get_watermark_metadata(
        model="deepseek-chat",
        user_id=str(current_user.id),
        content_type="chat",
    )

    try:
        result = await llm_service.chat(messages=messages_for_llm, temperature=0.7, max_tokens=4096)
        assistant_content = result.get("content", "")
    except Exception:
        assistant_content = "抱歉，处理您的法律咨询时出现了技术问题。请稍后重试。"

    # Post-process output through content safety filter
    filtered_content = safety_filter.check_output(assistant_content)
    if filtered_content != assistant_content:
        content_filtered = True
        assistant_content = filtered_content

    # Citation existence verification (法条号存在性校验回路, P0-3)
    citation_check = None
    citation_gate: dict[str, Any] | None = None
    try:
        citation_check = await verify_citations(db, assistant_content)
        unverified = citation_check.get("unverified", [])
        if unverified:
            assistant_content += build_unverified_note(unverified)
            logger.warning(
                "Unverified legal citations detected: %s",
                [f"《{c.get('law_name')}》第{c.get('article_number_raw')}条" for c in unverified],
            )
        citation_gate = build_citation_gate(citation_check)
        if not citation_gate["allow_definitive"]:
            assistant_content += citation_gate["note"]
            logger.warning(
                "Citation gate DEGRADED (0 verified of %s) - definitive conclusion withheld",
                citation_check.get("citation_count", 0),
            )
    except Exception as exc:
        logger.warning("Citation verification skipped: %s", exc)

    # P0-4：引用校验结果写入审计日志（合规凭证）
    try:
        db.add(AuditLog(
            user_id=str(current_user.id),
            tenant_id=getattr(current_user, "tenant_id", None) or DEFAULT_TENANT_ID,
            action="ai.citation_verify",
            resource_type="conversation",
            resource_id=str(conversation.id),
            status_code=200,
            request_summary=summarize_verification(citation_check),
        ))
        await db.flush()
    except Exception as exc:  # noqa: BLE001 - 留痕失败不应影响回答
        logger.warning("Failed to write citation audit log: %s", exc)

    # Apply AI content identification watermarks (人工智能生成合成内容标识办法)
    assistant_content = watermark_service.add_explicit_watermark(assistant_content, content_type="chat")
    assistant_content = watermark_service.add_implicit_watermark(assistant_content, watermark_meta)

    metadata: dict[str, Any] = {
        "intent": intent,
        "colloquial_normalization": norm_meta,
        "citations": [
            {"law_name": r.get("law_name", ""), "article_number": r.get("article_number", ""),
             "relevance_score": r.get("score", 0)}
            for r in retrieved[:5]
        ],
        "content_filtered": content_filtered,
        "citation_verification": citation_check,
        "citation_gate": citation_gate,
        "disclaimer": DISCLAIMER_PREFIX,
        "ai_generated": True,
        "generation_metadata": watermark_meta,
    }

    assistant_message = Message(
        conversation_id=conversation.id, role=MessageRole.ASSISTANT,
        content=assistant_content, tokens_used=len(assistant_content) // 2,
        metadata_=json.dumps(metadata, ensure_ascii=False),
    )
    db.add(assistant_message)
    await db.flush()

    if conversation.title == "New Conversation":
        conversation.title = payload.message[:100]

    # Fire-and-forget: update conversation summary + fact sheet in background
    if assistant_content and len(assistant_content) > 30:
        asyncio.create_task(
            _background_update_conversation_context(
                conversation_id=str(conversation.id),
                latest_user_message=payload.message,
            ),
            name=f"ctx_update_{str(conversation.id)[:8]}",
        )

    return ChatResponse(
        conversation_id=conversation.id, message_id=assistant_message.id,
        role=MessageRole.ASSISTANT, content=assistant_content,
        tokens_used=len(assistant_content) // 2, metadata=metadata,
        created_at=datetime.now(timezone.utc),
        content_filtered=content_filtered,
        disclaimer=DISCLAIMER_PREFIX,
        ai_generated=True,
        watermark_info=watermark_meta,
        generation_metadata=watermark_meta,
    )


# ============================================================================
# POST /chat/summarize  -- Manual conversation summarization trigger
# ============================================================================

@router.post("/chat/summarize")
async def summarize_conversation(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Manually trigger conversation summarization and fact-sheet extraction.

    Body: {"conversation_id": "..."}

    Forces an immediate update of the conversation's summary, fact sheet, and
    topic segments using the ContextManager.  Returns the updated summary and
    fact sheet so the caller can display them.
    """
    conversation_id = str(payload.get("conversation_id", "") or "").strip()
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required")

    # Verify ownership
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    manager = ContextManager(db)

    # 1. Update fact sheet
    fact_sheet_updated = False
    fact_sheet_data = {}
    try:
        # Use a placeholder message since we are not processing a new user input;
        # the fact extractor will look at the most recent messages in the DB.
        fact_sheet = await manager._get_or_update_fact_sheet(conversation, "")
        conversation.fact_sheet = fact_sheet.to_dict()
        fact_sheet_updated = True
        fact_sheet_data = fact_sheet.to_dict()
    except Exception as exc:
        logger.warning("Manual summarize: fact sheet update failed: %s", exc)

    # 2. Update summary
    summary_updated = False
    new_summary = ""
    try:
        recent = await manager._get_recent_messages(conversation_id, max_turns=15)
        if conversation.summary:
            new_summary = await manager._incremental_update(conversation, recent)
        else:
            # Generate from all available messages
            msg_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(50)
            )
            messages = list(reversed(msg_result.scalars().all()))
            if messages:
                new_summary = await manager._generate_summary(messages)
        if new_summary:
            conversation.summary = new_summary
            summary_updated = True
    except Exception as exc:
        logger.warning("Manual summarize: summary update failed: %s", exc)

    # 3. Detect topic shift on the latest user message
    topic_shift_detected = False
    try:
        latest_user_result = await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.USER,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        latest_user_msg = latest_user_result.scalar_one_or_none()
        if latest_user_msg:
            topic_shift_detected = await manager._detect_topic_shift(
                conversation, latest_user_msg.content
            )
    except Exception as exc:
        logger.warning("Manual summarize: topic shift detection failed: %s", exc)

    await db.commit()

    message_count = await manager._get_message_count(conversation_id)

    return {
        "conversation_id": conversation_id,
        "summary_updated": summary_updated,
        "summary": conversation.summary or "",
        "fact_sheet_updated": fact_sheet_updated,
        "fact_sheet": fact_sheet_data,
        "topic_shift_detected": topic_shift_detected,
        "message_count": message_count,
    }


# ============================================================================
# GET /conversations
# ============================================================================

@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ConversationListResponse:
    count_result = await db.execute(
        select(func.count(Conversation.id)).where(Conversation.user_id == current_user.id)
    )
    total = count_result.scalar() or 0
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc()).offset(offset).limit(page_size)
    )
    return ConversationListResponse(
        conversations=[ConversationResponse.model_validate(c) for c in result.scalars().all()],
        total=total, page=page, page_size=page_size,
    )


# ============================================================================
# GET /conversations/{conversation_id}
# ============================================================================

# ============================================================================
# GET /conversations/{conversation_id}/stats  (Phase 2: Context Management)
# NOTE: Must be registered BEFORE /conversations/{conversation_id} so the
# literal "/stats" suffix is not swallowed by the {conversation_id} path param.
# ============================================================================

@router.get("/conversations/{conversation_id}/stats")
async def get_conversation_stats_endpoint(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get conversation statistics for debugging context management.

    Returns message count, turn count, summary status and token estimates.
    """
    from app.services.context_manager import get_conversation_stats

    # Verify ownership
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return await get_conversation_stats(db, conversation_id)


# ============================================================================
# GET /conversations/{conversation_id}
# ============================================================================

@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_messages(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailResponse:
    result = await db.execute(
        select(Conversation).options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return ConversationDetailResponse(
        id=conversation.id, user_id=conversation.user_id,
        title=conversation.title, agent_type=conversation.agent_type,
        summary=conversation.summary,
        created_at=conversation.created_at.replace(tzinfo=timezone.utc) if conversation.created_at else datetime.now(timezone.utc),
        updated_at=conversation.updated_at.replace(tzinfo=timezone.utc) if conversation.updated_at else datetime.now(timezone.utc),
        messages=[MessageResponse.model_validate(m) for m in conversation.messages],
    )


# ============================================================================
# DELETE /conversations/{conversation_id}
# ============================================================================

@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == current_user.id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg_result = await db.execute(select(Message).where(Message.conversation_id == conversation_id))
    for msg in msg_result.scalars().all():
        await db.delete(msg)
    await db.delete(conversation)
    await db.flush()


# ============================================================================
# POST /chat/voice  -- Voice input endpoint
# ============================================================================

# Accepted audio MIME types and max size
_ALLOWED_AUDIO_MIME_TYPES: set[str] = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/webm",
    "audio/ogg",
}

_AUDIO_EXTENSIONS: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
}


@router.post("/voice")
async def chat_voice(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(..., description="Audio file (WAV, MP3, M4A, WebM)"),
    conversation_id: str | None = None,
    agent_type: str = "legal_consultation",
) -> StreamingResponse:
    """Accept an audio file, transcribe it, then process as a chat message.

    Returns an SSE stream identical to /chat/stream, with an extra
    ``transcribed_text`` meta field containing the STT result.
    """
    from app.core.config import settings
    from app.services.voice_service import get_voice_service

    # --- Check if voice is enabled ---
    if not settings.VOICE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音输入功能已禁用",
        )

    # --- Validate MIME type ---
    content_type = (file.content_type or "").strip().lower()
    if content_type not in _ALLOWED_AUDIO_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的音频格式: {content_type}。支持: WAV, MP3, M4A, WebM",
        )

    # --- Read and validate size ---
    audio_content = await file.read()
    max_bytes = settings.MAX_AUDIO_FILE_SIZE_MB * 1024 * 1024
    if len(audio_content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"音频文件过大，最大允许 {settings.MAX_AUDIO_FILE_SIZE_MB}MB",
        )

    # --- Determine extension ---
    ext = _AUDIO_EXTENSIONS.get(content_type, "")
    if not ext:
        # Infer from filename
        import os as _os
        ext = _os.path.splitext(file.filename or "audio.wav")[1].lower()
        if ext not in (".wav", ".mp3", ".m4a", ".webm", ".ogg"):
            ext = ".wav"

    # --- Transcribe ---
    voice_service = get_voice_service()
    try:
        transcription = await voice_service.transcribe(audio_content, f"audio{ext}")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Voice transcription failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"语音识别失败: {exc}",
        )

    transcribed_text = transcription["text"]
    audio_duration = transcription.get("duration_seconds", 0.0)

    if not transcribed_text or not transcribed_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未能从音频中识别出文字，请检查音频质量后重试",
        )

    # --- Build a ChatRequest and delegate to the streaming chat pipeline ---
    payload = ChatRequest(
        conversation_id=conversation_id,
        message=transcribed_text,
        agent_type=agent_type,
    )

    # Content safety check on transcribed text
    safety_filter = get_content_safety_filter()
    is_safe, safety_reason = safety_filter.check_input(payload.message)

    # Create or retrieve conversation
    if payload.conversation_id:
        result = await db.execute(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conversation = Conversation(
            user_id=current_user.id,
            tenant_id=getattr(current_user, "tenant_id", None) or DEFAULT_TENANT_ID,
            title=transcribed_text[:100],
            agent_type=payload.agent_type,
        )
        db.add(conversation)
        await db.flush()

    # Save user message (original transcribed text)
    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=transcribed_text,
    )
    db.add(user_message)
    await db.flush()

    if not is_safe:
        refusal_content = f"抱歉，{safety_reason or '您的输入未通过内容安全检查'}。请重新表述您的问题。"

        async def voice_refusal_generator():
            meta = {
                "conversation_id": str(conversation.id),
                "intent": "blocked",
                "citations": [],
                "content_filtered": True,
                "transcribed_text": transcribed_text,
                "audio_duration": audio_duration,
            }
            yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'token', 'content': refusal_content}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': refusal_content}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            voice_refusal_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    sanitized_message = safety_filter.sanitize_prompt(payload.message)
    intent = classify_intent(sanitized_message)
    retrieved = retrieve_legal_knowledge(sanitized_message, top_k=5)
    rag_context = format_rag_context(retrieved)
    system_prompt = build_system_prompt_for_intent(intent, rag_context)

    # Build context using ContextManager (Phase 2) or fallback
    messages_for_llm = await _build_context_with_manager(
        db=db,
        conversation_id=conversation.id,
        current_message=sanitized_message,
        rag_context=system_prompt,
    )

    llm_service = get_raw_llm_service()
    watermark_service = get_content_watermark_service()

    # Build watermark metadata for voice chat
    watermark_meta = watermark_service.get_watermark_metadata(
        model="deepseek-chat",
        user_id=str(current_user.id),
        content_type="chat",
    )

    async def voice_event_generator():
        full_content = ""
        content_filtered = False
        try:
            meta = {
                "conversation_id": str(conversation.id),
                "intent": intent,
                "citations": [
                    {"law_name": r.get("law_name", ""), "article_number": r.get("article_number", ""),
                     "content": r.get("content", ""), "score": r.get("score", 0)}
                    for r in retrieved[:5]
                ],
                "content_filtered": False,
                "transcribed_text": transcribed_text,
                "audio_duration": audio_duration,
                "disclaimer": DISCLAIMER_PREFIX,
                "ai_generated": True,
                "watermark_info": watermark_meta,
                "generation_metadata": watermark_meta,
            }
            yield f"data: {json.dumps({'type': 'meta', 'data': meta}, ensure_ascii=False)}\n\n"

            async for chunk in llm_service.chat_stream(
                messages=messages_for_llm, temperature=0.7, max_tokens=4096,
            ):
                full_content += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"

            filtered_content = safety_filter.check_output(full_content)
            if filtered_content != full_content:
                content_filtered = True
                full_content = filtered_content

            # Apply AI content identification watermarks (人工智能生成合成内容标识办法)
            watermarked_content = watermark_service.add_explicit_watermark(full_content, content_type="chat")
            watermarked_content = watermark_service.add_implicit_watermark(watermarked_content, watermark_meta)

            yield f"data: {json.dumps({'type': 'done', 'content': watermarked_content}, ensure_ascii=False)}\n\n"

        except Exception as exc:
            logger.exception("Voice streaming error: %s", exc)
            fallback = "抱歉，AI服务暂时不可用。请稍后重试。"
            watermarked_content = fallback
            yield f"data: {json.dumps({'type': 'token', 'content': fallback}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': watermarked_content}, ensure_ascii=False)}\n\n"

        # Save assistant message
        assistant_message = Message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content=watermarked_content,
            tokens_used=len(watermarked_content) // 2,
            metadata_=json.dumps({
                "intent": intent,
                "transcribed_text": transcribed_text,
                "audio_duration": audio_duration,
                "content_filtered": content_filtered,
                "disclaimer": DISCLAIMER_PREFIX,
                "ai_generated": True,
                "generation_metadata": watermark_meta,
            }, ensure_ascii=False),
        )
        db.add(assistant_message)
        if conversation.title == "New Conversation":
            conversation.title = transcribed_text[:100]
        await db.commit()

    return StreamingResponse(
        voice_event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# POST /voice/tts  -- Text-to-Speech endpoint (streaming audio output)
# ============================================================================

@router.post("/voice/tts")
async def text_to_speech(
    request: TTSRequest,
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Convert text to speech audio stream.

    Returns a streaming MP3 audio response. Uses edge-tts for Chinese TTS
    with support for multiple voice presets (formal_female, formal_male,
    warm_female) and optional SSML for better prosody.

    Gracefully degrades with a 503 error if edge-tts is not installed.
    """
    from app.services.tts_service import VOICE_PRESET_MAP, get_tts_service

    tts_service = get_tts_service()

    # Check if TTS engine is available
    if not tts_service.engine_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="语音合成服务暂不可用（edge-tts 未安装）。请联系管理员安装: pip install edge-tts",
        )

    # Validate voice preset if specified
    if request.preset and request.preset not in VOICE_PRESET_MAP:
        # Still allow it, but log a warning — the service will fall back
        logger.warning("Unknown TTS preset '%s', will use default voice", request.preset)

    # Resolve voice/preset up-front so an invalid value fails loudly instead of
    # streaming a zero-byte audio response.  Callers (including the built-in web
    # front-end) often pass a preset id in the `voice` field.
    resolved_voice, _preset_cfg = tts_service.resolve_voice(request.voice, request.preset)

    # Build the streaming audio generator
    async def audio_stream():
        """Yield audio chunks from the TTS service."""
        async for chunk in tts_service.synthesize_stream(
            text=request.text,
            voice=resolved_voice,
            rate=request.rate,
            volume=request.volume,
            pitch=request.pitch,
            preset=request.preset,
            use_ssml=request.use_ssml,
            style=request.style,
        ):
            yield chunk

    # Check if the generator produced any output by wrapping it
    # We use StreamingResponse with audio/mpeg for MP3 output
    response_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
        "X-TTS-Voice": resolved_voice,
    }

    # Add Content-Disposition for browser download if needed
    if request.response_format == "mp3":
        response_headers["Content-Disposition"] = 'inline; filename="tts_output.mp3"'

    return StreamingResponse(
        audio_stream(),
        media_type="audio/mpeg",
        headers=response_headers,
    )


@router.get("/voice/presets")
async def list_voice_presets(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """List available voice presets for TTS.

    Returns preset IDs with metadata (label, gender, style description).
    """
    from app.services.tts_service import get_tts_service

    tts_service = get_tts_service()
    return {
        "presets": tts_service.list_presets(),
        "engine_available": tts_service.engine_available,
        "default_preset": "warm_female",
    }


@router.get("/voice/voices")
async def list_tts_voices(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """List all available Chinese TTS voices.

    Returns the full voice catalogue from Microsoft Edge TTS.
    """
    from app.services.tts_service import get_tts_service

    tts_service = get_tts_service()
    return {
        "voices": tts_service.list_voices(),
        "engine_available": tts_service.engine_available,
    }





# ============================================================================
# P1-5 跨会话长期记忆：查看 / 删除（PIPL 用户可携权与删除权）
# ============================================================================

@router.get("/memory")
async def get_my_long_term_memory(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """查看 AI 为我保存的跨会话长期画像。"""
    from app.services.long_term_memory import get_memory

    memory = await get_memory(db, str(current_user.id))
    if memory is None:
        return {"exists": False, "entry_count": 0, "profile_text": "", "profile": {}}

    profile: dict = {}
    if memory.profile_json:
        try:
            profile = json.loads(memory.profile_json)
        except (json.JSONDecodeError, TypeError):
            profile = {}
    return {
        "exists": True,
        "entry_count": int(memory.entry_count or 0),
        "profile_text": memory.profile_text or "",
        "profile": profile,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
    }


@router.delete("/memory", status_code=204)
async def clear_my_long_term_memory(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """一键清除 AI 为我保存的长期记忆（删除权）。"""
    from app.services.long_term_memory import get_memory

    memory = await get_memory(db, str(current_user.id))
    if memory is not None:
        await db.delete(memory)
        await db.flush()
