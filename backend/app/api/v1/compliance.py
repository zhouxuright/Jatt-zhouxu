"""Compliance Risk Management API endpoints.

Endpoints:
- POST /compliance/assess — Risk assessment
- POST /compliance/checklist — Generate compliance checklist
- POST /compliance/track — Track regulation updates (ad-hoc web search)
- POST /compliance/report — Generate compliance report
- GET  /compliance/domains — List compliance domains
- GET/POST /compliance/watchlists — Watchlist CRUD (P1.5)
- PUT/DELETE /compliance/watchlists/{id}
- POST /compliance/scan — Run a monitor cycle now (P1.5)
- GET  /compliance/alerts — List compliance alerts (P1.5)
- POST /compliance/alerts/{id}/ack — Acknowledge an alert (P1.5)
- GET  /compliance/regulation-changes — Recent regulation changes (P1.5)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.compliance_risk_agent import (
    ComplianceRiskAgent,
    COMPLIANCE_DOMAINS,
    get_compliance_risk_agent,
)
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.compliance import ComplianceAlert, ComplianceWatchlist, RegulationChange
from app.models.user import User
from app.services.regulation_monitor import match_watchlists, sync_regulation_changes

logger = logging.getLogger(__name__)
router = APIRouter()


class RiskAssessmentRequest(BaseModel):
    business_description: str = Field(..., min_length=1, max_length=10000)
    industry: str = Field(default="")
    compliance_domains: list[str] | None = Field(default=None)


class ChecklistRequest(BaseModel):
    industry: str = Field(..., min_length=1)
    compliance_domains: list[str] | None = Field(default=None)


class TrackRegulationsRequest(BaseModel):
    topics: list[str] = Field(..., min_length=1, max_length=10)
    days_back: int = Field(default=30, ge=1, le=365)


class ComplianceReportRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    business_description: str = Field(..., min_length=1)
    industry: str = Field(default="")
    compliance_domains: list[str] | None = Field(default=None)


class WatchlistCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    industry: str = Field(default="", max_length=64)
    topics: list[str] = Field(default_factory=list, max_length=20)
    compliance_domains: list[str] = Field(default_factory=list, max_length=8)
    enabled: bool = True


class WatchlistUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    industry: str | None = Field(default=None, max_length=64)
    topics: list[str] | None = Field(default=None, max_length=20)
    compliance_domains: list[str] | None = Field(default=None, max_length=8)
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Watchlists (P1.5)
# ---------------------------------------------------------------------------

def _watchlist_to_dict(w: ComplianceWatchlist) -> dict[str, Any]:
    return {
        "id": w.id,
        "name": w.name,
        "industry": w.industry,
        "topics": json.loads(w.topics or "[]"),
        "compliance_domains": json.loads(w.compliance_domains or "[]"),
        "enabled": w.enabled,
        "last_scan_at": w.last_scan_at.isoformat() if w.last_scan_at else None,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }


@router.get("/compliance/watchlists", summary="监控配置列表")
async def list_watchlists(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await db.execute(
        select(ComplianceWatchlist)
        .where(ComplianceWatchlist.user_id == current_user.id)
        .order_by(ComplianceWatchlist.created_at.desc())
    )
    return {"items": [_watchlist_to_dict(w) for w in rows.scalars().all()]}


@router.post("/compliance/watchlists", summary="创建监控配置", status_code=201)
async def create_watchlist(
    request: WatchlistCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    invalid = [d for d in request.compliance_domains if d not in COMPLIANCE_DOMAINS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"未知合规领域: {invalid}")

    watchlist = ComplianceWatchlist(
        user_id=current_user.id,
        name=request.name,
        industry=request.industry,
        topics=json.dumps([t.strip() for t in request.topics if t.strip()],
                          ensure_ascii=False),
        compliance_domains=json.dumps(request.compliance_domains),
        enabled=request.enabled,
    )
    db.add(watchlist)
    await db.commit()
    return _watchlist_to_dict(watchlist)


@router.put("/compliance/watchlists/{watchlist_id}", summary="更新监控配置")
async def update_watchlist(
    watchlist_id: str,
    request: WatchlistUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(ComplianceWatchlist).where(
            ComplianceWatchlist.id == watchlist_id,
            ComplianceWatchlist.user_id == current_user.id,
        )
    )
    watchlist = result.scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status_code=404, detail="监控配置不存在")

    if request.name is not None:
        watchlist.name = request.name
    if request.industry is not None:
        watchlist.industry = request.industry
    if request.topics is not None:
        watchlist.topics = json.dumps(
            [t.strip() for t in request.topics if t.strip()], ensure_ascii=False)
    if request.compliance_domains is not None:
        invalid = [d for d in request.compliance_domains if d not in COMPLIANCE_DOMAINS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"未知合规领域: {invalid}")
        watchlist.compliance_domains = json.dumps(request.compliance_domains)
    if request.enabled is not None:
        watchlist.enabled = request.enabled
    await db.commit()
    return _watchlist_to_dict(watchlist)


@router.delete("/compliance/watchlists/{watchlist_id}", summary="删除监控配置")
async def delete_watchlist(
    watchlist_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    result = await db.execute(
        select(ComplianceWatchlist).where(
            ComplianceWatchlist.id == watchlist_id,
            ComplianceWatchlist.user_id == current_user.id,
        )
    )
    watchlist = result.scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status_code=404, detail="监控配置不存在")
    await db.delete(watchlist)
    await db.commit()
    return {"message": "监控配置已删除", "id": watchlist_id}


@router.post("/compliance/scan", summary="立即扫描法规动态")
async def scan_now(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days_back: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """Run a monitor cycle now: sync FLK changes then match this user's watchlists."""
    changes = await sync_regulation_changes(days_back=days_back)
    # Rematch the full window so a newly created watchlist also matches
    # changes synced by earlier cycles; unique constraints keep it idempotent.
    alerts = await match_watchlists(
        days_back=days_back, only_user_id=current_user.id,
    )
    return {
        "changes_synced": len(changes),
        "alerts_created": alerts,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/compliance/alerts", summary="合规告警列表")
async def list_alerts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status_filter: str | None = Query(None, alias="status",
                                      description="open/acknowledged/resolved"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    conditions = [ComplianceAlert.user_id == current_user.id]
    if status_filter:
        conditions.append(ComplianceAlert.status == status_filter)

    total = (await db.execute(
        select(func.count(ComplianceAlert.id)).where(*conditions)
    )).scalar() or 0

    rows = await db.execute(
        select(ComplianceAlert, RegulationChange, ComplianceWatchlist)
        .join(RegulationChange,
              RegulationChange.id == ComplianceAlert.regulation_change_id)
        .join(ComplianceWatchlist,
              ComplianceWatchlist.id == ComplianceAlert.watchlist_id)
        .where(*conditions)
        .order_by(ComplianceAlert.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = []
    for alert, change, watchlist in rows.all():
        try:
            analysis = json.loads(alert.analysis) if alert.analysis else None
        except (json.JSONDecodeError, TypeError):
            analysis = None
        items.append({
            "id": alert.id,
            "risk_level": alert.risk_level,
            "matched_keyword": alert.matched_keyword,
            "status": alert.status,
            "created_at": alert.created_at.isoformat() if alert.created_at else None,
            "watchlist_name": watchlist.name,
            "regulation": {
                "title": change.title,
                "law_type": change.law_type,
                "publish_date": change.publish_date,
                "effective_date": change.effective_date,
                "status": change.status,
                "url": change.url,
            },
            "analysis": analysis,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.post("/compliance/alerts/{alert_id}/ack", summary="确认告警")
async def acknowledge_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(ComplianceAlert).where(
            ComplianceAlert.id == alert_id,
            ComplianceAlert.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": alert.id, "status": alert.status}


@router.get("/compliance/regulation-changes", summary="近期法规变更")
async def list_regulation_changes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days_back: int = Query(30, ge=1, le=365),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Recent regulation changes detected by the monitor."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    conditions = [RegulationChange.publish_date >= since]
    total = (await db.execute(
        select(func.count(RegulationChange.id)).where(*conditions)
    )).scalar() or 0

    rows = await db.execute(
        select(RegulationChange)
        .where(*conditions)
        .order_by(RegulationChange.publish_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [{
        "id": c.id,
        "title": c.title,
        "law_type": c.law_type,
        "publish_date": c.publish_date,
        "effective_date": c.effective_date,
        "status": c.status,
        "url": c.url,
    } for c in rows.scalars().all()]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


# ---------------------------------------------------------------------------
# Assessment / checklist / tracking / report (existing capabilities)
# ---------------------------------------------------------------------------

@router.post("/compliance/assess", summary="合规风险评估")
async def risk_assessment(
    request: RiskAssessmentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Assess compliance risks for a business operation."""
    agent = get_compliance_risk_agent()
    return await agent.risk_assessment(
        business_description=request.business_description,
        industry=request.industry,
        compliance_domains=request.compliance_domains,
    )


@router.post("/compliance/checklist", summary="生成合规检查清单")
async def generate_checklist(
    request: ChecklistRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate industry-specific compliance checklist."""
    agent = get_compliance_risk_agent()
    return await agent.generate_checklist(
        industry=request.industry,
        compliance_domains=request.compliance_domains,
    )


@router.post("/compliance/track", summary="法规动态追踪")
async def track_regulations(
    request: TrackRegulationsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Track recent regulation updates for specified topics."""
    agent = get_compliance_risk_agent()
    return await agent.track_regulation_updates(
        topics=request.topics,
        days_back=request.days_back,
    )


@router.post("/compliance/report", summary="生成合规报告")
async def generate_report(
    request: ComplianceReportRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate comprehensive compliance audit report."""
    agent = get_compliance_risk_agent()

    # First run assessment
    assessment = await agent.risk_assessment(
        business_description=request.business_description,
        industry=request.industry,
        compliance_domains=request.compliance_domains,
    )

    # Then generate report
    report = await agent.generate_compliance_report(
        company_name=request.company_name,
        assessment_result=assessment,
    )

    return {
        "company": request.company_name,
        "assessment": assessment,
        "report": report.get("report", ""),
        "generated_at": report.get("generated_at", ""),
    }


@router.get("/compliance/domains", summary="合规领域列表")
async def list_domains(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all available compliance domains."""
    domains = []
    for domain_id, domain in COMPLIANCE_DOMAINS.items():
        domains.append({
            "id": domain_id,
            "name": domain["name"],
            "regulations": domain["regulations"],
            "keywords": domain["keywords"],
        })
    return {"domains": domains, "total": len(domains)}
