"""Document generation endpoints.

Uses the DocumentGenAgent for AI-powered legal document drafting.
Includes batch upload and processing endpoints.
"""

import io
import os
import zipfile
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.document_gen_agent import DocumentGenAgent, create_document_gen_agent, DOCUMENT_TEMPLATES
from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.tenancy import tenant_of
from app.core.validators import validate_file_upload, sanitize_filename
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.rag.document_processor import DocumentProcessor
from app.schemas.batch import (
    BatchStatusResponse,
    BatchUploadResponse,
    DocumentHistoryResponse,
    DocumentItem,
    FileResult,
)
from app.schemas.document import (
    DocumentGenRequest,
    DocumentGenResponse,
    DocumentTemplateListResponse,
    DocumentTemplateResponse,
)
from app.services.batch_processor import get_batch_processor
from app.services.task_manager import get_task_manager, execute_document_generation
from app.services.content_watermark import get_content_watermark_service

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy-loaded agent instance
# ---------------------------------------------------------------------------
_document_gen_agent: DocumentGenAgent | None = None


def get_document_gen_agent() -> DocumentGenAgent:
    """Return a singleton DocumentGenAgent instance."""
    global _document_gen_agent
    if _document_gen_agent is None:
        _document_gen_agent = create_document_gen_agent()
    return _document_gen_agent


# ---------------------------------------------------------------------------
# Build available templates from the agent's built-in templates
# ---------------------------------------------------------------------------

_AVAILABLE_TEMPLATES: list[DocumentTemplateResponse] = []


def _init_templates() -> list[DocumentTemplateResponse]:
    """Build template list from the DocumentGenAgent's DOCUMENT_TEMPLATES."""
    global _AVAILABLE_TEMPLATES
    if not _AVAILABLE_TEMPLATES:
        template_map = {
            "民事起诉状": {
                "template_type": "complaint_filing",
                "category": "litigation",
                "description": "民事案件原告向人民法院提起诉讼的文书",
                "required_fields": ["原告姓名/名称", "原告住所地", "被告姓名/名称", "被告住所地", "诉讼请求", "案件事实", "法律依据"],
            },
            "民事答辩状": {
                "template_type": "defense_statement",
                "category": "litigation",
                "description": "民事案件被告针对起诉状进行答辩的文书",
                "required_fields": ["答辩人姓名/名称", "被答辩人（原告）信息", "案由", "答辩意见", "反驳事实与理由"],
            },
            "法律意见书": {
                "template_type": "legal_opinion",
                "category": "legal_document",
                "description": "对特定法律问题进行分析论证的专业文书",
                "required_fields": ["委托事项", "案件事实", "法律分析", "结论与建议"],
            },
            "律师函": {
                "template_type": "lawyer_letter",
                "category": "legal_document",
                "description": "律师代表当事人向对方发出的正式法律通知文书",
                "required_fields": ["委托人信息", "收函人信息", "委托事项", "事实陈述", "权利主张", "期限要求"],
            },
            "仲裁申请书": {
                "template_type": "arbitration_request",
                "category": "litigation",
                "description": "向仲裁机构提交的仲裁申请文书",
                "required_fields": ["申请人姓名/名称", "被申请人姓名/名称", "仲裁请求", "事实与理由", "仲裁协议依据"],
            },
        }
        for name, info in template_map.items():
            if name in DOCUMENT_TEMPLATES:
                _AVAILABLE_TEMPLATES.append(
                    DocumentTemplateResponse(
                        template_type=info["template_type"],
                        name=name,
                        description=info["description"],
                        category=info["category"],
                        required_fields=info["required_fields"],
                    )
                )
    return _AVAILABLE_TEMPLATES


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------


