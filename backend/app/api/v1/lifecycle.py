"""Contract Lifecycle Management API endpoints.

Endpoints:
- POST /lifecycle/draft — Draft a contract (persisted as Contract v1)
- POST /lifecycle/contracts — Import an existing contract text as a draft
- POST /lifecycle/compare — Compare two contract versions (optionally saves a new version)
- POST /lifecycle/compliance — Compliance check
- POST /lifecycle/extract-dates — Extract key dates (optionally persisted)
- POST /lifecycle/redline — Apply redline changes (optionally saves a new version)
- GET  /lifecycle/contracts — List user's contracts
- GET  /lifecycle/contracts/{id} — Contract detail with versions, key dates, review
- POST /lifecycle/contracts/{id}/review — Review current content, link result
- POST /lifecycle/contracts/{id}/archive — Archive the contract
- DELETE /lifecycle/contracts/{id} — Delete the contract
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contract_lifecycle_agent import get_contract_lifecycle_agent
from app.agents.contract_review_agent import ContractReviewAgent, create_contract_review_agent
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.contract import Contract, ContractStatus, ContractVersion, ContractKeyDate
from app.models.document import ContractReview
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class DraftContractRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=10000)
    title: str = Field(default="", max_length=200, description="合同标题（默认自动生成）")
    contract_type: str = Field(default="", description="合同类型（如劳动合同、买卖合同）")
    fields: dict[str, str] | None = Field(default=None, description="模板字段")
    save: bool = Field(default=True, description="是否保存为合同草稿")


class ImportContractRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    contract_type: str = Field(default="")


class CompareContractsRequest(BaseModel):
    original: str = Field(..., min_length=1)
    modified: str = Field(..., min_length=1)
    original_label: str = Field(default="原版")
    modified_label: str = Field(default="修改版")
    contract_id: str | None = Field(
        default=None, description="若提供，将修改版保存为该合同的新版本",
    )


class ComplianceCheckRequest(BaseModel):
    contract_text: str = Field(..., min_length=1, max_length=50000)
    industry: str = Field(default="")


class ExtractDatesRequest(BaseModel):
    contract_text: str = Field(..., min_length=1, max_length=50000)
    contract_id: str | None = Field(
        default=None, description="若提供，将提取的日期保存到该合同",
    )


class RedlineRequest(BaseModel):
    original: str = Field(..., min_length=1)
    suggested_changes: list[dict[str, str]] = Field(..., description="修改建议列表")
    contract_id: str | None = Field(
        default=None, description="若提供，将红线修改结果保存为该合同的新版本",
    )


class ArchiveRequest(BaseModel):
    final_content: str | None = Field(default=None, max_length=200000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_owned_contract(
    contract_id: str, user: User, db: AsyncSession
) -> Contract:
    result = await db.execute(
        select(Contract).where(Contract.id == contract_id, Contract.user_id == user.id)
    )
    contract = result.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="合同不存在")
    return contract


async def _save_new_version(
    db: AsyncSession,
    contract: Contract,
    content: str,
    source: str,
    change_summary: str | None = None,
) -> ContractVersion:
    version = ContractVersion(
        contract_id=contract.id,
        version_number=contract.current_version + 1,
        source=source,
        content=content,
        change_summary=change_summary,
    )
    db.add(version)
    contract.current_version = version.version_number
    contract.content = content
    if contract.status == ContractStatus.ARCHIVED:
        contract.status = ContractStatus.REVIEWED
        contract.archived_at = None
    await db.flush()
    return version


def _make_title(description: str, contract_type: str) -> str:
    base = contract_type or "合同"
    snippet = description.strip().replace("\n", " ")[:30]
    return f"{base}（{snippet}）" if snippet else base


def _version_to_dict(v: ContractVersion) -> dict[str, Any]:
    return {
        "version_number": v.version_number,
        "source": v.source,
        "content": v.content,
        "change_summary": v.change_summary,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _key_date_to_dict(d: ContractKeyDate) -> dict[str, Any]:
    return {
        "id": d.id,
        "date_type": d.date_type,
        "date_value": d.date_value,
        "description": d.description,
        "reminder_days_before": d.reminder_days_before,
        "notified": d.notified,
    }


def _contract_to_dict(c: Contract, *, include_content: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": c.id,
        "title": c.title,
        "contract_type": c.contract_type,
        "status": c.status,
        "current_version": c.current_version,
        "latest_review_id": c.latest_review_id,
        "archived_at": c.archived_at.isoformat() if c.archived_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if include_content:
        data["content"] = c.content
    return data


# ---------------------------------------------------------------------------
# Draft & import
# ---------------------------------------------------------------------------

@router.post("/lifecycle/draft", summary="起草合同")
async def draft_contract(
    request: DraftContractRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Draft a contract from description or template and persist it as a draft."""
    agent = get_contract_lifecycle_agent()
    result = await agent.draft_contract(
        description=request.description,
        contract_type=request.contract_type,
        fields=request.fields,
    )
    if result.get("method") == "error":
        raise HTTPException(status_code=500, detail=result.get("error"))

    if request.save:
        content = result.get("content", "")
        if not content:
            raise HTTPException(status_code=500, detail="起草结果为空，未保存")
        contract = Contract(
            user_id=current_user.id,
            title=request.title or _make_title(request.description, request.contract_type),
            contract_type=request.contract_type,
            status=ContractStatus.DRAFT,
            current_version=1,
            content=content,
        )
        db.add(contract)
        await db.flush()
        db.add(ContractVersion(
            contract_id=contract.id,
            version_number=1,
            source="draft",
            content=content,
            change_summary="初始起草（{}）".format(
                "模板: " + result["template_name"] if result.get("method") == "template" else "AI生成"
            ),
        ))
        await db.commit()
        result["contract_id"] = contract.id
        result["version"] = 1
    return result


