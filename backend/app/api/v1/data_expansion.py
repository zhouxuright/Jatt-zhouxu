"""Data expansion API endpoints -- data source management and pipeline execution.

Endpoints:
- GET /data/sources -- List all data sources
- GET /data/stats -- Get pipeline statistics
- POST /data/import/cail -- Import CAIL dataset
- POST /data/crawl/wenshu -- Start wenshu crawl
- POST /data/crawl/flk -- Start FLK crawl
- POST /data/pipeline/run -- Run full expansion pipeline
"""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.rag.data_expansion_pipeline import (
    DataExpansionPipeline,
    CAILDatasetImporter,
    get_data_expansion_pipeline,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Request / Response schemas
# ============================================================================

class CAILImportRequest(BaseModel):
    """Request to import CAIL dataset."""
    dataset_id: str = Field(..., description="Dataset ID: cail2018, cail2019, cail2021")
    data_dir: str | None = Field(default=None, description="Directory containing dataset files")
    batch_size: int = Field(default=200, ge=1, le=1000)


class PipelineRunRequest(BaseModel):
    """Request to run the data expansion pipeline."""
    source_ids: list[str] | None = Field(default=None, description="Specific sources to run (None = all)")
    max_records: int | None = Field(default=None, description="Max records per source")


# ============================================================================
# GET /data/sources -- List all data sources
# ============================================================================

@router.get("/data/sources", summary="List all legal data sources")
async def list_data_sources() -> dict[str, Any]:
    """List all configured legal data sources with status."""
    pipeline = get_data_expansion_pipeline()
    sources = pipeline.get_sources()
    return {
        "sources": sources,
        "total": len(sources),
        "total_estimated_records": sum(s["estimated_records"] for s in sources),
    }


# ============================================================================
# GET /data/stats -- Pipeline statistics
# ============================================================================

@router.get("/data/stats", summary="Get data pipeline statistics")
async def get_pipeline_stats() -> dict[str, Any]:
    """Get statistics about data import progress.

    `DataExpansionPipeline.get_stats()` counts only in-process import events,
    so it reports `imported: 0` on every restart no matter how much data the
    database actually holds. The corpus tables are the source of truth, so
    measure them directly and report the pipeline's view alongside.
    """
    from sqlalchemy import text

    from app.core.database import async_session_factory

    pipeline_stats = get_data_expansion_pipeline().get_stats()

    # Table -> the pipeline source id it satisfies. Counts are exact; the
    # corpus is large enough that an estimate would be misleading here.
    table_to_source = {
        "legal_articles": "flk_npc",
        "laws": "flk_npc",
        "court_cases": "wenshu",
        "judicial_interpretations": "judicial_interpretations",
        "legal_qa_pairs": "cail_dataset",
        "legal_knowledge_entries": "pkulaw",
    }

    measured: dict[str, int] = {}
    try:
        async with async_session_factory() as session:
            for table in table_to_source:
                result = await session.execute(text(f"SELECT count(*) FROM {table}"))
                measured[table] = int(result.scalar() or 0)
    except Exception as exc:  # pragma: no cover - diagnostics must not 500
        logger.warning("Could not measure corpus tables: %s", exc)
        measured = {}

    total_in_db = sum(measured.values())
    sources = pipeline_stats.get("sources", {})
    for table, source_id in table_to_source.items():
        if source_id in sources and table in measured:
            sources[source_id]["records_in_db"] = measured[table]
            if measured[table] > 0:
                sources[source_id]["status"] = "imported"

    return {
        **pipeline_stats,
        "measured_tables": measured,
        "total_records_in_db": total_in_db,
        "coverage_percent": round(
            total_in_db / max(pipeline_stats["total_estimated_records"], 1) * 100, 4
        ),
        "note": (
            "total_records_imported counts this process's import events and "
            "resets on restart; use total_records_in_db (measured) instead."
        ),
    }


# ============================================================================
# POST /data/import/cail -- Import CAIL dataset
# ============================================================================

@router.post("/data/import/cail", summary="Import CAIL open-source dataset")
async def import_cail_dataset(request: CAILImportRequest) -> dict[str, Any]:
    """Import a CAIL dataset into the database.

    Supported datasets:
    - cail2018: 268万 criminal case documents
    - cail2019: 50万 judicial exam questions
    - cail2021: 10万 legal reasoning samples
    """
    importer = CAILDatasetImporter()
    result = await importer.import_dataset(
        dataset_id=request.dataset_id,
        data_dir=request.data_dir,
        batch_size=request.batch_size,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Import failed"),
        )

    return result


# ============================================================================
# POST /data/pipeline/run -- Run expansion pipeline (streaming)
# ============================================================================

@router.post("/data/pipeline/run", summary="Run data expansion pipeline")
async def run_expansion_pipeline(request: PipelineRunRequest) -> StreamingResponse:
    """Run the data expansion pipeline with streaming progress.

    Returns SSE events showing progress for each data source.
    """
    import json

    pipeline = get_data_expansion_pipeline()

    async def event_generator():
        async for event in pipeline.run_expansion(
            source_ids=request.source_ids,
            max_records=request.max_records,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# GET /data/sources/{source_id} -- Get single source details
# ============================================================================

@router.get("/data/sources/{source_id}", summary="Get data source details")
async def get_data_source(source_id: str) -> dict[str, Any]:
    """Get details for a specific data source."""
    pipeline = get_data_expansion_pipeline()
    sources = pipeline.get_sources()
    for source in sources:
        if source["id"] == source_id:
            return source
    raise HTTPException(status_code=404, detail=f"Data source '{source_id}' not found")
