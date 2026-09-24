"""Litigation Support API endpoints.

Endpoints:
- POST /litigation/analyze — Comprehensive case analysis
- POST /litigation/evidence — Generate evidence list
- POST /litigation/trial-outline — Generate trial preparation outline
- POST /litigation/predict — Predict case outcome (auto-retrieves similar cases if absent)
- POST /litigation/costs — Calculate litigation costs
- POST /litigation/similar-cases — P1.4: retrieve & compare similar cases
- POST /litigation/report — P1.4: one-click full litigation report (输出成文)
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.litigation_support_agent import get_litigation_support_agent
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


class CaseAnalyzeRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=10000)
    evidence_list: str = Field(default="")
    claims: str = Field(default="")


class EvidenceListRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=5000)
    cause_of_action: str = Field(default="")


class TrialOutlineRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=5000)
    party: str = Field(default="plaintiff", description="plaintiff 或 defendant")
    analysis: dict[str, Any] | None = Field(default=None, description="前置分析结果")


class PredictOutcomeRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=5000)
    cause_of_action: str = Field(default="")
    similar_cases: list[dict] | None = Field(default=None)


class CostCalcRequest(BaseModel):
    claim_amount: float = Field(..., gt=0, description="诉讼标的额（元）")
    case_type: str = Field(default="civil")


class SimilarCasesRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=5000)
    cause_of_action: str = Field(default="")
    top_k: int = Field(default=5, ge=1, le=10)


class LitigationReportRequest(BaseModel):
    case_description: str = Field(..., min_length=1, max_length=10000)
    evidence_list: str = Field(default="", max_length=5000)
    claims: str = Field(default="", max_length=2000)
    party: str = Field(default="plaintiff", description="plaintiff 或 defendant")
    top_k: int = Field(default=5, ge=1, le=10)


@router.post("/litigation/analyze", summary="案件综合分析")
async def analyze_case(
    request: CaseAnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Comprehensive case analysis for litigation."""
    agent = get_litigation_support_agent()
    result = await agent.analyze_case(
        case_description=request.case_description,
        evidence_list=request.evidence_list,
        claims=request.claims,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error"))
    return result


@router.post("/litigation/evidence", summary="生成证据清单")
async def generate_evidence_list(
    request: EvidenceListRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate structured evidence list for litigation."""
    agent = get_litigation_support_agent()
    return await agent.generate_evidence_list(
        case_description=request.case_description,
        cause_of_action=request.cause_of_action,
    )


@router.post("/litigation/trial-outline", summary="生成庭审提纲")
async def generate_trial_outline(
    request: TrialOutlineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate trial preparation outline."""
    agent = get_litigation_support_agent()
    return await agent.generate_trial_outline(
        case_description=request.case_description,
        party=request.party,
        analysis=request.analysis,
    )


@router.post("/litigation/predict", summary="裁判结果预测")
async def predict_outcome(
    request: PredictOutcomeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Predict litigation outcome based on case facts."""
    agent = get_litigation_support_agent()
    return await agent.predict_outcome(
        case_description=request.case_description,
        cause_of_action=request.cause_of_action,
        similar_cases=request.similar_cases,
    )


@router.post("/litigation/costs", summary="诉讼费用计算")
async def calculate_costs(
    request: CostCalcRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Calculate litigation costs."""
    agent = get_litigation_support_agent()
    return await agent.calculate_litigation_costs(
        claim_amount=request.claim_amount,
        case_type=request.case_type,
    )


@router.post("/litigation/similar-cases", summary="类案检索与比对 (P1.4)")
async def similar_case_comparison(
    request: SimilarCasesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve similar cases from the local court_cases library (semantic
    search) and run a comparison analysis: outcome distribution, amount
    statistics (code-side), and qualitative fact comparison (LLM)."""
    agent = get_litigation_support_agent()
    return await agent.compare_similar_cases(
        case_description=request.case_description,
        cause_of_action=request.cause_of_action,
        top_k=request.top_k,
    )


@router.post("/litigation/report", summary="一键生成诉讼分析报告 (P1.4)")
async def generate_litigation_report(
    request: LitigationReportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One-click full litigation report: case analysis + evidence list +
    similar case comparison + outcome prediction, assembled into a formal
    markdown document (输出成文). Two-phase parallel pipeline keeps latency
    well under the 300s gateway timeout."""
    agent = get_litigation_support_agent()
    result = await agent.generate_litigation_report(
        case_description=request.case_description,
        evidence_list=request.evidence_list,
        claims=request.claims,
        party=request.party,
        top_k=request.top_k,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail="报告生成失败，请稍后重试")
    return result