@router.post("/lifecycle/contracts", summary="导入现有合同文本", status_code=201)
async def import_contract(
    request: ImportContractRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Import existing contract text into lifecycle tracking (as draft v1)."""
    contract = Contract(
        user_id=current_user.id,
        title=request.title,
        contract_type=request.contract_type,
        status=ContractStatus.DRAFT,
        current_version=1,
        content=request.content,
    )
    db.add(contract)
    await db.flush()
    db.add(ContractVersion(
        contract_id=contract.id,
        version_number=1,
        source="import",
        content=request.content,
        change_summary="导入现有合同文本",
    ))
    await db.commit()
    return _contract_to_dict(contract, include_content=True)


# ---------------------------------------------------------------------------
# Compare / compliance / dates / redline
# ---------------------------------------------------------------------------

@router.post("/lifecycle/compare", summary="对比合同版本")
async def compare_contracts(
    request: CompareContractsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Compare two contract versions and analyze changes."""
    agent = get_contract_lifecycle_agent()
    result = await agent.compare_contracts(
        original=request.original,
        modified=request.modified,
        original_label=request.original_label,
        modified_label=request.modified_label,
    )
    if request.contract_id:
        contract = await _get_owned_contract(request.contract_id, current_user, db)
        summary = (result.get("analysis") or {}).get("summary", "")
        version = await _save_new_version(
            db, contract, request.modified, "compare",
            f"版本对比后保存：{summary[:200] if summary else '新增%d行/删除%d行' % (result.get('additions', 0), result.get('deletions', 0))}",
        )
        await db.commit()
        result["saved_version"] = version.version_number
        result["contract_id"] = contract.id
    return result


@router.post("/lifecycle/compliance", summary="合同合规检查")
async def compliance_check(
    request: ComplianceCheckRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Check contract compliance against current regulations."""
    agent = get_contract_lifecycle_agent()
    return await agent.compliance_check(
        contract_text=request.contract_text,
        industry=request.industry,
    )


@router.post("/lifecycle/extract-dates", summary="提取合同关键日期")
async def extract_dates(
    request: ExtractDatesRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Extract key dates from a contract for tracking."""
    agent = get_contract_lifecycle_agent()
    result = await agent.extract_key_dates(contract_text=request.contract_text)

    if request.contract_id:
        contract = await _get_owned_contract(request.contract_id, current_user, db)
        # Replace previously saved key dates with the latest extraction
        existing = await db.execute(
            select(ContractKeyDate).where(ContractKeyDate.contract_id == contract.id)
        )
        for row in existing.scalars():
            await db.delete(row)
        for d in result.get("dates", []):
            db.add(ContractKeyDate(
                contract_id=contract.id,
                date_type=str(d.get("type", "其他"))[:64],
                date_value=str(d.get("date", ""))[:64] or "未明确",
                description=d.get("description"),
                reminder_days_before=d.get("reminder_days_before"),
            ))
        await db.commit()
        result["saved_to_contract"] = contract.id
    return result


@router.post("/lifecycle/redline", summary="合同红线修改")
async def apply_redline(
    request: RedlineRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Apply suggested changes to a contract (redline mode)."""
    agent = get_contract_lifecycle_agent()
    result = await agent.generate_redline(
        original=request.original,
        suggested_changes=request.suggested_changes,
    )
    if request.contract_id and result.get("modified_content"):
        contract = await _get_owned_contract(request.contract_id, current_user, db)
        if result.get("changes_applied", 0) > 0:
            version = await _save_new_version(
                db, contract, result["modified_content"], "redline",
                f"红线修改：应用{result['changes_applied']}处修改",
            )
            await db.commit()
            result["saved_version"] = version.version_number
            result["contract_id"] = contract.id
    return result


# ---------------------------------------------------------------------------
# Contract CRUD + lifecycle transitions
# ---------------------------------------------------------------------------

@router.get("/lifecycle/contracts", summary="我的合同列表")
async def list_contracts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status", description="draft/reviewed/archived"),
) -> dict[str, Any]:
    """List the current user's contracts with pagination."""
    conditions = [Contract.user_id == current_user.id]
    if status_filter:
        conditions.append(Contract.status == status_filter)

    total_result = await db.execute(
        select(func.count(Contract.id)).where(*conditions)
    )
    total = total_result.scalar() or 0

    rows = await db.execute(
        select(Contract)
        .where(*conditions)
        .order_by(Contract.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    contracts = rows.scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_contract_to_dict(c) for c in contracts],
    }


@router.get("/lifecycle/contracts/{contract_id}", summary="合同详情")
async def get_contract_detail(
    contract_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get contract detail: content, versions, key dates and latest review."""
    contract = await _get_owned_contract(contract_id, current_user, db)

    versions_result = await db.execute(
        select(ContractVersion)
        .where(ContractVersion.contract_id == contract.id)
        .order_by(ContractVersion.version_number.desc())
    )
    versions = [_version_to_dict(v) for v in versions_result.scalars()]

    dates_result = await db.execute(
        select(ContractKeyDate).where(ContractKeyDate.contract_id == contract.id)
    )
    key_dates = [_key_date_to_dict(d) for d in dates_result.scalars()]

    review_summary = None
    if contract.latest_review_id:
        review_result = await db.execute(
            select(ContractReview).where(ContractReview.id == contract.latest_review_id)
        )
        review = review_result.scalar_one_or_none()
        if review:
            review_summary = {
                "id": review.id,
                "risk_score": review.risk_score,
                "summary": review.summary,
                "created_at": review.created_at.isoformat() if review.created_at else None,
            }

    data = _contract_to_dict(contract, include_content=True)
    data["versions"] = versions
    data["key_dates"] = key_dates
    data["latest_review"] = review_summary
    return data


@router.post("/lifecycle/contracts/{contract_id}/review", summary="审查合同当前版本")
async def review_contract(
    contract_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run the contract review agent on the current content and link the result."""
    global _review_agent
    contract = await _get_owned_contract(contract_id, current_user, db)

    if _review_agent is None:
        _review_agent = create_contract_review_agent()
    result = await _review_agent.run({"contract_text": contract.content})
    if not result.get("final_output") and not result.get("report"):
        raise HTTPException(status_code=500, detail="合同审查失败")

    review = ContractReview(
        user_id=current_user.id,
        document_id=None,
        original_filename=f"{contract.title}.txt",
        risk_score=float(result.get("risk_score", 0)),
        risk_items=json.dumps(result.get("risk_items", []), ensure_ascii=False),
        summary=result.get("final_output", "")[:2000],
        full_analysis=result.get("report", ""),
    )
    db.add(review)
    await db.flush()

    contract.latest_review_id = review.id
    if contract.status == ContractStatus.DRAFT:
        contract.status = ContractStatus.REVIEWED
    await db.commit()

    return {
        "review_id": review.id,
        "contract_id": contract.id,
        "contract_status": contract.status,
        "risk_score": review.risk_score,
        "risk_level": result.get("risk_level", "low"),
        "risk_items": result.get("risk_items", []),
        "missing_clauses": result.get("missing_clauses", []),
        "report": result.get("report", ""),
        "final_output": result.get("final_output", ""),
    }


@router.post("/lifecycle/contracts/{contract_id}/archive", summary="归档合同")
async def archive_contract(
    contract_id: str,
    request: ArchiveRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Archive a contract, optionally saving final content as a new version."""
    contract = await _get_owned_contract(contract_id, current_user, db)

    if request and request.final_content and request.final_content != contract.content:
        await _save_new_version(
            db, contract, request.final_content, "manual", "归档前最终定稿",
        )

    contract.status = ContractStatus.ARCHIVED
    contract.archived_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "id": contract.id,
        "status": contract.status,
        "archived_at": contract.archived_at.isoformat(),
        "current_version": contract.current_version,
    }


@router.delete("/lifecycle/contracts/{contract_id}", summary="删除合同")
async def delete_contract(
    contract_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Delete a contract and all its versions / key dates."""
    contract = await _get_owned_contract(contract_id, current_user, db)
    await db.delete(contract)
    await db.commit()
    return {"message": "合同已删除", "id": contract_id}


# Lazy singleton for the review agent
_review_agent: ContractReviewAgent | None = None
