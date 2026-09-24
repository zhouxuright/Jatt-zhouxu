"""Batch upload API -- knowledge-base ingestion with async tracking.

Provides endpoints for uploading multiple documents (PDF, DOCX, TXT, MD),
parsing them, generating embeddings, and storing in both PostgreSQL (metadata)
and Milvus/ChromaDB (vectors). Includes status polling, history listing,
and semantic search within uploaded documents.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.tenancy import tenant_of
from app.core.validators import sanitize_filename
from app.models.document import Document, DocumentStatus
from app.models.user import User
from app.rag.document_processor import DocumentProcessor
from app.rag.embedding_service import EmbeddingService
from app.rag.vector_store import VectorStoreManager
from app.services.batch_processor import get_batch_processor

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BATCH_MAX_FILES = 20
_BATCH_MAX_TOTAL_SIZE_MB = 100

# Extended MIME types including Markdown (the base validators cover PDF/DOCX/TXT)
_ALLOWED_MIME_TYPES: set[str] = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}

_ALLOWED_EXTENSIONS: set[str] = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown"}

# ---------------------------------------------------------------------------
# Lazy-loaded singletons
# ---------------------------------------------------------------------------

_embedding_service: EmbeddingService | None = None
_vector_store: VectorStoreManager | None = None
_document_processor: DocumentProcessor | None = None


def _get_embedding_service() -> EmbeddingService:
    """Return a singleton EmbeddingService."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


def _get_vector_store() -> VectorStoreManager:
    """Return a singleton VectorStoreManager."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreManager(embedding_service=_get_embedding_service())
    return _vector_store


def _get_document_processor() -> DocumentProcessor:
    """Return a singleton DocumentProcessor."""
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor


# ===========================================================================
# Request / Response schemas (inline to keep this module self-contained)
# ===========================================================================


class BatchUploadResponse(BaseModel):
    """Response after submitting a batch upload for knowledge-base ingestion."""

    task_id: str = Field(..., description="Unique task identifier for tracking")
    total_files: int
    status: str = Field(default="processing")
    message: str


class BatchStatusDetail(BaseModel):
    """Processing status for a single file inside a batch."""

    filename: str
    status: str
    error: str | None = None
    document_id: str | None = None
    chunks_count: int | None = None
    processing_time_seconds: float | None = None


class BatchStatusResponse(BaseModel):
    """Full status of a batch knowledge-base upload."""

    task_id: str
    status: str = Field(..., description="pending, processing, completed, failed, partial")
    total_files: int
    completed_files: int
    failed_files: int
    progress_percent: float = Field(..., ge=0, le=100)
    started_at: str
    completed_at: str | None = None
    results: list[BatchStatusDetail] = []


class BatchHistoryItem(BaseModel):
    """A single batch upload record in history."""

    task_id: str
    total_files: int
    completed_files: int
    failed_files: int
    status: str
    started_at: str
    completed_at: str | None = None


class BatchHistoryResponse(BaseModel):
    """Paginated list of past batch uploads."""

    uploads: list[BatchHistoryItem]
    total: int
    page: int = 1
    page_size: int = 20


class SearchRequest(BaseModel):
    """Search query within uploaded documents."""

    query: str = Field(..., min_length=1, max_length=2000, description="Search query text")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results")
    collection: str = Field(
        default="user_documents",
        description="Vector collection to search",
    )


class SearchResultItem(BaseModel):
    """A single search result."""

    id: str
    text: str
    score: float | None = None
    metadata: dict[str, Any] = {}


class SearchResponse(BaseModel):
    """Response from document search."""

    query: str
    results: list[SearchResultItem]
    total: int


# ===========================================================================
# Helpers
# ===========================================================================


async def _validate_files(
    files: list[UploadFile],
) -> list[tuple[str, bytes, str]]:
    """Validate uploaded files.

    Returns:
        List of (safe_filename, content, extension) tuples.

    Raises:
        HTTPException on validation failure.
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

    validated: list[tuple[str, bytes, str]] = []
    total_size = 0

    for file in files:
        content = await file.read()
        await file.seek(0)  # reset for potential re-reads

        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件 '{file.filename}' 为空",
            )

        total_size += len(content)
        if total_size > _BATCH_MAX_TOTAL_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件总大小超过限制（最大 {_BATCH_MAX_TOTAL_SIZE_MB}MB）",
            )

        # Sanitize filename
        safe_name = sanitize_filename(file.filename)
        ext = os.path.splitext(safe_name)[1].lower()

        if ext not in _ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"不支持的文件格式: {ext}。支持: PDF, DOCX, DOC, TXT, MD",
            )

        # Validate MIME type (allow Markdown which the base validator doesn't)
        content_type = (file.content_type or "").strip().lower()
        if ext in (".md", ".markdown"):
            # Markdown files may report as text/plain
            if content_type and content_type not in _ALLOWED_MIME_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"文件 '{file.filename}' MIME 类型不允许: {content_type}",
                )
        else:
            # For non-markdown, enforce the standard MIME check
            if content_type and content_type not in _ALLOWED_MIME_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"文件 '{file.filename}' 类型不允许: {content_type}",
                )

        # Quick magic-byte sanity check for binary formats
        if ext == ".pdf" and not content[:4].startswith(b"%PDF"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件 '{file.filename}' 不是有效的 PDF 文件",
            )
        if ext == ".docx" and not content[:2].startswith(b"PK"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件 '{file.filename}' 不是有效的 DOCX 文件",
            )

        validated.append((safe_name, content, ext))

    return validated


