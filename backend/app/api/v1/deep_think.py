"""Deep Thinking API endpoints -- reasoning with chain-of-thought visualization.

Endpoints:
- POST /deep-think -- Non-streaming deep reasoning
- POST /deep-think/stream -- Streaming deep reasoning with SSE
- GET /deep-think/models -- List available reasoning models
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.deep_thinking_agent import (
    DeepThinkingAgent,
    create_deep_thinking_agent,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Request / Response schemas
# ============================================================================

class DeepThinkRequest(BaseModel):
    """Request for deep thinking reasoning."""
    query: str = Field(..., description="Legal question to reason about", min_length=1, max_length=10000)
    use_reasoning_model: bool = Field(default=True, description="Whether to use a reasoning-capable model")
    model_override: str | None = Field(default=None, description="Override the reasoning model")
    include_rag: bool = Field(default=True, description="Whether to include RAG context")
    conversation_id: str | None = Field(default=None, description="Conversation ID for context")
    depth: str = Field(
        default="deep",
        description="Analysis depth: standard | deep | expert. "
                    "Controls how many sub-issues are analysed and whether the "
                    "self-verification pass runs.",
        pattern="^(standard|deep|expert)$",
    )


class ReasoningStepResponse(BaseModel):
    """A single reasoning step in the chain-of-thought."""
    step_id: int
    step_type: str
    title: str
    content: str
    confidence: float = 0.0
    sources: list[str] = Field(default_factory=list)


class DeepThinkResponse(BaseModel):
    """Response from deep thinking."""
    query: str
    thinking_process: str = ""
    reasoning_steps: list[ReasoningStepResponse] = Field(default_factory=list)
    final_answer: str = ""
    confidence: float = 0.0
    # IRAC phase content is produced by the LLM as free-form JSON, so values
    # are heterogeneous: strings for issue/rule/application/conclusion, a
    # float for `confidence`, and a list for `sources`.  Typing this as
    # ``dict[str, str]`` made the response model reject a valid result with
    # ``irac_analysis.confidence: Input should be a valid string`` and turned
    # the whole endpoint into a 500.
    irac_analysis: dict[str, Any] = Field(default_factory=dict)
    verification_notes: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    model_used: str = ""
    tokens_used: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningModelInfo(BaseModel):
    """Info about a reasoning model."""
    model_id: str
    name: str
    provider: str
    description: str
    supports_streaming: bool = True
    is_default: bool = False


# ============================================================================
# POST /deep-think -- Non-streaming deep reasoning
# ============================================================================

@router.post("/deep-think", response_model=DeepThinkResponse, summary="Deep reasoning with chain-of-thought")
async def deep_think(request: DeepThinkRequest) -> DeepThinkResponse:
    """Perform deep reasoning on a legal question with visible chain-of-thought.

    Uses the IRAC (Issue-Rule-Application-Conclusion) framework to structure
    legal reasoning, with self-verification for accuracy.
    """
    try:
        agent = create_deep_thinking_agent(model_override=request.model_override)

        # Optional RAG context
        rag_context = ""
        if request.include_rag:
            try:
                from app.api.v1.chat import retrieve_legal_knowledge, format_rag_context
                retrieved = retrieve_legal_knowledge(request.query, top_k=5)
                rag_context = format_rag_context(retrieved)
            except Exception as exc:
                logger.warning("RAG retrieval for deep think failed: %s", exc)

        result = await agent.think(
            query=request.query,
            rag_context=rag_context,
            use_reasoning_model=request.use_reasoning_model,
            depth=request.depth,
        )

        return DeepThinkResponse(
            query=result.query,
            thinking_process=result.thinking_process,
            reasoning_steps=[
                ReasoningStepResponse(
                    step_id=s.step_id,
                    step_type=s.step_type,
                    title=s.title,
                    content=s.content,
                    confidence=s.confidence,
                    sources=s.sources,
                )
                for s in result.reasoning_steps
            ],
            final_answer=result.final_answer,
            confidence=result.confidence,
            irac_analysis=result.irac_analysis,
            verification_notes=result.verification_notes,
            elapsed_seconds=result.elapsed_seconds,
            model_used=result.model_used,
            tokens_used=result.tokens_used,
            metadata=result.metadata,
        )

    except Exception as exc:
        logger.error("Deep thinking failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deep thinking failed: {str(exc)}",
        ) from exc


# ============================================================================
# POST /deep-think/stream -- Streaming deep reasoning with SSE
# ============================================================================

@router.post("/deep-think/stream", summary="Streaming deep reasoning with CoT")
async def deep_think_stream(request: DeepThinkRequest) -> StreamingResponse:
    """Stream deep reasoning with real-time chain-of-thought visualization.

    Returns SSE events for each phase of reasoning:
    - thinking_start: Reasoning process begins
    - phase_start/phase_complete: Phase transitions
    - irac_complete: IRAC analysis for each sub-issue
    - token: Streaming tokens of final answer
    - thinking_complete: Final result
    """
    agent = create_deep_thinking_agent(model_override=request.model_override)

    # Optional RAG context
    rag_context = ""
    if request.include_rag:
        try:
            from app.api.v1.chat import retrieve_legal_knowledge, format_rag_context
            retrieved = retrieve_legal_knowledge(request.query, top_k=5)
            rag_context = format_rag_context(retrieved)
        except Exception as exc:
            logger.warning("RAG retrieval for deep think stream failed: %s", exc)

    async def event_generator():
        try:
            async for event in agent.think_stream(
                query=request.query,
                rag_context=rag_context,
                depth=request.depth,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error("Deep think streaming error: %s", exc)
            error_event = {"type": "error", "error": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# GET /deep-think/models -- List available reasoning models
# ============================================================================

@router.get("/deep-think/models", summary="List available reasoning models")
async def list_reasoning_models() -> dict[str, Any]:
    """List available reasoning-capable models."""
    models = [
        ReasoningModelInfo(
            model_id="deepseek-reasoner",
            name="DeepSeek-R1",
            provider="deepseek",
            description="DeepSeek推理模型，支持深度思考和链式推理，擅长复杂法律推理",
            supports_streaming=True,
            is_default=True,
        ),
        ReasoningModelInfo(
            model_id="deepseek-chat",
            name="DeepSeek-V3",
            provider="deepseek",
            description="DeepSeek对话模型，支持IRAC分析和多步推理",
            supports_streaming=True,
            is_default=False,
        ),
        ReasoningModelInfo(
            model_id="o1",
            name="OpenAI o1",
            provider="openai",
            description="OpenAI推理模型，擅长复杂逻辑推理和数学证明",
            supports_streaming=False,
            is_default=False,
        ),
        ReasoningModelInfo(
            model_id="qwen-max",
            name="通义千问Max",
            provider="qwen",
            description="阿里云通义千问大模型，支持深度思考模式",
            supports_streaming=True,
            is_default=False,
        ),
    ]

    return {
        "models": [m.model_dump() for m in models],
        "default_model": "deepseek-reasoner",
    }
