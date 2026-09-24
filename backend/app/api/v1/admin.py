"""Admin endpoints -- user management, audit log queries, data cleanup."""

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db, get_tenant_id
from app.core.config import settings
from app.core.tenancy import is_platform_admin, scope_query
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.document import ContractReview, Document
from app.models.feedback import Feedback
from app.models.message import Message
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AdminUserResponse(BaseModel):
    """User record returned to admins."""
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    tenant_id: str | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    deletion_requested_at: datetime | None = None

    model_config = {"from_attributes": True}


class AdminUserListResponse(BaseModel):
    """Paginated list of users."""
    users: list[AdminUserResponse]
    total: int
    page: int
    page_size: int


class AuditLogEntry(BaseModel):
    """Single audit log entry returned to admins."""
    id: str
    user_id: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_method: str | None = None
    request_path: str | None = None
    status_code: int | None = None
    request_summary: str | None = None
    request_body_hash: str | None = None
    duration_ms: int | None = None
    response_status: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    """Paginated list of audit logs."""
    logs: list[AuditLogEntry]
    total: int
    page: int
    page_size: int


class CleanupResponse(BaseModel):
    """Response from the data cleanup endpoint."""
    message: str
    permanently_deleted_users: int
    data_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of deleted data by type",
    )


# ---------------------------------------------------------------------------
# GET /admin/users -- List all users
# ---------------------------------------------------------------------------