async def _process_batch_task(
    task_id: str,
    file_records: list[dict[str, Any]],
    user_id: str,
    tenant_id: str,
) -> None:
    """Background task: parse files, store in DB and vector store.

    Args:
        task_id: The batch task identifier.
        file_records: List of dicts with keys: filename, content (bytes), ext, file_path.
        user_id: The owning user ID.
        tenant_id: The owning tenant ID (P0-2，批量上传也须打租户标).
    """
    processor = get_batch_processor()
    doc_processor = _get_document_processor()
    vector_store = _get_vector_store()

    # Ensure vector store is initialized
    try:
        await vector_store.initialize()
    except Exception as exc:
        logger.warning("Vector store initialization failed (continuing without vectors): %s", exc)

    batch_data = await processor.get_batch_raw(task_id)
    if not batch_data:
        logger.error("Task %s not found for processing", task_id)
        return

    results = batch_data.get("results", [])
    total = len(results)

    for i, record in enumerate(file_records):
        filename = record["filename"]
        file_path = record["file_path"]
        result_entry = results[i] if i < len(results) else None

        result_entry_status = "processing"
        if result_entry is not None:
            result_entry["status"] = "processing"
            batch_data["status"] = "processing"
            await processor._save_batch(task_id, batch_data)

        start_time = time.time()

        try:
            # ---------------------------------------------------------------
            # 1. Parse the document
            # ---------------------------------------------------------------
            parsed = await doc_processor.parse_file(file_path)
            text = parsed.get("text", "")
            chunks = parsed.get("chunks", [])
            metadata = parsed.get("metadata", {})

            if not text.strip():
                raise ValueError("No extractable text found in file")

            # ---------------------------------------------------------------
            # 2. Store metadata in PostgreSQL
            # ---------------------------------------------------------------
            doc_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            from app.core.database import async_session_factory

            async with async_session_factory() as session:
                doc_record = Document(
                    id=doc_id,
                    user_id=user_id,
                    # P0-2：写入时即打租户标
                    tenant_id=tenant_id,
                    filename=f"{task_id}_{filename}",
                    original_filename=filename,
                    file_type=record["ext"].lstrip("."),
                    file_size=len(record["content"]),
                    file_path=file_path,
                    status=DocumentStatus.READY,
                    summary=_generate_brief_summary(text),
                )
                session.add(doc_record)
                await session.commit()

            # ---------------------------------------------------------------
            # 3. Generate embeddings and store in vector store
            # ---------------------------------------------------------------
            vectors_stored = 0
            try:
                # Use chunks for vector storage; fall back to full text if no chunks
                chunks_to_store = chunks if chunks else [text[:6000]]

                # Build per-chunk metadata
                chunk_metadatas = []
                for ci, chunk in enumerate(chunks_to_store):
                    chunk_metadatas.append({
                        "document_id": doc_id,
                        "user_id": user_id,
                        "filename": filename,
                        "chunk_index": ci,
                        "total_chunks": len(chunks_to_store),
                        "upload_task_id": task_id,
                        "upload_time": now.isoformat(),
                        **({k: str(v) for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}),
                    })

                chunk_ids = [
                    f"{doc_id}_chunk_{ci}" for ci in range(len(chunks_to_store))
                ]

                await vector_store.add_documents(
                    texts=chunks_to_store,
                    collection_name="user_documents",
                    metadatas=chunk_metadatas,
                    ids=chunk_ids,
                )
                vectors_stored = len(chunks_to_store)

                logger.info(
                    "Task %s file '%s': stored %d chunks in vector store",
                    task_id, filename, vectors_stored,
                )
            except Exception as vec_exc:
                logger.warning(
                    "Task %s file '%s': vector store failed (metadata still saved): %s",
                    task_id, filename, vec_exc,
                )

            elapsed = time.time() - start_time

            if result_entry is not None:
                result_entry["status"] = "completed"
                result_entry["document_id"] = doc_id
                result_entry["chunks_count"] = len(chunks_to_store)
                result_entry["processing_time_seconds"] = round(elapsed, 2)
                result_entry["error"] = None

            batch_data["completed_files"] = batch_data.get("completed_files", 0) + 1

            logger.info(
                "Task %s file %d/%d completed: %s (%d chunks, %.2fs)",
                task_id, i + 1, total, filename, vectors_stored, elapsed,
            )

        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error(
                "Task %s file %d/%d failed: %s - %s",
                task_id, i + 1, total, filename, exc,
            )

            if result_entry is not None:
                result_entry["status"] = "failed"
                result_entry["error"] = str(exc)
                result_entry["processing_time_seconds"] = round(elapsed, 2)

            batch_data["failed_files"] = batch_data.get("failed_files", 0) + 1

        # Update progress
        processed = batch_data.get("completed_files", 0) + batch_data.get("failed_files", 0)
        batch_data["progress_percent"] = round((processed / total) * 100, 1) if total > 0 else 0
        await processor._save_batch(task_id, batch_data)

    # Clean up internal fields
    for fr in batch_data.get("results", []):
        fr.pop("_file_path", None)
        fr.pop("_content", None)
        fr.pop("_ext", None)

    # Determine final status
    completed = batch_data.get("completed_files", 0)
    failed = batch_data.get("failed_files", 0)
    if completed > 0 and failed > 0:
        batch_data["status"] = "partial"
    elif completed > 0:
        batch_data["status"] = "completed"
    else:
        batch_data["status"] = "failed"

    batch_data["completed_at"] = datetime.now(timezone.utc).isoformat()
    batch_data["progress_percent"] = 100.0
    await processor._save_batch(task_id, batch_data)

    logger.info(
        "Task %s finished: %d completed, %d failed out of %d",
        task_id, completed, failed, total,
    )