@router.post("/generate", response_model=DocumentGenResponse, status_code=status.HTTP_201_CREATED)
async def generate_document(
    payload: DocumentGenRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentGenResponse:
    """Generate a legal document using the DocumentGenAgent.

    Accepts a document type and form fields, invokes the DocumentGenAgent
    via LangGraph to produce a professionally formatted legal document
    in Markdown format, and saves the result to the database.
    """
    templates = _init_templates()

    # Validate template exists
    template = next(
        (t for t in templates if t.template_type == payload.template_type),
        None,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown template type: {payload.template_type}",
        )

    # Validate required fields are present
    missing = [
        field for field in template.required_fields
        if field not in payload.parameters
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required parameters: {', '.join(missing)}",
        )

    # Build a description from the parameters for the agent
    description_parts: list[str] = []
    for key, value in payload.parameters.items():
        description_parts.append(f"{key}：{value}")
    description = f"请生成一份{template.name}。\n" + "\n".join(description_parts)

    # ------------------------------------------------------------------
    # Invoke the DocumentGenAgent
    # ------------------------------------------------------------------
    try:
        agent = get_document_gen_agent()
        agent_result = await agent.run({
            "document_type": template.name,
            "description": description,
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document generation failed: {str(exc)}",
        )

    generated_content = agent_result.get("generated_document", "")
    final_output = agent_result.get("final_output", "")
    missing_fields = agent_result.get("missing_fields", [])

    if not generated_content:
        # Fallback to final_output if generated_document is empty
        generated_content = final_output or (
            f"[文档生成失败] 未能生成 {template.name}。请检查参数后重试。"
        )

    # Apply AI content identification watermarks (人工智能生成合成内容标识办法)
    watermark_service = get_content_watermark_service()
    watermark_meta = watermark_service.get_watermark_metadata(
        model="deepseek-chat",
        user_id=str(current_user.id),
        content_type="document",
    )
    generated_content = watermark_service.add_explicit_watermark(generated_content, content_type="document")
    generated_content = watermark_service.add_implicit_watermark(generated_content, watermark_meta)

    doc_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Save generated document to the database as a Document record
    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)
    doc_filename = f"{doc_id}_generated.md"
    doc_path = os.path.join(upload_dir, doc_filename)
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(generated_content)

    doc_record = Document(
        id=doc_id,
        user_id=current_user.id,
        # P0-2：生成类文书同样在写入时打租户标
        tenant_id=tenant_of(current_user),
        filename=doc_filename,
        original_filename=f"{template.name}.md",
        file_type="md",
        file_size=len(generated_content.encode("utf-8")),
        file_path=doc_path,
        status=DocumentStatus.READY,
        summary=f"Generated {template.name}",
    )
    db.add(doc_record)

    return DocumentGenResponse(
        id=doc_id,
        template_type=payload.template_type,
        title=template.name,
        content=generated_content,
        language=payload.language,
        created_at=now,
        ai_generated=True,
        watermark_info=watermark_meta,
        generation_metadata=watermark_meta,
    )


# ---------------------------------------------------------------------------
# GET /templates
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=DocumentTemplateListResponse)
async def list_templates(
    current_user: Annotated[User, Depends(get_current_user)],
) -> DocumentTemplateListResponse:
    """List all available document templates."""
    templates = _init_templates()
    return DocumentTemplateListResponse(
        templates=templates,
        total=len(templates),
    )


# ---------------------------------------------------------------------------
# POST /generate-async  (async version)
# ---------------------------------------------------------------------------