@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    search: str | None = Query(None, description="Search by username or email"),
    role: str | None = Query(None, description="Filter by role"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    all_tenants: bool = Query(False, description="平台管理员：跨租户查看"),
) -> AdminUserListResponse:
    """List all users (admin only). Supports pagination and basic filtering.

    P0-2：默认仅返回 **本租户** 用户；平台管理员可显式带上
    ``all_tenants=true`` 跨租户查看（用于运营后台）。
    """
    query = select(User)
    count_query = select(func.count()).select_from(User)

    # 租户作用域（平台管理员可显式放开）
    effective_tenant: str | None = tenant_id
    if all_tenants and is_platform_admin(_admin):
        effective_tenant = None
    query = scope_query(query, User, effective_tenant)
    count_query = scope_query(count_query, User, effective_tenant)

    # Apply filters
    if search:
        # email 已字段级加密，无法做子串匹配；含 "@" 时按盲索引精确命中，
        # 否则仅对用户名做模糊匹配。
        if "@" in search:
            email_clause = User.email_lookup(search.strip())
            query = query.where(User.username.ilike(f"%{search}%") | email_clause)
            count_query = count_query.where(User.username.ilike(f"%{search}%") | email_clause)
        else:
            search_pattern = f"%{search}%"
            query = query.where(User.username.ilike(search_pattern))
            count_query = count_query.where(User.username.ilike(search_pattern))
    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(User.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    users = result.scalars().all()

    return AdminUserListResponse(
        users=[AdminUserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /admin/audit-logs -- Query audit logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs", response_model=AuditLogListResponse)
async def query_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Page size"),
    user_id: str | None = Query(None, description="Filter by user ID"),
    action: str | None = Query(None, description="Filter by action"),
    request_method: str | None = Query(None, description="Filter by HTTP method"),
    status_code: int | None = Query(None, description="Filter by status code"),
    start_date: datetime | None = Query(None, description="Filter from date"),
    end_date: datetime | None = Query(None, description="Filter to date"),
    all_tenants: bool = Query(False, description="平台管理员：跨租户查看"),
) -> AuditLogListResponse:
    """Query audit logs with filters (admin only). P0-2：默认限定本租户。"""
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    effective_tenant: str | None = tenant_id
    if all_tenants and is_platform_admin(_admin):
        effective_tenant = None
    query = scope_query(query, AuditLog, effective_tenant)
    count_query = scope_query(count_query, AuditLog, effective_tenant)

    # Apply filters
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
        count_query = count_query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if request_method:
        query = query.where(AuditLog.request_method == request_method.upper())
        count_query = count_query.where(AuditLog.request_method == request_method.upper())
    if status_code is not None:
        query = query.where(AuditLog.status_code == status_code)
        count_query = count_query.where(AuditLog.status_code == status_code)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
        count_query = count_query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
        count_query = count_query.where(AuditLog.created_at <= end_date)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    logs = result.scalars().all()

    return AuditLogListResponse(
        logs=[AuditLogEntry.model_validate(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# POST /admin/cleanup -- Hard-delete soft-deleted accounts past grace period
# ---------------------------------------------------------------------------

@router.post("/cleanup", response_model=CleanupResponse)
async def run_cleanup(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> CleanupResponse:
    """Permanently delete accounts that were soft-deleted beyond the grace period.

    This removes the user record and any remaining associated data.
    """
    grace_days = settings.ACCOUNT_DELETION_GRACE_PERIOD_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)

    # Find users whose deletion_requested_at is past the grace period
    result = await db.execute(
        select(User).where(
            User.is_active == False,  # noqa: E712
            User.deleted_at.isnot(None),
            User.deletion_requested_at <= cutoff,
        )
    )
    expired_users = result.scalars().all()

    if not expired_users:
        return CleanupResponse(
            message="No accounts past the grace period found.",
            permanently_deleted_users=0,
            data_counts={},
        )

    data_counts: dict[str, int] = {
        "feedbacks": 0,
        "contract_reviews": 0,
        "documents": 0,
        "audit_logs": 0,
        "messages": 0,
        "conversations": 0,
        "users": 0,
    }

    for user in expired_users:
        uid = user.id

        # Delete remaining associated data (defensive -- most may already be gone)
        fb_count = await db.execute(
            select(func.count()).select_from(Feedback).where(Feedback.user_id == uid)
        )
        data_counts["feedbacks"] += fb_count.scalar() or 0
        await db.execute(delete(Feedback).where(Feedback.user_id == uid))

        cr_count = await db.execute(
            select(func.count()).select_from(ContractReview).where(ContractReview.user_id == uid)
        )
        data_counts["contract_reviews"] += cr_count.scalar() or 0
        await db.execute(delete(ContractReview).where(ContractReview.user_id == uid))

        doc_count = await db.execute(
            select(func.count()).select_from(Document).where(Document.user_id == uid)
        )
        data_counts["documents"] += doc_count.scalar() or 0
        await db.execute(delete(Document).where(Document.user_id == uid))

        al_count = await db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.user_id == uid)
        )
        data_counts["audit_logs"] += al_count.scalar() or 0
        await db.execute(delete(AuditLog).where(AuditLog.user_id == uid))

        # Delete messages via conversations
        conv_ids_result = await db.execute(
            select(Conversation.id).where(Conversation.user_id == uid)
        )
        conv_ids = [row[0] for row in conv_ids_result.all()]
        if conv_ids:
            msg_count = await db.execute(
                select(func.count()).select_from(Message).where(
                    Message.conversation_id.in_(conv_ids)
                )
            )
            data_counts["messages"] += msg_count.scalar() or 0
            await db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))

        conv_count = await db.execute(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == uid)
        )
        data_counts["conversations"] += conv_count.scalar() or 0
        await db.execute(delete(Conversation).where(Conversation.user_id == uid))

        # Hard-delete the user
        await db.delete(user)
        data_counts["users"] += 1

    await db.flush()

    return CleanupResponse(
        message=(
            f"Permanently deleted {data_counts['users']} account(s) "
            f"past the {grace_days}-day grace period."
        ),
        permanently_deleted_users=data_counts["users"],
        data_counts=data_counts,
    )


# =============================================================================
# GET /corpus-registry -- 法律语料资产与来源治理台账
# =============================================================================

CORPUS_TARGET = 100_000_000


class CorpusRegistryItem(BaseModel):
    """One row of the legal-corpus provenance ledger."""

    source_key: str
    display_name: str
    storage: str
    location: str
    provenance: str = Field(description="authoritative | open_source | synthetic")
    citable: bool = Field(description="是否可作为法律依据向用户引用")
    record_count: int
    commercial_use: str | None = None
    note: str | None = None


class CorpusRegistryResponse(BaseModel):
    """Corpus asset overview, split by citation eligibility."""

    items: list[CorpusRegistryItem]
    citable_total: int = Field(description="可引用（citation-grade）语料总条数")
    non_citable_total: int = Field(description="不可引用语料总条数")
    synthetic_total: int = Field(description="合成数据条数（严禁作为判例展示）")
    target: int
    completion_pct: float