def _generate_brief_summary(text: str, max_len: int = 300) -> str:
    """Generate a brief summary from the first portion of text."""
    text = text.strip()
    if len(text) <= max_len:
        return text

    summary = text[:max_len]
    # Try to cut at a sentence boundary
    for punct in ("。", "！", "？", ".", "!", "?"):
        idx = summary.rfind(punct)
        if idx > 100:
            return summary[: idx + 1]

    return summary + "..."


# ===========================================================================
# POST /batch/upload
# ===========================================================================


@router.post(
    "/batch/upload",
    response_model=BatchUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload multiple files to knowledge base",
    description=(
        "Accept up to 20 files (PDF, DOCX, TXT, MD) for parsing, embedding "
        "generation, and storage in the knowledge base. Returns a task_id for "
        "async progress tracking."
    ),
)
async def batch_upload(
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    files: list[UploadFile] = File(..., description="Upload multiple document files (max 20)"),
) -> BatchUploadResponse:
    """Upload multiple files for knowledge-base ingestion.

    Each file is parsed using DocumentParser, chunks are embedded via the
    embedding service, and results are stored in PostgreSQL (metadata) and
    Milvus/ChromaDB (vectors). Processing happens asynchronously.
    """
    # Validate all files
    validated = await _validate_files(files)

    # Create a task_id for this batch
    task_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Persist files to disk for async processing
    upload_dir = os.path.join(settings.UPLOAD_DIR, "batch", task_id)
    os.makedirs(upload_dir, exist_ok=True)

    file_records: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []

    for idx, (safe_name, content, ext) in enumerate(validated):
        file_path = os.path.join(upload_dir, f"{idx}_{safe_name}")
        with open(file_path, "wb") as f:
            f.write(content)

        file_records.append({
            "filename": safe_name,
            "content": content,
            "ext": ext,
            "file_path": file_path,
        })

        file_results.append({
            "filename": safe_name,
            "status": "pending",
            "document_id": None,
            "error": None,
            "chunks_count": None,
            "processing_time_seconds": None,
            "_file_path": file_path,
        })

    # Initialize batch tracking in the batch processor (reuses Redis/memory store)
    processor = get_batch_processor()
    batch_data = {
        "batch_id": task_id,
        "user_id": current_user.id,
        "status": "processing",
        "mode": "knowledge_ingest",
        "total_files": len(validated),
        "completed_files": 0,
        "failed_files": 0,
        "progress_percent": 0.0,
        "started_at": now,
        "completed_at": None,
        "results": file_results,
    }
    await processor._save_batch(task_id, batch_data)

    # Launch background processing
    background_tasks.add_task(
        _process_batch_task,
        task_id,
        file_records,
        current_user.id,
        tenant_of(current_user),
    )

    logger.info(
        "Batch upload task %s created: %d files, user=%s",
        task_id, len(validated), current_user.id[:8],
    )

    return BatchUploadResponse(
        task_id=task_id,
        total_files=len(validated),
        status="processing",
        message=f"批量上传任务已提交，共 {len(validated)} 个文件，正在后台处理。",
    )


