"""Enterprise Knowledge Base & Fine-tuning API endpoints.

Endpoints:
- POST /enterprise/kb/namespaces — Create namespace
- POST /enterprise/kb/upload — Upload document
- POST /enterprise/kb/search — Search knowledge base
- GET  /enterprise/kb/documents — List documents
- DELETE /enterprise/kb/documents/{doc_id} — Delete document
- POST /enterprise/finetuning/prepare — Prepare training data
- POST /enterprise/finetuning/config — Get fine-tuning config
- GET  /enterprise/finetuning/benchmark — Get evaluation benchmark
- POST /chat/tts — Text-to-speech synthesis
- GET  /chat/tts/voices — List available voices
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.enterprise_knowledge_base import get_enterprise_knowledge_base
from app.services.finetuning import get_finetuning_pipeline
from app.services.tts_service import get_tts_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _derive_tenant_id(user: User) -> str:
    """Derive tenant isolation from the authenticated user.

    The old code accepted `tenant_id` from the request body and defaulted to
    ``"default"`` — any authenticated user could then read any other tenant's
    knowledge base by guessing the string. Tenant identity must be derived from
    the auth context so it is the caller's identity, not the caller's input.

    Currently the User model has no explicit tenant/organization column, so we
    use ``user_id`` as the tenant key. When a real multi-tenant schema is added
    (e.g. ``users.organization_id``), this is the single function to update.
    """
    return user.id


# ============================================================================
# TTS Endpoints (Voice Output)
# ============================================================================

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str | None = Field(default=None, description="Voice ID")
    rate: str = Field(default="+0%", description="Speech rate adjustment")


@router.post("/chat/tts", summary="文字转语音")
async def text_to_speech(
    request: TTSRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Convert text to speech audio (MP3 format)."""
    tts = get_tts_service()

    result = await tts.synthesize(
        text=request.text,
        voice=request.voice,
        rate=request.rate,
    )

    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    audio = result.get("audio_content", b"")
    if not audio:
        raise HTTPException(status_code=500, detail="TTS synthesis produced no audio")

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "X-Duration": str(result.get("duration_estimate", 0)),
            "X-Voice": result.get("voice", ""),
        },
    )


@router.get("/chat/tts/voices", summary="列出可用语音")
async def list_tts_voices(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all available TTS voices."""
    tts = get_tts_service()
    voices = tts.list_voices()
    return {"voices": voices, "total": len(voices)}


# ============================================================================
# Enterprise Knowledge Base Endpoints
# ============================================================================

class NamespaceRequest(BaseModel):
    namespace: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="")
    # tenant_id removed — now derived from authenticated user (security fix)


class KBSearchRequest(BaseModel):
    namespace: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=500)
    # tenant_id removed — now derived from authenticated user (security fix)
    top_k: int = Field(default=5, ge=1, le=20)


@router.post("/enterprise/kb/namespaces", summary="创建知识库命名空间")
async def create_namespace(
    request: NamespaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a new isolated knowledge base namespace."""
    tenant_id = _derive_tenant_id(current_user)
    kb = get_enterprise_knowledge_base()
    result = await kb.create_namespace(
        namespace=request.namespace,
        description=request.description,
        tenant_id=tenant_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/enterprise/kb/upload", summary="上传知识文档")
async def upload_knowledge_document(
    namespace: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Upload a document to the enterprise knowledge base."""
    tenant_id = _derive_tenant_id(current_user)
    kb = get_enterprise_knowledge_base()

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大，最大50MB")

    result = await kb.upload_document(
        namespace=namespace,
        filename=file.filename or "unknown",
        content=content,
        tenant_id=tenant_id,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/enterprise/kb/search", summary="搜索企业知识库")
async def search_knowledge_base(
    request: KBSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Search within a tenant's knowledge base."""
    tenant_id = _derive_tenant_id(current_user)
    kb = get_enterprise_knowledge_base()
    return await kb.search(
        namespace=request.namespace,
        query=request.query,
        tenant_id=tenant_id,
        top_k=request.top_k,
    )


@router.get("/enterprise/kb/documents", summary="列出知识库文档")
async def list_kb_documents(
    namespace: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List documents in a knowledge base namespace."""
    tenant_id = _derive_tenant_id(current_user)
    kb = get_enterprise_knowledge_base()
    return await kb.list_documents(namespace=namespace, tenant_id=tenant_id)


@router.delete("/enterprise/kb/documents/{doc_id}", summary="删除知识库文档")
async def delete_kb_document(
    doc_id: str,
    namespace: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete a document from the knowledge base."""
    tenant_id = _derive_tenant_id(current_user)
    kb = get_enterprise_knowledge_base()
    result = await kb.delete_document(
        namespace=namespace, doc_id=doc_id, tenant_id=tenant_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================================
# Fine-tuning Endpoints
# ============================================================================

class PrepareDataRequest(BaseModel):
    task_type: str = Field(default="legal_qa", description="Task type for training data")
    source: str = Field(default="database", description="Data source")
    max_samples: int = Field(default=1000, ge=10, le=100000)


class FinetuneConfigRequest(BaseModel):
    base_model: str = Field(default="qwen2.5-7b")
    task_type: str = Field(default="legal_qa")


@router.post("/enterprise/finetuning/prepare", summary="准备微调训练数据")
async def prepare_training_data(
    request: PrepareDataRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Prepare training data for model fine-tuning."""
    pipeline = get_finetuning_pipeline()
    result = await pipeline.prepare_training_data(
        task_type=request.task_type,
        source=request.source,
        max_samples=request.max_samples,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/enterprise/finetuning/config", summary="获取微调配置")
async def get_finetune_config(
    request: FinetuneConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate LoRA fine-tuning configuration."""
    pipeline = get_finetuning_pipeline()
    return pipeline.get_finetuning_config(
        base_model=request.base_model,
        task_type=request.task_type,
    )


@router.get("/enterprise/finetuning/benchmark", summary="获取评测基准")
async def get_evaluation_benchmark(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the legal AI evaluation benchmark specification."""
    pipeline = get_finetuning_pipeline()
    return pipeline.get_evaluation_benchmark()


@router.get("/enterprise/finetuning/models", summary="支持的基座模型")
async def list_base_models(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List supported base models for fine-tuning."""
    pipeline = get_finetuning_pipeline()
    models = []
    for model_id, info in pipeline.SUPPORTED_BASE_MODELS.items():
        models.append({"id": model_id, **info})
    return {"models": models, "total": len(models)}


@router.get("/enterprise/finetuning/tasks", summary="支持的法律任务类型")
async def list_legal_tasks(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List supported legal task types for fine-tuning."""
    pipeline = get_finetuning_pipeline()
    tasks = [{"id": k, "description": v} for k, v in pipeline.LEGAL_TASKS.items()]
    return {"tasks": tasks, "total": len(tasks)}