@router.get(
    "/corpus-registry",
    response_model=CorpusRegistryResponse,
    summary="法律语料资产与来源治理台账",
)
async def get_corpus_registry(
    current_user: Annotated[User, Depends(get_current_admin_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CorpusRegistryResponse:
    """Return the measured legal-corpus inventory with provenance labels.

    This is the single source of truth for "how much legal data do we have,
    and which of it may be cited to a user".  Synthetic records are reported
    separately and flagged as non-citable on purpose: surfacing them as
    precedent is how legal-AI products end up fabricating citations.

    The registry is refreshed by ``scripts/corpus_audit.py``.
    """
    from sqlalchemy import text as _text

    try:
        rows = (
            await db.execute(
                _text(
                    "SELECT source_key, display_name, storage, location, provenance,"
                    "       citable, record_count, commercial_use, note"
                    "  FROM corpus_registry"
                    " ORDER BY record_count DESC"
                )
            )
        ).all()
    except Exception:
        # The ledger is created by ``scripts/corpus_audit.py``.  Until it has
        # been run once the table is absent -- degrade to an empty ledger
        # instead of returning a 500.
        await db.rollback()
        logger.info("corpus_registry not initialised yet; run scripts/corpus_audit.py")
        rows = []

    items = [
        CorpusRegistryItem(
            source_key=r[0],
            display_name=r[1],
            storage=r[2],
            location=r[3],
            provenance=r[4],
            citable=bool(r[5]),
            record_count=int(r[6] or 0),
            commercial_use=r[7],
            note=r[8],
        )
        for r in rows
    ]

    citable_total = sum(i.record_count for i in items if i.citable)
    non_citable_total = sum(i.record_count for i in items if not i.citable)
    synthetic_total = sum(i.record_count for i in items if i.provenance == "synthetic")

    return CorpusRegistryResponse(
        items=items,
        citable_total=citable_total,
        non_citable_total=non_citable_total,
        synthetic_total=synthetic_total,
        target=CORPUS_TARGET,
        completion_pct=round(citable_total / CORPUS_TARGET * 100, 2),
    )


# ===========================================================================
# P0-2 租户管理（多租户运营）
# ===========================================================================

class TenantCreateRequest(BaseModel):
    """新建租户。"""
    name: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    plan: str = Field(default="standard", max_length=32)
    max_users: int = Field(default=50, ge=1, le=100000)
    contact_email: str | None = Field(default=None, max_length=256)
    remark: str | None = Field(default=None, max_length=2000)


class TenantResponse(BaseModel):
    """租户信息。"""
    id: str
    name: str
    code: str
    status: str
    plan: str
    max_users: int
    contact_email: str | None = None
    remark: str | None = None
    user_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class TenantListResponse(BaseModel):
    tenants: list[TenantResponse]
    total: int


class AssignTenantRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=36)


@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> TenantListResponse:
    """列出全部租户（仅平台管理员）。"""
    if not is_platform_admin(_admin):
        raise HTTPException(status_code=403, detail="Platform admin privileges required")

    total = (await db.execute(select(func.count()).select_from(Tenant))).scalar() or 0
    rows = (
        await db.execute(
            select(Tenant).order_by(Tenant.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()

    counts = dict(
        (await db.execute(
            select(User.tenant_id, func.count(User.id)).group_by(User.tenant_id)
        )).all()
    )

    return TenantListResponse(
        tenants=[
            TenantResponse(
                id=t.id, name=t.name, code=t.code, status=t.status, plan=t.plan,
                max_users=t.max_users, contact_email=t.contact_email,
                remark=t.remark, user_count=int(counts.get(t.id, 0) or 0),
                created_at=t.created_at,
            )
            for t in rows
        ],
        total=total,
    )


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> TenantResponse:
    """创建租户（仅平台管理员）。"""
    if not is_platform_admin(_admin):
        raise HTTPException(status_code=403, detail="Platform admin privileges required")

    exists = (
        await db.execute(select(Tenant).where(Tenant.code == payload.code))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail="Tenant code already exists")

    tenant = Tenant(
        name=payload.name, code=payload.code, status=TenantStatus.ACTIVE,
        plan=payload.plan, max_users=payload.max_users,
        contact_email=payload.contact_email, remark=payload.remark,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    return TenantResponse(
        id=tenant.id, name=tenant.name, code=tenant.code, status=tenant.status,
        plan=tenant.plan, max_users=tenant.max_users,
        contact_email=tenant.contact_email, remark=tenant.remark,
        user_count=0, created_at=tenant.created_at,
    )


@router.put("/users/{user_id}/tenant", response_model=AdminUserResponse)
async def assign_user_tenant(
    user_id: str,
    payload: AssignTenantRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
) -> AdminUserResponse:
    """把用户划归某个租户（仅平台管理员）。"""
    if not is_platform_admin(_admin):
        raise HTTPException(status_code=403, detail="Platform admin privileges required")

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == payload.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.tenant_id = tenant.id
    await db.flush()
    await db.refresh(user)
    return AdminUserResponse.model_validate(user)


# ===========================================================================
# P0-4 合规审计日志导出（客户可查凭证）
# ===========================================================================

_AUDIT_EXPORT_COLUMNS = [
    ("created_at", "时间戳(UTC)"),
    ("user_id", "操作人ID"),
    ("tenant_id", "租户ID"),
    ("action", "操作/AI动作"),
    ("resource_type", "资源类型"),
    ("resource_id", "资源ID"),
    ("request_method", "HTTP方法"),
    ("request_path", "请求路径"),
    ("status_code", "状态码"),
    ("duration_ms", "耗时(ms)"),
    ("ip_address", "来源IP"),
    ("request_summary", "摘要/引用校验结果"),
    ("request_body_hash", "请求体指纹"),
]


def _audit_query(
    tenant_id: str | None, user_id: str | None, action: str | None,
    start_date: datetime | None, end_date: datetime | None, status_code: int | None,
):
    stmt = select(AuditLog)
    stmt = scope_query(stmt, AuditLog, tenant_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if status_code is not None:
        stmt = stmt.where(AuditLog.status_code == status_code)
    if start_date:
        stmt = stmt.where(AuditLog.created_at >= start_date)
    if end_date:
        stmt = stmt.where(AuditLog.created_at <= end_date)
    return stmt.order_by(AuditLog.created_at.desc())


@router.get("/audit-logs/export")
async def export_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[User, Depends(get_current_admin_user)],
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    format: str = Query("csv", pattern="^(csv|json)$", description="导出格式"),
    user_id: str | None = Query(None),
    action: str | None = Query(None),
    status_code: int | None = Query(None),
    start_date: datetime | None = Query(None),
    end_date: datetime | None = Query(None),
    limit: int = Query(20000, ge=1, le=200000, description="最多导出条数"),
) -> Response:
    """导出合规审计日志（P0-4）。

    面向监管检查与客户取证，导出内容包含：操作人、AI 动作、引用校验结果、
    时间戳、来源 IP 与请求指纹。默认限定当前租户。
    """
    stmt = _audit_query(tenant_id, user_id, action, start_date, end_date, status_code)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if format == "json":
        payload = [
            {key: (getattr(r, key).isoformat() if key == "created_at" and getattr(r, key) else getattr(r, key)) for key, _ in _AUDIT_EXPORT_COLUMNS}
            for r in rows
        ]
        return Response(
            content=json.dumps(
                {
                    "exported_at": stamp,
                    "tenant_id": tenant_id,
                    "count": len(payload),
                    "columns": [c[1] for c in _AUDIT_EXPORT_COLUMNS],
                    "items": payload,
                },
                ensure_ascii=False, indent=2, default=str,
            ),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="audit_logs_{stamp}.json"'},
        )

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM：Excel 直接打开不乱码
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in _AUDIT_EXPORT_COLUMNS])
    for r in rows:
        writer.writerow([
            getattr(r, key).isoformat() if key == "created_at" and getattr(r, key) else (getattr(r, key) if getattr(r, key) is not None else "")
            for key, _ in _AUDIT_EXPORT_COLUMNS
        ])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit_logs_{stamp}.csv"'},
    )