# ===========================================================================
# GET /batch/status/{task_id}
# ===========================================================================


@router.get(
    "/batch/status/{task_id}",
    response_model=BatchStatusResponse,
    summary="Check batch upload processing status",
)
async def get_batch_status(
    task_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> BatchStatusResponse:
    """Return the processing status and per-file results for a batch upload."""
    processor = get_batch_processor()
    raw = await processor.get_batch_raw(task_id)

    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="批次任务不存在或已过期",
        )

    # Verify ownership
    if raw.get("user_id") != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问该批次任务",
        )

    # Also check if it was a knowledge_ingest task (could also accept regular batch tasks)
    result_details: list[BatchStatusDetail] = []
    for r in raw.get("results", []):
        result_details.append(BatchStatusDetail(
            filename=r.get("filename", "unknown"),
            status=r.get("status", "pending"),
            error=r.get("error"),
            document_id=r.get("document_id"),
            chunks_count=r.get("chunks_count"),
            processing_time_seconds=r.get("processing_time_seconds"),
        ))

    return BatchStatusResponse(
        task_id=task_id,
        status=raw.get("status", "unknown"),
        total_files=raw.get("total_files", 0),
        completed_files=raw.get("completed_files", 0),
        failed_files=raw.get("failed_files", 0),
        progress_percent=raw.get("progress_percent", 0.0),
        started_at=raw.get("started_at", ""),
        completed_at=raw.get("completed_at"),
        results=result_details,
    )


# ===========================================================================
# GET /batch/history
# ===========================================================================