@router.post("/generate-async", status_code=status.HTTP_202_ACCEPTED)
async def generate_document_async(
    payload: DocumentGenRequest,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """异步文书生成 — 提交后立即返回 task_id，后台执行生成。

    通过 GET /tasks/{task_id} 查询进度和结果。
    """
    templates = _init_templates()
    template = next(
        (t for t in templates if t.template_type == payload.template_type),
        None,
    )
    if template is None:
        raise HTTPException(status_code=404, detail=f"未知模板类型: {payload.template_type}")

    missing = [f for f in template.required_fields if f not in payload.parameters]
    if missing:
        raise HTTPException(status_code=422, detail=f"缺少必填参数: {', '.join(missing)}")

    description_parts = [f"{k}：{v}" for k, v in payload.parameters.items()]
    description = f"请生成一份{template.name}。\n" + "\n".join(description_parts)

    task_mgr = get_task_manager()
    task_id = await task_mgr.create_task(
        task_type="document_generation",
        user_id=current_user.id,
        metadata={"template_type": payload.template_type, "template_name": template.name},
    )
    background_tasks.add_task(execute_document_generation, task_id, template.name, description)

    return {
        "task_id": task_id,
        "status": "pending",
        "message": f"文书生成任务已提交（{template.name}），正在后台处理。",
    }


# =========================================================================
# 批量文档处理 API
# =========================================================================

_BATCH_MAX_FILES = 20
_BATCH_MAX_TOTAL_SIZE_MB = 100


async def _validate_batch_files(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Validate all files in a batch upload request.

    Returns a list of (original_filename, file_content) tuples.
    Raises HTTPException on any validation failure.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少上传一个文件",
        )

    if len(files) > _BATCH_MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单次最多上传 {_BATCH_MAX_FILES} 个文件",
        )

    validated: list[tuple[str, bytes]] = []
    total_size = 0

    for file in files:
        try:
            file_content, ext = await validate_file_upload(file)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件 '{file.filename}' 验证失败: {str(exc)}",
            )

        total_size += len(file_content)
        if total_size > _BATCH_MAX_TOTAL_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件总大小超过限制（最大 {_BATCH_MAX_TOTAL_SIZE_MB}MB）",
            )

        original_filename = sanitize_filename(file.filename)
        validated.append((original_filename, file_content))

    return validated


# ---------------------------------------------------------------------------
# POST /batch-upload
# ---------------------------------------------------------------------------


@router.post("/batch-upload", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def batch_upload(
    files: list[UploadFile] = File(..., description="上传多个文档文件（最多 20 个）"),
    mode: str = Form(default="analyze", description="处理模式: extract, summarize, analyze"),
    current_user: Annotated[User, Depends(get_current_user)] = ...,
) -> BatchUploadResponse:
    """Upload multiple files at once for batch processing.

    Accept up to 20 files (PDF, DOCX, TXT). Max total size: 100MB.
    Returns a batch_id for tracking progress.
    """
    if mode not in ("extract", "summarize", "analyze"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的处理模式，可选值: extract, summarize, analyze",
        )

    validated_files = await _validate_batch_files(files)

    processor = get_batch_processor()
    result = await processor.process_batch(
        files=validated_files,
        user_id=current_user.id,
        mode=mode,
    )

    return BatchUploadResponse(**result)


# ---------------------------------------------------------------------------
# GET /batch-status/{batch_id}
# ---------------------------------------------------------------------------


@router.get("/batch-status/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
) -> BatchStatusResponse:
    """Get batch processing status."""
    processor = get_batch_processor()
    data = await processor.get_batch_status(batch_id)

    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次任务不存在或已过期",
        )

    # Verify ownership
    raw = await processor.get_batch_raw(batch_id)
    if raw and raw.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该批次任务",
        )

    results = [FileResult(**r) for r in data.get("results", [])]

    return BatchStatusResponse(
        batch_id=data["batch_id"],
        status=data["status"],
        total_files=data["total_files"],
        completed_files=data.get("completed_files", 0),
        failed_files=data.get("failed_files", 0),
        progress_percent=data.get("progress_percent", 0.0),
        mode=data.get("mode", "analyze"),
        started_at=data["started_at"],
        completed_at=data.get("completed_at"),
        results=results,
    )


# ---------------------------------------------------------------------------
# GET /batch-results/{batch_id}
# ---------------------------------------------------------------------------


@router.get("/batch-results/{batch_id}")
async def get_batch_results(
    batch_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
) -> dict[str, Any]:
    """Get batch processing results with full content."""
    processor = get_batch_processor()
    raw = await processor.get_batch_raw(batch_id)

    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次任务不存在或已过期",
        )

    if raw.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该批次任务",
        )

    data = await processor.get_batch_results(batch_id)
    return data


