"""Authentication endpoints -- register, login, get current user, account deletion."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.tenancy import get_default_tenant_id
from app.models.audit_log import AuditLog
from app.models.conversation import Conversation
from app.models.document import ContractReview, Document
from app.models.feedback import Feedback
from app.models.message import Message
from app.models.user import User
from app.schemas.auth import (
    AccountDeleteRequest,
    AccountDeleteResponse,
    LoginResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    Token,
    UserCreate,
    UserLogin,
    UserResponse,
)

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _create_access_token(
    user_id: str, tenant_id: str | None = None
) -> tuple[str, datetime]:
    """签发访问令牌。

    ``tid``（租户标识）随令牌下发，使审计中间件无需为每个请求额外查库
    即可把租户写入 ``audit_logs``——多租户隔离在审计链路上同样成立。
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    if tenant_id:
        payload["tid"] = tenant_id
    token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return token, expire


def _create_refresh_token(
    user_id: str, tenant_id: str | None = None
) -> tuple[str, datetime]:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    if tenant_id:
        payload["tid"] = tenant_id
    token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return token, expire


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Register a new user account."""
    # Check if username already exists
    result = await db.execute(select(User).where(User.username == payload.username))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    # Check if email already exists (email 已加密，走盲索引精确匹配)
    result = await db.execute(select(User).where(User.email_lookup(payload.email)))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=_hash_password(payload.password),
        role=payload.role,
        # P0-2：注册即绑定租户，避免出现"无租户"的孤儿用户
        tenant_id=get_default_tenant_id(),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: UserLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Authenticate and return a JWT access token."""
    # Try by username first, then by email (email 密文随机化，故走盲索引)
    result = await db.execute(
        select(User).where(
            (User.username == payload.username) | User.email_lookup(payload.username)
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not _verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    user_tenant = str(user.tenant_id) if user.tenant_id else None
    token_str, expire = _create_access_token(str(user.id), user_tenant)
    refresh_token_str, _ = _create_refresh_token(str(user.id), user_tenant)
    expires_in = int((expire - datetime.now(timezone.utc)).total_seconds())

    return LoginResponse(
        token=Token(
            access_token=token_str,
            refresh_token=refresh_token_str,
            expires_in=expires_in,
        ),
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(payload: RefreshTokenRequest) -> RefreshTokenResponse:
    """Validate a refresh token and issue a new access token + refresh token."""
    try:
        decoded = jwt.decode(
            payload.refresh_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = decoded.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    tenant_id = decoded.get("tid")
    access_token_str, access_expire = _create_access_token(user_id, tenant_id)
    refresh_token_str, _ = _create_refresh_token(user_id, tenant_id)
    expires_in = int(
        (access_expire - datetime.now(timezone.utc)).total_seconds()
    )

    return RefreshTokenResponse(
        access_token=access_token_str,
        refresh_token=refresh_token_str,
        expires_in=expires_in,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Return the currently authenticated user's profile."""
    return current_user


@router.delete("/account", response_model=AccountDeleteResponse)
async def delete_account(
    payload: AccountDeleteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AccountDeleteResponse:
    """Soft-delete the authenticated user's account and all associated data.

    The account is deactivated (is_active=False) and marked for permanent
    deletion after the grace period defined in
    ``settings.ACCOUNT_DELETION_GRACE_PERIOD_DAYS`` (default 30 days).
    This is required by China's Personal Information Protection Law (PIPL).
    """
    # Verify password before allowing deletion
    if not _verify_password(payload.password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="密码验证失败，无法删除账户",
        )

    user_id = current_user.id

    # Count and delete associated data
    data_counts: dict[str, int] = {}

    # Delete feedbacks (linked to user directly and through messages)
    feedbacks_result = await db.execute(
        select(func.count()).select_from(Feedback).where(Feedback.user_id == user_id)
    )
    data_counts["feedbacks"] = feedbacks_result.scalar() or 0
    await db.execute(delete(Feedback).where(Feedback.user_id == user_id))

    # Delete contract reviews
    cr_result = await db.execute(
        select(func.count()).select_from(ContractReview).where(ContractReview.user_id == user_id)
    )
    data_counts["contract_reviews"] = cr_result.scalar() or 0
    await db.execute(delete(ContractReview).where(ContractReview.user_id == user_id))

    # Delete documents
    docs_result = await db.execute(
        select(func.count()).select_from(Document).where(Document.user_id == user_id)
    )
    data_counts["documents"] = docs_result.scalar() or 0
    await db.execute(delete(Document).where(Document.user_id == user_id))

    # Delete audit logs for this user
    audit_result = await db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.user_id == user_id)
    )
    data_counts["audit_logs"] = audit_result.scalar() or 0
    await db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))

    # Delete messages (through conversations)
    conv_ids_result = await db.execute(
        select(Conversation.id).where(Conversation.user_id == user_id)
    )
    conv_ids = [row[0] for row in conv_ids_result.all()]

    messages_count = 0
    if conv_ids:
        msgs_result = await db.execute(
            select(func.count()).select_from(Message).where(
                Message.conversation_id.in_(conv_ids)
            )
        )
        messages_count = msgs_result.scalar() or 0
        await db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
    data_counts["messages"] = messages_count

    # Delete conversations
    convs_result = await db.execute(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
    )
    data_counts["conversations"] = convs_result.scalar() or 0
    await db.execute(delete(Conversation).where(Conversation.user_id == user_id))

    # Soft-delete the user account
    now = datetime.now(timezone.utc)
    current_user.is_active = False
    current_user.deleted_at = now
    current_user.deletion_requested_at = now

    await db.flush()

    return AccountDeleteResponse(
        message=(
            f"账户已成功删除。所有关联数据已清除。"
            f"账户将在 {settings.ACCOUNT_DELETION_GRACE_PERIOD_DAYS} 天后永久删除。"
        ),
        deleted_at=now,
        grace_period_days=settings.ACCOUNT_DELETION_GRACE_PERIOD_DAYS,
        data_counts=data_counts,
    )