@router.get(
    "/batch/history",
    response_model=BatchHistoryResponse,
    summary="List past batch uploads for the current user",
)
async def get_batch_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
) -> BatchHistoryResponse:
    """Return paginated history of batch uploads, derived from document records.

    Groups documents by their upload patterns to reconstruct batch records.
    """
    # Count documents that were uploaded via batch (have a batch-like naming pattern)
    # We query documents owned by the user and group by upload_time proximity
    count_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.user_id == current_user.id,
        )
    )
    total_docs = count_result.scalar_one()

    # Fetch paginated documents
    offset = (page - 1) * page_size
    result = await db.execute(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.upload_time.desc())
        .offset(offset)
        .limit(page_size)
    )
    docs = result.scalars().all()

    # Group documents into "batch" items by file_path prefix pattern (batch/<task_id>/)
    batches: dict[str, list[Document]] = {}
    for doc in docs:
        # Extract task_id from file path if it follows the batch/<task_id>/ pattern
        parts = doc.file_path.replace("\\", "/").split("/")
        task_id = None
        for pi, part in enumerate(parts):
            if part == "batch" and pi + 1 < len(parts):
                task_id = parts[pi + 1]
                break

        if task_id:
            batches.setdefault(task_id, []).append(doc)
        else:
            # Individual (non-batch) upload -- use doc id as key
            batches.setdefault(f"single_{doc.id}", []).append(doc)

    history_items: list[BatchHistoryItem] = []
    for tid, batch_docs in batches.items():
        is_batch = not tid.startswith("single_")
        total_files = len(batch_docs)
        completed_files = sum(1 for d in batch_docs if d.status == DocumentStatus.READY)
        failed_files = sum(1 for d in batch_docs if d.status == DocumentStatus.FAILED)

        # Derive status
        if failed_files == total_files:
            item_status = "failed"
        elif completed_files == total_files:
            item_status = "completed"
        elif completed_files > 0:
            item_status = "partial"
        else:
            item_status = "processing"

        # Use earliest upload time
        earliest = min(
            (d.upload_time for d in batch_docs if d.upload_time),
            default=datetime.now(timezone.utc),
        )

        history_items.append(BatchHistoryItem(
            task_id=tid if is_batch else batch_docs[0].id,
            total_files=total_files,
            completed_files=completed_files,
            failed_files=failed_files,
            status=item_status,
            started_at=earliest.isoformat() if earliest else "",
            completed_at=earliest.isoformat() if earliest else None,
        ))

    # Sort by started_at descending
    history_items.sort(key=lambda x: x.started_at, reverse=True)

    return BatchHistoryResponse(
        uploads=history_items,
        total=len(history_items),
        page=page,
        page_size=page_size,
    )


# ===========================================================================
# POST /batch/search
# ===========================================================================


@router.post(
    "/batch/search",
    response_model=SearchResponse,
    summary="Search within uploaded documents",
)
async def search_documents(
    payload: SearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> SearchResponse:
    """Semantic search within user-uploaded documents using vector similarity.

    Searches the 'user_documents' collection in the vector store, filtering
    results to only those belonging to the authenticated user.
    """
    vector_store = _get_vector_store()

    try:
        await vector_store.initialize()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"向量存储服务不可用: {str(exc)}",
        )

    try:
        raw_results = await vector_store.similarity_search_with_score(
            query=payload.query,
            collection_name=payload.collection,
            top_k=payload.top_k * 2,  # fetch extra for post-filtering
        )
    except Exception as exc:
        logger.error("Vector search failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"搜索失败: {str(exc)}",
        )

    # Filter results to only those belonging to the current user
    user_results: list[SearchResultItem] = []
    for r in raw_results:
        meta = r.get("metadata", {})
        result_user_id = meta.get("user_id")

        # If metadata contains user_id, enforce ownership
        if result_user_id and result_user_id != current_user.id:
            continue

        user_results.append(SearchResultItem(
            id=r.get("id", ""),
            text=r.get("text", ""),
            score=r.get("score"),
            metadata=meta,
        ))

        if len(user_results) >= payload.top_k:
            break

    return SearchResponse(
        query=payload.query,
        results=user_results,
        total=len(user_results),
    )
