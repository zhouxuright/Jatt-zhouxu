"""Batch processing schemas for document upload and processing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FileResult(BaseModel):
    """Result of processing a single file in a batch."""
    filename: str
    status: str = Field(..., description="completed, failed, processing, pending")
    text: str | None = None
    summary: str | None = None
    key_points: str | None = None
    error: str | None = None
    processing_time_seconds: float | None = None
    document_id: str | None = None


class BatchUploadResponse(BaseModel):
    """Response after submitting a batch upload."""
    batch_id: str
    total_files: int
    status: str = Field(default="processing", description="pending, processing, completed, failed")
    message: str


class BatchStatusResponse(BaseModel):
    """Status of a batch processing job."""
    batch_id: str
    status: str = Field(..., description="pending, processing, completed, failed")
    total_files: int
    completed_files: int
    failed_files: int
    progress_percent: float = Field(..., ge=0, le=100)
    mode: str = Field(default="analyze")
    started_at: str
    completed_at: str | None = None
    results: list[FileResult] = []


class BatchDownloadResponse(BaseModel):
    """Placeholder for batch download info (actual endpoint returns a file)."""
    batch_id: str
    filename: str
    download_url: str


class DocumentItem(BaseModel):
    """A single document in the user's processing history."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    original_filename: str
    file_type: str
    file_size: int
    status: str
    summary: str | None = None
    error_message: str | None = None
    upload_time: datetime
    batch_id: str | None = None


class DocumentHistoryResponse(BaseModel):
    """Paginated list of user's processed documents."""
    documents: list[DocumentItem]
    total: int
    page: int = 1
    page_size: int = 20


class BatchContractReviewResponse(BaseModel):
    """Response after submitting a batch contract review."""
    batch_id: str
    total_files: int
    status: str = Field(default="processing")
    message: str
