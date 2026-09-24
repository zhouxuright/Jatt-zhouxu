"""Contract review endpoints.

Uses the ContractReviewAgent for AI-powered contract analysis and risk detection.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contract_review_agent import ContractReviewAgent, create_contract_review_agent
from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.tenancy import tenant_of
from app.core.validators import validate_file_upload, sanitize_filename, sanitize_input
from app.middleware.content_safety import ContentTooLongError, get_content_safety_filter
from app.models.document import ContractReview, Document, DocumentStatus
from app.models.user import User
from app.rag.document_processor import DocumentProcessor
from app.services.ocr_service import get_ocr_service
from app.schemas.contract import (
    ContractReviewListResponse,
    ContractReviewRequest,
    ContractReviewResponse,
    RiskItem,
)
from app.services.task_manager import get_task_manager, execute_contract_review
from app.schemas.batch import BatchContractReviewResponse
from app.services.contract_templates import get_contract_template_engine
from app.services.content_watermark import get_content_watermark_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Virus scan placeholder
# ---------------------------------------------------------------------------
_VIRUS_SCAN_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB


def _virus_scan_placeholder(file_content: bytes, filename: str) -> None:
    """Placeholder for virus/malware scanning.

    In production, integrate ClamAV, AWS GuardDuty, or a similar service.
    For now, log a warning for files exceeding the threshold.
    """
    if len(file_content) > _VIRUS_SCAN_THRESHOLD_BYTES:
        logger.warning(
            "Virus scan placeholder: file '%s' is %d bytes (> %d MB). "
            "Integrate a real virus scanner (e.g. ClamAV) for production use.",
            filename,
            len(file_content),
            _VIRUS_SCAN_THRESHOLD_BYTES // (1024 * 1024),
        )

# ---------------------------------------------------------------------------
# File upload security (delegates to app.core.validators)
# ---------------------------------------------------------------------------


def _sanitize_and_rename(filename: str | None) -> tuple[str, str]:
    """Sanitize filename and generate a UUID-based name.

    Returns (uuid_filename, original_extension).
    """
    safe = sanitize_filename(filename)
    ext = os.path.splitext(safe)[1].lower()
    return f"{uuid.uuid4()}{ext}", ext


async def _validate_uploaded_file(file: UploadFile) -> tuple[bytes, str]:
    """Validate an uploaded file using the centralized validators.

    Returns (file_content, safe_extension).
    Raises HTTPException on any validation failure.
    """
    file_content, ext = await validate_file_upload(file)

    # Virus scan placeholder -- warn for large files
    _virus_scan_placeholder(file_content, file.filename or "upload.txt")

    return file_content, ext

# ---------------------------------------------------------------------------
# Lazy-loaded agent instance
# ---------------------------------------------------------------------------
_contract_review_agent: ContractReviewAgent | None = None


def get_contract_review_agent() -> ContractReviewAgent:
    """Return a singleton ContractReviewAgent instance."""
    global _contract_review_agent
    if _contract_review_agent is None:
        _contract_review_agent = create_contract_review_agent()
    return _contract_review_agent


# ---------------------------------------------------------------------------
# POST /review
# ---------------------------------------------------------------------------


@router.post("/review", response_model=ContractReviewResponse, status_code=status.HTTP_201_CREATED)
async def review_contract(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile | None = File(
        default=None,
        description="Contract file to review (PDF, DOCX, or TXT)",
    ),
    document_id: UUID | None = Form(
        default=None,
        description="ID of a previously uploaded document",
    ),
    review_type: str = Form(
        default="comprehensive",
        description="Review type: comprehensive, quick, risk_only, compliance",
    ),
) -> ContractReviewResponse:
    """Upload and review a contract for legal risks.

    Accepts either a file upload or a document_id (previously uploaded).
    The contract is parsed, then analyzed by the ContractReviewAgent via LangGraph.
    Results include structured risk analysis with risk levels, categories,
    descriptions, and suggestions.
    """
    contract_text = ""
    doc_id: UUID | None = document_id

    # ------------------------------------------------------------------
    # 1. Obtain contract text from file upload or existing document
    # ------------------------------------------------------------------
    if file is not None:
        # Validate file: size, MIME type, extension, magic bytes
        file_content, ext = await _validate_uploaded_file(file)

        # Save the uploaded file with UUID filename
        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        safe_name, _ = _sanitize_and_rename(file.filename)
        save_path = os.path.join(upload_dir, safe_name)

        with open(save_path, "wb") as f:
            f.write(file_content)

        # Record document in database
        doc_record = Document(
            user_id=current_user.id,
            # P0-2：写入时即打租户标，避免出现 tenant_id 为空的"隐形"文档
            # （仅靠一次性回填会在新写入路径上持续漏标）
            tenant_id=tenant_of(current_user),
            filename=safe_name,
            original_filename=os.path.basename(file.filename or "upload.txt"),
            file_type=ext.lstrip("."),
            file_size=len(file_content),
            file_path=save_path,
            status=DocumentStatus.PROCESSING,
        )
        db.add(doc_record)
        await db.flush()
        doc_id = doc_record.id

        # Parse the document
        try:
            processor = DocumentProcessor()
            parsed = await processor.parse_file(save_path)
            contract_text = parsed.get("text", "")
        except Exception as exc:
            doc_record.status = DocumentStatus.FAILED
            doc_record.error_message = str(exc)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to parse document: {str(exc)}",
            )

        # --- OCR fallback for scanned documents ---
        # If extracted text is very short, the PDF may be a scanned document.
        # Try OCR automatically if the feature is enabled and available.
        if settings.OCR_ENABLED and len(contract_text.strip()) < 100:
            try:
                ocr_service = get_ocr_service()
                if ocr_service.available:
                    original_filename = os.path.basename(file.filename or "upload.pdf")
                    ocr_result = await ocr_service.process_contract_upload(
                        file_content, original_filename,
                    )
                    ocr_text = ocr_result.get("text", "")
                    if len(ocr_text.strip()) > len(contract_text.strip()):
                        logger.info(
                            "OCR fallback: extracted %d chars (was %d) from '%s'",
                            len(ocr_text), len(contract_text), file.filename,
                        )
                        contract_text = ocr_text
            except Exception as ocr_exc:
                logger.warning("OCR fallback failed for '%s': %s", file.filename, ocr_exc)

        doc_record.status = DocumentStatus.READY
        doc_record.summary = contract_text[:500] if contract_text else ""

    elif document_id is not None:
        # Load from existing document
        result = await db.execute(
            select(Document)
            .where(Document.id == document_id)
            .where(Document.user_id == current_user.id)
        )
        doc_record = result.scalar_one_or_none()
        if doc_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )

        if doc_record.status != DocumentStatus.READY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document is not ready for review. Status: {doc_record.status}",
            )

        # Parse the stored document
        try:
            processor = DocumentProcessor()
            parsed = await processor.parse_file(doc_record.file_path)
            contract_text = parsed.get("text", "")
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to parse document: {str(exc)}",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either file upload or document_id is required.",
        )

    if not contract_text or not contract_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No extractable text found in the document.",
        )

    # 长度校验（与内容安全解耦）。此前长度上限（10,000 字符，本是聊天输入的限制）
    # 被塞在 check_input 里，导致上万字的合同/法条被当成"内容不安全"拒绝并返回 400，
    # 前端只显示一句"请求失败"，用户完全无从判断。现改为：超长 → 413 + 可操作提示。
    safety_filter = get_content_safety_filter()
    try:
        safety_filter.validate_length(contract_text, settings.MAX_CONTRACT_REVIEW_CHARS)
    except ContentTooLongError as exc:
        raise HTTPException(
            # 直接用数值：413 常量名在各 Starlette 版本间有变
            # （REQUEST_ENTITY_TOO_LARGE 已更名为 CONTENT_TOO_LARGE）
            status_code=413,
            detail=(
                f"合同文本过长：共 {exc.length:,} 字符，超过上限 {exc.max_length:,} 字符。"
                f"请拆分合同后分批审查，或仅上传需要审查的章节。"
            ),
        ) from exc

    # Content safety check on contract text
    is_safe, safety_reason = safety_filter.check_input(
        contract_text, max_length=settings.MAX_CONTRACT_REVIEW_CHARS
    )
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"合同内容未通过安全检查：{safety_reason}",
        )

    # ------------------------------------------------------------------
    # 2. Run contract review through the ContractReviewAgent
    # ------------------------------------------------------------------
    try:
        agent = get_contract_review_agent()
        agent_result = await agent.run({
            "contract_text": contract_text,
            "review_focus": review_type,
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Contract review failed: {str(exc)}",
        )

    # ------------------------------------------------------------------
    # 3. Build structured response
    # ------------------------------------------------------------------
    risk_score = agent_result.get("risk_score", 0)
    risk_level = agent_result.get("risk_level", "low")
    risk_items_raw = agent_result.get("risk_items", [])
    report = agent_result.get("report", "")
    missing_clauses = agent_result.get("missing_clauses", [])

    # Map risk items to the response schema
    risks: list[RiskItem] = []
    for item in risk_items_raw:
        risk = RiskItem(
            risk_level=item.get("risk_level", "low"),
            category=item.get("risk_category", "unknown"),
            clause=item.get("clause_text", "")[:500],
            description=item.get("risk_description", "")[:500],
            suggestion=item.get("suggestion", "")
            or "请根据相关法律法规审查该条款的具体内容。",
        )
        risks.append(risk)

    # Build summary
    if report:
        summary = report[:1000]
    else:
        summary = (
            f"合同审查完成。综合风险等级：{risk_level}，"
            f"风险评分：{risk_score}/100，"
            f"识别风险项：{len(risks)}条，"
            f"缺失条款：{len(missing_clauses)}项。"
        )

    # Apply AI content identification watermark (人工智能生成合成内容标识办法)
    watermark_service = get_content_watermark_service()
    watermark_meta = watermark_service.get_watermark_metadata(
        model="deepseek-chat",
        user_id=str(current_user.id),
        content_type="contract",
    )
    summary = watermark_service.add_explicit_watermark(summary, content_type="contract")

    review_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    response = ContractReviewResponse(
        id=review_id,
        document_id=doc_id,
        status="completed",
        overall_score=risk_score,
        summary=summary,
        risks=risks,
        created_at=now,
        ai_generated=True,
        watermark_info=watermark_meta,
        generation_metadata=watermark_meta,
    )

    # Persist review to database
    original_filename = ""
    if file is not None:
        original_filename = os.path.basename(file.filename or "upload.txt")
    elif document_id is not None:
        original_filename = doc_record.original_filename if doc_record else ""

    db_review = ContractReview(
        id=review_id,
        user_id=current_user.id,
        document_id=doc_id,
        original_filename=original_filename,
        risk_score=float(risk_score) if risk_score is not None else None,
        risk_items=json.dumps([r.model_dump() for r in risks], ensure_ascii=False),
        summary=summary,
        full_analysis=report if report else None,
    )
    db.add(db_review)

    return response


# ---------------------------------------------------------------------------
# GET /reviews
# ---------------------------------------------------------------------------


@router.get("/reviews", response_model=ContractReviewListResponse)
async def list_reviews(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ContractReviewListResponse:
    """List all contract reviews for the authenticated user."""
    # Get total count
    count_result = await db.execute(
        select(func.count(ContractReview.id)).where(
            ContractReview.user_id == current_user.id
        )
    )
    total = count_result.scalar_one()

    # Get paginated reviews
    offset = (page - 1) * page_size
    result = await db.execute(
        select(ContractReview)
        .where(ContractReview.user_id == current_user.id)
        .order_by(ContractReview.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    db_reviews = result.scalars().all()

    reviews = []
    for r in db_reviews:
        risks_data = []
        if r.risk_items:
            try:
                risks_data = json.loads(r.risk_items)
            except (json.JSONDecodeError, TypeError):
                risks_data = []

        reviews.append(ContractReviewResponse(
            id=r.id,
            document_id=r.document_id,
            status="completed",
            overall_score=int(r.risk_score) if r.risk_score is not None else None,
            summary=r.summary,
            risks=[RiskItem(**item) for item in risks_data],
            created_at=r.created_at,
        ))

    return ContractReviewListResponse(
        reviews=reviews,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /reviews/{review_id}
# ---------------------------------------------------------------------------


@router.get("/reviews/{review_id}", response_model=ContractReviewResponse)
async def get_review(
    review_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContractReviewResponse:
    """Get a specific contract review by ID."""
    result = await db.execute(
        select(ContractReview)
        .where(ContractReview.id == review_id)
        .where(ContractReview.user_id == current_user.id)
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    risks_data = []
    if review.risk_items:
        try:
            risks_data = json.loads(review.risk_items)
        except (json.JSONDecodeError, TypeError):
            risks_data = []

    return ContractReviewResponse(
        id=review.id,
        document_id=review.document_id,
        status="completed",
        overall_score=int(review.risk_score) if review.risk_score is not None else None,
        summary=review.summary,
        risks=[RiskItem(**item) for item in risks_data],
        created_at=review.created_at,
    )


# ---------------------------------------------------------------------------
# POST /review-async  (async version)
# ---------------------------------------------------------------------------

@router.post("/review-async", status_code=status.HTTP_202_ACCEPTED)
async def review_contract_async(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile | None = File(default=None),
    contract_text: str = Form(default=""),
    review_type: str = Form(default="comprehensive"),
) -> dict[str, Any]:
    """异步合同审查 — 提交后立即返回 task_id，后台执行审查。

    通过 GET /tasks/{task_id} 查询进度和结果。
    """
    from app.rag.document_processor import DocumentProcessor
    from app.models.document import Document, DocumentStatus

    text = contract_text
    doc_record: Document | None = None

    if file is not None:
        # Validate file: size, MIME type, extension, magic bytes
        file_content, ext = await _validate_uploaded_file(file)

        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        safe_name, _ = _sanitize_and_rename(file.filename)
        save_path = os.path.join(upload_dir, safe_name)

        with open(save_path, "wb") as f:
            f.write(file_content)

        doc_record = Document(
            user_id=current_user.id,
            # P0-2：写入时即打租户标，避免出现 tenant_id 为空的"隐形"文档
            # （仅靠一次性回填会在新写入路径上持续漏标）
            tenant_id=tenant_of(current_user),
            filename=safe_name,
            original_filename=os.path.basename(file.filename or "upload.txt"),
            file_type=ext.lstrip("."),
            file_size=len(file_content),
            file_path=save_path,
            status=DocumentStatus.PROCESSING,
        )
        db.add(doc_record)
        await db.flush()

        try:
            processor = DocumentProcessor()
            parsed = await processor.parse_file(save_path)
            text = parsed.get("text", "")
            doc_record.status = DocumentStatus.READY
        except Exception as exc:
            doc_record.status = DocumentStatus.FAILED
            raise HTTPException(status_code=422, detail=f"文件解析失败: {exc}")

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="未提供合同文本")

    # 长度校验（与内容安全解耦，语义同 /review；超长返回 413 而非伪装的"内容不安全"）
    safety_filter = get_content_safety_filter()
    try:
        safety_filter.validate_length(text, settings.MAX_CONTRACT_REVIEW_CHARS)
    except ContentTooLongError as exc:
        raise HTTPException(
            # 直接用数值：413 常量名在各 Starlette 版本间有变
            # （REQUEST_ENTITY_TOO_LARGE 已更名为 CONTENT_TOO_LARGE）
            status_code=413,
            detail=(
                f"合同文本过长：共 {exc.length:,} 字符，超过上限 {exc.max_length:,} 字符。"
                f"请拆分合同后分批审查，或仅上传需要审查的章节。"
            ),
        ) from exc

    # Content safety check on contract text
    is_safe, safety_reason = safety_filter.check_input(
        text, max_length=settings.MAX_CONTRACT_REVIEW_CHARS
    )
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"合同内容未通过安全检查：{safety_reason}",
        )

    await db.commit()

    task_mgr = get_task_manager()
    task_id = await task_mgr.create_task(
        task_type="contract_review",
        user_id=current_user.id,
        metadata={
            "review_type": review_type,
            # 后台任务据此把审查记录落库（否则异步审查不出现在「审查历史」里）
            "document_id": str(doc_record.id) if doc_record is not None else None,
            "original_filename": (
                os.path.basename(file.filename or "upload.txt") if file is not None else ""
            ),
        },
    )
    background_tasks.add_task(execute_contract_review, task_id, text, review_type)

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "合同审查任务已提交，正在后台处理。",
    }


# =========================================================================
# 合同模板库 API
# =========================================================================

@router.get("/templates/catalog")
async def list_contract_templates(
    current_user: Annotated[User, Depends(get_current_user)],
    category: str | None = Query(default=None, description="按分类筛选"),
) -> dict[str, Any]:
    """获取合同模板目录（不含模板正文）。"""
    engine = get_contract_template_engine()
    templates = engine.list_templates(category=category)
    categories = engine.list_categories()

    return {
        "templates": templates,
        "categories": categories,
        "total": len(templates),
    }


@router.get("/templates/catalog/{template_id}")
async def get_contract_template(
    template_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """获取单个合同模板详情（含模板正文）。"""
    engine = get_contract_template_engine()
    template = engine.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    return template


@router.post("/templates/fill")
async def fill_contract_template(
    payload: dict[str, Any],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """用用户填写的字段填充合同模板，生成合同文本。"""
    template_id = payload.get("template_id", "")
    fields = payload.get("fields", {})

    if not template_id:
        raise HTTPException(status_code=400, detail="缺少 template_id")

    engine = get_contract_template_engine()
    result = engine.fill_template(template_id, fields)

    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "模板不存在"))

    return result


# =========================================================================
# 批量合同审查 API
# =========================================================================

_BATCH_REVIEW_MAX_FILES = 10


@router.post("/batch-review", response_model=BatchContractReviewResponse, status_code=status.HTTP_202_ACCEPTED)
async def batch_review_contracts(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: list[UploadFile] = File(..., description="上传多个合同文件（最多 10 个）"),
    review_type: str = Form(default="comprehensive", description="审查类型: comprehensive, quick, risk_only, compliance"),
) -> BatchContractReviewResponse:
    """Upload multiple contracts for batch review.

    Accept up to 10 contract files. Each is processed asynchronously.
    Returns a batch_id for tracking progress via GET /tasks/{task_id}.
    """
    from app.services.batch_processor import get_batch_processor

    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个合同文件")

    if len(files) > _BATCH_REVIEW_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多上传 {_BATCH_REVIEW_MAX_FILES} 个合同文件",
        )

    # Validate all files and collect text content
    validated_files: list[tuple[str, bytes]] = []
    for file in files:
        try:
            file_content, ext = await _validate_uploaded_file(file)
            validated_files.append((sanitize_filename(file.filename), file_content))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"文件 '{file.filename}' 验证失败: {str(exc)}",
            )

    # Use batch processor with 'analyze' mode for contract review
    processor = get_batch_processor()
    result = await processor.process_batch(
        files=validated_files,
        user_id=current_user.id,
        mode="analyze",
    )

    return BatchContractReviewResponse(
        batch_id=result["batch_id"],
        total_files=result["total_files"],
        status=result["status"],
        message=f"批量合同审查任务已提交，共 {result['total_files']} 个合同，正在后台处理。",
    )


# =========================================================================
# OCR: 扫描件文字识别 API
# =========================================================================

# Accepted MIME types for OCR uploads (PDFs + images)
_OCR_ALLOWED_MIME_TYPES: set[str] = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/bmp",
    "image/tiff",
    "image/webp",
}

_OCR_ALLOWED_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}


@router.post("/ocr")
async def ocr_extract(
    current_user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(
        ...,
        description="扫描件或图片文件（PDF, JPG, PNG）",
    ),
) -> dict[str, Any]:
    """Extract text from a scanned contract (image or PDF) using OCR.

    Accepts image files (JPG, PNG) and PDF files containing scanned pages.
    Returns the extracted text along with metadata about the OCR process.
    """
    if not settings.OCR_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OCR功能已禁用",
        )

    # --- Validate MIME type ---
    content_type = (file.content_type or "").strip().lower()
    if content_type not in _OCR_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: {content_type}。支持: PDF, JPG, PNG, BMP, TIFF, WebP",
        )

    # --- Read file content ---
    file_content = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(file_content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件过大，最大允许 {settings.MAX_UPLOAD_SIZE_MB}MB",
        )

    # --- Determine extension ---
    ext = _OCR_ALLOWED_EXTENSIONS.get(content_type, "")
    if not ext:
        ext = os.path.splitext(sanitize_filename(file.filename))[1].lower()

    original_filename = sanitize_filename(file.filename)
    safe_name = f"{ext}"

    # --- Run OCR ---
    try:
        ocr_service = get_ocr_service()
        if not ocr_service.available:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OCR引擎不可用，请安装paddleocr或pytesseract",
            )

        result = await ocr_service.process_contract_upload(file_content, original_filename)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR failed for '%s': %s", file.filename, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OCR识别失败: {exc}",
        )

    extracted_text = result.get("text", "")
    if not extracted_text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="未能从文件中识别出文字，请检查文件质量后重试",
        )

    return {
        "text": extracted_text,
        "pages": result.get("pages", []),
        "metadata": result.get("metadata", {}),
        "used_ocr": result.get("used_ocr", True),
        "filename": original_filename,
    }