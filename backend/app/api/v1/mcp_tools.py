"""MCP Tool Calling API endpoints.

Provides REST API for tool discovery, execution, and management.
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_admin_user
from app.mcp import (
    ToolRegistry,
    ToolResult,
    get_tool_registry,
    build_tool_calling_prompt,
    parse_tool_calls_from_text,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Executing a tool spends LLM/API budget, and tools that are not configured
# (e.g. `enterprise_lookup`, see app/mcp/__init__.py) answer with placeholder
# data -- `{"note": "...尚未配置", "source": "demo_mode"}` -- which must never
# reach a lawyer as if it were a real result. That is why the debug page lives
# at `/admin/tools` (frontend/src/router/index.ts).
#
# The frontend gate alone was not enough: any authenticated non-admin could
# still POST here directly. Discovery (`GET /tools`) stays open to all
# authenticated users because the chat page's tool picker uses it; only
# execution is admin-only. Chat's own LLM-driven tool calls do not come through
# these endpoints at all -- they run in-process via
# `services/tool_orchestrator.py` -> `get_tool_registry()`.
_admin_only = [Depends(get_current_admin_user)]


# ============================================================================
# Request / Response schemas
# ============================================================================

class ToolCallRequest(BaseModel):
    """Request to execute a single tool."""
    tool_name: str = Field(..., description="Tool name to execute")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")


class ToolCallResponse(BaseModel):
    """Response from tool execution."""
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchToolCallRequest(BaseModel):
    """Request to execute multiple tools in parallel."""
    calls: list[dict[str, Any]] = Field(
        ..., description='List of tool calls: [{"name": "tool_name", "param1": "val1", ...}]'
    )


class ToolInfo(BaseModel):
    """Schema information for a tool."""
    name: str
    description: str
    category: str
    version: str
    parameters: dict[str, Any]


class ToolListResponse(BaseModel):
    """Response listing available tools."""
    tools: list[ToolInfo]
    categories: list[str]
    total: int


class ToolCallPromptResponse(BaseModel):
    """Response containing the tool-calling system prompt."""
    system_prompt: str
    tool_count: int
    categories: list[str]


# ============================================================================
# GET /tools -- List all available tools
# ============================================================================

@router.get("/tools", response_model=ToolListResponse, summary="List all available MCP tools")
async def list_tools(
    category: str | None = Query(default=None, description="Filter by category"),
) -> ToolListResponse:
    """List all registered MCP tools with their schemas."""
    registry = await get_tool_registry()
    tools_data = registry.list_tools(category=category)
    categories = registry.list_categories()

    tools = [ToolInfo(**t) for t in tools_data]
    return ToolListResponse(tools=tools, categories=categories, total=len(tools))


# ============================================================================
# GET /tools/categories -- List tool categories
# ============================================================================

@router.get("/tools/categories", summary="List tool categories")
async def list_categories() -> dict[str, Any]:
    """List all available tool categories."""
    registry = await get_tool_registry()
    categories = registry.list_categories()
    return {"categories": categories}


# ============================================================================
# GET /tools/system-prompt -- must precede /tools/{tool_name} to avoid route shadowing
# ============================================================================

@router.get("/tools/system-prompt", response_model=ToolCallPromptResponse,
            summary="Get tool-calling system prompt")
async def get_tool_system_prompt() -> ToolCallPromptResponse:
    """Get the system prompt that enables tool-calling for the LLM."""
    registry = await get_tool_registry()
    prompt = await build_tool_calling_prompt()
    return ToolCallPromptResponse(
        system_prompt=prompt,
        tool_count=len(registry.list_tools()),
        categories=registry.list_categories(),
    )


# ============================================================================
# GET /tools/stats -- must precede /tools/{tool_name} to avoid route shadowing
# ============================================================================

@router.get("/tools/stats", summary="Get tool registry statistics")
async def get_registry_stats() -> dict[str, Any]:
    """Get statistics about tool usage and registry state."""
    registry = await get_tool_registry()
    return registry.get_stats()


# ============================================================================
# GET /tools/execution-log -- must precede /tools/{tool_name} to avoid route shadowing
# ============================================================================

@router.get("/tools/execution-log", summary="Get recent tool execution log")
async def get_execution_log(
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Get recent tool execution log entries for auditing."""
    registry = await get_tool_registry()
    log = registry.get_execution_log(limit=limit)
    return {"entries": log, "count": len(log)}


# ============================================================================
# GET /tools/{tool_name} -- Get tool schema
# ============================================================================

@router.get("/tools/{tool_name}", summary="Get tool schema")
async def get_tool_schema(tool_name: str) -> dict[str, Any]:
    """Get the full schema for a specific tool."""
    registry = await get_tool_registry()
    tool = registry.get_tool(tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return tool.get_schema()


# ============================================================================
# POST /tools/execute -- Execute a single tool
# ============================================================================

@router.post("/tools/execute", response_model=ToolCallResponse, summary="Execute an MCP tool",
             dependencies=_admin_only)
async def execute_tool(request: ToolCallRequest) -> ToolCallResponse:
    """Execute a registered MCP tool with the given parameters."""
    registry = await get_tool_registry()

    # Validate tool exists
    tool = registry.get_tool(request.tool_name)
    if not tool:
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{request.tool_name}' not found. Available: {list(registry._tools.keys())}",
        )

    result = await registry.execute_tool(request.tool_name, **request.parameters)

    return ToolCallResponse(
        tool_name=request.tool_name,
        success=result.success,
        data=result.data,
        error=result.error,
        elapsed_seconds=result.elapsed_seconds,
        metadata=result.metadata,
    )


# ============================================================================
# POST /tools/execute-batch -- Execute multiple tools in parallel
# ============================================================================

@router.post("/tools/execute-batch", summary="Execute multiple tools in parallel",
             dependencies=_admin_only)
async def execute_tools_batch(request: BatchToolCallRequest) -> list[ToolCallResponse]:
    """Execute multiple tools in parallel and return all results."""
    registry = await get_tool_registry()
    results = await registry.execute_tools_parallel(request.calls)

    responses = []
    for i, call in enumerate(request.calls):
        tool_name = call.get("name", f"unknown_{i}")
        result = results[i] if i < len(results) else ToolResult(success=False, error="No result")
        responses.append(ToolCallResponse(
            tool_name=tool_name,
            success=result.success,
            data=result.data,
            error=result.error,
            elapsed_seconds=result.elapsed_seconds,
            metadata=result.metadata,
        ))

    return responses


# ============================================================================
# POST /tools/parse-calls -- Parse tool calls from LLM output
# ============================================================================

@router.post("/tools/parse-calls", summary="Parse tool calls from LLM output text")
async def parse_tool_calls(payload: dict[str, str]) -> dict[str, Any]:
    """Extract tool call JSON blocks from LLM output text."""
    text = payload.get("text", "")
    calls = parse_tool_calls_from_text(text)
    return {"tool_calls": calls, "count": len(calls)}
