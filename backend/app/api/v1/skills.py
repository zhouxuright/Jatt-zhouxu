"""Skills API endpoints -- skill discovery, execution, and management.

Endpoints:
- GET /skills -- List available skills
- GET /skills/categories -- List skill categories
- GET /skills/{skill_id} -- Get skill detail
- POST /skills/execute -- Execute a skill
- POST /skills/execute-stream -- Execute with streaming progress (SSE)
- POST /skills/search -- Search skills by keyword
"""
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.skills import SkillEngine, get_skill_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Request / Response schemas
# ============================================================================

class SkillExecuteRequest(BaseModel):
    """Request to execute a skill."""
    skill_id: str = Field(..., description="Skill ID to execute")
    input_data: dict[str, Any] = Field(default_factory=dict, description="Skill input parameters")


class SkillSearchRequest(BaseModel):
    """Request to search skills."""
    query: str = Field(..., description="Search query")
    category: str | None = Field(default=None, description="Filter by category")


class SkillStepInfo(BaseModel):
    """Info about a skill step."""
    id: str
    name: str
    type: str
    description: str = ""


class SkillDetailResponse(BaseModel):
    """Detailed skill information."""
    id: str
    name: str
    description: str
    category: str
    version: str
    author: str
    tags: list[str]
    steps_count: int
    steps: list[SkillStepInfo] = []
    estimated_time_seconds: int
    difficulty: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class SkillListResponse(BaseModel):
    """Response listing available skills."""
    skills: list[dict[str, Any]]
    categories: list[str]
    total: int


class SkillExecuteResponse(BaseModel):
    """Response from skill execution."""
    success: bool
    skill_id: str = ""
    skill_name: str = ""
    steps_executed: int = 0
    elapsed_seconds: float = 0.0
    final_summary: str = ""
    step_results: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ============================================================================
# GET /skills -- List all available skills
# ============================================================================

@router.get("/skills", response_model=SkillListResponse, summary="List available skills")
async def list_skills(
    category: str | None = Query(default=None, description="Filter by category"),
) -> SkillListResponse:
    """List all available skill packages."""
    engine = get_skill_engine()
    skills = engine.list_skills(category=category)
    categories = engine.list_categories()
    return SkillListResponse(skills=skills, categories=categories, total=len(skills))


# ============================================================================
# GET /skills/categories -- List skill categories
# ============================================================================

@router.get("/skills/categories", summary="List skill categories")
async def list_skill_categories() -> dict[str, Any]:
    """List all available skill categories."""
    engine = get_skill_engine()
    return {"categories": engine.list_categories()}


# ============================================================================
# GET /skills/stats -- Skill engine statistics (must precede /skills/{skill_id})
# ============================================================================

@router.get("/skills/stats", summary="Get skill engine statistics")
async def get_skill_stats() -> dict[str, Any]:
    """Get statistics about skill usage and engine state."""
    engine = get_skill_engine()
    return engine.get_stats()


# ============================================================================
# GET /skills/{skill_id} -- Get skill detail
# ============================================================================

@router.get("/skills/{skill_id}", response_model=SkillDetailResponse, summary="Get skill detail")
async def get_skill_detail(skill_id: str) -> SkillDetailResponse:
    """Get detailed information about a specific skill."""
    engine = get_skill_engine()
    skill = engine.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")

    steps_info = [
        SkillStepInfo(id=s.id, name=s.name, type=s.type, description=s.description)
        for s in skill.steps
    ]

    return SkillDetailResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        category=skill.category,
        version=skill.version,
        author=skill.author,
        tags=skill.tags,
        steps_count=len(skill.steps),
        steps=steps_info,
        estimated_time_seconds=skill.estimated_time_seconds,
        difficulty=skill.difficulty,
        input_schema=skill.input_schema,
    )


# ============================================================================
# POST /skills/execute -- Execute a skill
# ============================================================================

@router.post("/skills/execute", response_model=SkillExecuteResponse, summary="Execute a skill")
async def execute_skill(request: SkillExecuteRequest) -> SkillExecuteResponse:
    """Execute a skill package with the given input data."""
    engine = get_skill_engine()

    result = await engine.execute_skill(
        skill_id=request.skill_id,
        input_data=request.input_data,
    )

    return SkillExecuteResponse(
        success=result.get("success", False),
        skill_id=result.get("skill_id", ""),
        skill_name=result.get("skill_name", ""),
        steps_executed=result.get("steps_executed", 0),
        elapsed_seconds=result.get("elapsed_seconds", 0.0),
        final_summary=result.get("final_summary", ""),
        step_results=result.get("step_results", {}),
        error=result.get("error"),
    )


# ============================================================================
# POST /skills/execute-stream -- Execute with SSE streaming progress
# ============================================================================

@router.post("/skills/execute-stream", summary="Execute skill with streaming progress")
async def execute_skill_stream(request: SkillExecuteRequest) -> StreamingResponse:
    """Execute a skill with Server-Sent Events streaming progress updates."""
    engine = get_skill_engine()

    async def event_generator():
        async for event in engine.execute_skill_stream(
            skill_id=request.skill_id,
            input_data=request.input_data,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# ============================================================================
# POST /skills/search -- Search skills
# ============================================================================

@router.post("/skills/search", summary="Search skills by keyword")
async def search_skills(request: SkillSearchRequest) -> dict[str, Any]:
    """Search skills by keyword in name, description, or tags."""
    engine = get_skill_engine()
    results = engine.search_skills(request.query)

    if request.category:
        results = [r for r in results if r.get("category") == request.category]

    return {"results": results, "count": len(results)}