# ---------------------------------------------------------------------------
# GET /batch-download/{batch_id}
# ---------------------------------------------------------------------------


@router.get("/batch-download/{batch_id}")
async def download_batch_results(
    batch_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
) -> StreamingResponse:
    """Download all batch results as a ZIP file."""
    processor = get_batch_processor()
    raw = await processor.get_batch_raw(batch_id)

    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次任务不存在或已过期",
        )

    if raw.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该批次任务",
        )

    data = await processor.get_batch_results(batch_id)
    results = data.get("results", [])

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write a summary file
        summary_lines = [
            f"批量处理结果",
            f"批次ID: {batch_id}",
            f"处理模式: {data.get('mode', 'analyze')}",
            f"开始时间: {data.get('started_at', '')}",
            f"完成时间: {data.get('completed_at', '')}",
            f"总文件数: {data.get('total_files', 0)}",
            f"成功: {data.get('completed_files', 0)}",
            f"失败: {data.get('failed_files', 0)}",
            "",
            "=" * 60,
            "",
        ]
        zf.writestr("summary.txt", "\n".join(summary_lines))

        for i, r in enumerate(results):
            filename = r.get("filename", f"file_{i}")
            base_name = os.path.splitext(filename)[0]

            if r.get("status") == "completed":
                # Write extracted text
                if r.get("text"):
                    zf.writestr(f"{base_name}_text.txt", r["text"])

                # Write summary
                if r.get("summary"):
                    zf.writestr(f"{base_name}_summary.txt", r["summary"])

                # Write key points
                if r.get("key_points"):
                    zf.writestr(f"{base_name}_key_points.txt", r["key_points"])
            elif r.get("status") == "failed":
                zf.writestr(f"{base_name}_error.txt", f"处理失败: {r.get('error', 'Unknown error')}")

    zip_buffer.seek(0)
    zip_filename = f"batch_results_{batch_id[:8]}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
    )


# ---------------------------------------------------------------------------
# GET /history
# ---------------------------------------------------------------------------


@router.get("/history", response_model=DocumentHistoryResponse)
async def get_document_history(
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db)] = ...,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DocumentHistoryResponse:
    """Get user's document processing history."""
    # Get total count
    count_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.user_id == current_user.id
        )
    )
    total = count_result.scalar_one()

    # Get paginated documents
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.upload_time.desc())
        .offset(offset)
        .limit(page_size)
    )
    docs = result.scalars().all()

    items = []
    for doc in docs:
        items.append(DocumentItem(
            id=doc.id,
            filename=doc.filename,
            original_filename=doc.original_filename,
            file_type=doc.file_type,
            file_size=doc.file_size,
            status=doc.status,
            summary=doc.summary,
            error_message=doc.error_message,
            upload_time=doc.upload_time,
            batch_id=None,
        ))

    return DocumentHistoryResponse(
        documents=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /{document_id}
# ---------------------------------------------------------------------------


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_user)] = ...,
    db: Annotated[AsyncSession, Depends(get_db)] = ...,
) -> dict[str, Any]:
    """Get a specific document's details."""
    result = await db.execute(
        select(Document)
        .where(Document.id == document_id)
        .where(Document.user_id == current_user.id)
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # Try to read extracted text if available
    text_content = ""
    if doc.file_path and os.path.exists(doc.file_path):
        try:
            processor = DocumentProcessor()
            parsed = await processor.parse_file(doc.file_path)
            text_content = parsed.get("text", "")
        except Exception:
            text_content = ""

    return {
        "id": doc.id,
        "filename": doc.filename,
        "original_filename": doc.original_filename,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status,
        "summary": doc.summary,
        "error_message": doc.error_message,
        "upload_time": doc.upload_time.isoformat() if doc.upload_time else None,
        "text": text_content[:10000] if text_content else "",
    }